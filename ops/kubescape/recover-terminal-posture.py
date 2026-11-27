#!/usr/bin/env python3
"""Guarded acknowledgement of the exact completed 27 November posture request.

No POST, requalification, status renewal, marker removal or report mutation.
Run only in the reviewed maintenance Job with the original private history.
"""
import argparse
from datetime import datetime
import fcntl
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

SCAN_ID = '0a454406-e77a-4570-86a1-b692cae5c84a'
SCANNER = 'quay.io/kubescape/kubescape:v4.0.20@sha256:439bab2d738f8bd27c742b15444cde77e607ce7d769e805ee1bc25605b355060'
JOB = 'assessment-recover-terminal-posture-0a454406'
NS = '/api/v1'
BATCH = '/apis/batch/v1'


class Rejected(Exception):
    pass


def require(ok, reason):
    if not ok:
        raise Rejected(reason)


def read_regular(path, limit=64 * 1024**2):
    # Refuse symlinks in every component, including the run directory.
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink_evidence')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, 'invalid_evidence_file')
        return stream.read(limit + 1)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unzip(raw):
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        value = stream.read(64 * 1024**2 + 1)
    require(len(value) <= 64 * 1024**2, 'evidence_too_large')
    return value


def read_intent(root):
    raw = read_regular(root / 'inflight.json', 65536)
    intent = json.loads(raw)
    require(set(intent) == {'mode', 'scanID', 'scannerPodUID', 'submittedAt'}
            and intent['mode'] == 'posture' and intent['scanID'] == SCAN_ID
            and str(uuid.UUID(intent['scannerPodUID'])) == intent['scannerPodUID'], 'intent_mismatch')
    require(datetime.fromisoformat(intent['submittedAt'].replace('Z', '+00:00')).utcoffset() is not None,
            'intent_time_invalid')
    return raw, intent


def terminal_job(job):
    return any(c.get('type') in ('Complete', 'Failed') and c.get('status') == 'True'
               for c in job.get('status', {}).get('conditions', []))


def verify_cluster(api, service, intent, own_uid):
    schedules = {j['metadata']['name']: j for j in api.items(BATCH, 'cronjobs', 'kubescape')}
    required = {'kubescape-assessment-' + m for m in ('posture', 'vulnerability', 'refresh')}
    require(required <= schedules.keys(), 'schedules_missing')
    require(all(j['spec'].get('suspend') is True and not j.get('status', {}).get('active')
                for j in schedules.values()), 'schedules_not_quiescent')
    jobs = api.items(BATCH, 'jobs', 'kubescape')
    own = [j for j in jobs if j['metadata']['name'] == JOB]
    require(len(own) == 1 and not own[0]['metadata'].get('deletionTimestamp'), 'maintenance_identity')
    own_job_uid = own[0]['metadata']['uid']
    require(all(j['metadata']['uid'] == own_job_uid or terminal_job(j) for j in jobs), 'conflicting_job')
    pods = api.items(NS, 'pods', 'kubescape')
    own_pods = [p for p in pods if p['metadata']['uid'] == own_uid]
    require(len(own_pods) == 1 and any(o.get('kind') == 'Job' and o.get('uid') == own_job_uid
            for o in own_pods[0]['metadata'].get('ownerReferences', [])), 'maintenance_pod_identity')
    require(all(p['metadata']['uid'] == own_uid or p.get('status', {}).get('phase') in ('Succeeded', 'Failed')
                or not any(o.get('kind') == 'Job' for o in p['metadata'].get('ownerReferences', []))
                for p in pods), 'conflicting_job_pod')
    scanners = [p for p in pods if p['metadata'].get('labels', {}).get('app.kubernetes.io/component') == 'kubescape']
    require(len(scanners) == 1, 'scanner_ambiguous')
    scanner = scanners[0]
    require(scanner['metadata']['uid'] == intent['scannerPodUID']
            and not scanner['metadata'].get('deletionTimestamp')
            and scanner.get('status', {}).get('phase') == 'Running'
            and any(c.get('type') == 'Ready' and c.get('status') == 'True'
                    for c in scanner.get('status', {}).get('conditions', [])), 'original_scanner_unavailable')
    containers = [c for c in scanner['spec']['containers'] if c['image'] == SCANNER]
    require(len(containers) == 1, 'scanner_pin')
    statuses = [c for c in scanner['status']['containerStatuses'] if c['name'] == containers[0]['name']]
    require(len(statuses) == 1 and statuses[0].get('ready') is True
            and statuses[0].get('restartCount') == 0
            and statuses[0].get('state', {}).get('running') is not None
            and statuses[0]['imageID'].split('@')[-1] == SCANNER.split('@')[-1], 'scanner_process_changed')
    require(service('kubescape', '/v1/status').get('type') == 'notBusy', 'scanner_busy_or_unknown')
    require(service('kubescape', '/v1/status?id=' + SCAN_ID).get('type') == 'notBusy', 'request_busy_or_unknown')
    require(service('kubevuln', '/v1/diagnostics').get('queueDepth') == 0, 'engine_queue_not_empty')
    response = service('kubescape', '/v1/results?id=' + SCAN_ID + '&keep=true')
    require(response.get('id') == SCAN_ID and response.get('type') == 'v1results', 'terminal_unverified')
    return response


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def recover(root, verify, receipts, apply=False):
    require(set(receipts) == {'privateExportSHA256', 'intentSHA256', 'terminalSHA256', 'failureSHA256'}
            and all(re.fullmatch('[a-f0-9]{64}', v) for v in receipts.values()), 'private_receipts_required')
    fd = os.open(root / 'assessment.lock', os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r+') as lock:
        require(stat.S_ISREG(os.fstat(lock.fileno()).st_mode), 'lock_not_regular')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw, intent = read_intent(root)
        require(sha(raw) == receipts['intentSHA256'], 'intent_hash_mismatch')
        failed_raw = read_regular(root / 'latest-posture.json')
        require(sha(failed_raw) == receipts['failureSHA256'], 'failure_hash_mismatch')
        failed = json.loads(failed_raw)
        require(failed.get('mode') == 'posture' and failed.get('state') == 'failed'
                and failed.get('reasons') == ['posture_report_stale'] and failed.get('assessments') == []
                and re.fullmatch(r'posture-[0-9]{14}-[a-f0-9]{6}', failed.get('runID', '')), 'failure_mismatch')
        require(datetime.fromisoformat(failed['startedAt'].replace('Z', '+00:00')) <=
                datetime.fromisoformat(intent['submittedAt'].replace('Z', '+00:00')) <=
                datetime.fromisoformat(failed['finishedAt'].replace('Z', '+00:00')), 'failure_time_mismatch')
        run = root / failed['runID']
        # The per-run result must agree too: a different latest failure is not proof.
        retained_failure = unzip(read_regular(run / 'result.json.gz'))
        require(json.loads(retained_failure) == failed, 'retained_failure_mismatch')
        terminal_raw = read_regular(run / 'scanner-result.json.gz')
        require(sha(terminal_raw) == receipts['terminalSHA256'], 'terminal_hash_mismatch')
        response = json.loads(unzip(terminal_raw))
        require(response.get('id') == SCAN_ID and response.get('type') == 'v1results'
                and isinstance(response.get('response'), (dict, str)), 'terminal_unverified')
        # Two independent live reads, on the original unchanged scanner process.
        require(verify(intent) == response and verify(intent) == response, 'terminal_reread_mismatch')
        require(read_intent(root)[0] == raw and read_regular(root / 'latest-posture.json') == failed_raw
                and read_regular(run / 'scanner-result.json.gz') == terminal_raw, 'evidence_changed')
        receipt = {'schemaVersion': 1, 'scanID': SCAN_ID, 'intent': intent,
                   'scanOutcome': 'completed', 'qualificationState': 'failed', 'reason': 'posture_report_stale',
                   'action': 'terminal_request_acknowledged' if apply else 'preview',
                   'originalRunID': failed['runID'], **receipts}
        if not apply:
            return receipt
        # Exclusive creation + directory fsync before unlinking preserves the
        # intent, terminal result, original failed run and every completion marker.
        archive = root / ('recovered-posture-' + SCAN_ID + '-' + uuid.uuid4().hex + '.json')
        os.link(root / 'inflight.json', archive, follow_symlinks=False)
        receipt['preservedFence'] = archive.name
        with archive.with_suffix('.receipt.json').open('x') as out:
            json.dump(receipt, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        fsync_directory(root)
        (root / 'inflight.json').unlink()
        fsync_directory(root)
        return receipt


def export_module():
    spec = importlib.util.spec_from_file_location('private_export', Path(__file__).with_name('export-posture-6b2a9db3.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('export', 'verify-export', 'preview', 'acknowledge'))
    p.add_argument('--history', type=Path, default=Path('/history'))
    p.add_argument('--archive', type=Path)
    p.add_argument('--receipts', type=Path)
    args = p.parse_args()
    if args.action == 'export':
        _, intent = read_intent(args.history)
        exporter = export_module()
        exporter.EXPECTED = intent
        exporter.export(args.history, sys.stdout.buffer)
        return
    if args.action == 'verify-export':
        # Export verifier checks the entire gzip stream, member bounds and types.
        # Obtain only the exact fence from the private tar, without extraction.
        import tarfile
        with tarfile.open(args.archive, 'r:gz') as archive:
            raw = archive.extractfile('assessments/inflight.json').read(65537)
        intent = json.loads(raw)
        require(intent['mode'] == 'posture' and intent['scanID'] == SCAN_ID, 'export_intent_mismatch')
        exporter = export_module()
        exporter.EXPECTED = intent
        checked = exporter.verify(args.archive)
        with tarfile.open(args.archive, 'r:gz') as archive:
            failure_raw = archive.extractfile('assessments/latest-posture.json').read(64 * 1024**2 + 1)
            failure = json.loads(failure_raw)
            require(re.fullmatch(r'posture-[0-9]{14}-[a-f0-9]{6}', failure.get('runID', '')), 'failure_mismatch')
            terminal_raw = archive.extractfile('assessments/' + failure['runID'] + '/scanner-result.json.gz').read(64 * 1024**2 + 1)
        require(len(failure_raw) <= 64 * 1024**2 and len(terminal_raw) <= 64 * 1024**2, 'evidence_too_large')
        print(json.dumps({'privateExportSHA256': checked['privateExportSHA256'],
                          'intentSHA256': checked['exportedInflightSHA256'],
                          'terminalSHA256': sha(terminal_raw), 'failureSHA256': sha(failure_raw)}, sort_keys=True))
        return
    sys.path.insert(0, '/code')
    bundle = json.loads(Path('/code/client-bundle.json').read_text())
    for name, source in bundle.items():
        Path('/tmp', name).write_text(source)
    sys.path.insert(0, '/tmp')
    import runner
    api = runner.API()
    verify = lambda intent: verify_cluster(api, runner.service, intent, os.environ['POD_UID'])
    print(json.dumps(recover(args.history, verify, json.loads(args.receipts.read_text()),
                             apply=args.action == 'acknowledge'), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'state': 'failed', 'reason': str(error) if isinstance(error, Rejected)
                          else type(error).__name__}), file=sys.stderr)
        sys.exit(1)
