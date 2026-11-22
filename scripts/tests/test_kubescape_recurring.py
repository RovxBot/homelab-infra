"""Exercise production runner failure boundaries without disrupting applications."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("recurring", ROOT / "infra/kubescape/recurring/runner.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
CONFIG = json.loads((ROOT / "infra/kubescape/recurring/config.json").read_text())
# The pre-expansion single-Memos scenarios remain independent of the live cohort.
CANARY_CONFIG = {**CONFIG, "targets": CONFIG["targets"][:1]}
EXPECTED = json.loads((ROOT / "infra/kubescape/grype-database.json").read_text())
NOW = datetime(2026, 11, 22, 5, tzinfo=timezone.utc)


def row():
    return {"namespace": "memos", "kind": "Deployment", "workload": "memos", "container": "memos",
            "workloadUID": "workload-uid", "generation": 1, "podUIDs": ["pod-uid"], "ready": True,
            "containerType": "containers", "runningDigest": R.client.MEMOS_IMAGE}


class CoverageTests(unittest.TestCase):
    def test_coverage_states_and_stale_database(self):
        config = {**CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]}
        current = row()
        record = {"createdAt": R.db.stamp(NOW - timedelta(hours=1)), "database": EXPECTED["metadata"],
                  "severities": {"Critical": 1}, "fixesAvailable": True}
        def state(record, target=current):
            return R.coverage([copy.deepcopy(target)], {R.row_key(target): record}, config, NOW)["rows"][0]["assessmentState"]
        self.assertEqual(state({}), "unknown")
        self.assertEqual(state(record), "healthy")
        self.assertEqual(state({**record, "createdAt": R.db.stamp(NOW - timedelta(hours=73))}), "stale")
        self.assertEqual(state({**record, "lastAttempt": {"state": "failed"}}), "failed")
        self.assertEqual(state({**record, "lastAttempt": {"state": "unknown"}}), "unknown")
        failed = {**record, "lastAttempt": {"state": "failed"}}
        inventory = R.coverage([copy.deepcopy(current)], {R.row_key(current): failed}, config, NOW)
        self.assertEqual(inventory["rows"][0]["scanAgeHours"], 1.0)
        self.assertEqual(state(record, {**current, "workload": "other"}), "deliberately_excluded")
        self.assertEqual(state({}, {**current, "runningDigest": None}), "unsupported")
        changed = {**record, "database": {**EXPECTED["metadata"], "checksum": "sha256:" + "0" * 64}}
        self.assertEqual(state(changed), "stale")
        inventory = R.coverage([copy.deepcopy(current)], {}, config, NOW)
        self.assertIn("validUntil", inventory)

    def test_new_digest_cannot_inherit_old_workload_coverage(self):
        previous = row()
        changed = {**previous, "runningDigest": "docker.io/image@sha256:" + "f" * 64}
        config = {**CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]}
        inventory = R.coverage([changed], {R.row_key(previous): {"createdAt": R.db.stamp(NOW)}}, config, NOW)
        self.assertEqual(inventory["rows"][0]["assessmentState"], "unknown")


    def test_curated_targets_resolve_independently(self):
        self.assertEqual(len(CONFIG["targets"]), 6)
        home = {**row(), "namespace": "homepage", "workload": "homepage-assets",
                "container": "nginx", "workloadUID": "homepage-uid"}
        config = {**CONFIG, "targets": CONFIG["targets"][:2]}
        selected, outcomes = R.resolve_targets([row(), {**home, "ready": False}], config)
        self.assertEqual(selected, [row()])
        self.assertEqual(outcomes[1]["state"], "skipped")
        selected, outcomes = R.resolve_targets([row()], config)
        self.assertEqual(selected, [row()])
        self.assertEqual(outcomes[1]["reasons"], ["workload_missing"])
        selected, outcomes = R.resolve_targets([row(), {**home, "runningDigest": None}], config)
        self.assertEqual(outcomes[1]["state"], "unsupported")


class BatchTests(unittest.TestCase):
    def rows(self):
        result = []
        for i, target in enumerate(CONFIG["targets"]):
            namespace, kind, workload, container = target["identity"].split("/")
            result.append({**row(), "namespace": namespace, "kind": kind, "workload": workload,
                           "container": container, "workloadUID": "uid-" + str(i),
                           "runningDigest": "registry/image@sha256:" + str(i) * 64})
        return result

    def config(self):
        return {**CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]}

    def test_batch_rotates_unscanned_changed_stale_and_failures(self):
        rows, config = self.rows(), self.config()
        self.assertEqual(R.select_batch(rows, {}, config), rows[:4])
        records = {R.row_key(r): {"createdAt": R.db.stamp(NOW), "database": EXPECTED["metadata"]} for r in rows[:4]}
        self.assertEqual(R.select_batch(rows, records, config)[:2], rows[4:])
        records[R.row_key(rows[2])]["createdAt"] = R.db.stamp(NOW - timedelta(hours=74))
        self.assertEqual(R.select_batch(rows, records, config)[2], rows[2])
        records[R.row_key(rows[1])]["database"] = {"checksum": "old"}
        self.assertEqual(R.select_batch(rows, records, config)[0], rows[4])
        self.assertIn(rows[1], R.select_batch(rows, records, config))
        changed = {**rows[0], "runningDigest": "registry/image@sha256:" + "f" * 64}
        self.assertEqual(R.select_batch([changed, *rows[1:]], records, config)[0], changed)
        # Failed new images get an attempt timestamp and cannot monopolise a batch.
        failures = {R.row_key(r): {"lastAttempt": {"checkedAt": R.db.stamp(NOW),
                    "databaseChecksum": config["databaseChecksum"]}} for r in rows[:4]}
        self.assertEqual(R.select_batch(rows, failures, config)[:2], rows[4:])

    def test_failure_isolated_and_successes_survive_restart_and_publish(self):
        rows, config = self.rows(), self.config()
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db, "utcnow", return_value=NOW):
            history = Path(path)
            old = {"createdAt": R.db.stamp(NOW - timedelta(hours=73)), "database": EXPECTED["metadata"]}
            R.atomic(history / "records.json", {R.row_key(r): old.copy() for r in rows})
            runner = R.Runner(mock.Mock(), config, EXPECTED, history, "vulnerability")
            def scan(r, _):
                if r == rows[1]:
                    raise R.db.Rejected("scan_failed")
                result = {"createdAt": R.db.stamp(NOW), "database": EXPECTED["metadata"]}
                runner.records[R.row_key(r)] = result
                R.atomic(history / "records.json", runner.records)
                runner.result["assessments"].append(result)
                runner.result["lastSuccessfulAssessmentAt"] = result["createdAt"]
            with mock.patch.object(R, "workload_rows", return_value=rows), mock.patch.object(runner, "publish"), \
                    mock.patch.object(runner, "preflight"), mock.patch.object(runner, "audit", return_value={"checkedAt": R.db.stamp(NOW)}), \
                    mock.patch.object(runner, "gate"), mock.patch.object(runner, "vulnerability", side_effect=scan) as call:
                self.assertEqual(runner.run(), 4)
            self.assertEqual(call.call_count, 4)
            self.assertEqual(runner.records[R.row_key(rows[1])]["createdAt"], old["createdAt"])
            self.assertEqual(runner.records[R.row_key(rows[0])]["createdAt"], R.db.stamp(NOW))
            runner.api.call.return_value = {"data": {"posture.json": "retained"}}
            with mock.patch.object(R, "workload_rows", return_value=rows), mock.patch("builtins.print"):
                runner.publish()
            published = runner.api.call.call_args.args[1]["data"]
            self.assertEqual(published["posture.json"], "retained")
            self.assertEqual(published["vulnerability.lastSuccessfulAssessmentAt"], R.db.stamp(NOW))
            restarted = R.Runner(mock.Mock(), config, EXPECTED, history, "vulnerability")
            self.assertEqual(restarted.records, runner.records)
            self.assertEqual(restarted.result["lastSuccessfulAssessmentAt"], R.db.stamp(NOW))

    def test_ambiguous_later_request_preserves_peer_success_and_stops_batch(self):
        rows, config = self.rows()[:3], {**self.config(), "targets": CONFIG["targets"][:3]}
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db, "utcnow", return_value=NOW):
            history = Path(path)
            runner = R.Runner(mock.Mock(), config, EXPECTED, history, "vulnerability")
            def scan(r, _):
                if r == rows[1]:
                    R.atomic(history / "inflight.json", {"jobID": "ambiguous"})
                    raise OSError("lost response")
                result = {"createdAt": R.db.stamp(NOW), "database": EXPECTED["metadata"]}
                runner.records[R.row_key(r)] = result
                R.atomic(history / "records.json", runner.records)
                runner.result["assessments"].append(result)
                runner.result["lastSuccessfulAssessmentAt"] = result["createdAt"]
            with mock.patch.object(R, "workload_rows", return_value=rows), mock.patch.object(runner, "publish"), \
                    mock.patch.object(runner, "preflight"), mock.patch.object(runner, "audit", return_value={"checkedAt": R.db.stamp(NOW)}), \
                    mock.patch.object(runner, "gate"), mock.patch.object(runner, "vulnerability", side_effect=scan) as call:
                self.assertEqual(runner.run(), 3)
            self.assertEqual(call.call_count, 2)
            self.assertTrue((history / "inflight.json").exists())
            stored = json.loads((history / "records.json").read_text())
            self.assertEqual(stored[R.row_key(rows[0])]["createdAt"], R.db.stamp(NOW))
            self.assertEqual(stored[R.row_key(rows[1])]["lastAttempt"]["state"], "unknown")
            self.assertNotIn(R.row_key(rows[2]), stored)
            self.assertEqual(runner.result["targets"][2]["reasons"], ["uncertain_previous_request"])

    def test_existing_other_mode_success_is_exposed_without_another_scan(self):
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db, "utcnow", return_value=NOW):
            api = mock.Mock()
            api.call.return_value = {"data": {"posture.json": json.dumps({"state": "healthy", "assessments": [{"createdAt": R.db.stamp(NOW)}]})}}
            runner = R.Runner(api, self.config(), EXPECTED, Path(path), "vulnerability")
            with mock.patch.object(R, "workload_rows", return_value=[]), mock.patch("builtins.print"):
                runner.publish()
            published = api.call.call_args.args[1]["data"]
            self.assertEqual(published["posture.lastSuccessfulAssessmentAt"], R.db.stamp(NOW))
            self.assertEqual(published["posture.state"], "healthy")

    def test_posture_submission_does_not_resolve_vulnerability_workloads(self):
        def summary(namespace, name):
            return {"metadata": {"namespace": namespace, "name": name,
                    "annotations": {"kubescape.io/report-created-at": R.db.stamp(NOW)}}, "spec": {"controls": {"x": {}}}}
        summaries = [summary("memos", "deployment-memos"), summary("kubescape", "shared")]
        result = {"summaryDetails": {"frameworks": [{"controls": [{"status": "passed"}]}]}}
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db, "utcnow", return_value=NOW):
            api = mock.Mock()
            api.items.return_value = summaries
            api.get.side_effect = lambda base, kind, name, namespace: next(s for s in summaries if s["metadata"]["name"] == name)
            runner = R.Runner(api, self.config(), EXPECTED, Path(path), "posture")
            with mock.patch.object(R, "workload_rows", side_effect=AssertionError("target coupling")), \
                    mock.patch.object(R, "component_pod", return_value={"metadata": {"uid": "scanner"}}), \
                    mock.patch.object(R, "service", side_effect=[{"id": "posture"}, {"type": "success", "response": result}]), \
                    mock.patch.object(runner, "gate"), mock.patch.object(runner, "observe"):
                runner.posture({})
            self.assertEqual(runner.result["lastSuccessfulAssessmentAt"], R.db.stamp(NOW))
            self.assertFalse((Path(path) / "inflight.json").exists())

    def test_missing_vulnerability_targets_do_not_gate_posture(self):
        with tempfile.TemporaryDirectory() as path:
            runner = R.Runner(mock.Mock(), self.config(), EXPECTED, Path(path), "posture")
            with mock.patch.object(runner, "publish"), mock.patch.object(runner, "preflight"), \
                    mock.patch.object(runner, "audit"), mock.patch.object(runner, "gate"), \
                    mock.patch.object(runner, "posture") as posture, mock.patch.object(R, "targets", side_effect=AssertionError("coupled")):
                self.assertEqual(runner.run(), 0)
            posture.assert_called_once()


class SchedulingTests(unittest.TestCase):
    def test_duplicate_process_fails_without_queueing(self):
        with tempfile.TemporaryDirectory() as path:
            with R.locked(Path(path)):
                with self.assertRaisesRegex(R.db.Rejected, "duplicate_execution"):
                    with R.locked(Path(path)):
                        self.fail("second process acquired lock")
            with R.locked(Path(path)):
                pass

    def test_uncertain_request_checks_existing_id_and_never_posts(self):
        with tempfile.TemporaryDirectory() as path:
            runner = R.Runner(mock.Mock(), CANARY_CONFIG, EXPECTED, Path(path), "vulnerability")
            R.atomic(Path(path) / "inflight.json", {"jobID": "existing-request"})
            with mock.patch.object(R, "service", return_value={"state": "succeeded"}) as call:
                with self.assertRaisesRegex(R.db.Rejected, "uncertain_previous_request"):
                    runner.fence()
            call.assert_called_once_with("kubevuln", "/v1/scanStatus/existing-request")
            self.assertTrue((Path(path) / "inflight.json").exists())

    def test_fenced_posture_lost_response_cannot_resubmit(self):
        with tempfile.TemporaryDirectory() as path:
            runner = R.Runner(mock.Mock(), CANARY_CONFIG, EXPECTED, Path(path), "posture")
            R.atomic(Path(path) / "inflight.json", {"mode": "posture"})
            with mock.patch.object(R, "service") as call:
                with self.assertRaisesRegex(R.db.Rejected, "uncertain_previous_request"):
                    runner.fence()
            call.assert_not_called()


class FailureTests(unittest.TestCase):
    def run_failure(self, reason, state="failed"):
        with tempfile.TemporaryDirectory() as path:
            runner = R.Runner(mock.Mock(), CANARY_CONFIG, EXPECTED, Path(path), "vulnerability")
            with mock.patch.object(R, "workload_rows", return_value=[row()]), mock.patch.object(runner, "publish"), \
                    mock.patch.object(runner, "preflight", side_effect=R.db.Rejected(reason)), mock.patch.object(runner, "audit") as audit:
                self.assertNotEqual(runner.run(), 0)
                self.assertEqual(runner.result["state"], state)
                self.assertEqual(runner.result["reasons"], [reason])
                audit.assert_not_called()

    def test_stale_database(self):
        self.assertEqual(R.db.database_freshness(EXPECTED["metadata"]["built"], NOW + timedelta(days=8))["state"], "grace")
        self.run_failure("stale_database", "stale")

    def test_refresh_in_progress(self):
        self.run_failure("database_refresh_in_progress")

    def test_scanner_unavailable(self):
        api = mock.Mock()
        api.items.return_value = []
        with self.assertRaisesRegex(R.db.Rejected, "kubevuln_unavailable"):
            R.component_pod(api, "kubevuln")
        self.run_failure("kubevuln_unavailable")

    def test_storage_api_unavailable(self):
        with tempfile.TemporaryDirectory() as path:
            runner = R.Runner(mock.Mock(), CANARY_CONFIG, EXPECTED, Path(path), "vulnerability")
            with mock.patch.object(R, "workload_rows", return_value=[row()]), mock.patch.object(runner, "publish"), \
                    mock.patch.object(runner, "preflight", side_effect=R.APIError(503)):
                self.assertEqual(runner.run(), 3)
                self.assertEqual(runner.result["state"], "unknown")

    def test_low_storage(self):
        self.run_failure("database_pvc_low_space")
        self.run_failure("replica_disk_low_space")
        self.run_failure("reports_pvc_low_space")

    def test_failed_timeout_missing_report_and_changed_image_keep_fence(self):
        for case in ("scan_failed", "scan_timeout", "report_missing", "workload_image_changed", "engine_restarted"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as path:
                runner = R.Runner(mock.Mock(), {**CANARY_CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]}, EXPECTED, Path(path), "vulnerability")
                def fail(*_):
                    R.atomic(Path(path) / "inflight.json", {"jobID": "submitted"})
                    raise R.db.Rejected(case)
                with mock.patch.object(R, "workload_rows", return_value=[row()]), mock.patch.object(runner, "publish"), \
                        mock.patch.object(runner, "preflight", return_value={}), mock.patch.object(runner, "audit", return_value={"checkedAt": R.db.stamp(R.db.utcnow())}), \
                        mock.patch.object(runner, "gate"), mock.patch.object(runner, "vulnerability", side_effect=fail) as scan:
                    self.assertEqual(runner.run(), 3)
                    scan.assert_called_once()
                    self.assertTrue((Path(path) / "inflight.json").exists())
                    self.assertEqual(runner.records[R.row_key(row())]["lastAttempt"]["state"], "unknown")

    def test_incomplete_or_old_reports_rejected_before_success(self):
        report = {"metadata": {"annotations": {"kubescape.io/status": "incomplete"}}}
        with self.assertRaisesRegex(R.db.Rejected, "report_incomplete"):
            R.check_report(report, {}, row(), EXPECTED, R.db.stamp(NOW))
        report = {"metadata": {"annotations": {"kubescape.io/status": "ready"}},
                  "spec": {"metadata": {"report": {"createdAt": R.db.stamp(NOW - timedelta(hours=1))}}}}
        with self.assertRaisesRegex(R.db.Rejected, "report_not_new"):
            R.check_report(report, {}, row(), EXPECTED, R.db.stamp(NOW))

    def test_posture_needs_actual_controls_and_stored_results(self):
        for result in ({}, {"summaryDetails": {"frameworks": []}},
                       {"summaryDetails": {"frameworks": [{"controls": [{"status": "skipped"}]}]}}):
            with self.assertRaises(R.db.Rejected):
                R.check_posture(result, [], R.db.stamp(NOW))

    def test_posture_distinguishes_action_required_and_unsupported(self):
        result = {"summaryDetails": {"frameworks": [{"controls": [
            {"status": "passed"}, {"status": "failed"}, {"status": "actionRequired"}, {"status": "skipped"}]}]}}
        summary = {"metadata": {"name": "deployment-memos", "namespace": "memos",
                               "annotations": {"kubescape.io/report-created-at": R.db.stamp(NOW)}}, "spec": {"controls": {"control": {}}}}
        shared = copy.deepcopy(summary)
        shared["metadata"].update(name="shared-rbac", namespace="kubescape")
        counts = R.check_posture(result, [summary, shared], R.db.stamp(NOW))["controls"]
        self.assertEqual(counts, {"Passed": 1, "Failed": 1, "Action Required": 2})
        summary["spec"]["controls"] = {}
        with self.assertRaisesRegex(R.db.Rejected, "posture_controls_empty"):
            R.check_posture(result, [summary, shared], R.db.stamp(NOW))

    def test_pinned_scanner_controls_map_and_review_substatus(self):
        result = {"summaryDetails": {"frameworks": [{"name": "NSA", "controls": {
            "passed": {"status": "passed", "statusInfo": {"status": "passed"}},
            "manual": {"status": "skipped", "statusInfo": {"status": "skipped", "subStatus": "manual review"}},
            "host": {"status": "skipped", "statusInfo": {"status": "skipped", "subStatus": "notEvaluated"}},
            "cloud": {"status": "passed", "statusInfo": {"status": "passed", "subStatus": "irrelevant"}}
        }}]}}
        def summary(namespace, name):
            return {"metadata": {"namespace": namespace, "name": name,
                    "annotations": {"kubescape.io/report-created-at": R.db.stamp(NOW)}}, "spec": {"controls": {"x": {}}}}
        assessed = R.check_posture(result, [summary("memos", "deployment-memos"), summary("kubescape", "shared")], R.db.stamp(NOW))
        self.assertEqual(assessed["controls"], {"Passed": 2, "Action Required": 2})
        self.assertEqual(assessed["coverage"]["notEvaluatedControls"], 1)
        self.assertEqual(assessed["coverage"]["notEvaluatedControlIDs"], ["host"])
        self.assertEqual(assessed["coverage"]["incompleteCoverageControls"], 0)


    def test_native_counts_and_not_evaluated_overlay(self):
        # Synthetic 26-control shape reproducing the pinned printer's *status*
        # semantics; this is NOT a fixture exported from the production PVC.
        controls = {}
        for i in range(15):
            controls[f"C-P-{i}"] = {"status": "passed", "statusInfo": {"status": "passed"}}
        for i in range(3):
            controls[f"C-I-{i}"] = {"status": "passed", "statusInfo": {"status": "passed", "subStatus": "irrelevant"}}
        for i in range(3):
            controls[f"C-F-{i}"] = {"status": "failed", "statusInfo": {"status": "failed"}}
        for i in range(3):
            controls[f"C-R-{i}"] = {"status": "skipped", "statusInfo": {"status": "skipped", "subStatus": "manual review"}}
        for i in range(2):
            controls[f"C-N-{i}"] = {"status": "skipped", "statusInfo": {"status": "skipped", "subStatus": "notEvaluated"}}
        result = {"summaryDetails": {"frameworks": [{"name": "NSA", "controls": controls}]}}
        summary = lambda namespace, name: {"metadata": {"namespace": namespace, "name": name,
            "annotations": {"kubescape.io/report-created-at": R.db.stamp(NOW)}},
            "spec": {"controls": {"C-X": {"status": "passed"}}}}
        assessed = R.check_posture(result, [summary("memos", "deployment-memos"),
                                            summary("kubescape", "shared-rbac")], R.db.stamp(NOW))
        self.assertEqual(assessed["controls"], {"Passed": 18, "Failed": 3, "Action Required": 5})
        self.assertEqual(sum(assessed["controls"].values()), 26)
        self.assertEqual(assessed["coverage"]["notEvaluatedControls"], 2)
        self.assertEqual(assessed["coverage"]["notEvaluatedControlIDs"], ["C-N-0", "C-N-1"])

    def test_statusinfo_overrides_legacy_and_unknown_is_not_silently_safe(self):
        summary = lambda namespace, name: {"metadata": {"namespace": namespace, "name": name,
            "annotations": {"kubescape.io/report-created-at": R.db.stamp(NOW)}},
            "spec": {"controls": {"C-X": {}}}}
        summaries = [summary("memos", "deployment-memos"), summary("kubescape", "shared-rbac")]
        result = {"summaryDetails": {"frameworks": [{"controls": {
            "C-1": {"status": "passed", "statusInfo": {"status": "failed"}},
            "C-2": {"status": "passed", "statusInfo": {"status": "passed", "subStatus": "incompleteCoverage"}},
        }}]}}
        assessed = R.check_posture(result, summaries, R.db.stamp(NOW))
        self.assertEqual(assessed["controls"], {"Failed": 1, "Passed": 1})
        self.assertEqual(assessed["coverage"]["incompleteCoverageControlIDs"], ["C-2"])
        result["summaryDetails"]["frameworks"][0]["controls"]["C-1"]["statusInfo"]["status"] = "unknownNewStatus"
        with self.assertRaisesRegex(R.db.Rejected, "posture_status_unrecognised"):
            R.check_posture(result, summaries, R.db.stamp(NOW))

class SubmissionTests(unittest.TestCase):
    """Drive the actual POST/poll/report path, including ambiguous acceptance."""
    def test_full_production_path_requires_actual_result_then_deduplicates(self):
        fixture = json.loads((ROOT / "scripts/tests/fixtures/kubescape-report-identity.json").read_text())
        current = row()
        report, sbom = fixture["report"], fixture["sbom"]
        for obj in (report, sbom):
            obj["metadata"].update(name=R.client.REPORT)
            obj["metadata"]["annotations"]["kubescape.io/image-id"] = current["runningDigest"]
        api = mock.Mock()
        api.items.return_value = [report]
        summary = {"metadata": {"annotations": {
            "kubescape.io/image-id": current["runningDigest"],
            "kubescape.io/workload-container-name": "memos",
            "kubescape.io/wlid": "wlid://cluster-home/namespace-memos/deployment-memos"}},
            "spec": {"vulnerabilitiesRef": {"all": {"name": R.client.REPORT}}}}
        def get(base, resource, *args, **kwargs):
            return copy.deepcopy(summary if resource == "vulnerabilitymanifestsummaries" else sbom if resource == "sbomsyfts" else report)
        api.get.side_effect = get
        api.call.return_value = {"metadata": {"name": "kubescape-assessment-status"}, "data": {}}
        posted = []
        def service(component, path, value=None):
            if value is not None:
                posted.append(value)
                self.assertIs(value["args"]["forceRescan"], True)
                report["spec"]["metadata"]["report"]["createdAt"] = R.db.stamp(NOW)
                return {"accepted": True}
            return {"state": "succeeded", "phase": "completed", "startedAt": R.db.stamp(NOW), "finishedAt": R.db.stamp(NOW + timedelta(seconds=1))}
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db, "utcnow", return_value=NOW), \
                mock.patch.object(R, "workload_rows", return_value=[current]), mock.patch.object(R, "service", side_effect=service), \
                mock.patch.object(R, "component_pod", return_value={"metadata": {"uid": "engine-uid"}}):
            history = Path(path)
            config = {**CANARY_CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]}
            runner = R.Runner(api, config, EXPECTED, history, "vulnerability")
            with mock.patch.object(runner, "preflight", return_value={}), \
                    mock.patch.object(runner, "audit", return_value={"podUID": "engine-uid", "checkedAt": R.db.stamp(NOW)}), \
                    mock.patch.object(runner, "gate"), mock.patch.object(runner, "observe"), mock.patch("builtins.print"):
                self.assertEqual(runner.run(), 0)
            self.assertEqual(runner.result["state"], "healthy")
            self.assertEqual(len(posted), 1)
            self.assertFalse((history / "inflight.json").exists())
            stored = json.loads((history / "records.json").read_text())
            self.assertEqual(stored[R.row_key(current)]["createdAt"], R.db.stamp(NOW))
            self.assertEqual(json.loads((history / "coverage.json").read_text())["rows"][0]["assessmentState"], "healthy")
            self.assertTrue(list(runner.work.glob("previous-*.json.gz")))
            duplicate = R.Runner(api, config, EXPECTED, history, "vulnerability")
            with mock.patch("builtins.print"):
                self.assertEqual(duplicate.run(), 4)
            self.assertEqual(duplicate.result["reasons"], ["duplicate_execution"])
            self.assertEqual(len(posted), 1)

    def test_terminal_failure_does_not_resubmit_and_clears_intent(self):
        self.submission_case("failed", "scan_failed")

    def test_missing_report_cannot_pass_a_successful_api_job(self):
        self.submission_case("succeeded", "report_missing")

    def test_timeout_leaves_a_queryable_intent_without_retry(self):
        self.submission_case("running", "scan_timeout", timeout=True)

    def test_abandoned_request_keeps_fence(self):
        self.submission_case("abandoned", "scan_abandoned")

    def test_restart_after_acceptance_leaves_fence(self):
        self.submission_case("succeeded", "engine_restarted", restarted=True)

    def test_lost_post_response_queries_id_without_post_retry(self):
        self.submission_case("succeeded", "report_missing", lost=True)

    def submission_case(self, status_state, reason, timeout=False, restarted=False, lost=False):
        with tempfile.TemporaryDirectory() as path:
            api = mock.Mock()
            api.items.return_value = []  # No full report/SBOM persisted.
            runner = R.Runner(api, CANARY_CONFIG, EXPECTED, Path(path), "vulnerability")
            requests = []
            def service(component, endpoint, value=None):
                requests.append((component, endpoint, value))
                if endpoint == "/v1/scanImage":
                    if lost:
                        raise OSError("lost response")
                    return {"accepted": True}
                return {"state": status_state, "phase": "completed" if status_state == "succeeded" else "scanning"}
            audit = {"podUID": "engine-uid"}
            with mock.patch.object(runner, "preflight"), mock.patch.object(runner, "gate"), \
                    mock.patch.object(runner, "observe"), mock.patch.object(R, "service", side_effect=service), \
                    mock.patch.object(R, "component_pod", return_value={"metadata": {"uid": "other" if restarted else "engine-uid"}}), \
                    mock.patch.object(R.time, "monotonic", side_effect=[0, 0, 361] if timeout else lambda: 0):
                with self.assertRaisesRegex(R.db.Rejected, reason):
                    runner.vulnerability(row(), audit)
            intent_path = Path(path) / "inflight.json"
            self.assertEqual(intent_path.exists(), timeout or restarted or status_state == "abandoned")
            intent = json.loads(intent_path.read_text()) if intent_path.exists() else {"jobID": requests[0][2]["jobID"]}
            posts = [r for r in requests if r[2] is not None]
            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0][2]["jobID"], intent["jobID"])
            self.assertEqual(posts[0][2]["imageHash"], row()["runningDigest"])
            self.assertEqual(runner.result["assessments"], [])
            if lost:
                self.assertEqual(requests[1][1], "/v1/scanStatus/" + intent["jobID"])


class PrerequisiteTests(unittest.TestCase):
    def test_storage_timestamp_precision_preserves_new_and_rejects_cached_results(self):
        fixture = json.loads((ROOT / "scripts/tests/fixtures/kubescape-report-identity.json").read_text())
        report, sbom = fixture["report"], fixture["sbom"]
        old = copy.deepcopy(report)
        report["spec"]["metadata"]["report"]["createdAt"] = "2026-11-22T04:37:22Z"
        result = R.check_report(report, sbom, row(), EXPECTED, "2026-11-22T04:37:22.261402Z", old)
        self.assertEqual(result["createdAt"], "2026-11-22T04:37:22Z")
        with self.assertRaisesRegex(R.db.Rejected, "report_not_new"):
            R.check_report(old, sbom, row(), EXPECTED, "2026-11-22T04:37:22.261402Z", old)
        with self.assertRaisesRegex(R.db.Rejected, "report_not_new"):
            R.check_report(report, sbom, row(), EXPECTED, "2026-11-22T04:37:22.261402Z", report)
        sbom["spec"]["syft"]["source"]["metadata"]["config"] = "YWx0ZXJlZA=="
        with self.assertRaisesRegex(R.db.Rejected, "sbom_identity_invalid"):
            R.check_report(report, sbom, row(), EXPECTED, "2026-11-22T04:37:22.261402Z", old)

    def test_list_items_inherit_kind_from_kubernetes_list(self):
        api = R.API(local=True)
        with mock.patch.object(api, "get", return_value={"kind": "DeploymentList", "items": [{"metadata": {"name": "example"}}]}):
            self.assertEqual(api.items(R.APPS, "deployments")[0]["kind"], "Deployment")

    def test_actual_preflight_rejects_refresh_low_space_and_memory(self):
        for failure in ("database_refresh_in_progress", "database_pvc_low_space", "memory_budget_exhausted"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as path:
                api = mock.Mock()
                release = {"metadata": {"generation": 3}, "spec": {"values": {"grypeOfflineDB": {"image": {"sha": EXPECTED["indexDigest"]}}}},
                           "status": {"observedGeneration": 2 if failure == "database_refresh_in_progress" else 3,
                                      "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 3}]}}
                api.get.return_value = release
                api.items.return_value = [{"metadata": {"name": "engine"}, "timestamp": R.db.stamp(NOW),
                                           "containers": [{"name": "kubevuln", "usage": {"cpu": "1m", "memory": "3Gi"}}]}]
                config = {**CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]}
                runner = R.Runner(api, config, EXPECTED, Path(path), "vulnerability")
                free = 1024**3 if failure == "database_pvc_low_space" else 6 * 1024**3
                with mock.patch.object(R.db, "utcnow", return_value=NOW), \
                        mock.patch.object(R, "component_pod", return_value={"metadata": {"name": "engine", "uid": "engine-uid"}}), \
                        mock.patch.object(R.os, "statvfs", return_value=SimpleNamespace(f_bavail=free, f_frsize=1)):
                    with self.assertRaisesRegex(R.db.Rejected, failure):
                        runner.preflight()

    def test_actual_physical_disk_gate_includes_retained_allocations(self):
        with tempfile.TemporaryDirectory() as path:
            api = mock.Mock()
            api.get.return_value = {"status": {"phase": "Bound"}, "spec": {"volumeName": "volume"}}
            volume = {"metadata": {"name": "volume"}, "status": {"robustness": "healthy"}}
            replicas = [{"metadata": {"name": str(i)}, "spec": {"volumeName": "volume", "nodeID": "node", "diskID": "disk"},
                         "status": {"currentState": "running"}} for i in range(3)]
            node = {"metadata": {"name": "node"}, "status": {"diskStatus": {"disk": {
                "diskUUID": "disk", "storageAvailable": 9 * 1024**3, "storageMaximum": 100 * 1024**3}}}}
            api.items.side_effect = [ [volume], replicas, [node] ]
            runner = R.Runner(api, CANARY_CONFIG, EXPECTED, Path(path), "vulnerability")
            with self.assertRaisesRegex(R.db.Rejected, "replica_disk_low_space"):
                runner.storage_gate()


if __name__ == "__main__":
    unittest.main()
