"""Single-writer durable state machine. Saturation fails closed, never evicts keys."""
from dataclasses import asdict
from datetime import timedelta
import fcntl
import json
import os
import sqlite3

from .model import Observation, Rejected, require, stamp, timestamp


class Collector:
    def __init__(self, path, *, max_incidents=200, max_receipts=20000, events_per_hour=1000,
                 storage_bytes=64 * 1024 * 1024, allow_assessment_resolution=True, retain_evidence=False):
        self.allow_assessment_resolution = allow_assessment_resolution
        self.retain_evidence = retain_evidence
        self._lock_fd = None
        if str(path) != ":memory:":
            # Create private state before SQLite can write it; do not follow symlinks.
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                raise Rejected('writer_already_active') from None
            self._lock_fd = fd
        self.db = sqlite3.connect(path)
        # DELETE journal has at most the bounded database's dirty pages. Reserve
        # twice storage_bytes on the private filesystem for atomic transactions.
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        page_size = self.db.execute('PRAGMA page_size').fetchone()[0]
        quota_pages = max(32, storage_bytes // page_size)
        actual = self.db.execute(f'PRAGMA max_page_count={quota_pages}').fetchone()[0]
        if actual > quota_pages:
            self.close()
            require(False, 'storage_existing_over_quota')
        self.db.row_factory = sqlite3.Row
        self.max_incidents, self.max_receipts = max_incidents, max_receipts
        self.events_per_hour = events_per_hour
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS incidents (key TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts (
                id TEXT PRIMARY KEY, received TEXT NOT NULL, observation_hash TEXT);
            CREATE TABLE IF NOT EXISTS deployments (key TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS delivery (
                key TEXT PRIMARY KEY, attempts INTEGER DEFAULT 0, next TEXT,
                ambiguous INTEGER DEFAULT 0, published TEXT);
            CREATE TABLE IF NOT EXISTS api_calls (at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS writes (at TEXT NOT NULL, creation INTEGER NOT NULL);
        ''')
        # Old receipts cannot prove the observation they accepted. Preserve them
        # with a NULL hash and fail closed on vulnerability replay, never backfill
        # their identity from a potentially changed current assessment.
        if 'observation_hash' not in {row['name'] for row in self.db.execute('PRAGMA table_info(receipts)')}:
            with self.db:
                self.db.execute('ALTER TABLE receipts ADD COLUMN observation_hash TEXT')

    def close(self):
        self.db.close()
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def get(self, key):
        row = self.db.execute('SELECT data FROM incidents WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self):
        return [json.loads(row[0]) for row in self.db.execute('SELECT data FROM incidents ORDER BY key')]

    def _save(self, incident):
        self.db.execute('INSERT OR REPLACE INTO incidents VALUES (?,?)',
                        (incident['key'], json.dumps(incident, sort_keys=True)))

    def deployment(self, deployment):
        """Called only by a trusted inventory reader; changed digest invalidates recovery."""
        with self.db:
            old = self.db.execute('SELECT data FROM deployments WHERE key=?', (deployment.workload.key,)).fetchone()
            previous = json.loads(old[0]) if old else None
            if old:
                require(timestamp(deployment.observed_at) >= timestamp(previous['observed_at']), 'inventory_out_of_order')
            else:
                require(self.db.execute('SELECT count(*) FROM deployments').fetchone()[0] < self.max_incidents, 'inventory_capacity')
            same = (previous is not None and previous['digest'] == deployment.digest
                    and previous['generation'] == deployment.generation and previous.get('uid') == deployment.uid)
            # The recurring record has UID/digest but no historical generation.
            # A pre-request independent observation of this monotonic generation
            # is required by Stage B; current coverage cannot invent that history.
            value = dict(asdict(deployment), epoch_started=previous.get('epoch_started', previous['observed_at'])
                         if same else deployment.observed_at)
            self.db.execute('INSERT OR REPLACE INTO deployments VALUES (?,?)',
                            (deployment.workload.key, json.dumps(value)))
            for incident in self.all():
                if incident['workload_key'] != deployment.workload.key or incident['category'] != 'vulnerability':
                    continue
                if (incident['digest'] != deployment.digest or incident['generation'] != deployment.generation
                        or incident.get('workload_uid') != deployment.uid):
                    incident.update(digest=deployment.digest, generation=deployment.generation,
                                    workload_uid=deployment.uid,
                                    freshness='unknown', confidence='unverified', status='open',
                                    resolution=None, valid_until=None)
                    self._save(incident)

    def inventory_epoch(self, workload):
        row = self.db.execute('SELECT data FROM deployments WHERE key=?', (workload.key,)).fetchone()
        require(row is not None, 'inventory_missing')
        value = json.loads(row[0])
        return value.get('epoch_started', value['observed_at'])

    def is_replay(self, event):
        row = self.db.execute('SELECT observation_hash FROM receipts WHERE id=?', (event.event_id,)).fetchone()
        if row is None:
            return False
        require(event.category != 'vulnerability' or row['observation_hash'] == event.observation_hash,
                'assessment_identity_conflict')
        return True

    def ingest(self, event: Observation, now):
        # Revalidate frozen contracts even if a caller bypassed construction.
        event.__post_init__()
        require(timedelta(0) <= now - timestamp(event.observed_at) <= timedelta(days=7), 'event_outside_replay_window')
        with self.db:
            if not self.retain_evidence:
                self.db.execute('DELETE FROM receipts WHERE received < ?', (stamp(now - timedelta(days=7)),))
            if self.is_replay(event):
                return self.get(event.incident_key)
            require(self.db.execute('SELECT count(*) FROM receipts WHERE received >= ?',
                                    (stamp(now - timedelta(hours=1)),)).fetchone()[0] < self.events_per_hour, 'ingest_rate_limit')
            require(self.db.execute('SELECT count(*) FROM receipts').fetchone()[0] < self.max_receipts, 'receipt_capacity')
            incident = self.get(event.incident_key)
            if incident is None and event.category == 'vulnerability' and not event.identities:
                # Empty/stale/unknown reports cannot open a synthetic finding.
                self.db.execute('INSERT INTO receipts (id,received,observation_hash) VALUES (?,?,?)',
                                (event.event_id, stamp(now), event.observation_hash))
                return None
            if incident is None:
                require(self.db.execute('SELECT count(*) FROM incidents').fetchone()[0] < self.max_incidents, 'incident_capacity')
                incident = dict(key=event.incident_key, workload_key=event.workload.key,
                                category=event.category, source=event.source, first=event.observed_at,
                                last=event.observed_at, count=0, history=[], status='open', resolution=None,
                                assessed_at=None, generation=None)
            incident['first'] = min(incident['first'], event.observed_at, key=timestamp)
            previous_last = incident['last']
            incident['last'] = max(previous_last, event.observed_at, key=timestamp)
            incident['count'] = min(2**63 - 1, incident['count'] + 1)
            incident['history'] = incident['history'] + [dict(at=event.observed_at, digest=event.digest,
                fingerprint=event.event_id, findings=len(event.identities), identities=list(event.identities[:50]),
                severity=event.severity, confidence=event.confidence, remediation=event.remediation,
                freshness=event.freshness, runtime_identity=event.runtime_identity)]
            if not self.retain_evidence:
                incident['history'] = incident['history'][-32:]
            if timestamp(event.observed_at) >= timestamp(previous_last):
                incident.update(confidence=event.confidence, freshness=event.freshness,
                                valid_until=event.valid_until)
                qualified = False
                if event.category == 'runtime':
                    incident.update(digest=event.digest, status='open', resolution=None,
                                    runtime_identity=event.runtime_identity)
                else:
                    row = self.db.execute('SELECT data FROM deployments WHERE key=?', (event.workload.key,)).fetchone()
                    deployed = json.loads(row[0]) if row else None
                    current = (deployed is not None and deployed['digest'] == event.digest
                               and deployed['generation'] == event.generation
                               and deployed.get('uid') == event.workload_uid
                               and timedelta(0) <= now - timestamp(deployed['observed_at']) <= timedelta(hours=1)
                               and timestamp(event.observed_at) >= timestamp(deployed['deployed_at']))
                    incident.update(digest=deployed['digest'] if deployed else event.digest,
                                    generation=deployed['generation'] if deployed else event.generation,
                                    workload_uid=deployed.get('uid') if deployed else event.workload_uid,
                                    assessed_at=event.observed_at)
                    qualified = (current and event.confidence == 'qualified'
                                 and event.freshness == 'confirmed' and event.valid_until is not None
                                 and timestamp(event.valid_until) > now)
                    clean = qualified and event.clean and self.allow_assessment_resolution
                    incident.update(status='resolved' if clean else 'open', resolution='assessment' if clean else None)
                    if not current:
                        incident.update(freshness='unknown', confidence='unverified')
                # Retain the last qualified finding snapshot separately from the
                # latest attempt/freshness, even after history rolls over or a deploy.
                if event.category == 'runtime' or qualified or not incident.get('findings_qualified', False):
                    incident.update(identities=list(event.identities[:50]), finding_count=len(event.identities),
                                    severity=event.severity, provenance=event.provenance,
                                    remediation=event.remediation, findings_at=event.observed_at,
                                    findings_digest=event.digest, findings_generation=event.generation,
                                    findings_uid=event.workload_uid,
                                    findings_qualified=qualified)
            self.db.execute('INSERT INTO receipts (id,received,observation_hash) VALUES (?,?,?)',
                            (event.event_id, stamp(now), event.observation_hash))
            self._save(incident)
            return incident

    def telemetry(self, workload, state, *, source, category):
        """A reader gap invalidates only its own source/category evidence."""
        require(source == 'kubescape', 'source_unsupported')
        require(category in ('runtime', 'vulnerability'), 'category_unsupported')
        require(state in ('unknown', 'failed', 'stale', 'unsupported'), 'telemetry_state_invalid')
        with self.db:
            for incident in self.all():
                if (incident['workload_key'] == workload.key and incident['source'] == source
                        and incident['category'] == category):
                    incident.update(freshness=state, confidence='unverified')
                    if incident['resolution'] == 'assessment':
                        incident.update(status='open', resolution=None)
                    self._save(incident)

    def refresh(self, now):
        with self.db:
            if not self.retain_evidence:
                self.db.execute('DELETE FROM receipts WHERE received < ?', (stamp(now - timedelta(days=7)),))
            self.db.execute('DELETE FROM api_calls WHERE at < ?', (stamp(now - timedelta(hours=1)),))
            self.db.execute('DELETE FROM writes WHERE at < ?', (stamp(now - timedelta(days=1)),))
            for incident in self.all():
                expired = (incident['valid_until'] is not None and now >= timestamp(incident['valid_until']))
                if incident['category'] == 'runtime':
                    expired |= now - timestamp(incident['last']) > timedelta(hours=24)
                else:
                    row = self.db.execute('SELECT data FROM deployments WHERE key=?', (incident['workload_key'],)).fetchone()
                    expired |= row is None or now - timestamp(json.loads(row[0])['observed_at']) > timedelta(hours=1)
                if expired:
                    incident.update(freshness='stale', confidence='unverified')
                    if incident['resolution'] == 'assessment':
                        incident.update(status='open', resolution=None)
                    self._save(incident)

    def disposition(self, key, *, action, reviewed_issue):
        """Trusted operator API only. Public event data can never invoke this method."""
        require(action in ('acknowledged', 'false_positive', 'investigated', 'accepted_risk'), 'disposition_invalid')
        require(type(reviewed_issue) is int and 0 < reviewed_issue < 10**9, 'review_reference_invalid')
        with self.db:
            incident = self.get(key)
            require(incident is not None, 'incident_missing')
            incident.update(status='acknowledged' if action == 'acknowledged' else 'resolved',
                            resolution=action, reviewed_issue=reviewed_issue)
            self._save(incident)
