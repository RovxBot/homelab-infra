"""Focused tests for the separate native GitHub publisher. No network."""
import copy
from datetime import datetime, timezone
import importlib.util
import hashlib
import io
import json
import pathlib
import sqlite3
import sys
import unittest
import zipfile
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
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
    def test_private_runtime_cohort_accepts_only_reviewed_identities(self):
        for workload in ('koito/Deployment/koito', 'koito/Deployment/koito-lastfm-bridge',
                         'monitoring/Deployment/gatus', 'openbrew/Deployment/openbrew', 'openbrew/Deployment/openbrew-db', 'stirling-pdf/Deployment/stirling-pdf',
                         'cloudflared/Deployment/cloudflared', 'mqtt/Deployment/mosquitto', 'security/Deployment/vaultwarden',
                         'wotlk/StatefulSet/wotlk-mariadb', 'mop/StatefulSet/skyfire-mariadb', 'kube-system/DaemonSet/kube-proxy'):
            self.assertEqual(pub.validate(envelope(dict(incident(),workload=workload)))[0]['workload'],workload)
        self.assertTrue(pub.RECEIVER.endswith('/v1/private-incidents'))

    def test_expanded_runtime_projection_bound(self):
        values=[]
        for i in range(80):
            values.append(dict(incident(),key=format(i+1,"064x")))
        self.assertEqual(len(pub.validate({"schema":1,"source":"kubescape-native","incidents":values})),80)
        with self.assertRaises(ValueError):
            pub.validate({"schema":1,"source":"kubescape-native","incidents":values+[dict(incident(),key=format(81,"064x"))]})

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


    def test_native_budget_deferral_prioritizes_critical_and_preserves_updates(self):
        existing=dict(self.entry,key="f"*64)
        self.assertEqual(self.writer.sync(existing),"created")
        critical=dict(self.entry,key="c"*64,severity="Critical")
        older=dict(self.entry,key="d"*64,first="2026-11-24T04:00:00Z")
        deferred=dict(self.entry,key="a"*64)
        updated=dict(existing,count=2,last="2026-11-24T05:00:00Z")
        # Ensure the existing update sorts after the deferred new key.
        outcomes=pub.sync_native_entries(self.writer,[deferred,updated,older,critical])
        self.assertEqual(outcomes,["created","created","deferred","updated"])
        self.assertEqual(self.github.create_calls,3)
        self.assertEqual(self.github.comments_written,1)
        self.assertIsNone(self.db.execute("SELECT key FROM issues WHERE key=?",
                                         (deferred["key"],)).fetchone())
        writer=pub.DurablePublisher(self.db,self.github,now=self.writer.now+86401)
        self.assertEqual(pub.sync_native_entries(writer,[deferred,updated,older,critical]).count("created"),1)
        self.assertEqual(self.github.create_calls,4)
        self.assertEqual(self.github.comments_written,1)

    def test_native_ambiguous_write_stops_batch_without_blind_replay(self):
        first=dict(self.entry,key="b"*64,severity="Critical")
        later=dict(self.entry,key="c"*64)
        self.github.fail_after_create=True
        with self.assertRaises(OSError):
            pub.sync_native_entries(self.writer,[later,first])
        self.assertEqual(self.github.create_calls,1)
        self.assertEqual(self.db.execute("SELECT pending_create FROM issues WHERE key=?",
                                        (first["key"],)).fetchone(),(1,))
        self.assertIsNone(self.db.execute("SELECT key FROM issues WHERE key=?",
                                         (later["key"],)).fetchone())
        self.github.fail_after_create=False
        pub.sync_native_entries(self.writer,[later,first])
        self.assertEqual(self.github.create_calls,2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM issues WHERE pending_create != 0").fetchone()[0],0)


class CVEPublisherTests(unittest.TestCase):
    def test_remediation_candidates_exact_reviewed_controller_and_digest(self):
        candidate_spec=importlib.util.spec_from_file_location(
            "remediation_candidates",ROOT/"scripts/security_remediation_candidates.py")
        candidate=importlib.util.module_from_spec(candidate_spec)
        candidate_spec.loader.exec_module(candidate)
        def current(path):
            value=ROOT/path
            return value.read_text() if value.is_file() else None
        unchanged=candidate.correlate(current,current)
        self.assertEqual(unchanged["candidates"],[])
        self.assertEqual({x["workload"] for x in unchanged["unsupported"]},
                         {"monitoring/Deployment/gatus","kube-system/DaemonSet/kube-proxy"})
        workload="memos/Deployment/memos"
        path=pub.CVE_SOURCES[workload][0]
        old=candidate.declared_image(current,workload,"memos")
        self.assertIsNotNone(old)
        new=old.rsplit("@",1)[0]+"@sha256:"+"f"*64
        def changed(source):
            value=current(source)
            return value.replace(old,new) if source==path else value
        result=candidate.correlate(current,changed)
        self.assertEqual(len(result["candidates"]),1)
        self.assertEqual(result["candidates"][0]["workload"],workload)
        self.assertEqual(result["candidates"][0]["state"],"candidate_not_assessed")
        self.assertEqual(result["candidates"][0]["after"],new)
        def alias(source):
            value=current(source)
            return value.replace(old,"docker.io/"+old) if source==path else value
        self.assertEqual(candidate.correlate(current,alias)["candidates"],[])
        def spoof(source):
            value=current(source)
            return value.replace(old,"other.example/app@sha256:"+"f"*64) if source==path else value
        self.assertEqual(candidate.correlate(current,spoof)["candidates"],[])
        self.assertEqual(candidate.image_identity("mysql:8.0"),None)
        self.assertEqual(candidate.image_identity("registry.example:5000/app:v1@sha256:"+"a"*64),
                         ("registry.example:5000/app","sha256:"+"a"*64))

    def test_remediation_dependencies_are_container_specific(self):
        spec = importlib.util.spec_from_file_location(
            "multi_container_remediation", ROOT/"scripts/security_remediation_candidates.py")
        candidate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(candidate)
        workload = "test/Deployment/app"
        path = "apps/test/deployment.yaml"
        before = {
            "main": "example/main:v1@sha256:"+"a"*64,
            "sidecar": "example/sidecar:v1@sha256:"+"b"*64,
        }
        after = {name: image.rsplit("@",1)[0]+"@sha256:"+digest*64
                 for (name,image),digest in zip(before.items(),("c","d"))}
        def reader(images):
            return lambda source: json.dumps({
                "apiVersion":"apps/v1","kind":"Deployment",
                "metadata":{"name":"app","namespace":"test"},
                "spec":{"template":{"spec":{"containers":[
                    {"name":name,"image":image} for name,image in images.items()]}}}
            }) if source == path else None
        sources = {workload:(path,("main","sidecar"))}
        deps = {workload:{"main":"example/main","sidecar":"example/sidecar"}}
        with patch.object(candidate.P,"CVE_SOURCES",sources), patch.object(candidate.P,"CVE_DEPENDENCIES",deps):
            result = candidate.correlate(reader(before),reader(after))
            self.assertEqual(result["unsupported"],[])
            self.assertEqual({x["container"]:x["dependency"] for x in result["candidates"]},
                             {"main":"docker.io/example/main","sidecar":"docker.io/example/sidecar"})
            swapped = dict(after, sidecar=after["main"])
            self.assertIsNone(candidate.declared_image(reader(swapped),workload,"sidecar"))
            self.assertIsNone(candidate.P.reviewed_dependency(workload,"unreviewed"))
        now = datetime(2026,11,24,6,1,tzinfo=timezone.utc)
        snapshot = {"schema":1,"repository":pub.REMEDIATION_REPO,"base":"a"*40,
                    "state":"observed","openPrCount":1,"skipped":{},
                    "candidates":[dict(x,pr=123,base="a"*40,head="b"*40) for x in result["candidates"]],
                    "generatedAt":"2026-11-24T06:00:00Z"}
        with patch.object(pub,"CVE_SOURCES",sources), patch.object(pub,"CVE_DEPENDENCIES",deps):
            self.assertEqual(pub.reviewed_dependency(workload,"sidecar"),"example/sidecar")
            self.assertEqual(pub.dependency_description(workload,("main","sidecar")),
                             "main: example/main; sidecar: example/sidecar")
            self.assertEqual(len(pub.validate_remediation_snapshot(snapshot,"a"*40,now)),2)
            bad = copy.deepcopy(snapshot)
            bad["candidates"][1]["dependency"] = "docker.io/example/main"
            with self.assertRaises(ValueError):
                pub.validate_remediation_snapshot(bad,"a"*40,now)

    def test_live_remediation_discovery_fences_untrusted_or_stale_prs(self):
        candidate_spec = importlib.util.spec_from_file_location(
            "remediation_candidates", ROOT/"scripts/security_remediation_candidates.py")
        candidate = importlib.util.module_from_spec(candidate_spec)
        candidate_spec.loader.exec_module(candidate)
        base, head = "a"*40, "b"*40
        repo = {"full_name": candidate.REPOSITORY}
        def pr(number, **extra):
            return dict({"number": number, "state": "open", "draft": False,
                         "base": {"ref": "main", "sha": base, "repo": repo},
                         "head": {"sha": head, "repo": repo}}, **extra)
        pulls = [pr(1), pr(2, draft=True), pr(3, head={"sha": head, "repo": {"full_name": "other/repo"}}),
                 pr(4), pr(5, head={"sha": "c"*40, "repo": repo}),
                 pr(6, base={"ref": "main", "sha": "d"*40, "repo": repo})]
        fetched = []
        def fetch(number):
            fetched.append(number)
            return "e"*40 if number == 4 else pulls[number-1]["head"]["sha"]
        def current(path):
            f = ROOT/path
            return f.read_text() if f.is_file() else None
        workload = "memos/Deployment/memos"
        path = pub.CVE_SOURCES[workload][0]
        old = candidate.declared_image(current, workload, "memos")
        new = old.rsplit("@",1)[0] + "@sha256:" + "f"*64
        def reader(commit):
            return lambda source: current(source).replace(old,new) if commit == head and source == path else current(source)
        result = candidate.live_candidates(pulls, base, fetch,
                                           lambda before, after: after == head, reader)
        self.assertEqual(fetched, [1,4,5])
        self.assertEqual(result["skipped"], {"not_ready":1, "unsupported_base_or_repository":2,
                                             "head_changed":1, "branch_behind_main":1})
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["pr"], 1)
        self.assertEqual(result["candidates"][0]["state"], "candidate_not_assessed")
        with self.assertRaises(ValueError):
            candidate.live_candidates([pr(1), pr(1)], base, fetch, lambda *_: True, reader)
        with self.assertRaises(ValueError):
            candidate.live_candidates([pr(i) for i in range(1,52)], base, fetch, lambda *_: True, reader)

    def test_private_remediation_snapshot_exact_digest_and_artifact_boundary(self):
        now = datetime(2026,11,24,6,1,tzinfo=timezone.utc)
        base, head = "a"*40, "b"*40
        workload = "memos/Deployment/memos"
        old = "neosmemo/memos:v1@sha256:" + "c"*64
        new = "neosmemo/memos:v2@sha256:" + "d"*64
        candidate = {"workload":workload, "container":"memos", "path":pub.CVE_SOURCES[workload][0],
                     "dependency":"docker.io/neosmemo/memos", "before":old, "after":new,
                     "state":"candidate_not_assessed", "pr":123, "base":base, "head":head}
        snapshot = {"schema":1, "repository":pub.REMEDIATION_REPO, "base":base, "state":"observed",
                    "openPrCount":1, "skipped":{}, "candidates":[candidate],
                    "generatedAt":"2026-11-24T06:00:00Z"}
        def encoded(value, name="candidates.json"):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(name, json.dumps(value))
            data = stream.getvalue()
            return data, "sha256:" + hashlib.sha256(data).hexdigest()
        raw, digest = encoded(snapshot)
        self.assertEqual(pub.decode_remediation_artifact(raw, digest), snapshot)
        with self.assertRaises(ValueError):
            pub.decode_remediation_artifact(raw, "sha256:" + "f"*64)
        with self.assertRaises(ValueError):
            pub.decode_remediation_artifact(*encoded(snapshot, "../candidates.json"))
        def request(opener, url, token, **kw):
            self.assertEqual(token, "read-only")
            self.assertEqual(kw, {"method":"GET", "payload":None, "phase":"github_api"})
            self.assertTrue(url.startswith(pub.REMEDIATION_API + "/actions/"))
            if "/workflows/" in url:
                return {"workflow_runs":[{"id":1,"head_sha":base,"head_branch":"main",
                        "head_repository":{"full_name":pub.REMEDIATION_REPO},"event":"schedule",
                        "conclusion":"success","created_at":"2026-11-24T05:59:00Z"}]}
            return {"total_count":1,"artifacts":[{"name":pub.REMEDIATION_ARTIFACT,"id":2,
                    "expired":False,"size_in_bytes":len(raw),"digest":digest}]}
        def download(url, token):
            self.assertEqual(url, pub.REMEDIATION_API + "/actions/artifacts/2/zip")
            self.assertEqual(token, "read-only")
            return raw
        validated = pub.fetch_remediation_candidates("read-only", now, request, download)
        self.assertEqual(validated, [candidate])
        projection = self.rich_fixture()
        entry = next(e for e in projection["entries"] if e["workload"] == workload)
        entry["containers"][0]["image"] = "docker.io/neosmemo/memos@sha256:" + "c"*64
        ready = pub.validate_cve(projection, now)
        entry = next(e for e in ready if e["workload"] == workload)
        revision = pub.cve_revision(entry)
        self.assertEqual(pub.correlate_remediation(ready, validated), 1)
        self.assertNotEqual(revision, pub.cve_revision(entry))
        body = pub.render_cve(entry)[1]
        self.assertIn("https://github.com/RovxBot/homelab-infra/pull/123", body)
        self.assertIn("not assessed", body)
        self.assertIn(new, body)
        for bad_image in ("other/memos@sha256:"+"c"*64, new):
            other = copy.deepcopy(entry)
            other.pop("remediationCandidates")
            other["containers"][0]["image"] = bad_image
            self.assertEqual(pub.correlate_remediation([other], validated), 0)
        for field, value in (("path","private"),("dependency","other"),("pr",True),("after",old),
                             ("head",base),("state","fixed")):
            bad = copy.deepcopy(snapshot)
            bad["candidates"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                pub.validate_remediation_snapshot(bad, base, now)
        bad = copy.deepcopy(snapshot)
        bad["generatedAt"] = "2026-11-24T03:00:00Z"
        with self.assertRaises(ValueError):
            pub.validate_remediation_snapshot(bad, base, now)

    def test_remediation_failure_reasons_are_fixed_and_redacted(self):
        now = datetime(2026,11,24,6,1,tzinfo=timezone.utc)
        run = {"id":1,"head_sha":"a"*40,"head_branch":"main",
               "head_repository":{"full_name":pub.REMEDIATION_REPO},"event":"schedule",
               "conclusion":"success","created_at":"2026-11-24T05:59:00Z"}
        artifact = {"name":pub.REMEDIATION_ARTIFACT,"id":2,"expired":False,
                    "size_in_bytes":1,"digest":"sha256:"+"a"*64}
        def call(runs, artifacts=None, downloader=None):
            def request(opener, url, token, **kw):
                value = runs if "/workflows/" in url else artifacts
                if isinstance(value, Exception):
                    raise value
                return value
            return pub.fetch_remediation_candidates("fixture-token", now, request,
                downloader or (lambda *_: b"x"))
        for runs, artifacts, download, code in (
                (pub.PublisherFailure("github_api_http_401"),None,None,"workflow_api_http_401"),
                (pub.PublisherFailure("github_api_SECRET"),None,None,"workflow_api"),
                ({"workflow_runs":[]},None,None,"no_successful_run"),
                ({"workflow_runs":[dict(run,created_at="2026-11-24T01:00:00Z")]},None,None,"workflow_freshness"),
                ({"workflow_runs":[run]},pub.PublisherFailure("github_api_http_403"),None,"artifact_list_api_http_403"),
                ({"workflow_runs":[run]},{"total_count":0,"artifacts":[]},None,"artifact_metadata"),
                ({"workflow_runs":[run]},{"total_count":1,"artifacts":[artifact]},
                 lambda *_: (_ for _ in ()).throw(ValueError("SECRET")),"artifact_download"),
                ({"workflow_runs":[run]},{"total_count":1,"artifacts":[artifact]},None,"artifact_integrity")):
            with self.subTest(code=code), self.assertRaises(pub.RemediationUnavailable) as caught:
                call(runs,artifacts,download)
            self.assertEqual(caught.exception.code,code)
            self.assertNotIn("SECRET",str(caught.exception))
            self.assertNotIn("fixture-token",str(caught.exception))

    def test_private_projection_scope_matches_reviewed_scan_config(self):
        import json
        config=json.loads((ROOT/"infra/kubescape/recurring/config.json").read_text())
        self.assertEqual(pub.CVE_ALLOWED,{t["identity"].rsplit("/",1)[0] for t in config["targets"]})
        self.assertEqual(len(pub.CVE_ALLOWED),12)
        self.assertEqual(len(pub.ALLOWED),15)
        from security_receiver.store import PRIVATE_WORKLOADS
        self.assertEqual(pub.ALLOWED, set(PRIVATE_WORKLOADS.values()))
        self.assertNotIn("kubescape-public-cve",SOURCE.read_text())

    def fixture(self):
        return {"schema":1,"source":"kubescape-cve","generatedAt":"2026-11-24T06:00:00Z","entries":[
            {"workload":w,"state":"complete","assessedAt":"2026-11-24T05:00:00Z",
             "findings":[{"id":"CVE-2026-4738","severity":"Critical","fixAvailable":True}],
             "otherHighCriticalMatches":0} for w in sorted(pub.CVE_ALLOWED)]}

    def test_freshness_privacy_and_exact_fields(self):
        projection = self.fixture()
        now = datetime(2026,11,24,6,1,tzinfo=timezone.utc)
        self.assertEqual(len(pub.validate_cve(projection,now)),12)
        for change in ({"workload":"gitea/Deployment/private"},
                       {"findings":[{"id":"CVE-2026-4738","severity":"High","fixAvailable":True,"package":"secret"}]},
                       {"assessedAt":"2026-11-20T05:00:00Z"}):
            bad=copy.deepcopy(projection)
            bad["entries"][0].update(change)
            with self.assertRaises((ValueError,TypeError)):
                pub.validate_cve(bad,now)
        with self.assertRaises(ValueError):
            pub.validate_cve(projection,datetime(2026,11,26,tzinfo=timezone.utc))


    def test_reviewed_source_map_and_actionable_legacy_template(self):
        import json
        config=json.loads((ROOT/"infra/kubescape/recurring/config.json").read_text())
        expected={}
        for target in config["targets"]:
            workload, container = target["identity"].rsplit("/",1)
            expected.setdefault(workload,set()).add(container)
        self.assertEqual(set(pub.CVE_SOURCES),set(expected))
        for workload,(path,names) in pub.CVE_SOURCES.items():
            self.assertEqual(set(names),expected[workload])
            if path:
                self.assertTrue((ROOT/path).is_file())
        entry=pub.validate_cve(self.fixture(),datetime(2026,11,24,6,1,tzinfo=timezone.utc))[0]
        title,body=pub.render_cve(entry)
        for required in ("## Summary","## Affected resources","## Required action",
                         "## Resolution checklist","not established by this report",
                         "summary-only evidence","Automatic closure is not enabled yet"):
            self.assertIn(required,body)
        self.assertEqual(body.count(pub.marker(entry["key"])),1)

    def rich_fixture(self):
        projection=self.fixture()
        projection["schema"]=2
        for entry in projection["entries"]:
            entry["containers"]=[{
                "name":name,"image":"registry.example/app@sha256:"+"a"*64,
                "assessedAt":entry["assessedAt"],"detailState":"complete","omittedMatches":0,
                "matches":[{"id":"CVE-2026-4738","severity":"Critical","package":"zlib",
                            "ecosystem":"apk","installedVersion":"1.3.2-r1",
                            "fixedVersions":["1.4-r0"],"fixState":"fixed"}]
            } for name in pub.CVE_SOURCES[entry["workload"]][1]]
        return projection

    def proof_fixture(self):
        projection=self.rich_fixture()
        projection["schema"]=3
        for entry in projection["entries"]:
            entry["recoveryProof"]={"workloadIdentity":"c"*64,"containers":[
                {"name":c["name"],"databaseBuilt":"2026-11-23T00:00:00Z",
                 "databaseChecksum":"d"*64,"assessmentId":"e"*64,
                 "allCves":["CVE-2026-4738","CVE-2026-9999"]}
                for c in entry["containers"]]}
        return projection

    def test_recovery_proof_contract_and_compatibility(self):
        now=datetime(2026,11,24,6,1,tzinfo=timezone.utc)
        projection=self.proof_fixture()
        rich=pub.validate_cve(projection,now)[0]
        old=pub.validate_cve(self.rich_fixture(),now)[0]
        self.assertEqual(pub.cve_revision(rich),pub.cve_revision(old))
        self.assertEqual(pub.cve_recovery_snapshot(rich)["proof"],rich["recoveryProof"])
        projection["entries"][0]["recoveryProof"]=None
        self.assertIsNone(pub.validate_cve(projection,now)[0]["recoveryProof"])
        for field,value in (("databaseBuilt","2026-11-01T00:00:00Z"),
                            ("databaseBuilt","2026-11-24T06:00:00Z"),
                            ("databaseChecksum","untrusted"),("assessmentId","unknown"),
                            ("name","unreviewed"),("allCves",[]),
                            ("allCves",["CVE-2026-4738"]*2),
                            ("allCves",[f"CVE-2026-{1000+i}" for i in range(257)])):
            projection=self.proof_fixture()
            projection["entries"][0]["recoveryProof"]["containers"][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                pub.validate_cve(projection,now)
        projection=self.proof_fixture()
        projection["entries"][0].update(state="unknown",assessedAt=None,findings=[],containers=[])
        with self.assertRaises(ValueError):
            pub.validate_cve(projection,now)

    def test_recovery_proof_rejects_downgrade_replay_and_changed_scope(self):
        now=datetime(2026,11,24,6,1,tzinfo=timezone.utc)
        entry=pub.validate_cve(self.proof_fixture(),now)[0]
        baseline=pub.cve_recovery_snapshot(entry)
        clean=copy.deepcopy(entry)
        clean["findings"]=[]
        for c in clean["containers"]:
            c.update(matches=[],image="registry.example/app@sha256:"+"f"*64,
                     assessedAt="2026-11-24T05:30:00Z")
        for c in clean["recoveryProof"]["containers"]:
            c.update(allCves=["CVE-2026-9999"],assessmentId="a"*64)
        candidate=pub.recovery_candidate(baseline,clean)
        self.assertTrue(pub.proof_qualified_recovery(baseline,candidate))
        for mutation in ("downgrade","replay","older-db","changed-db","new-controller","no-proof","non-cve"):
            old,new=copy.deepcopy(baseline),copy.deepcopy(candidate)
            if mutation=="downgrade":
                new["proof"]["containers"][0]["allCves"].append("CVE-2026-4738")
            elif mutation=="replay":
                new["proof"]["containers"][0]["assessmentId"]="e"*64
            elif mutation=="older-db":
                new["proof"]["containers"][0]["databaseBuilt"]="2026-11-22T00:00:00Z"
            elif mutation=="changed-db":
                new["proof"]["containers"][0]["databaseChecksum"]="b"*64
            elif mutation=="new-controller":
                new["proof"]["workloadIdentity"]="f"*64
            elif mutation=="no-proof":
                new.pop("proof")
            else:
                old["otherHighCriticalMatches"]=1
            with self.subTest(mutation=mutation):
                self.assertFalse(pub.proof_qualified_recovery(old,new))
        for c in clean["containers"]:
            c["image"]="different.example/app@sha256:"+"a"*64
        self.assertIsNone(pub.recovery_candidate(baseline,clean))

    def test_rich_private_evidence_and_stable_revision(self):
        projection=self.rich_fixture()
        now=datetime(2026,11,24,6,1,tzinfo=timezone.utc)
        entry=pub.validate_cve(projection,now)[0]
        title,body=pub.render_cve(entry)
        for value in ("zlib","1.3.2-r1","1.4-r0","registry.example/app@sha256:",
                      "Package-detail coverage: complete"):
            self.assertIn(value,body)
        legacy=pub.validate_cve(self.fixture(),now)[0]
        self.assertEqual(entry["key"],legacy["key"])
        self.assertNotEqual(pub.cve_revision(entry),pub.cve_revision(legacy))
        newer=copy.deepcopy(entry)
        newer["assessedAt"]="2026-11-24T05:30:00Z"
        for container in newer["containers"]:
            container["assessedAt"]=newer["assessedAt"]
        self.assertEqual(pub.cve_revision(newer),pub.cve_revision(entry))

    def test_rich_partial_unavailable_and_invalid_evidence(self):
        now=datetime(2026,11,24,6,1,tzinfo=timezone.utc)
        for state,omitted in (("partial",2),("unavailable",None)):
            projection=self.rich_fixture()
            projection["entries"][0]["containers"][0].update(detailState=state,omittedMatches=omitted,matches=[])
            body=pub.render_cve(pub.validate_cve(projection,now)[0])[1]
            self.assertIn("Package-detail coverage: "+state,body)
        for key,value in (("package","x|fake"),("package","https://example.invalid"),
                          ("package","<script>"),("installedVersion","x\nsecret"),
                          ("id","CVE-2026-9999"),("fixState","invented"),
                          ("fixedVersions",["1","2","3","4","5"])):
            projection=self.rich_fixture()
            projection["entries"][0]["containers"][0]["matches"][0][key]=value
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                pub.validate_cve(projection,now)
        for key,value in (("name","unreviewed"),("image","app:latest"),
                          ("omittedMatches",1),("matches",[]),("assessedAt","2026-11-20T05:00:00Z")):
            projection=self.rich_fixture()
            projection["entries"][0]["containers"][0][key]=value
            with self.subTest(key=key), self.assertRaises(ValueError):
                pub.validate_cve(projection,now)

    def test_projection_observation_expires_before_assessment(self):
        projection=self.fixture()
        self.assertEqual(len(pub.validate_cve(projection,datetime(2026,11,24,7,5,tzinfo=timezone.utc))),12)
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

    def test_recovery_shadow_requires_published_positive_baseline_and_new_clean_digest(self):
        now=datetime(2026,11,24,6,1,tzinfo=timezone.utc)
        entry=pub.validate_cve(self.rich_fixture(),now)[0]
        github=FakeGitHub()
        with sqlite3.connect(":memory:") as db:
            def writer():
                return pub.DurablePublisher(db,github,renderer=pub.render_cve,
                    fingerprint=pub.cve_revision,owns=pub.owns_cve)
            positive=writer()
            self.assertEqual(pub.observe_cve_recovery(positive,[entry],["deferred"])["baselines"],0)
            outcomes=pub.sync_cve_entries(positive,[entry])
            self.assertEqual(pub.observe_cve_recovery(positive,[entry],outcomes)["baselines"],1)
            clean=copy.deepcopy(entry)
            clean["findings"]=[]
            for c in clean["containers"]:
                c["matches"]=[]
            def observe(value):
                return pub.observe_cve_recovery(writer(),[value],pub.sync_cve_entries(writer(),[value]))
            self.assertEqual(observe(clean)["candidates"],0)
            clean["assessedAt"]="2026-11-24T05:30:00Z"
            for c in clean["containers"]:
                c["assessedAt"]=clean["assessedAt"]
            self.assertEqual(observe(clean)["candidates"],0)
            clean["containers"][0]["image"]="registry.example/app@sha256:"+"b"*64
            self.assertEqual(observe(clean)["candidates"],1)
            self.assertEqual(observe(clean)["candidates"],1)
            self.assertEqual(github.create_calls,1)
            self.assertEqual(github.comments_written,0)
            self.assertEqual(next(iter(github.issues.values()))["state"],"open")
            # A missing/stale/unknown projection is excluded by validate_cve.
            self.assertEqual(pub.observe_cve_recovery(writer(),[],[])["candidates"],0)
            self.assertEqual(db.execute("SELECT candidate FROM cve_recovery_observations").fetchone(),(None,))
            self.assertEqual(observe(clean)["candidates"],1)
            partial=copy.deepcopy(clean)
            partial["containers"][0].update(detailState="partial",omittedMatches=1)
            self.assertEqual(observe(partial)["candidates"],0)
            legacy={k:v for k,v in clean.items() if k!="containers"}
            self.assertEqual(observe(legacy)["candidates"],0)
            other=dict(clean,otherHighCriticalMatches=1)
            # Non-CVE positives are not clean even when the CVE list is empty.
            self.assertIsNone(pub.recovery_candidate(pub.cve_recovery_snapshot(entry),other))
            self.assertEqual(observe(clean)["candidates"],1)
            db.execute("UPDATE issues SET pending_update=?",("a"*64,))
            db.commit()
            self.assertEqual(observe(clean)["candidates"],0)
            db.execute("UPDATE issues SET pending_update=NULL")
            db.commit()
            next(iter(github.issues.values()))["state"]="closed"
            result=observe(clean)
            self.assertEqual(result["candidates"],0)
            self.assertEqual(result["manualClosed"],1)
            self.assertEqual(github.comments_written,0)

    def test_recovery_shadow_rejects_scope_change_partial_rescan_and_unowned_issue(self):
        now=datetime(2026,11,24,6,1,tzinfo=timezone.utc)
        entry=pub.validate_cve(self.rich_fixture(),now)[0]
        baseline=pub.cve_recovery_snapshot(entry)
        clean=copy.deepcopy(entry)
        clean["findings"]=[]
        c=clean["containers"][0]
        c.update(matches=[],image="registry.example/app@sha256:"+"c"*64,
                 assessedAt="2026-11-24T05:30:00Z")
        self.assertIsNotNone(pub.recovery_candidate(baseline,clean))
        changed=copy.deepcopy(clean)
        changed["containers"].append(dict(c,name="sidecar"))
        self.assertIsNone(pub.recovery_candidate(baseline,changed))
        expanded=copy.deepcopy(baseline)
        expanded["containers"]["sidecar"]=dict(baseline["containers"][c["name"]])
        self.assertIsNone(pub.recovery_candidate(expanded,changed | {
            "containers":[c,dict(changed["containers"][1],assessedAt="2026-11-24T05:00:00Z")]}))
        github=FakeGitHub()
        with sqlite3.connect(":memory:") as db:
            writer=pub.DurablePublisher(db,github,renderer=pub.render_cve,
                fingerprint=pub.cve_revision,owns=pub.owns_cve)
            outcomes=pub.sync_cve_entries(writer,[entry])
            pub.observe_cve_recovery(writer,[entry],outcomes)
            next(iter(github.issues.values()))["body"]="human-owned"
            with self.assertRaises(ValueError):
                pub.observe_cve_recovery(writer,[clean],[])
            self.assertEqual(github.comments_written,0)

    def test_durable_closure_disabled_and_qualified_success(self):
        now=datetime(2026,11,24,6,1,tzinfo=timezone.utc)
        entry=pub.validate_cve(self.proof_fixture(),now)[0]
        clean=copy.deepcopy(entry)
        clean["findings"]=[]
        for c in clean["containers"]:
            c.update(matches=[],image="registry.example/app@sha256:"+"f"*64,
                     assessedAt="2026-11-24T05:30:00Z")
        for c in clean["recoveryProof"]["containers"]:
            c.update(allCves=[],assessmentId="a"*64)
        class BotGitea(FakeGitHub):
            def create(self,title,body):
                issue=super().create(title,body)
                issue["user"]={"login":pub.SECURITY_BOT}
                return issue
            def comment(self,number,body):
                result=super().comment(number,body)
                self.issues[number]["comment_items"][-1]["user"]={"login":pub.SECURITY_BOT}
                return result
            def close_cve(self,number):
                self.issues[number]["state"]="closed"
                return self.issues[number]
        for scenario in ("success","human","budget","ambiguous","notice","notice_lost"):
            with self.subTest(scenario=scenario),sqlite3.connect(":memory:") as db:
                github=BotGitea()
                writer=pub.DurablePublisher(db,github,renderer=pub.render_cve,
                    fingerprint=pub.cve_revision,owns=pub.owns_cve)
                result=pub.sync_cve_entries(writer,[entry])
                pub.observe_cve_recovery(writer,[entry],result)
                self.assertEqual(pub.observe_cve_recovery(writer,[clean],[])["proofQualified"],1)
                number=next(iter(github.issues))
                self.assertFalse(pub.CVE_CLOSURE_ENABLED)
                self.assertEqual(pub.sync_cve_closure(writer,[clean],[])["closed"],0)
                self.assertEqual(github.comments_written,0)
                if scenario in ("notice","notice_lost"):
                    with patch.object(github,"close_cve",side_effect=AssertionError("state mutation")) as close:
                        self.assertEqual(pub.sync_cve_closure(writer,[],[],notify_only=True)["notified"],0)
                        if scenario=="notice_lost":
                            original = github.comment
                            def lost_notice(number,body):
                                original(number,body)
                                raise OSError("lost response")
                            with patch.object(github,"comment",side_effect=lost_notice):
                                with self.assertRaises(OSError):
                                    pub.sync_cve_closure(writer,[clean],[],notify_only=True)
                            self.assertEqual(db.execute("SELECT phase FROM cve_closure_intents").fetchone()[0],"comment_pending")
                        first=pub.sync_cve_closure(writer,[clean],[],notify_only=True)
                        self.assertEqual(first["notified"],1)
                        self.assertEqual(first["pending"],0)
                        self.assertEqual(pub.sync_cve_closure(writer,[clean],[],notify_only=True)["notified"],0)
                        self.assertEqual(github.comments_written,1)
                        self.assertEqual(github.issues[number]["state"],"open")
                        self.assertEqual(db.execute("SELECT phase FROM cve_closure_intents").fetchone()[0],"comment_confirmed")
                        self.assertIn("Automatic closure is disabled",github.issues[number]["comment_items"][0]["body"])
                        close.assert_not_called()
                    continue
                if scenario=="human":
                    github.issues[number]["comment_items"]=[{"body":"review","user":{"login":"human"}}]
                    self.assertEqual(pub.sync_cve_closure(writer,[clean],[],enabled=True)["manualReview"],1)
                    self.assertEqual(github.issues[number]["state"],"open")
                    continue
                if scenario=="budget":
                    with patch.object(writer,"reserve",side_effect=pub.WriteBudgetDeferred("write_budget_deferred")):
                        self.assertEqual(pub.sync_cve_closure(writer,[clean],[],enabled=True)["deferred"],1)
                    self.assertEqual(db.execute("SELECT count(*) FROM cve_closure_intents").fetchone()[0],0)
                    continue
                if scenario=="ambiguous":
                    def lost(number):
                        github.issues[number]["state"]="closed"
                        raise OSError("lost response")
                    with patch.object(github,"close_cve",side_effect=lost):
                        with self.assertRaises(OSError):
                            pub.sync_cve_closure(writer,[clean],[],enabled=True)
                    self.assertEqual(db.execute("SELECT phase FROM cve_closure_intents").fetchone()[0],"patch_pending")
                    self.assertEqual(pub.sync_cve_closure(writer,[clean],[],enabled=True)["manualReview"],1)
                    self.assertEqual(db.execute("SELECT phase FROM cve_closure_intents").fetchone()[0],"ambiguous_closed")
                    continue
                self.assertEqual(pub.sync_cve_closure(writer,[],[],enabled=True)["closed"],0)
                self.assertEqual(pub.sync_cve_closure(writer,[clean],[],enabled=True)["closed"],1)
                self.assertEqual(github.issues[number]["state"],"closed")
                self.assertEqual(github.comments_written,1)
                self.assertEqual(pub.sync_cve_closure(writer,[clean],[],enabled=True)["closed"],0)

    def test_recurrence_only_from_confirmed_machine_closure(self):
        now=datetime(2026,11,24,6,1,tzinfo=timezone.utc)
        entry=pub.validate_cve(self.proof_fixture(),now)[0]
        clean=copy.deepcopy(entry)
        clean["findings"]=[]
        for c in clean["containers"]:
            c.update(matches=[],image="registry.example/app@sha256:"+"f"*64,
                     assessedAt="2026-11-24T05:30:00Z")
        for c in clean["recoveryProof"]["containers"]:
            c.update(allCves=[],assessmentId="a"*64)
        again=copy.deepcopy(entry)
        for c in again["containers"]:
            c.update(assessedAt="2026-11-24T06:00:00Z")
        for c in again["recoveryProof"]["containers"]:
            c.update(assessmentId="b"*64)
        class BotGitea(FakeGitHub):
            def create(self,title,body):
                issue=super().create(title,body)
                issue.update(user={"login":pub.SECURITY_BOT},labels=[],assignees=[],milestone=None)
                return issue
            def comment(self,number,body):
                result=super().comment(number,body)
                self.issues[number]["comment_items"][-1]["user"]={"login":pub.SECURITY_BOT}
                return result
            def close_cve(self,number):
                self.issues[number].update(state="closed",closed_at="2026-11-24T05:40:00Z",
                                           updated_at="2026-11-24T05:40:00Z")
                return self.issues[number]
            def reopen_cve(self,number):
                self.issues[number]["state"]="open"
                return self.issues[number]
        for scenario in ("success","human","ambiguous","manual","changed","budget"):
            with self.subTest(scenario=scenario),sqlite3.connect(":memory:") as db:
                github=BotGitea()
                writer=pub.DurablePublisher(db,github,renderer=pub.render_cve,
                    fingerprint=pub.cve_revision,owns=pub.owns_cve)
                pub.observe_cve_recovery(writer,[entry],pub.sync_cve_entries(writer,[entry]))
                pub.observe_cve_recovery(writer,[clean],[])
                number=next(iter(github.issues))
                if scenario=="manual":
                    github.issues[number]["state"]="closed"
                    self.assertEqual(pub.sync_cve_recurrence(writer,[again],enabled=True)["candidates"],0)
                    continue
                self.assertEqual(pub.sync_cve_closure(writer,[clean],[],enabled=True)["closed"],1)
                self.assertFalse(pub.CVE_REOPEN_ENABLED)
                self.assertEqual(pub.sync_cve_recurrence(writer,[again])["candidates"],1)
                self.assertEqual(github.issues[number]["state"],"closed")
                self.assertEqual(pub.sync_cve_recurrence(writer,[entry],enabled=True)["candidates"],0)
                if scenario=="human":
                    github.issues[number]["comment_items"].append({"body":"accepted risk","user":{"login":"human"}})
                if scenario=="changed":
                    github.issues[number]["title"]="manual review"
                if scenario in ("human","changed"):
                    self.assertEqual(pub.sync_cve_recurrence(writer,[again],enabled=True)["manualReview"],1)
                    continue
                if scenario=="budget":
                    with patch.object(writer,"reserve",side_effect=pub.WriteBudgetDeferred("write_budget_deferred")):
                        self.assertEqual(pub.sync_cve_recurrence(writer,[again],enabled=True)["deferred"],1)
                    self.assertEqual(db.execute("SELECT count(*) FROM cve_recurrence_intents").fetchone()[0],0)
                    continue
                if scenario=="ambiguous":
                    def lost(number):
                        github.issues[number]["state"]="open"
                        raise OSError("lost response")
                    with patch.object(github,"reopen_cve",side_effect=lost):
                        with self.assertRaises(OSError):
                            pub.sync_cve_recurrence(writer,[again],enabled=True)
                    self.assertEqual(pub.sync_cve_recurrence(writer,[again],enabled=True)["manualReview"],1)
                    self.assertEqual(db.execute("SELECT phase FROM cve_recurrence_intents").fetchone()[0],"patch_pending")
                    continue
                self.assertEqual(pub.sync_cve_recurrence(writer,[again],enabled=True)["reopened"],1)
                self.assertEqual(github.issues[number]["state"],"open")
                self.assertEqual(github.create_calls,1)
                self.assertEqual(github.comments_written,2)
                self.assertEqual(pub.sync_cve_recurrence(writer,[again],enabled=True)["reopened"],0)
                self.assertEqual(db.execute("SELECT phase FROM cve_recurrence_intents").fetchone()[0],"reopened")
                self.assertEqual(pub.observe_cve_recovery(writer,[again],pub.sync_cve_entries(writer,[again]))["baselines"],1)

    def test_cve_budget_deferral_does_not_block_other_keys_or_create_intents(self):
        entries = pub.validate_cve(self.fixture(), datetime(2026,11,24,6,1,tzinfo=timezone.utc))[:9]
        github = FakeGitHub()
        with sqlite3.connect(":memory:") as connection:
            writer = pub.DurablePublisher(connection, github, now=100000,
                renderer=pub.render_cve, fingerprint=pub.cve_revision, owns=pub.owns_cve,
                create_limit=1)
            self.assertEqual(writer.sync(entries[-1]), "created")
            outcomes = pub.sync_cve_entries(writer, entries)
            self.assertEqual(outcomes.count("deferred"), 8)
            self.assertEqual(outcomes.count("unchanged"), 1)
            self.assertEqual(github.create_calls, 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM issues").fetchone()[0], 1)
            writer = pub.DurablePublisher(connection, github, now=103601,
                renderer=pub.render_cve, fingerprint=pub.cve_revision, owns=pub.owns_cve,
                create_limit=min(9, len(pub.CVE_ALLOWED)))
            outcomes = pub.sync_cve_entries(writer, entries)
            self.assertEqual(outcomes.count("created"), 8)
            self.assertEqual(github.create_calls, 9)
            self.assertEqual(pub.sync_cve_entries(writer, entries).count("unchanged"), 9)
            self.assertEqual(connection.execute("SELECT count(*) FROM issues WHERE pending_create != 0 OR pending_update IS NOT NULL").fetchone()[0], 0)

    def test_expanded_cohort_preserves_daily_create_limit(self):
        entries = pub.validate_cve(self.fixture(), datetime(2026,11,24,6,1,tzinfo=timezone.utc))
        github = FakeGitHub()
        with sqlite3.connect(":memory:") as connection:
            def writer(now):
                return pub.DurablePublisher(connection, github, now=now,
                    renderer=pub.render_cve, fingerprint=pub.cve_revision, owns=pub.owns_cve,
                    create_limit=min(9,len(pub.CVE_ALLOWED)))
            outcomes=pub.sync_cve_entries(writer(100000),entries)
            self.assertEqual(outcomes.count("created"),9)
            self.assertEqual(outcomes.count("deferred"),3)
            self.assertEqual(pub.sync_cve_entries(writer(103601),entries).count("deferred"),3)
            outcomes=pub.sync_cve_entries(writer(186401),entries)
            self.assertEqual(outcomes.count("created"),3)
            self.assertEqual(outcomes.count("unchanged"),9)
            self.assertEqual(github.create_calls,12)

    def test_cve_batch_propagates_ambiguous_write_failure(self):
        entries = pub.validate_cve(self.fixture(), datetime(2026,11,24,6,1,tzinfo=timezone.utc))
        github = FakeGitHub()
        github.fail_before_create = True
        with sqlite3.connect(":memory:") as connection:
            writer = pub.DurablePublisher(connection, github, now=100000,
                renderer=pub.render_cve, fingerprint=pub.cve_revision, owns=pub.owns_cve)
            with self.assertRaises(OSError):
                pub.sync_cve_entries(writer, entries)
            self.assertEqual(github.create_calls, 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM issues WHERE pending_create != 0").fetchone()[0], 1)

    def test_empty_projection_is_distinct_from_invalid_projection_and_api_failure(self):
        obj={"kind":"ConfigMap","metadata":{"namespace":"kubescape","name":"kubescape-private-cve"}}
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

    def test_old_public_issue_number_is_never_adopted(self):
        key=pub.cve_key("memos/Deployment/memos")
        issue={"number":2181,"body":"Existing public triage for memos/Deployment/memos"}
        self.assertFalse(pub.owns_cve(issue,key))
        self.assertTrue(pub.owns_cve(dict(issue,body=pub.marker(key)),key))
        self.assertFalse(pub.owns_cve(dict(issue,body=pub.marker(key)*2),key))


class PrivateDestinationTests(unittest.TestCase):
    def setUp(self):
        self.repo={"id":pub.REPO_ID,"full_name":pub.REPO,"private":True,
                   "has_issues":True,"archived":False}
        self.calls=[]
        self.issues=[]

    def request(self, opener, url, token, *, method="GET", payload=None):
        self.calls.append((url,method,payload))
        if url==pub.GITEA+pub.REPO_PATH:
            return dict(self.repo)
        if method=="GET" and "?state=all&" in url:
            return self.issues
        if method=="POST":
            return {"number":1, **payload}
        raise AssertionError("Unexpected request")

    def test_private_repository_identity_verified_before_every_write(self):
        client=pub.PrivateGitea("test-token",requester=self.request)
        client.create("title","body")
        self.assertEqual([method for _,method,_ in self.calls],["GET","GET","POST"])
        self.repo["private"]=False
        with self.assertRaises(ValueError):
            client.create("title","body")
        self.assertEqual([method for _,method,_ in self.calls].count("POST"),1)
        for changed in ({"private":False},{"id":999},{"full_name":"other/repo"},
                        {"has_issues":False},{"archived":True}):
            with self.subTest(changed=changed):
                self.repo.update(private=True,id=pub.REPO_ID,full_name=pub.REPO,
                                 has_issues=True,archived=False)
                self.repo.update(changed)
                with self.assertRaises(ValueError):
                    pub.PrivateGitea("test-token",requester=self.request)

    def test_fixed_endpoint_no_arbitrary_routes_or_methods(self):
        client=pub.PrivateGitea("test-token",requester=self.request)
        for path,method in (("/repos/other/repo/issues","POST"),
                            ("/repos/"+pub.REPO+"/security-advisories","POST"),
                            (pub.ISSUE_PATH+"/1","PATCH"),
                            (pub.ISSUE_PATH+"/../issues","GET")):
            with self.subTest(path=path,method=method), self.assertRaises(ValueError):
                client.call(path,method,{"body":"forbidden"} if method!="GET" else None)
        self.assertEqual(len(self.calls),1)

    def test_cve_closure_patch_is_explicitly_gated_and_state_only(self):
        client=pub.PrivateGitea("test-token",requester=self.request)
        with self.assertRaises(ValueError):
            client.close_cve(1)
        def request(opener,url,token,*,method="GET",payload=None):
            if method=="PATCH":
                self.calls.append((url,method,payload))
                return {"number":1,"state":"closed"}
            return self.request(opener,url,token,method=method,payload=payload)
        client=pub.PrivateGitea("test-token",requester=request,allow_cve_closure=True)
        self.assertEqual(client.close_cve(1)["state"],"closed")
        for payload in ({"state":"open"},{"state":"closed","title":"overwrite"},{"body":"overwrite"}):
            with self.assertRaises(ValueError):
                client.call(pub.ISSUE_PATH+"/1","PATCH",payload)
        self.repo["private"]=False
        with self.assertRaises(ValueError):
            client.close_cve(1)
        self.assertEqual([m for _,m,_ in self.calls].count("PATCH"),1)

    def test_reopen_permission_is_separate_and_state_only(self):
        def request(opener,url,token,*,method="GET",payload=None):
            if method=="PATCH":
                self.calls.append((url,method,payload))
                return {"number":1,"state":"open"}
            return self.request(opener,url,token,method=method,payload=payload)
        client=pub.PrivateGitea("test-token",requester=request,allow_cve_reopen=True)
        self.assertEqual(client.reopen_cve(1)["state"],"open")
        with self.assertRaises(ValueError):
            client.close_cve(1)
        with self.assertRaises(ValueError):
            client.call(pub.ISSUE_PATH+"/1","PATCH",{"state":"open","title":"overwrite"})
        self.repo["private"]=False
        with self.assertRaises(ValueError):
            client.reopen_cve(1)
        self.assertEqual([m for _,m,_ in self.calls].count("PATCH"),1)

    def test_dedup_includes_closed_and_refuses_duplicate_markers(self):
        client=pub.PrivateGitea("test-token",requester=self.request)
        key="a"*64
        self.issues=[{"number":12,"state":"closed","body":pub.marker(key)}]
        self.assertEqual(client.search(key)["number"],12)
        self.issues.append({"number":13,"body":pub.marker(key)})
        with self.assertRaises(ValueError):
            client.search(key)

    def test_old_public_databases_not_reused_and_credential_isolated(self):
        import yaml
        manifest=list(yaml.safe_load_all((SOURCE.parent/"workload.yaml").read_text()))
        job=next(d for d in manifest if d["kind"]=="CronJob")
        env=job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["env"]
        credential=next(e for e in env if e["name"]=="GITEA_TOKEN")
        self.assertEqual(credential["valueFrom"]["secretKeyRef"]["name"],"homelab-security-gitea")
        self.assertFalse(any(e["name"]=="GITHUB_TOKEN" for e in env))
        source=SOURCE.read_text()
        self.assertIn("/state/gitea-native-publisher.sqlite",source)
        self.assertIn("/state/gitea-cve-publisher.sqlite",source)
        self.assertNotIn('"/state/publisher.sqlite"',source)
        self.assertNotIn('"/state/cve-publisher.sqlite"',source)
        # GitHub is now an explicitly scoped read-only candidate source, not
        # an issue destination. Keep the original private writer boundary.
        reader=next(e for e in env if e["name"]=="REMEDIATION_GITHUB_READ_TOKEN")
        self.assertEqual(reader["valueFrom"]["secretKeyRef"],
                         {"name":"security-remediation-github-read","key":"token","optional":True})
        self.assertNotEqual(reader["valueFrom"]["secretKeyRef"]["name"],
                            credential["valueFrom"]["secretKeyRef"]["name"])
        self.assertEqual(pub.REMEDIATION_API, "https://api.github.com/repos/RovxBot/homelab-infra")
        self.assertEqual(source.count("api.github.com"),1)
        client=pub.PrivateGitea("test-token",requester=self.request)
        with self.assertRaises(ValueError):
            client.call(pub.REMEDIATION_API+"/issues","POST",{"title":"x","body":"x"})
        with self.assertRaises(ValueError):
            client.call(pub.REMEDIATION_API+"/actions/runs")
        self.assertTrue(all(url.startswith(pub.GITEA+"/") for url, _, _ in self.calls))
        old=yaml.safe_load((ROOT/"infra/kyverno/automation/github-issue-sync-cronjob.yaml").read_text())
        self.assertIs(old["spec"]["suspend"],True)
        oldenv=old["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertFalse(any(e["name"]=="GITHUB_TOKEN" for e in oldenv))


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
