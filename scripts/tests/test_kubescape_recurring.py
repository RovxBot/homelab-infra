"""Exercise production runner failure boundaries without disrupting applications."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("recurring", ROOT / "infra/kubescape/recurring/runner.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
CONFIG = json.loads((ROOT / "infra/kubescape/recurring/config.json").read_text())
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
        self.assertEqual(state({}), "never_scanned")
        self.assertEqual(state(record), "healthy")
        self.assertEqual(state({**record, "createdAt": R.db.stamp(NOW - timedelta(hours=27))}), "stale")
        self.assertEqual(state({**record, "lastAttempt": {"state": "failed"}}), "failed")
        self.assertEqual(state({**record, "lastAttempt": {"state": "unknown"}}), "unknown")
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
        self.assertEqual(inventory["rows"][0]["assessmentState"], "never_scanned")


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
            runner = R.Runner(mock.Mock(), CONFIG, EXPECTED, Path(path), "vulnerability")
            R.atomic(Path(path) / "inflight.json", {"jobID": "existing-request"})
            with mock.patch.object(R, "service", return_value={"state": "succeeded"}) as call:
                with self.assertRaisesRegex(R.db.Rejected, "uncertain_previous_request"):
                    runner.fence()
            call.assert_called_once_with("kubevuln", "/v1/scanStatus/existing-request")
            self.assertTrue((Path(path) / "inflight.json").exists())

    def test_fenced_posture_lost_response_cannot_resubmit(self):
        with tempfile.TemporaryDirectory() as path:
            runner = R.Runner(mock.Mock(), CONFIG, EXPECTED, Path(path), "posture")
            R.atomic(Path(path) / "inflight.json", {"mode": "posture"})
            with mock.patch.object(R, "service") as call:
                with self.assertRaisesRegex(R.db.Rejected, "uncertain_previous_request"):
                    runner.fence()
            call.assert_not_called()


class FailureTests(unittest.TestCase):
    def run_failure(self, reason, state="failed"):
        with tempfile.TemporaryDirectory() as path:
            runner = R.Runner(mock.Mock(), CONFIG, EXPECTED, Path(path), "vulnerability")
            with mock.patch.object(R, "workload_rows", return_value=[row()]), mock.patch.object(runner, "publish"), \
                    mock.patch.object(runner, "preflight", side_effect=R.db.Rejected(reason)), mock.patch.object(runner, "audit") as audit:
                self.assertNotEqual(runner.run(), 0)
                self.assertEqual(runner.result["state"], state)
                self.assertEqual(runner.result["reasons"], [reason])
                audit.assert_not_called()

    def test_stale_database(self):
        with mock.patch.object(R.db, "utcnow", return_value=NOW + timedelta(days=4)):
            with self.assertRaisesRegex(R.db.Rejected, "stale_database"):
                R.db.metadata(EXPECTED["metadata"])
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
            runner = R.Runner(mock.Mock(), CONFIG, EXPECTED, Path(path), "vulnerability")
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
                runner = R.Runner(mock.Mock(), CONFIG, EXPECTED, Path(path), "vulnerability")
                def fail(*_):
                    R.atomic(Path(path) / "inflight.json", {"jobID": "submitted"})
                    raise R.db.Rejected(case)
                with mock.patch.object(R, "workload_rows", return_value=[row()]), mock.patch.object(runner, "publish"), \
                        mock.patch.object(runner, "preflight", return_value={}), mock.patch.object(runner, "audit"), \
                        mock.patch.object(runner, "gate"), mock.patch.object(runner, "vulnerability", side_effect=fail) as scan:
                    self.assertEqual(runner.run(), 4)
                    scan.assert_called_once()
                    self.assertTrue((Path(path) / "inflight.json").exists())
                    self.assertEqual(runner.records[R.row_key(row())]["lastAttempt"]["state"], "failed")

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
        self.assertEqual(counts, {"Passed": 1, "Failed": 1, "Action Required": 1, "Not Evaluated": 1})
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
        self.assertEqual(assessed["controls"], {"Passed": 1, "Action Required": 1, "Not Evaluated": 2})


if __name__ == "__main__":
    unittest.main()
