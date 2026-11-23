"""Bounded sanitized spool on the collector's single-writer private database.

No raw evidence is retained. Cursor and normalized queue commit together; drain
is at-least-once into the collector's transactional replay receipts.
"""
from dataclasses import asdict
from datetime import timedelta
import json
import sqlite3

from .model import Observation, Workload, fingerprint, require, stamp, timestamp


class Intake:
    def __init__(self, collector, *, max_pending=1000, pending_bytes=8 * 1024 * 1024):
        self.c = collector
        self.max_pending, self.pending_bytes = max_pending, pending_bytes
        collector.db.executescript('''
            CREATE TABLE IF NOT EXISTS pending (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, received TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cursors (key TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reader_status (
                key TEXT PRIMARY KEY, category TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS polls (key TEXT PRIMARY KEY, at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS source_observations (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runtime_source_status (key TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runtime_identities (
                source TEXT NOT NULL, identity TEXT NOT NULL, accepted TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY (source, identity));
            CREATE TABLE IF NOT EXISTS runtime_evidence (
                source TEXT NOT NULL, id TEXT NOT NULL, attributed INTEGER NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY (source, id));
        ''')

    def cursor(self, key):
        row = self.c.db.execute('SELECT data FROM cursors WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def source_status(self, key):
        row = self.c.db.execute('SELECT data FROM runtime_source_status WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def source_health(self, key, node, now, state, reason, *, identity_invalid=False):
        """Additive source records; never rewrite or reset a legacy cursor on failure."""
        old, cursor = self.source_status(key) or {}, self.cursor(key)
        value = dict(node=node, checked_at=stamp(now), state=state, reason=reason,
                     read_ok=state in ('confirmed', 'unknown'), identity_invalid=identity_invalid,
                     invalid_identity_count=old.get('invalid_identity_count', 0) + identity_invalid,
                     last_invalid_identity=stamp(now) if identity_invalid else old.get('last_invalid_identity'),
                     last_success=stamp(now) if state in ('confirmed', 'unknown') else old.get('last_success'),
                     gap=bool((cursor and cursor.get('gap')) or old.get('gap') or reason in GAPS),
                     last_gap=stamp(now) if reason in GAPS else old.get('last_gap'),
                     successful_reads=old.get('successful_reads', 0) + (state in ('confirmed', 'unknown')),
                     event_count=old.get('event_count', 0),
                     attributed_detections=old.get('attributed_detections', 0))
        # Cursor counters are cumulative and commit with the corresponding queue.
        if cursor:
            value.update(event_count=cursor.get('event_count', cursor.get('attributed_events', 0)
                         + cursor.get('unattributed_events', 0)),
                         attributed_detections=cursor.get('attributed_detections', cursor.get('attributed_events', 0)))
        with self.c.db:
            self.c.db.execute('INSERT OR REPLACE INTO runtime_source_status VALUES (?,?)',
                              (key, json.dumps(value, sort_keys=True)))
        return value

    def reserve_poll(self, key, now):
        """Persisted 15-second minimum interval, including failed API reads."""
        with self.c.db:
            row = self.c.db.execute('SELECT at FROM polls WHERE key=?', (key,)).fetchone()
            require(not row or now - timestamp(row[0]) >= timedelta(seconds=15), 'poll_rate_limit')
            require(row or self.c.db.execute('SELECT count(*) FROM polls').fetchone()[0]
                    < self.c.max_incidents * 2, 'reader_capacity')
            self.c.db.execute('INSERT OR REPLACE INTO polls VALUES (?,?)', (key, stamp(now)))

    def enqueue(self, events, now, *, cursor_key=None, cursor=None, source_observations=(), source_identity=None):
        require(len(events) <= self.max_pending, 'spool_capacity')
        with self.c.db:
            if source_identity is not None:
                token = fingerprint(source_identity)
                exists = self.c.db.execute('SELECT 1 FROM runtime_identities WHERE source=? AND identity=?',
                                          (cursor_key, token)).fetchone()
                if not exists:
                    require(self.c.db.execute('SELECT count(*) FROM runtime_identities WHERE source=?',
                            (cursor_key,)).fetchone()[0] < 128, 'source_identity_capacity')
                    require(self.c.db.execute('SELECT count(*) FROM runtime_identities WHERE source=? AND accepted>=?',
                            (cursor_key, stamp(now - timedelta(hours=1)))).fetchone()[0] < 4,
                            'source_refresh_rate_limit')
                    self.c.db.execute('INSERT INTO runtime_identities VALUES (?,?,?,?)',
                        (cursor_key, token, stamp(now), json.dumps(source_identity, sort_keys=True)))
            if source_identity is not None:
                proofs = [(e.event_id, 1, dict(event_id=e.event_id, at=e.observed_at,
                          rule=e.identities[0], workload=e.workload.key, digest=e.digest,
                          identity=e.runtime_identity)) for e in events]
                proofs += [(o['id'], 0, o) for o in source_observations]
                for event_id, attributed, proof in proofs:
                    exists = self.c.db.execute('SELECT 1 FROM runtime_evidence WHERE source=? AND id=?',
                                              (cursor_key, event_id)).fetchone()
                    require(exists or self.c.db.execute('SELECT count(*) FROM runtime_evidence').fetchone()[0]
                            < self.c.max_receipts, 'receipt_capacity')
                    self.c.db.execute('INSERT OR IGNORE INTO runtime_evidence VALUES (?,?,?,?)',
                                      (cursor_key, event_id, attributed, json.dumps(proof, sort_keys=True)))
            for observation in source_observations:
                require(self.c.db.execute('SELECT 1 FROM source_observations WHERE id=?',
                        (observation['id'],)).fetchone() or self.c.db.execute(
                        'SELECT count(*) FROM source_observations').fetchone()[0] < self.max_pending,
                        'spool_capacity')
                self.c.db.execute('INSERT OR IGNORE INTO source_observations VALUES (?,?)',
                                 (observation['id'], json.dumps(observation, sort_keys=True)))
            count, size = self.c.db.execute(
                'SELECT count(*), coalesce(sum(length(data)),0) FROM pending').fetchone()
            for event in events:
                event.__post_init__()
                pending = self.c.db.execute('SELECT data FROM pending WHERE id=?', (event.event_id,)).fetchone()
                if pending and event.category == 'vulnerability':
                    previous = json.loads(pending['data'])
                    if previous.get('runtime_identity') is None:
                        previous.pop('runtime_identity', None)
                    require(fingerprint(previous) == event.observation_hash,
                            'assessment_identity_conflict')
                replay = self.c.is_replay(event)
                if pending or replay:
                    continue
                value = json.dumps(asdict(event), sort_keys=True)
                count, size = count + 1, size + len(value.encode())
                require(count <= self.max_pending and size <= self.pending_bytes, 'spool_capacity')
                self.c.db.execute('INSERT INTO pending VALUES (?,?,?)', (event.event_id, value, stamp(now)))
            if cursor_key is not None:
                if source_identity is not None:
                    count, attributed = self.c.db.execute(
                        'SELECT count(*),coalesce(sum(attributed),0) FROM runtime_evidence WHERE source=?',
                        (cursor_key,)).fetchone()
                    cursor.update(event_count=count, attributed_detections=attributed)
                require(self.cursor(cursor_key) is not None or self.c.db.execute(
                    'SELECT count(*) FROM cursors').fetchone()[0] < 8, 'cursor_capacity')
                self.c.db.execute('INSERT OR REPLACE INTO cursors VALUES (?,?)',
                                 (cursor_key, json.dumps(cursor, sort_keys=True)))

    def status(self, workload, category, now, state, reason):
        require(category in ('runtime', 'vulnerability'), 'category_unsupported')
        require(state in ('confirmed', 'unknown', 'failed', 'stale', 'unsupported'), 'state_invalid')
        # Reasons are code-owned; callers must never pass exceptions or payload text.
        require(reason in REASONS, 'reader_reason_invalid')
        key = workload.key + '/' + category
        with self.c.db:
            row = self.c.db.execute('SELECT data FROM reader_status WHERE key=?', (key,)).fetchone()
            require(row or self.c.db.execute('SELECT count(*) FROM reader_status').fetchone()[0]
                    < self.c.max_incidents * 2, 'reader_capacity')
            old = json.loads(row[0]) if row else {}
            value = dict(workload=asdict(workload), checked_at=stamp(now), state=state, reason=reason,
                         last_success=stamp(now) if state == 'confirmed' else old.get('last_success'),
                         gap_count=min(2**63-1, old.get('gap_count', 0) + (reason in GAPS)),
                         last_gap=stamp(now) if reason in GAPS else old.get('last_gap'))
            self.c.db.execute('INSERT OR REPLACE INTO reader_status VALUES (?,?,?)',
                             (key, category, json.dumps(value)))
        if state != 'confirmed':
            self.c.telemetry(workload, state, source='kubescape', category=category)

    def health(self, now):
        """Call on every scheduler tick, even when no detections arrive."""
        result = []
        for row in self.c.db.execute('SELECT category,data FROM reader_status').fetchall():
            value = json.loads(row['data'])
            if now - timestamp(value['checked_at']) > timedelta(minutes=5):
                value.update(state='stale', reason='heartbeat_expired')
                self.c.telemetry(Workload(**value['workload']), 'stale',
                                 source='kubescape', category=row['category'])
            result.append(value)
        self.c.refresh(now)
        return result

    def drain(self, now, *, requalified=()):
        qualified = {}
        for current in requalified:
            require(isinstance(current, Observation), 'assessment_requalification_invalid')
            current.__post_init__()
            require(current.category == 'vulnerability' and current.freshness == 'confirmed'
                    and current.confidence == 'qualified', 'assessment_requalification_invalid')
            require(current.event_id not in qualified or qualified[current.event_id] == current.observation_hash,
                    'assessment_identity_conflict')
            qualified[current.event_id] = current.observation_hash
        for row in self.c.db.execute('SELECT id,data FROM pending ORDER BY received,id').fetchall():
            value = json.loads(row['data'])
            value['workload'] = Workload(**value['workload'])
            value['identities'] = tuple(value['identities'])
            event = Observation(**value)
            # A durable "qualified" bit is not a lease on external state. Only
            # this poll's fresh assessment qualification authorizes delivery.
            # Runtime draining must never consume an assessment left by a crash.
            if event.category == 'vulnerability':
                if event.event_id not in qualified:
                    continue
                require(event.observation_hash == qualified[event.event_id], 'assessment_identity_conflict')
            # Expired pending evidence is fenced for explicit operator handling;
            # no dropping evidence or receipts to make room automatically.
            require(now - timestamp(event.observed_at) <= timedelta(days=7), 'spool_evidence_expired')
            try:
                self.c.ingest(event, now)
                with self.c.db:
                    self.c.db.execute('DELETE FROM pending WHERE id=?', (row['id'],))
            except sqlite3.Error:
                # No exception text leaves this boundary.
                require(False, 'storage_unavailable')


GAPS = {'initial_gap', 'overlap_missing', 'agent_restarted', 'disconnected', 'read_failed', 'invalid_input',
        'saturated', 'heartbeat_expired', 'inventory_changed', 'attribution_unavailable',
        'source_enrollment_pending', 'source_identity_invalid', 'source_refresh_limited', 'uncovered_node'}
REASONS = GAPS | {'poll_ok', 'qualified', 'missing', 'assessment_failed', 'assessment_stale',
                 'assessment_incomplete', 'assessment_identity_conflict', 'unsupported', 'excluded', 'history_changed',
                 'history_unavailable'}
