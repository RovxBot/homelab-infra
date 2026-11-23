"""Read-only recurring history + independent storage/inventory qualification.

Never submits scans, acquires the runner lock, clears a fence or writes history.
The repository-reviewed DB lock is a trust root, not an event-supplied assertion.
"""
from collections import Counter
from datetime import timedelta
import gzip
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from urllib.parse import parse_qs, urlsplit

from .adapters import AssessmentContext, VulnerabilityAdapter, age_ok, decode
from .model import Rejected, fingerprint, require, timestamp
from .sources import InventoryReader, STORAGE, resource

RUN = re.compile(r'vulnerability-[0-9]{14}-[a-f0-9]{6}')
JOB = re.compile(r'recurring-[a-f0-9]{32}')


def healthy(state):
    if state != 'healthy':
        require(False, {'failed': 'assessment_failed', 'stale': 'assessment_stale',
                        'unsupported': 'assessment_unsupported',
                        'deliberately_excluded': 'assessment_excluded'}.get(state, 'assessment_incomplete'))


class History:
    def __init__(self, root):
        self.root = Path(root)

    def read(self, relative, *, optional=False):
        parts = relative.split('/')
        require(1 <= len(parts) <= 2 and all(re.fullmatch(r'[a-zA-Z0-9.-]+', p)
                and p not in ('.', '..') for p in parts), 'history_path_invalid')
        fds = []
        try:
            fds.append(os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            for part in parts[:-1]:
                fds.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1]))
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fds[-1])
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_size <= 4 * 1024 * 1024, 'history_limit')
                payload = source.read(4 * 1024 * 1024 + 1)
            if relative.endswith('.gz'):
                with gzip.GzipFile(fileobj=io.BytesIO(payload)) as source:
                    payload = source.read(4 * 1024 * 1024 + 1)
            return decode(payload, 4 * 1024 * 1024)
        except FileNotFoundError:
            if optional:
                return None
            raise Rejected('history_missing') from None
        except (OSError, EOFError):
            raise Rejected('history_unavailable') from None
        finally:
            for fd in reversed(fds):
                os.close(fd)

    def snapshot(self):
        return {name: self.read(name, optional=name == 'inflight.json') for name in
                ('records.json', 'latest-vulnerability.json', 'coverage.json', 'inflight.json')}

    def execution(self, record):
        # Fixed bounded scan of run indices; no recursive walk or report collection.
        with os.scandir(self.root) as entries:
            names = []
            for entry in entries:
                if RUN.fullmatch(entry.name):
                    require(entry.is_dir(follow_symlinks=False), 'history_path_invalid')
                    names.append(entry.name)
                    require(len(names) <= 128, 'history_index_limit')
        matches = []
        for name in sorted(names, reverse=True):
            result = self.read(name + '/result.json.gz')
            require(result['runID'] == name and result['mode'] == 'vulnerability'
                    and len(result['assessments']) <= 100, 'history_execution_invalid')
            if any(r.get('jobID') == record['jobID'] for r in result['assessments']):
                matches.append((name, result))
        require(len(matches) == 1, 'history_execution_missing')
        return matches[0]


class AssessmentReader:
    def __init__(self, api, intake, history, database_lock, targets):
        self.api, self.intake, self.history = api, intake, History(history)
        self.expected = json.loads(json.dumps(database_lock))
        self.targets = frozenset(t['identity'] for t in targets)

    def qualify(self, w, now):
        require(w.kind == 'Deployment', 'assessment_unsupported')
        inventory = InventoryReader(self.api).read(w, now)
        deployment = inventory.deployment
        self.intake.c.deployment(deployment)
        before = self.history.snapshot()
        require(before['inflight.json'] is None, 'assessment_incomplete')
        records, latest, coverage = (before[k] for k in
                                    ('records.json', 'latest-vulnerability.json', 'coverage.json'))
        identity = '/'.join((w.namespace, w.kind, w.name, w.container))
        require(identity in self.targets, 'assessment_excluded')
        require(latest['schemaVersion'] == coverage['schemaVersion'] == 1
                and latest['mode'] == 'vulnerability', 'history_schema_unsupported')
        healthy(latest['state'])
        require(age_ok(latest['finishedAt'], now, 26) and age_ok(coverage['generatedAt'], now, 26)
                and timestamp(coverage['generatedAt']) <= now < timestamp(coverage['validUntil']), 'assessment_stale')
        key = identity + '/' + deployment.uid + '/' + inventory.running_ref
        record = records.get(key)
        require(record is not None, 'assessment_missing')
        attempt = record['lastAttempt']
        healthy(attempt['state'])
        require(age_ok(attempt['checkedAt'], now, 72) and age_ok(record['createdAt'], now, 72), 'assessment_stale')
        require(JOB.fullmatch(record['jobID']) is not None, 'history_execution_invalid')
        require(record['workloadUID'] == deployment.uid and record['imageDigest'] == inventory.running_ref
                and record['container'] == w.container, 'assessment_attribution_invalid')
        rows = [r for r in coverage['rows'] if '/'.join(r[k] for k in
                ('namespace', 'kind', 'workload', 'container')) == identity]
        require(len(rows) == 1, 'assessment_incomplete')
        row = rows[0]
        healthy(row['assessmentState'])
        require(row['assessmentState'] == 'healthy' and row['ready'] is True
                and row['containerType'] == 'containers' and row['workloadUID'] == deployment.uid
                and row['generation'] == deployment.generation and row['runningDigest'] == inventory.running_ref
                and sorted(row['podUIDs']) == sorted(p['uid'] for p in inventory.pods)
                and row['lastAttempt'] == attempt and row['lastSuccessfulScan'] == record['createdAt']
                and row['database'] == record['database'], 'assessment_incomplete')
        expected = self.expected
        metadata = expected['metadata']
        require(expected['state'] == expected['registryIntegrity'] == 'verified'
                and expected['publisher']['status'] == 'verified'
                and expected['publisher']['document'] == metadata and metadata['status'] == 'active'
                and record['database'] == metadata, 'database_unverified')
        require(age_ok(metadata['built'], now, 168), 'assessment_stale')
        run, result = self.history.execution(record)
        require(any(r == record for r in result['assessments']), 'history_execution_invalid')
        require(timestamp(result['startedAt']) <= timestamp(record['createdAt'])
                <= timestamp(attempt['checkedAt']) <= timestamp(result['finishedAt']) <= now,
                'history_time_invalid')
        job = record['jobID']
        request = self.history.read(run + '/request-' + job + '.json.gz')
        terminal = self.history.read(run + '/terminal-' + job + '.json')
        audit = self.history.read(run + '/cache-audit.json.gz')
        require(request['jobID'] == job and request['imageHash'] == request['imageTag'] == inventory.running_ref
                and request['containerName'] == w.container
                and request['wlid'] == f'wlid://cluster-{w.cluster}/namespace-{w.namespace}/deployment-{w.name}'
                and request['args'] == {'platform': 'linux/amd64', 'forceRescan': True},
                'assessment_attribution_invalid')
        require(terminal['state'] == 'succeeded' and terminal['phase'] == 'completed'
                and timestamp(result['startedAt']) <= timestamp(terminal['startedAt'])
                <= timestamp(terminal['finishedAt']) <= timestamp(attempt['checkedAt']), 'assessment_failed')
        require(timestamp(terminal['startedAt']) >= timestamp(self.intake.c.inventory_epoch(w)),
                'assessment_generation_unverified')
        sql, checksum = audit['sqlite'], audit['checksum']
        source = parse_qs(urlsplit(checksum['from']).query).get('checksum', [None])[0]
        require(sql['metadataReadable'] is True and checksum['valid'] is True
                and checksum.get('error') in (None, '') and sql['sha256'] == expected['database']['sha256']
                and sql['schemaVersion'] == checksum['schemaVersion'] == metadata['schemaVersion']
                and sql['built'] == checksum['built'] == metadata['built']
                and source == metadata['checksum'] and sql['import']['source'] == checksum['from']
                and timedelta(0) <= timestamp(terminal['startedAt']) - timestamp(audit['checkedAt'])
                    <= timedelta(minutes=15), 'database_unverified')
        saved = self.history.read(run + '/reports-' + job + '.json.gz')
        summary = self.history.read(run + '/summary-' + job + '.json.gz')
        bundle = dict(report=saved['vulnerability'], sbom=saved['sbom'], summary=summary)
        # Fresh named reads independently verify saved attribution and immutable
        # report contents. Never infer success from a health scalar or filename.
        rereads = []
        for key, kind in (('report', 'vulnerabilitymanifests'), ('sbom', 'sbomsyfts'),
                          ('summary', 'vulnerabilitymanifestsummaries')):
            obj = bundle[key]
            namespace, name = obj['metadata']['namespace'], obj['metadata']['name']
            require(namespace == (w.namespace if key == 'summary' else 'kubescape'), 'assessment_attribution_invalid')
            path = resource(STORAGE, namespace, kind, name)
            live = self.api.get(path)
            require(live == obj, 'assessment_changed')
            rereads.append((path, live))
        created = bundle['report']['spec']['metadata']['report']['createdAt']
        require(created == record['createdAt'], 'assessment_attribution_invalid')
        matches = bundle['report']['spec']['payload']['matches']
        require(dict(Counter(m['vulnerability'].get('severity', 'Unknown') for m in matches)) == record['severities']
                and any(m['vulnerability'].get('fix', {}).get('state') == 'fixed' for m in matches)
                    == record['fixesAvailable'], 'assessment_incomplete')
        source = bundle['sbom']['spec']['syft']['source']['metadata']
        require(source['manifestDigest'] == record['manifestDigest'] and source['imageID'] == record['configDigest'],
                'assessment_attribution_invalid')
        ctx = AssessmentContext(metadata['checksum'], metadata['built'], metadata['schemaVersion'],
                                terminal['startedAt'], deployment.generation, 'healthy', True, deployment.uid)
        event = VulnerabilityAdapter(ctx).read(json.dumps(bundle).encode(), deployment, now)
        require(event.freshness == 'confirmed' and event.confidence == 'qualified', 'assessment_incomplete')
        require(InventoryReader(self.api).read(w, now).signature == inventory.signature, 'inventory_changed')
        require(all(self.api.get(path) == obj for path, obj in rereads), 'assessment_changed')
        require(fingerprint(self.history.snapshot()) == fingerprint(before), 'history_changed')
        return event

    def poll(self, w, now):
        self.intake.reserve_poll(w.key + '/vulnerability', now)
        try:
            event = self.qualify(w, now)
            self.intake.enqueue([event], now)
            self.intake.drain(now, requalified=(event,))
            state, reason = 'confirmed', 'qualified'
        except (Rejected, KeyError, TypeError, ValueError, AttributeError, sqlite3.Error, OSError) as exc:
            code = str(exc) if isinstance(exc, Rejected) else ''
            if code in ('assessment_unsupported', 'inventory_unsupported', 'history_schema_unsupported',
                        'finding_id_invalid', 'sbom_unsupported', 'assessment_excluded'):
                state, reason = 'unsupported', 'excluded' if code == 'assessment_excluded' else 'unsupported'
            elif code == 'assessment_stale':
                state, reason = 'stale', 'assessment_stale'
            elif code == 'assessment_failed':
                state, reason = 'failed', 'assessment_failed'
            elif code == 'history_changed':
                state, reason = 'unknown', 'history_changed'
            elif code == 'assessment_identity_conflict':
                state, reason = 'unknown', 'assessment_identity_conflict'
            elif code in ('history_missing', 'assessment_missing', 'inventory_missing'):
                state, reason = 'unknown', 'missing'
            elif code in ('spool_capacity', 'ingest_rate_limit', 'receipt_capacity', 'incident_capacity',
                          'storage_unavailable') or isinstance(exc, sqlite3.Error):
                state, reason = 'failed', 'saturated'
            else:
                state, reason = 'unknown', 'assessment_incomplete'
        self.intake.status(w, 'vulnerability', now, state, reason)
        return state
