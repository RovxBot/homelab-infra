"""Synthetic fixtures exercise report semantics and the real bounded CLI transport."""

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


SCRIPT = Path(__file__).resolve().parents[1] / "kyverno-timeout-evidence.py"
SPEC = importlib.util.spec_from_file_location("kyverno_evidence", SCRIPT)
E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E)
NOW = datetime.now(timezone.utc)
AT = E.iso(NOW - timedelta(seconds=20))
UID_A = "11111111-2222-3333-4444-555555555555"
UID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SECRET = "fixture-sensitive-token-NeverPublish"


def message(hook="validate-policy.kyverno.svc", resource="ClusterPolicy/test-policy", uid=UID_A):
    return (f'{resource} dry-run failed: failed calling webhook "{hook}": '
            f'Post "https://kyverno-svc.kyverno.svc:443/policyvalidate?timeout=10s": '
            f'context deadline exceeded requestUID="{uid}"')


def event(text=None, at=AT, reporter="kustomize-controller"):
    return {"metadata": {"creationTimestamp": at}, "lastTimestamp": at,
            "source": {"component": reporter},
            "involvedObject": {"kind": "Kustomization", "namespace": "flux-system", "name": "kyverno-policies"},
            "message": text if text is not None else message()}


def pod(pod_name, node):
    return {"metadata": {"name": pod_name, "uid": pod_name + "-uid",
                         "creationTimestamp": E.iso(NOW - timedelta(days=1)),
                         "labels": {"app.kubernetes.io/component": "admission-controller"}},
            "spec": {"nodeName": node, "containers": [{"name": "kyverno", "env": [{"value": SECRET}]}]},
            "status": {"conditions": [{"type": "Ready", "status": "True"}],
                       "containerStatuses": [{"name": "kyverno", "ready": True, "restartCount": 0}]}}


def endpoints(pod_name="kyverno-a", ready=True, address="10.1.2.3"):
    return [{"metadata": {"name": "kyverno-svc-fixture"}, "ports": [{"port": 9443}],
             "endpoints": [{"addresses": [address], "nodeName": "private-node-a.example",
                            "targetRef": {"name": pod_name, "uid": pod_name + "-uid"},
                            "conditions": {"ready": ready, "serving": True, "terminating": False}}]}]


class FixtureReader(E.Reader):
    def __init__(self):
        super().__init__("fixture-context")
        self.calls = []
        self.pods = [pod("kyverno-a", "private-node-a.example"), pod("kyverno-b", "private-node-b.example")]
        self.events = [event()]
        self.kustomizations = []
        self.slices = [endpoints(), endpoints()]
        self.logs = {
            "kyverno-a": f'{AT} level=error msg="context deadline exceeded" requestUID="{UID_A}" duration=10.2s token={SECRET}\n',
            "kyverno-b": f'{AT} level=error msg="discovery failed" requestUID="{UID_B}"\n',
        }
        self.metrics_missing = False

    def command(self, args, cap):
        self.calls.append(args)
        if "logs" in args:
            return self.logs.get(args[3], "")
        path = urlsplit(args[-1]).path
        if path.endswith("/endpointslices"):
            return json.dumps({"items": self.slices.pop(0)})
        if path.endswith("/services/kyverno-svc"):
            return json.dumps({"spec": {"type": "ClusterIP", "clusterIP": "10.99.1.2",
                                       "selector": {"app.kubernetes.io/component": "admission-controller"},
                                       "ports": [{"port": 443, "targetPort": 9443, "protocol": "TCP"}]}})
        if "/metrics.k8s.io/" in path:
            if self.metrics_missing:
                raise E.Unavailable("forbidden")
            return json.dumps({"items": [{"metadata": {"name": "kyverno-a"}, "timestamp": AT,
                                          "containers": [{"name": "kyverno", "usage": {"cpu": "41m", "memory": "104Mi"}}]}]})
        if path.endswith("/pods"):
            return json.dumps({"items": self.pods})
        if path.endswith("/events"):
            return json.dumps({"items": self.events})
        if path.endswith("/kustomizations"):
            return json.dumps({"items": self.kustomizations})
        raise AssertionError("Unexpected fixture API path")


class EvidenceTests(unittest.TestCase):
    def collect(self, reader=None, **kwargs):
        return E.collect(reader or FixtureReader(), **kwargs)

    def test_timeout_with_matching_controller_identifier_and_latency(self):
        report = self.collect()
        row = report["confirmed_observations"]["flux_timeouts"][0]
        self.assertEqual(row["webhook"], "validate-policy.kyverno.svc")
        self.assertEqual(row["kustomization"]["name"], "kyverno-policies")
        self.assertEqual(row["resource"]["name"], "test-policy")
        self.assertEqual(row["configured_deadline_seconds"], 10)
        self.assertEqual(row["at"], AT)
        matches = report["possible_correlations"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["pod"], "kyverno-a")
        self.assertEqual(matches[0]["basis"], "explicit identifier overlap")
        self.assertEqual(report["confirmed_observations"]["controller_evidence"][0]["operation_latency"], {"milliseconds": 10200})
        self.assertIn("Does not establish", matches[0]["interpretation"])

    def test_timeout_without_corresponding_request_is_not_claimed_as_nonreceipt(self):
        reader = FixtureReader()
        reader.logs = {"kyverno-a": f'{AT} level=info msg="health check"\n'}
        report = self.collect(reader)
        self.assertEqual(len(report["confirmed_observations"]["flux_timeouts"]), 1)
        self.assertEqual(report["confirmed_observations"]["controller_evidence"], [])
        self.assertEqual(report["possible_correlations"], [])
        self.assertIn("No corresponding request in this sample does not prove that Kyverno did not receive it.",
                      report["evidence_unavailable_or_limited"])

    def test_multiple_replicas_changing_endpoints_and_unknown_readiness(self):
        reader = FixtureReader()
        reader.slices[1] = endpoints("kyverno-b", False, "10.1.2.4")
        reader.slices[1][0]["endpoints"].append({"addresses": ["10.1.2.5"], "targetRef": {"name": "kyverno-c"}})
        report = self.collect(reader)["confirmed_observations"]
        self.assertEqual(len(report["pods"]), 2)
        self.assertEqual(len({p["node"] for p in report["pods"]}), 2)
        self.assertTrue(report["endpoints_changed_during_collection"])
        self.assertFalse(report["endpoints_end"][0]["ready"])
        self.assertIsNone(report["endpoints_end"][1]["ready"])
        self.assertEqual(report["pods"][0]["instance"], report["endpoints_start"][0]["instance"])
        self.assertEqual(report["pods"][0]["node"], report["endpoints_start"][0]["node"])

    def test_missing_metrics_audit_and_flows_are_explicit(self):
        reader = FixtureReader()
        reader.metrics_missing = True
        report = self.collect(reader)
        self.assertEqual(report["sources"]["pod-metrics"]["status"], "unavailable")
        self.assertEqual(report["confirmed_observations"]["pod_usage"], [])
        limits = " ".join(report["evidence_unavailable_or_limited"])
        for text in ("audit/trace data unavailable", "Cilium flow evidence unavailable", "resource usage unavailable", "do not prove API-server-to-webhook"):
            self.assertIn(text, limits)

    def test_safe_redaction_allowlist_excludes_raw_data_and_nested_objects(self):
        reader = FixtureReader()
        reader.events[0]["message"] += f' Authorization: Bearer {SECRET} password=secret AdmissionReview={{"object":{{"secret":"body-secret"}}}}'
        reader.logs["kyverno-a"] += f'{AT} ' + json.dumps({"level": "error", "msg": "error " + SECRET,
            "request": {"requestUID": UID_B, "object": {"password": "body-secret"}}, "token": SECRET}) + "\n"
        raw = E.encode_report(self.collect(reader))
        for forbidden in (SECRET, "body-secret", "Authorization", "Bearer", "10.1.2.3", "10.99.1.2", "private-node", UID_A, UID_B, '"env"', '"object"', '"message"'):
            self.assertNotIn(forbidden, raw)
        self.assertIn("test-policy", raw)

    def test_report_limits_and_no_raw_overflow_fallback(self):
        reader = FixtureReader()
        reader.events *= 200
        reader.logs["kyverno-a"] *= 1000
        report = self.collect(reader, max_items=3)
        self.assertEqual(len(report["confirmed_observations"]["flux_timeouts"]), 3)
        self.assertEqual(report["counts_before_report_limits"]["controller_evidence"], 1001)
        self.assertLessEqual(len(E.encode_report(report).encode()), E.MAX_REPORT_BYTES)
        large = copy.deepcopy(report)
        large["possible_correlations"] *= 1000
        self.assertLessEqual(len(E.encode_report(large).encode()), E.MAX_REPORT_BYTES)
        self.assertTrue(large["report_rows_trimmed"])

    def test_no_false_attribution_from_time_or_resource_uid(self):
        reader = FixtureReader()
        reader.events = [event(message(uid=UID_B))]
        reader.logs = {"kyverno-a": f'{AT} level=error requestUID="{UID_A}"\n'}
        self.assertEqual(self.collect(reader)["possible_correlations"], [])
        self.assertEqual(E.identifiers(f'uid="{UID_A}" objectUID="{UID_A}"'), {})
        self.assertEqual(E.identifiers(f'auditID="{UID_A}"').keys(), {"audit_id"})
        self.assertEqual(E.latency('duration=10 latency=500'), None)
        self.assertEqual(E.latency('duration_ms=25'), {"milliseconds": 25})

    def test_time_overlap_is_only_possible_and_different_id_types_do_not_join(self):
        reader = FixtureReader()
        reader.events = [event(message().replace("requestUID", "auditID"))]
        report = self.collect(reader)
        self.assertTrue(report["possible_correlations"])
        self.assertTrue(all(r["basis"] == "time overlap only" for r in report["possible_correlations"]))
        self.assertTrue(all(r["shared_identifier_types"] == [] for r in report["possible_correlations"]))

    def test_condition_transition_is_not_used_as_request_time(self):
        reader = FixtureReader()
        reader.events = []
        reader.kustomizations = [{"metadata": {"namespace": "flux-system", "name": "kyverno-policies"},
                                  "status": {"conditions": [{"lastTransitionTime": AT, "message": message().replace(f'requestUID="{UID_A}"', "")}]}}]
        report = self.collect(reader)
        self.assertEqual(report["possible_correlations"], [])
        self.assertIn("not request time", report["confirmed_observations"]["flux_timeouts"][0]["source"])

    def test_event_aggregation_window_reporter_and_multiline_identity(self):
        current = event(at="2000-01-01T00:00:00Z")
        current["series"] = {"lastObservedTime": AT, "count": 20}
        text = message("validate.kyverno.svc-fail", "Deployment/apps/bridge")
        rows = E.flux_evidence([current, event(text), event(reporter="other-controller"),
                               event(at="2000-01-01T00:00:00Z"), event(at="2099-01-01T00:00:00Z")], [], NOW - timedelta(minutes=1), NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["resource"], {"kind": "Deployment", "namespace": "apps", "name": "bridge"})
        self.assertEqual(E.timeout_rows('failed calling webhook "validate.kyverno.svc-fail"\nOther/x context deadline exceeded', NOW, "event", {}), [])

    def test_ambiguous_hooks_and_resource_names_do_not_get_attributed(self):
        self.assertEqual(E.timeout_rows(message() + ' failed calling webhook "another.example.com"', NOW, "event", {}), [])
        self.assertEqual(E.timeout_rows(message("not-kyverno.example.com"), NOW, "event", {}), [])
        rows = E.timeout_rows('ClusterPolicy/other ' + message(), NOW, "event", {})
        self.assertEqual(rows[0]["resource"]["name"], "unavailable")

    def test_log_bounds_timestamps_generic_uid_and_nested_ids(self):
        lines = [f'{AT} ' + json.dumps({"level": "error", "duration_ms": 10, "requestUID": UID_A}),
                 f'{AT} ' + json.dumps({"request": {"requestUID": UID_B, "duration_ms": 100}}),
                 'invalid-timestamp level=error requestUID="' + UID_B + '"',
                 f'2000-01-01T00:00:00Z level=error requestUID="{UID_B}"']
        rows = E.controller_evidence("\n".join(lines), "kyverno-a", "current", NOW - timedelta(minutes=1), NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation_latency"], {"milliseconds": 10})
        self.assertEqual(rows[0]["category"], "controller-error")

    def test_structured_message_does_not_supply_request_fields(self):
        line = f'{AT} ' + json.dumps({"level": "info", "msg": f'requestUID={UID_A} duration=10s',
                                     "object": {"requestUID": UID_B}})
        rows = E.controller_evidence(line, "kyverno-a", "current", NOW - timedelta(minutes=1), NOW)
        self.assertEqual(rows, [])

    def test_previous_container_logs_and_read_only_commands(self):
        reader = FixtureReader()
        reader.pods[0]["status"]["containerStatuses"][0]["restartCount"] = 1
        report = self.collect(reader)
        self.assertTrue(any("--previous=true" in args for args in reader.calls))
        self.assertEqual(report["confirmed_observations"]["pods"][0]["restarts"], 1)
        for args in reader.calls:
            self.assertTrue(args[0] == "get" and args[1] == "--raw" or args[:3] == ["-n", "kyverno", "logs"])
            if "logs" in args:
                self.assertIn("--tail=1000", args)
                self.assertIn("--limit-bytes=131072", args)
                self.assertFalse(any(a in args for a in ("--follow", "-f")))

    def test_missing_pod_status_is_unknown_and_old_metrics_do_not_attach(self):
        self.assertIsNone(E.pod_rows([{"metadata": {"name": "missing"}}])[0]["restarts"])
        metrics = [{"metadata": {"name": "kyverno-a"}, "timestamp": "2000-01-01T00:00:00Z", "containers": []}]
        self.assertEqual(E.metric_rows(metrics, [pod("kyverno-a", "node")]), [])


class TransportTests(unittest.TestCase):
    def test_pagination_continues_and_reports_bounded_incompleteness(self):
        reader = E.Reader("test")
        page = json.dumps({"items": [{"message": SECRET}], "metadata": {"continue": "opaque/next?token"}})
        with patch.object(reader, "command", return_value=page) as command:
            self.assertEqual(len(reader.listing("events", "/api/v1/events")), 3)
        self.assertTrue(reader.sources["events"]["truncated"])
        self.assertEqual(command.call_count, 3)
        query = parse_qs(urlsplit(command.call_args[0][0][-1]).query)
        self.assertEqual(query["continue"], ["opaque/next?token"])

    def test_unavailable_endpoint_sample_does_not_claim_change_or_stability(self):
        reader = FixtureReader()
        original = reader.command
        def fail_end(args, cap):
            if args[-1].startswith("/apis/discovery") and len(reader.slices) == 1:
                raise E.Unavailable("forbidden")
            return original(args, cap)
        with patch.object(reader, "command", side_effect=fail_end):
            report = E.collect(reader)
        self.assertIsNone(report["confirmed_observations"]["endpoints_changed_during_collection"])

    def fake_kubectl(self, source):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        executable = Path(tmp.name) / "kubectl"
        executable.write_text("#!/usr/bin/env python3\n" + source)
        executable.chmod(0o755)
        return patch.dict(os.environ, {"PATH": tmp.name + os.pathsep + os.environ["PATH"]})

    def test_real_subprocess_byte_limit_suppresses_output(self):
        with self.fake_kubectl(f'import sys\nsys.stdout.write({SECRET!r} * 10000)\n'):
            reader = E.Reader("fixture")
            self.assertEqual(reader.read("fixture", ["get", "--raw", "/api/v1/events"], cap=1000), {})
        self.assertEqual(reader.sources["fixture"]["reason"], "response-byte-limit-exceeded")
        self.assertNotIn(SECRET, json.dumps(reader.sources))

    def test_real_subprocess_failure_stderr_never_escapes(self):
        with self.fake_kubectl(f'import sys\nsys.stderr.write("Forbidden token={SECRET}")\nsys.exit(1)\n'):
            reader = E.Reader("fixture")
            reader.read("fixture", ["get", "--raw", "/api/v1/events"])
        self.assertEqual(reader.sources["fixture"]["reason"], "forbidden")
        self.assertNotIn(SECRET, json.dumps(reader.sources))

    def test_wall_clock_deadline_and_exhausted_budget(self):
        with self.fake_kubectl('import time\ntime.sleep(10)\n'):
            reader = E.Reader("fixture", budget=.15)
            started = time.monotonic()
            reader.read("first", ["get", "--raw", "/api/v1/events"])
            reader.read("second", ["get", "--raw", "/api/v1/events"])
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(reader.sources["first"]["reason"], "request-time-budget-exhausted")
        self.assertEqual(reader.sources["second"]["reason"], "snapshot-budget-exhausted")

    def test_cli_partial_report_exit_status_and_context_forwarding(self):
        with self.fake_kubectl('import sys\nassert sys.argv[1:3] == ["--context", "fixture"]\nsys.stderr.write("Forbidden")\nsys.exit(1)\n'):
            result = subprocess.run(["python3", str(SCRIPT), "--context", "fixture"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["sources"]["pods"]["status"], "unavailable")
        self.assertLessEqual(len(result.stdout.encode()), E.MAX_REPORT_BYTES)


if __name__ == "__main__":
    unittest.main()
