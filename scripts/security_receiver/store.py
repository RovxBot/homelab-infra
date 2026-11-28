"""One-writer, private SQLite receipts and deduplicated Kubescape incidents.

No raw HTTP payloads, process arguments, GitHub client, log cursors or cleanup
automation. Persist a validated normalized event and incident in one transaction
before acknowledging it. The legacy collector PVC is never opened by this code.
"""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .native import SafeEvent, Rejected, require, utc

MAX_RECEIPTS = 20000
MAX_INCIDENTS = 200
MAX_EVENTS_PER_HOUR = 1000
MAX_DATABASE_BYTES = 64 * 1024 * 1024

# Legacy narrow projection retained for compatibility during a staged rollout.
# Both projections require a separately authenticated publisher identity.
PUBLIC_WORKLOADS = {
    "home/homepage/Deployment/homepage/homepage": "homepage/Deployment/homepage",
    "home/homepage/Deployment/homepage-assets/nginx": "homepage/Deployment/homepage-assets",
    "home/memos/Deployment/memos/memos": "memos/Deployment/memos",
}

PRIVATE_WORKLOADS = {
    **PUBLIC_WORKLOADS,
    "home/koito/Deployment/koito/koito": "koito/Deployment/koito",
    "home/koito/Deployment/koito-lastfm-bridge/multi-scrobbler": "koito/Deployment/koito-lastfm-bridge",
    "home/monitoring/Deployment/gatus/gatus": "monitoring/Deployment/gatus",
    "home/stirling-pdf/Deployment/stirling-pdf/stirling-pdf": "stirling-pdf/Deployment/stirling-pdf",
    "home/cloudflared/Deployment/cloudflared/cloudflared": "cloudflared/Deployment/cloudflared",
    "home/mqtt/Deployment/mosquitto/mosquitto": "mqtt/Deployment/mosquitto",
    "home/security/Deployment/vaultwarden/vaultwarden": "security/Deployment/vaultwarden",
    "home/wotlk/StatefulSet/wotlk-mariadb/mysql": "wotlk/StatefulSet/wotlk-mariadb",
    "home/kube-system/DaemonSet/kube-proxy/kube-proxy": "kube-system/DaemonSet/kube-proxy",
}


class EventStore:
    def __init__(self, path):
        self._lock = None
        self.path = str(path)
        if self.path != ":memory:":
            target = Path(self.path)
            require(target.parent.exists() and not target.parent.is_symlink(),
                    "storage_path_invalid")
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                os.close(fd)
                raise Rejected("writer_already_active") from None
            self._lock = fd
        try:
            self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            size = self.db.execute("PRAGMA page_size").fetchone()[0]
            pages = MAX_DATABASE_BYTES // size
            require(self.db.execute(f"PRAGMA max_page_count={pages}").fetchone()[0] <= pages,
                    "database_capacity")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS incidents (
                    key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (
                    id TEXT PRIMARY KEY, incident_key TEXT NOT NULL,
                    received TEXT NOT NULL, normalized_sha256 TEXT NOT NULL,
                    data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS receipts_received ON receipts(received);
            """)
            require(self.db.execute("PRAGMA quick_check").fetchone()[0] == "ok",
                    "database_integrity_failed")
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            if self._lock is not None:
                os.close(self._lock)
            raise

    def close(self):
        self.db.close()
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None

    def record(self, event, now):
        require(type(event) is SafeEvent and isinstance(now, datetime)
                and now.tzinfo is not None, "normalized_event_required")
        # A revalidation here protects the storage boundary from bypass callers.
        require(len(event.event_id) == 64
                and all(c in "0123456789abcdef" for c in event.event_id),
                "event_id_invalid")
        require(event.digest_relation in ("equal", "different", "absent"),
                "digest_relation_invalid")
        require(timedelta(0) <= now.astimezone(timezone.utc) - utc(event.observed_at)
                <= timedelta(days=7), "event_outside_window")
        raw_safe = json.dumps(asdict(event), sort_keys=True, separators=(",", ":"))
        safe_hash = hashlib.sha256(raw_safe.encode()).hexdigest()
        incident_key = hashlib.sha256(json.dumps(
            ("kubescape", "runtime", event.workload_key, event.rule),
            separators=(",", ":")).encode()).hexdigest()
        received = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT normalized_sha256 FROM receipts WHERE id=?", (event.event_id,)
            ).fetchone()
            if row:
                require(row[0] == safe_hash, "replay_identity_conflict")
                self.db.execute("COMMIT")
                return {"accepted": True, "duplicate": True}
            require(self.db.execute("SELECT count(*) FROM receipts").fetchone()[0]
                    < MAX_RECEIPTS, "receipt_capacity")
            require(self.db.execute(
                "SELECT count(*) FROM receipts WHERE received >= ?",
                ((now-timedelta(hours=1)).astimezone(timezone.utc).isoformat()
                  .replace("+00:00", "Z"),)).fetchone()[0] < MAX_EVENTS_PER_HOUR,
                "event_rate_limit")
            row = self.db.execute(
                "SELECT data FROM incidents WHERE key=?", (incident_key,)).fetchone()
            if row:
                incident = json.loads(row[0])
                incident["count"] = min(2**63-1, incident["count"]+1)
                if utc(event.observed_at) > utc(incident["last"]):
                    incident["last"] = event.observed_at
                    incident["last_node"] = event.node
                    incident["last_digest_relation"] = event.digest_relation
                if utc(event.observed_at) < utc(incident["first"]):
                    incident["first"] = event.observed_at
                order = {"Low":0, "Medium":1, "High":2, "Critical":3}
                if order[event.severity] > order[incident["severity"]]:
                    incident["severity"] = event.severity
            else:
                require(self.db.execute("SELECT count(*) FROM incidents").fetchone()[0]
                        < MAX_INCIDENTS, "incident_capacity")
                incident = {"key":incident_key, "category":"runtime",
                            "source":"kubescape", "workload_key":event.workload_key,
                            "rule":event.rule, "first":event.observed_at,
                            "last":event.observed_at, "count":1,
                            "status":"open", "severity":event.severity,
                            "last_node":event.node,
                            "last_digest_relation":event.digest_relation}
            # Receipt detail is allowlisted, *not* the incoming HTTP event. Private
            # UIDs are confined to owner-controlled SQLite, never GitHub output.
            self.db.execute("INSERT INTO receipts VALUES (?,?,?,?,?)",
                            (event.event_id, incident_key, received, safe_hash, raw_safe))
            self.db.execute("INSERT OR REPLACE INTO incidents VALUES (?,?)",
                            (incident_key, json.dumps(incident, sort_keys=True)))
            self.db.execute("COMMIT")
            return {"accepted": True, "duplicate": False}
        except (Rejected, sqlite3.Error, ValueError, KeyError, TypeError):
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def public_incidents(self):
        return self.project_incidents(PUBLIC_WORKLOADS)

    def private_incidents(self):
        return self.project_incidents(PRIVATE_WORKLOADS)

    def project_incidents(self, workload_scope):
        """Bounded, high-impact projection; never include raw event or container identities."""
        output = []
        for (value,) in self.db.execute("SELECT data FROM incidents ORDER BY key"):
            record = json.loads(value)
            identity = workload_scope.get(record.get("workload_key"))
            if identity is None or record.get("severity") not in ("High", "Critical"):
                continue
            # All fields derive from normalized private events; fail closed
            # if storage is malformed, rather than interpolate arbitrary text.
            key, rule = record.get("key"), record.get("rule")
            first, last = record.get("first"), record.get("last")
            count = record.get("count")
            require(isinstance(key, str) and len(key) == 64
                    and all(c in "0123456789abcdef" for c in key)
                    and isinstance(rule, str) and len(rule) == 5
                    and rule[0] == "R" and rule[1:].isdigit()
                    and record.get("status") == "open"
                    and type(count) is int and 1 <= count <= 2**63-1
                    and isinstance(first, str) and len(first) <= 40
                    and isinstance(last, str) and len(last) <= 40
                    and utc(first) <= utc(last),
                    "publication_record_invalid")
            output.append({"key":key, "workload":identity, "rule":rule,
                           "severity":record["severity"], "first":first,
                           "last":last, "count":count, "state":"open"})
            require(len(output) <= (80 if workload_scope is PRIVATE_WORKLOADS else 30), "publication_capacity")
        return {"schema":1, "source":"kubescape-native", "incidents":output}

    def health(self):
        try:
            return {"database_ok":self.db.execute("PRAGMA quick_check").fetchone()[0]=="ok",
                    "receipts":self.db.execute("SELECT count(*) FROM receipts").fetchone()[0],
                    "incidents":self.db.execute("SELECT count(*) FROM incidents").fetchone()[0]}
        except sqlite3.Error:
            return {"database_ok":False,"receipts":None,"incidents":None}
