"""Fault-test the exact manual recovery fixture using temporary history only."""
import copy
import fcntl
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
JOB = yaml.safe_load((ROOT / "ops/kubescape/acknowledge-posture-6b2a9db3.yaml").read_text())
POD = JOB["spec"]["template"]["spec"]
CODE = POD["containers"][0]["command"][-1]
GLOBALS = {"__name__": "recovery_fixture"}
exec(compile(CODE, "acknowledge-posture-6b2a9db3.yaml", "exec"), GLOBALS)
R = SimpleNamespace(**GLOBALS)
SPEC = importlib.util.spec_from_file_location("private_history_export", ROOT / "ops/kubescape/export-posture-6b2a9db3.py")
E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        (self.root / "assessment.lock").touch()
        self.raw = json.dumps(R.EXPECTED).encode()
        (self.root / "inflight.json").write_bytes(self.raw)
        (self.root / "records.json").write_bytes(b'{"historical":true}')
        (self.root / "posture-2026-11-26-cohort.done").touch()
        (self.root / "previous").mkdir()
        (self.root / "previous/report.json.gz").write_bytes(b"retained evidence")
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.verify = mock.Mock()

    def acknowledge(self, **kwargs):
        return R.acknowledge(self.root, self.verify, kwargs.get("export_sha", "b" * 64),
                             kwargs.get("intent_sha", self.digest))

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def test_success_only_archives_intent_and_preserves_unknown(self):
        before = self.snapshot()
        with mock.patch.dict(GLOBALS, fsync_directory=mock.Mock(wraps=R.fsync_directory)):
            result = self.acknowledge()
            self.assertEqual(GLOBALS["fsync_directory"].call_count, 2)
        self.verify.assert_called_once()
        self.assertEqual(result["scanOutcome"], "unknown")
        self.assertEqual(result["action"], "abandoned_process_acknowledged")
        after = self.snapshot()
        self.assertEqual(after.pop(result["preservedFence"]), before.pop("inflight.json"))
        self.assertEqual(after, before)
        with self.assertRaises(FileNotFoundError):
            self.acknowledge()

    def test_wrong_intent_and_receipts_leave_history_intact(self):
        for key in R.EXPECTED:
            with self.subTest(key=key):
                value = {**R.EXPECTED, key: "different-request"}
                raw = json.dumps(value).encode()
                (self.root / "inflight.json").write_bytes(raw)
                before = self.snapshot()
                with self.assertRaises(R.Rejected):
                    self.acknowledge(intent_sha=hashlib.sha256(raw).hexdigest())
                self.assertEqual(self.snapshot(), before)
        (self.root / "inflight.json").write_bytes(self.raw)
        for kwargs in ({"export_sha": "REQUIRED"}, {"intent_sha": "REQUIRED"},
                       {"intent_sha": "a" * 64}):
            before = self.snapshot()
            with self.subTest(kwargs=kwargs), self.assertRaises(R.Rejected):
                self.acknowledge(**kwargs)
            self.assertEqual(self.snapshot(), before)
        self.verify.assert_not_called()

    def test_lock_contention_or_missing_existing_lock_fails_closed(self):
        with (self.root / "assessment.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.acknowledge()
        (self.root / "assessment.lock").unlink()
        with self.assertRaises(FileNotFoundError):
            self.acknowledge()
        self.assertFalse((self.root / "assessment.lock").exists())
        self.assertTrue((self.root / "inflight.json").exists())

    def test_symlink_fence_or_lock_rejected(self):
        for name in ("inflight.json", "assessment.lock"):
            with self.subTest(name=name):
                original = self.root / name
                target = self.root / (name + ".saved")
                original.rename(target)
                original.symlink_to(target)
                try:
                    with self.assertRaises(OSError):
                        self.acknowledge()
                finally:
                    original.unlink()
                    target.rename(original)

    def test_cluster_failure_or_intent_change_keeps_fence(self):
        before = self.snapshot()
        self.verify.side_effect = R.Rejected("scanner_busy_or_unknown")
        with self.assertRaises(R.Rejected):
            self.acknowledge()
        self.assertEqual(self.snapshot(), before)
        self.verify.side_effect = lambda: (self.root / "inflight.json").write_bytes(b"changed")
        with self.assertRaisesRegex(R.Rejected, "intent_changed"):
            self.acknowledge()
        self.assertFalse(list(self.root.glob("recovered-*")))

    def test_archive_collision_never_overwrites(self):
        archive = self.root / ("recovered-posture-" + R.EXPECTED["scanID"] + "-fixed.json")
        archive.write_bytes(b"existing recovery evidence")
        before = self.snapshot()
        with mock.patch.object(R.uuid, "uuid4", return_value=SimpleNamespace(hex="fixed")):
            with self.assertRaises(FileExistsError):
                self.acknowledge()
        self.assertEqual(self.snapshot(), before)

    def test_first_directory_fsync_failure_keeps_both_names(self):
        with mock.patch.dict(GLOBALS, fsync_directory=mock.Mock(side_effect=OSError("disk"))):
            with self.assertRaises(OSError):
                self.acknowledge()
        self.assertEqual((self.root / "inflight.json").read_bytes(), self.raw)
        self.assertEqual(next(self.root.glob("recovered-*")).read_bytes(), self.raw)

    def test_final_directory_fsync_failure_retains_archive_without_success(self):
        with mock.patch.dict(GLOBALS, fsync_directory=mock.Mock(side_effect=[None, OSError("disk")])):
            with self.assertRaises(OSError):
                self.acknowledge()
        self.assertEqual(next(self.root.glob("recovered-*")).read_bytes(), self.raw)

    def test_private_export_roundtrip_and_corruption(self):
        self.assertEqual(E.EXPECTED, R.EXPECTED)
        before = self.snapshot()
        output = io.BytesIO()
        E.export(self.root, output)
        self.assertEqual(self.snapshot(), before)
        # Archive outside the source history, as in the operator procedure.
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "private.tar.gz"
            archive.write_bytes(output.getvalue())
            receipt = E.verify(archive)
            self.assertEqual(receipt["exportedInflightSHA256"], self.digest)
            self.assertEqual(receipt["completionMarkers"], 1)
            self.assertEqual(receipt["historyBytes"], sum(map(len, before.values())))
            archive.write_bytes(output.getvalue()[:-8])
            with self.assertRaises((EOFError, OSError)):
                E.verify(archive)

    def test_export_rejects_symlinks_lock_contention_and_over_budget(self):
        (self.root / "unexpected-link").symlink_to("records.json")
        with self.assertRaises(ValueError):
            E.export(self.root, io.BytesIO())
        (self.root / "unexpected-link").unlink()
        with (self.root / "assessment.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                E.export(self.root, io.BytesIO())
        with mock.patch.object(E, "LIMIT", 1), self.assertRaises(ValueError):
            E.export(self.root, io.BytesIO())


class ClusterGatesTests(unittest.TestCase):
    def setUp(self):
        image = "v4.0.20@sha256:" + "a" * 64
        self.scanner = {"metadata": {"uid": R.CURRENT_UID, "labels": {
            "app.kubernetes.io/component": "kubescape"}},
            "spec": {"containers": [{"name": "scanner", "image": "quay.io/kubescape/kubescape:" + image}]},
            "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                       "containerStatuses": [{"name": "scanner", "ready": True,
                       "state": {"running": {}}, "imageID": "quay.io/kubescape/kubescape@sha256:" + "a" * 64}]}}
        self.own = {"metadata": {"uid": "own-pod", "ownerReferences": [{"kind": "Job", "uid": "own-job"}]}}
        self.values = {
            R.BATCH + "cronjobs": {"items": [{"metadata": {"name": n}, "spec": {"suspend": True}}
                                             for n in sorted(R.SCHEDULES | {"kubescape-scheduler"})]},
            R.BATCH + "jobs": {"items": [{"metadata": {"name": R.JOB, "uid": "own-job"}}]},
            R.NS + "pods": {"items": [self.scanner, self.own]},
            "/apis/helm.toolkit.fluxcd.io/v2/namespaces/kubescape/helmreleases/kubescape": {
                "spec": {"values": {"kubescape": {"image": {"repository": "quay.io/kubescape/kubescape", "tag": image}}}}},
        }
        self.services = {("kubescape", "/v1/status"): {"type": "notBusy"},
                         ("kubescape", "/v1/status?id=" + R.EXPECTED["scanID"]): {"type": "notBusy"},
                         ("kubevuln", "/v1/diagnostics"): {"queueDepth": 0}}

    def verify(self):
        return R.verify_cluster(self.values.__getitem__, lambda c, p: self.services[c, p], "own-pod")

    def test_ready_replacement_is_not_success_proof(self):
        self.assertEqual(self.verify(), R.CURRENT_UID)

    def test_each_schedule_must_be_suspended_and_inactive(self):
        for job in self.values[R.BATCH + "cronjobs"]["items"]:
            for field, value in (("spec", {"suspend": False}), ("status", {"active": [{}]})):
                before = copy.deepcopy(job)
                job[field] = value
                with self.subTest(schedule=job["metadata"]["name"], field=field), self.assertRaises(R.Rejected):
                    self.verify()
                job.clear()
                job.update(before)

    def test_nonterminal_jobs_and_lingering_job_pods_rejected(self):
        jobs = self.values[R.BATCH + "jobs"]["items"]
        other = {"metadata": {"name": "other", "uid": "other-job"}, "status": {"active": 0}}
        jobs.append(other)
        with self.assertRaisesRegex(R.Rejected, "conflicting_job"):
            self.verify()
        other["status"] = {"conditions": [{"type": "Complete", "status": "True"}]}
        self.verify()
        self.values[R.NS + "pods"]["items"].append({"metadata": {"uid": "other-pod", "ownerReferences": [
            {"kind": "Job", "uid": "other-job"}]}, "status": {"phase": "Running"}})
        with self.assertRaisesRegex(R.Rejected, "conflicting_job_pod"):
            self.verify()

    def test_old_scanner_even_terminating_and_wrong_replacement_rejected(self):
        pods = self.values[R.NS + "pods"]["items"]
        pods.append({"metadata": {"uid": R.EXPECTED["scannerPodUID"], "deletionTimestamp": "now"}})
        with self.assertRaisesRegex(R.Rejected, "old_scanner_present"):
            self.verify()
        pods.pop()
        for change in ({"metadata": {**self.scanner["metadata"], "uid": "another-replacement"}},
                       {"status": {**self.scanner["status"], "phase": "Pending"}},
                       {"status": {**self.scanner["status"], "conditions": []}}):
            original = copy.deepcopy(self.scanner)
            self.scanner.update(change)
            with self.assertRaises(R.Rejected):
                self.verify()
            self.scanner.clear()
            self.scanner.update(original)

    def test_busy_unknown_queue_and_incomplete_api_list_rejected(self):
        for key in self.services:
            original = self.services[key]
            for value in ({}, {"type": "busy", "queueDepth": 1}):
                self.services[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(R.Rejected):
                    self.verify()
            self.services[key] = original
        self.values[R.NS + "pods"]["metadata"] = {"continue": "another-page"}
        with self.assertRaisesRegex(R.Rejected, "list_incomplete"):
            self.verify()

    def test_image_mismatch_rejected(self):
        self.scanner["status"]["containerStatuses"][0]["imageID"] = "sha256:" + "b" * 64
        with self.assertRaisesRegex(R.Rejected, "scanner_runtime_image"):
            self.verify()


class FixtureBoundariesTests(unittest.TestCase):
    def test_offline_jobs_keep_existing_restricted_contract_and_image(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts/ci"))
        import check_kubescape as chart
        reference = yaml.safe_load((ROOT / "ops/kubescape/acknowledge-task25-first-request.yaml").read_text())
        export = yaml.safe_load((ROOT / "ops/kubescape/export-posture-6b2a9db3.yaml").read_text())
        source = (ROOT / "infra/kubescape/kustomization.yaml").read_text()
        for job, deadline in ((JOB, 180), (export, 600)):
            with self.subTest(job=job["metadata"]["name"]):
                self.assertNotIn(job["metadata"]["name"], source)
                self.assertEqual(job["spec"]["backoffLimit"], 0)
                self.assertEqual(job["spec"]["activeDeadlineSeconds"], deadline)
                pod = job["spec"]["template"]["spec"]
                self.assertEqual(pod["containers"][0]["image"],
                                 reference["spec"]["template"]["spec"]["containers"][0]["image"])
                self.assertEqual(pod["restartPolicy"], "Never")
                self.assertEqual(pod["containers"][0]["volumeMounts"][0]["subPath"], "assessments")
                # The chart allowlist intentionally excludes manual fixtures;
                # apply its full static Pod checks without widening that list.
                probe = {"apiVersion": "apps/v1", "kind": "Deployment",
                         "metadata": {"name": "kubescape"},
                         "spec": {"template": job["spec"]["template"]}}
                self.assertEqual(chart.findings([probe]), [])
        pod = export["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertTrue(pod["volumes"][0]["persistentVolumeClaim"]["readOnly"])
        self.assertTrue(pod["containers"][0]["volumeMounts"][0]["readOnly"])
        self.assertEqual({v["name"] for v in pod["volumes"]}, {"history", "scratch"})


if __name__ == "__main__":
    unittest.main()
