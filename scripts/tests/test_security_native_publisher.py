"""Focused tests for the separate native GitHub publisher. No network."""
import copy
from datetime import datetime, timezone
import importlib.util
import pathlib
import sqlite3
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "infra/observability/security-incident-publisher/publisher.py"
spec = importlib.util.spec_from_file_location("security_native_publisher", SOURCE)
pub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pub)


def incident():
    return {
        "key": "a"*64,
        "workload": "homepage/Deployment/homepage",
        "rule": "R1000",
        "severity": "High",
        "first": "2026-11-24T04:43:52Z",
        "last": "2026-11-24T04:43:52Z",
        "count": 1,
        "state": "open",
    }


def envelope(value):
    return {"schema":1, "source":"kubescape-native", "incidents":[value]}


class FakeGitHub:
    def __init__(self):
        self.issues = {}
        self.next_id = 2001
        self.create_calls = 0
        self.comments_written = 0
        self.fail_after_create = False
        self.fail_before_create = False
        self.fail_after_comment = False

    def search(self, key):
        items = [x for x in self.issues.values() if pub.marker(key) in x["body"]]
        if len(items) > 1:
            raise AssertionError("duplicate GitHub issues")
        return items[0] if items else None

    def create(self, title, body):
        self.create_calls += 1
        if self.fail_before_create:
            raise OSError("ambiguous failure containing a secret")
        number = self.next_id
        self.next_id += 1
        self.issues[number] = {"number":number, "title":title, "body":body,
                               "state":"open", "comments":0}
        if self.fail_after_create:
            raise OSError("simulated timeout after successful create")
        return self.issues[number]

    def issue(self, number):
        return self.issues[number]

    def comments(self, number):
        return self.issues[number].get("comment_items", [])

    def comment(self, number, body):
        self.comments_written += 1
        self.issues[number].setdefault("comment_items", []).append({"body":body})
        self.issues[number]["comments"] += 1
        if self.fail_after_comment:
            raise OSError("simulated uncertain comment")
        return {"id":self.comments_written}


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.github = FakeGitHub()
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.writer = pub.DurablePublisher(self.db, self.github, now=1795600000)
        self.entry = incident()

    def sync(self, entry=None):
        values = pub.validate(envelope(entry or self.entry))
        return self.writer.sync(values[0])

    def test_safe_failure_diagnostics_never_include_token_or_remote_exception(self):
        class RefusingOpener:
            def __init__(self, failure):
                self.failure = failure

            def open(self, *unused, **kwargs):
                raise self.failure

        import urllib.error
        examples = [
            (TimeoutError("Bearer TOP_SECRET"), "receiver_api_timeout"),
            (urllib.error.HTTPError("https://private.example", 403,
                                   "Bearer TOP_SECRET", {}, None),
             "receiver_api_http_403"),
            (urllib.error.URLError("TOKEN_UNTRUSTED_HOST"),
             "receiver_api_connection"),
        ]
        for error, expected in examples:
            with self.subTest(expected=expected):
                with patch.object(pub.time, "sleep"):
                    with self.assertRaises(pub.PublisherFailure) as raised:
                        pub.request_json(RefusingOpener(error), pub.RECEIVER,
                                         "PRIVATE_TOKEN", phase="receiver_api")
                self.assertEqual(raised.exception.code, expected)
                self.assertNotIn("SECRET", str(raised.exception))
                self.assertNotIn("TOKEN", str(raised.exception))
                self.assertNotIn("private.example", str(raised.exception))

    def test_bounded_read_only_retry_never_replays_github_writes(self):
        class SometimesUnavailable:
            def __init__(self):
                self.calls=0
            def open(self, *args, **kwargs):
                self.calls+=1
                raise TimeoutError("PRIVATE_BEARER_TOKEN")

        for method, phase, expected_calls in (("GET","receiver_api",2),
                                               ("GET","kubernetes_api",2),
                                               ("POST","github_api",1),
                                               ("GET","github_api",1)):
            with self.subTest(method=method, phase=phase):
                opener=SometimesUnavailable()
                with patch.object(pub.time, "sleep") as sleeping:
                    with self.assertRaises(pub.PublisherFailure):
                        pub.request_json(opener, pub.RECEIVER, "PRIVATE_TOKEN",
                                         method=method, phase=phase)
                self.assertEqual(opener.calls, expected_calls)
                self.assertEqual(sleeping.call_count, expected_calls-1)

    def test_allowlist_and_adversarial_event_are_rejected(self):
        bad = [
            {"workload":"git/Deployment/private"},
            {"workload":"kubescape-runtime-test/Deployment/runtime-acceptance"},
            {"severity":"Medium"},
            {"key":"not-a-hex-key"},
            {"rule":"R1000<script>"},
            {"count":"1"},
            {"count":True},
            {"first":"not-a-date"},
            {"state":"resolved"},
        ]
        for change in bad:
            with self.subTest(change=change):
                entry = copy.deepcopy(self.entry)
                entry.update(change)
                with self.assertRaises((ValueError,TypeError)):
                    pub.validate(envelope(entry))
        with self.assertRaises(ValueError):
            pub.validate({"schema":1,"source":"kubescape-native","incidents":[
                self.entry,self.entry]})

    def test_synthetic_detection_creates_one_sanitized_issue(self):
        self.assertEqual(self.sync(),"created")
        self.assertEqual(self.sync(),"unchanged")
        self.assertEqual(self.github.create_calls,1)
        body = next(iter(self.github.issues.values()))["body"]
        self.assertIn("Security (Kubescape)",next(iter(self.github.issues.values()))["title"])
        self.assertIn("R1000",body)
        for secret in ("Bearer ", "PRIVATE_CMDLINE", "podUID", "agent_pod_uid",
                       "container_id", "image_digest", "/state/", "TOKEN"):
            self.assertNotIn(secret,body)

    def test_new_occurrence_comments_without_overwriting_human_notes(self):
        self.sync()
        issue = next(iter(self.github.issues.values()))
        issue["body"] += "\n\n## Human investigation\nPreserve this note."
        changed = dict(self.entry,count=2,last="2026-11-24T05:00:00Z")
        self.assertEqual(self.sync(changed),"updated")
        self.assertEqual(self.sync(changed),"unchanged")
        self.assertIn("Preserve this note",issue["body"])
        self.assertEqual(self.github.create_calls,1)
        self.assertEqual(self.github.comments_written,1)
        issue["state"]="closed"
        self.assertEqual(self.sync(dict(changed,count=3)),"human_closed")
        self.assertEqual(self.github.comments_written,1)

    def test_ambiguous_create_does_not_retry_even_after_database_reopen(self):
        self.github.fail_before_create = True
        with self.assertRaises(OSError):
            self.sync()
        self.github.fail_before_create = False
        self.writer = pub.DurablePublisher(self.db,self.github,now=1795600600)
        self.assertEqual(self.sync(),"pending_manual_create")
        self.assertEqual(self.github.create_calls,1)

    def test_timeout_after_remote_create_reconciles_without_second_create(self):
        self.github.fail_after_create=True
        with self.assertRaises(OSError):
            self.sync()
        self.github.fail_after_create=False
        self.assertIn(self.sync(),("updated","unchanged"))
        self.assertEqual(self.github.create_calls,1)

    def test_ambiguous_update_is_not_reposted(self):
        self.sync()
        entry=dict(self.entry,count=2,last="2026-11-24T05:00:00Z")
        self.github.fail_after_comment=True
        with self.assertRaises(OSError):
            self.sync(entry)
        self.github.fail_after_comment=False
        self.assertEqual(self.sync(entry),"unchanged")
        self.assertEqual(self.github.comments_written,1)

    def test_daily_create_and_hourly_write_budget(self):
        for index in range(3):
            key=f"{index+10:064x}"
            self.assertEqual(self.sync(dict(self.entry,key=key)),"created")
        with self.assertRaises(ValueError):
            self.sync(dict(self.entry,key="f"*64))
        self.assertEqual(self.github.create_calls,3)


class CVEPublisherTests(unittest.TestCase):
    def fixture(self):
        return {"schema":1,"source":"kubescape-cve","generatedAt":"2026-11-24T06:00:00Z","entries":[
            {"workload":w,"state":"complete","assessedAt":"2026-11-24T05:00:00Z",
             "findings":[{"id":"CVE-2026-4738","severity":"Critical","fixAvailable":True}],
             "otherHighCriticalMatches":0} for w in sorted(pub.ALLOWED)]}

    def test_freshness_privacy_and_exact_fields(self):
        projection = self.fixture()
        now = datetime(2026,11,24,6,1,tzinfo=timezone.utc)
        self.assertEqual(len(pub.validate_cve(projection,now)),3)
        for change in ({"workload":"gitea/Deployment/private"},
                       {"findings":[{"id":"CVE-2026-4738","severity":"High","fixAvailable":True,"package":"secret"}]},
                       {"assessedAt":"2026-11-20T05:00:00Z"}):
            bad=copy.deepcopy(projection)
            bad["entries"][0].update(change)
            with self.assertRaises((ValueError,TypeError)):
                pub.validate_cve(bad,now)
        with self.assertRaises(ValueError):
            pub.validate_cve(projection,datetime(2026,11,26,tzinfo=timezone.utc))

    def test_projection_observation_expires_before_assessment(self):
        projection=self.fixture()
        self.assertEqual(len(pub.validate_cve(projection,datetime(2026,11,24,7,5,tzinfo=timezone.utc))),3)
        with self.assertRaises(ValueError):
            pub.validate_cve(projection,datetime(2026,11,24,7,6,tzinfo=timezone.utc))

    def test_repeat_scan_same_findings_is_not_a_new_comment(self):
        entry=pub.validate_cve(self.fixture(),datetime(2026,11,24,6,1,tzinfo=timezone.utc))[0]
        github=FakeGitHub()
        with sqlite3.connect(":memory:") as connection:
            writer=pub.DurablePublisher(connection,github,renderer=pub.render_cve,fingerprint=pub.cve_revision,owns=pub.owns_cve)
            self.assertEqual(writer.sync(entry),"created")
            self.assertEqual(writer.sync(dict(entry,assessedAt="2026-11-24T05:01:00Z")),"unchanged")
            self.assertEqual(github.create_calls,1)
            self.assertEqual(github.comments_written,0)
            issue=next(iter(github.issues.values()))
            self.assertIn("CVE-2026-4738",issue["body"])
            self.assertNotIn("sha256:",issue["body"])

    def test_empty_projection_is_distinct_from_invalid_projection_and_api_failure(self):
        obj={"kind":"ConfigMap","metadata":{"namespace":"kubescape","name":"kubescape-public-cve"}}
        with patch.object(pub.Path, "read_text", return_value="private-token"), \
                patch.object(pub.ssl, "create_default_context"), patch.object(pub, "build_opener"), \
                patch.object(pub, "request_json") as request:
            for data, expected in ((None,"cve_coverage_missing"), ({},"cve_coverage_missing"),
                                   ({"projection.json":"private-invalid-json"},"cve_projection_invalid"),
                                   ({"private":"secret"},"cve_projection_invalid")):
                with self.subTest(expected=expected):
                    request.return_value=dict(obj,data=data)
                    with self.assertRaises(pub.PublisherFailure) as raised:
                        pub.fetch_cve_projection()
                    self.assertEqual(raised.exception.code,expected)
                    self.assertNotIn("private",str(raised.exception))
            request.side_effect=pub.PublisherFailure("cve_api_http_403")
            with self.assertRaises(pub.PublisherFailure) as raised:
                pub.fetch_cve_projection()
            self.assertEqual(raised.exception.code,"cve_api_http_403")

    def test_cve_api_failure_code_is_static(self):
        import urllib.error
        error=urllib.error.HTTPError("https://private.example",403,"private-token",{},None)
        self.assertEqual(pub.boundary_failure("cve_api",error).code,"cve_api_http_403")

    def test_manual_memos_issue_identity_is_exact(self):
        key=pub.cve_key("memos/Deployment/memos")
        issue={"number":2181,"title":pub.MEMOS_TITLE,"body":"Existing triage for memos/Deployment/memos"}
        self.assertTrue(pub.owns_cve(issue,key))
        self.assertFalse(pub.owns_cve(dict(issue,number=999),key))
        self.assertFalse(pub.owns_cve(issue,pub.cve_key("homepage/Deployment/homepage")))


class StageHealthTests(unittest.TestCase):
    def test_each_source_runs_independently_and_termination_data_is_bounded(self):
        import tempfile
        import os
        for native_error, cve_error, expected in (
                (None, None, "security-v2|healthy|healthy|healthy"),
                (None, pub.PublisherFailure("cve_coverage_missing"), "security-v2|healthy|failed|healthy"),
                (OSError("private token"), None, "security-v2|failed|healthy|healthy"),
                (OSError("private token"), OSError("private payload"), "security-v2|failed|failed|healthy")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                path=pathlib.Path(directory)/"termination"
                with patch.dict(os.environ, {"ENABLE_CVE_PUBLISHER":"true"}), patch.object(pub,"main",side_effect=native_error) as native, patch.object(pub,"cve_main",side_effect=cve_error) as cve, patch.object(pub,"check_receiver_capacity") as capacity:
                    result=pub.run_stages(path)
                    native.assert_called_once()
                    cve.assert_called_once()
                    capacity.assert_called_once()
                self.assertEqual(path.read_text(),expected)
                self.assertEqual(result,0 if expected == "security-v2|healthy|healthy|healthy" else 1)

    def test_count_capacity_threshold_and_invalid_response(self):
        baseline = {"receiver_ready": True, "delivery_coverage": "unverified",
                    "receipts": 15999, "incidents": 159}
        with patch.object(pub,"receiver_connection",return_value=(object(),"test-token")), patch.object(pub,"request_json") as request:
            request.return_value = baseline
            pub.check_receiver_capacity()
            self.assertEqual(request.call_args.args[1], "https://security-receiver.kubescape.svc:8443/healthz")
            for changes, code in (({"receipts":16000},"receiver_capacity_threshold"),
                                  ({"incidents":160},"receiver_capacity_threshold"),
                                  ({"receipts":True},"receiver_capacity_invalid"),
                                  ({"incidents":201},"receiver_capacity_invalid"),
                                  ({"receiver_ready":False},"receiver_capacity_invalid"),
                                  ({"extra":"private"},"receiver_capacity_invalid")):
                request.return_value = dict(baseline,**changes)
                with self.assertRaises(pub.PublisherFailure) as raised:
                    pub.check_receiver_capacity()
                self.assertEqual(raised.exception.code,code)

if __name__=="__main__":
    unittest.main()
