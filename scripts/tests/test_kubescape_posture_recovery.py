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


TSPEC = importlib.util.spec_from_file_location('terminal_posture_recovery', ROOT / 'ops/kubescape/recover-terminal-posture.py')
T = importlib.util.module_from_spec(TSPEC)
TSPEC.loader.exec_module(T)


class TerminalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        (self.root / 'assessment.lock').touch()
        self.intent = {'mode': 'posture', 'scanID': T.SCAN_ID, 'scannerPodUID': R.CURRENT_UID,
                       'submittedAt': '2026-11-27T05:14:25.123456Z'}
        self.raw = json.dumps(self.intent).encode()
        (self.root / 'inflight.json').write_bytes(self.raw)
        self.failure = {'mode': 'posture', 'runID': 'posture-20261127051420-abcdef', 'state': 'failed',
                        'reasons': ['posture_report_stale'], 'assessments': [],
                        'startedAt': '2026-11-27T05:14:20Z', 'finishedAt': '2026-11-27T05:17:31Z'}
        failed_raw = json.dumps(self.failure).encode()
        (self.root / 'latest-posture.json').write_bytes(failed_raw)
        self.work = self.root / self.failure['runID']
        self.work.mkdir()
        import gzip
        (self.work / 'result.json.gz').write_bytes(gzip.compress(failed_raw))
        self.response = {'id': T.SCAN_ID, 'type': 'v1results', 'response': {'summaryDetails': {'retained': True}}}
        terminal_raw = gzip.compress(json.dumps(self.response).encode())
        (self.work / 'scanner-result.json.gz').write_bytes(terminal_raw)
        (self.root / 'posture-existing.done').write_bytes(b'original marker')
        (self.root / 'records.json').write_bytes(b'{"historical":true}')
        self.receipts = {'privateExportSHA256': 'b' * 64, 'intentSHA256': T.sha(self.raw),
                         'terminalSHA256': T.sha(terminal_raw), 'failureSHA256': T.sha(failed_raw)}
        self.verify = mock.Mock(return_value=self.response)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def recover(self, apply=True):
        return T.recover(self.root, self.verify, self.receipts, apply=apply)

    def test_preview_is_read_only_and_acknowledge_preserves_failed_history_and_markers(self):
        before = self.snapshot()
        self.assertEqual(self.recover(False)['action'], 'preview')
        self.assertEqual(before, self.snapshot())
        receipt = self.recover()
        self.assertEqual(receipt['scanOutcome'], 'completed')
        self.assertEqual(receipt['qualificationState'], 'failed')
        self.assertEqual(receipt['reason'], 'posture_report_stale')
        after = self.snapshot()
        self.assertEqual(after.pop(receipt['preservedFence']), before.pop('inflight.json'))
        saved = json.loads(after.pop(receipt['preservedFence'].replace('.json', '.receipt.json')))
        self.assertEqual(saved, receipt)
        self.assertEqual(after, before)
        self.assertEqual(self.verify.call_count, 4)

    def test_each_missing_or_wrong_receipt_fails_without_mutation(self):
        before = self.snapshot()
        for key in self.receipts:
            receipts = dict(self.receipts)
            for wrong in ('REQUIRED', 'a' * 64):
                if key == 'privateExportSHA256' and wrong != 'REQUIRED':
                    continue  # Export was verified privately; only its receipt is mounted.
                receipts[key] = wrong
                with self.subTest(key=key, wrong=wrong), self.assertRaises(T.Rejected):
                    T.recover(self.root, self.verify, receipts, apply=True)
                self.assertEqual(self.snapshot(), before)

    def test_other_request_wrong_terminal_and_changed_live_evidence_remain_fenced(self):
        before = self.snapshot()
        for response in ({'type': 'notBusy'}, {'id': 'other', 'type': 'v1results'},
                         {'id': T.SCAN_ID, 'type': 'busy'}):
            self.verify.return_value = response
            with self.assertRaisesRegex(T.Rejected, 'terminal_reread_mismatch'):
                self.recover()
            self.assertEqual(self.snapshot(), before)
        self.verify.side_effect = T.Rejected('original_scanner_unavailable')
        with self.assertRaises(T.Rejected):
            self.recover()
        self.assertEqual(self.snapshot(), before)

    def test_wrong_failed_run_and_missing_terminal_keep_fence(self):
        (self.root / 'latest-posture.json').write_text(json.dumps({**self.failure, 'state': 'unknown'}))
        self.receipts['failureSHA256'] = T.sha((self.root / 'latest-posture.json').read_bytes())
        with self.assertRaisesRegex(T.Rejected, 'failure_mismatch'):
            self.recover()
        (self.root / 'latest-posture.json').write_text(json.dumps(self.failure))
        self.receipts['failureSHA256'] = T.sha((self.root / 'latest-posture.json').read_bytes())
        (self.work / 'scanner-result.json.gz').unlink()
        with self.assertRaises(FileNotFoundError):
            self.recover()
        self.assertTrue((self.root / 'inflight.json').exists())

    def test_lock_contention_symlink_and_fsync_failure_keep_fence(self):
        before = self.snapshot()
        with (self.root / 'assessment.lock').open('r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.recover()
        self.assertEqual(self.snapshot(), before)
        terminal = self.work / 'scanner-result.json.gz'
        terminal.rename(self.work / 'saved.gz')
        terminal.symlink_to(self.work / 'saved.gz')
        with self.assertRaisesRegex(T.Rejected, 'symlink_evidence'):
            self.recover()
        terminal.unlink()
        (self.work / 'saved.gz').rename(terminal)
        with mock.patch.object(T, 'fsync_directory', side_effect=OSError('disk')), self.assertRaises(OSError):
            self.recover()
        self.assertEqual((self.root / 'inflight.json').read_bytes(), self.raw)
        self.assertEqual(next(p for p in self.root.glob('recovered-posture-*.json') if not p.name.endswith('.receipt.json')).read_bytes(), self.raw)

    def test_export_roundtrip_contains_exact_receipts_without_extraction(self):
        exporter = T.export_module()
        exporter.EXPECTED = self.intent
        output = io.BytesIO()
        exporter.export(self.root, output)
        archive = self.root.parent / (self.root.name + '.tar.gz')
        self.addCleanup(archive.unlink, missing_ok=True)
        archive.write_bytes(output.getvalue())
        receipt = exporter.verify(archive)
        self.assertEqual(receipt['exportedInflightSHA256'], self.receipts['intentSHA256'])
        self.assertEqual(receipt['completionMarkers'], 1)


class TerminalClusterGatesTests(unittest.TestCase):
    def setUp(self):
        self.intent = {'scannerPodUID': R.CURRENT_UID}
        self.scanner = {'metadata': {'uid': R.CURRENT_UID, 'labels': {'app.kubernetes.io/component': 'kubescape'}},
                        'spec': {'containers': [{'name': 'scanner', 'image': T.SCANNER}]},
                        'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                                   'containerStatuses': [{'name': 'scanner', 'ready': True, 'restartCount': 0,
                                      'state': {'running': {}}, 'imageID': T.SCANNER}]}}
        self.own = {'metadata': {'uid': 'own-pod', 'ownerReferences': [{'kind': 'Job', 'uid': 'own-job'}]}}
        self.values = {
            'cronjobs': [{'metadata': {'name': 'kubescape-assessment-' + m}, 'spec': {'suspend': True}}
                         for m in ('posture', 'vulnerability', 'refresh')],
            'jobs': [{'metadata': {'name': T.JOB, 'uid': 'own-job'}}], 'pods': [self.scanner, self.own]}
        self.api = mock.Mock()
        self.api.items.side_effect = lambda b, r, ns: self.values[r]
        self.response = {'id': T.SCAN_ID, 'type': 'v1results', 'response': {}}
        self.service = lambda c, p: {'queueDepth': 0} if c == 'kubevuln' else self.response if p.startswith('/v1/results') else {'type': 'notBusy'}

    def verify(self):
        return T.verify_cluster(self.api, self.service, self.intent, 'own-pod')

    def test_original_ready_scanner_and_named_terminal_result_required(self):
        self.assertEqual(self.verify(), self.response)
        self.scanner['status']['containerStatuses'][0]['restartCount'] = 1
        with self.assertRaisesRegex(T.Rejected, 'scanner_process_changed'):
            self.verify()
        self.scanner['status']['containerStatuses'][0]['restartCount'] = 0
        self.scanner['metadata']['uid'] = 'replacement'
        with self.assertRaisesRegex(T.Rejected, 'original_scanner_unavailable'):
            self.verify()

    def test_active_schedules_jobs_and_busy_scanner_refuse_recovery(self):
        self.values['cronjobs'][0]['spec']['suspend'] = False
        with self.assertRaisesRegex(T.Rejected, 'schedules_not_quiescent'):
            self.verify()
        self.values['cronjobs'][0]['spec']['suspend'] = True
        self.values['jobs'].append({'metadata': {'name': 'other', 'uid': 'other-job'}, 'status': {}})
        with self.assertRaisesRegex(T.Rejected, 'conflicting_job'):
            self.verify()
        self.values['jobs'].pop()
        self.service = lambda *args: {'type': 'busy'}
        with self.assertRaisesRegex(T.Rejected, 'scanner_busy_or_unknown'):
            self.verify()


class TerminalFixtureTests(unittest.TestCase):
    def test_manual_render_mounts_exact_source_and_keeps_restricted_contract(self):
        spec = importlib.util.spec_from_file_location('terminal_render', ROOT / 'ops/kubescape/terminal-recovery/render.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        code, job = module.render()
        self.assertEqual(code['data']['runner.py'], (ROOT / 'infra/kubescape/recurring/runner.py').read_text())
        self.assertEqual(code['data']['recover-terminal-posture.py'], (ROOT / 'ops/kubescape/recover-terminal-posture.py').read_text())
        self.assertTrue(code['immutable'])
        self.assertEqual(job['metadata']['name'], T.JOB)
        self.assertEqual(job['spec']['activeDeadlineSeconds'], 600)
        self.assertEqual(job['spec']['backoffLimit'], 0)
        pod = job['spec']['template']['spec']
        self.assertEqual(next(v for v in pod['volumes'] if v['name'] == 'code')['configMap']['name'], code['metadata']['name'])
        self.assertNotIn('terminal-recovery', (ROOT / 'infra/kubescape/kustomization.yaml').read_text())
        import sys
        sys.path.insert(0, str(ROOT / 'scripts/ci'))
        import check_kubescape as chart
        probe = {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': 'kubescape'},
                 'spec': {'template': job['spec']['template']}}
        self.assertEqual(chart.findings([probe]), [])
        self.assertEqual(pod['containers'][0]['image'], POD['containers'][0]['image'])


if __name__ == "__main__":
    unittest.main()
