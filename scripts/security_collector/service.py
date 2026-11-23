"""Production read loop. Private files carry detail; HTTP carries aggregate health.

No GitHub client, write credentials, event receiver or disposition API exists here.
Only the scheduler thread owns SQLite; the HTTP thread reads an immutable snapshot.
"""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time

from .assessment import AssessmentReader
from .engine import Collector
from .intake import Intake
from .issues import Publication, render_private
from .model import Rejected, Workload, require, stamp, timestamp
from .runtime import RuntimeSources
from .sources import KubernetesAPI


def atomic(path, value):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(value, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Service:
    def __init__(self, config, state, api, *, clock=time.monotonic):
        self.state, self.clock = Path(state), clock
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(not self.state.is_symlink(), 'state_path_invalid')
        os.chmod(self.state, 0o700)
        self.workloads = tuple(Workload(**w) for w in config['workloads'])
        require(0 < len(self.workloads) <= 10, 'runtime_scope_invalid')
        require(('agent' in config) != ('agents' in config), 'agent_configuration_invalid')
        require(len({w.key for w in self.workloads}) == len(self.workloads), 'runtime_scope_invalid')
        self.sources = RuntimeSources(api, None, self.workloads,
                                      config.get('agents', [config.get('agent')]))
        self.c = Collector(self.state / 'collector.sqlite', allow_assessment_resolution=False, retain_evidence=True)
        self.intake = Intake(self.c)
        self.sources.intake = self.intake
        for reader in self.sources.readers:
            reader.intake = self.intake
        self.runtime = self.sources.readers[0]  # Legacy callers; persistent metal6 key is unchanged.
        require(self.c.db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'database_integrity_failed')
        self.assessment = AssessmentReader(api, self.intake, config['history'],
            config['database_lock'], [{'identity': '/'.join((w.namespace, w.kind, w.name, w.container))}
                                      for w in self.workloads])
        self.assessment_available = config.get('assessment_history_available', True)
        self.publications = {w.key: Publication(w) for w in self.workloads}
        self.updated, self.started = None, clock()
        self.snapshot = {'ready': False, 'status': 'starting'}

    def tick(self, now):
        try:
            self.intake.health(now)
            sources = self.sources.poll(now)
            for workload in self.workloads:
                if self.assessment_available:
                    self.assessment.poll(workload, now)
                else:
                    self.intake.status(workload, 'vulnerability', now, 'unknown', 'history_unavailable')
            health = self.intake.health(now)
            cursors = {r.node: self.intake.cursor(r.key) for r in self.sources.readers}
            cursor = cursors.get('metal6')
            pending = self.c.db.execute('SELECT count(*),coalesce(sum(length(data)),0) FROM pending').fetchone()
            pages = self.c.db.execute('PRAGMA page_count').fetchone()[0]
            page_size = self.c.db.execute('PRAGMA page_size').fetchone()[0]
            fs = os.statvfs(self.state)
            storage_ok = (fs.f_bavail * fs.f_frsize >= 128 * 1024 * 1024
                          and pages * page_size < 64 * 1024 * 1024
                          and pending[0] < self.intake.max_pending
                          and pending[1] < self.intake.pending_bytes)
            runtime_ok = all(s['read_ok'] for s in sources)
            assessments = [json.loads(row['data']) for row in self.c.db.execute(
                "SELECT data FROM reader_status WHERE category='vulnerability'")]
            assessment_ok = len(assessments) == len(self.workloads) and all(
                r['state'] == 'confirmed' for r in assessments)
            # Historical source gaps remain visible even after removing a source during rollback.
            retained_sources = [json.loads(r[0]) for r in self.c.db.execute('SELECT data FROM runtime_source_status')]
            retained_cursors = [json.loads(r[0]) for r in self.c.db.execute("SELECT data FROM cursors WHERE key LIKE 'runtime/%'")]
            gap = any(s.get('gap') for s in retained_sources + retained_cursors)
            coverage = sum(w['covered'] for w in self.sources.coverage)
            saturated = (not storage_ok or any(r['reason'] == 'saturated' for r in health))
            public = dict(ready=storage_ok, runtime_read_ok=runtime_ok,
                assessment_available=self.assessment_available,
                assessment_qualified=assessment_ok, covered_workloads=coverage,
                uncovered_workloads=len(self.workloads) - coverage,
                enrolled_workloads=len(self.workloads), continuity_gap=gap,
                expected_agents=len(sources), healthy_agents=sum(s['read_ok'] for s in sources),
                invalid_source_identities=sum(s['identity_invalid'] for s in sources),
                event_count=sum(s['event_count'] for s in sources),
                attributed_detection_count=sum(s['attributed_detections'] for s in sources),
                pending=pending[0], saturated=saturated,
                database_ok=True, queue_ok=storage_ok,
                last_runtime_reads={s['node']: s['last_success'] for s in sources},
                last_runtime_read=min((s['last_success'] for s in sources), key=lambda t: timestamp(t))
                                  if all(s['last_success'] for s in sources) else None,
                last_qualified_assessments=[r.get('last_success') for r in assessments],
                collection_error=not runtime_ok or any(r['state'] in ('failed', 'unsupported', 'stale')
                                     or r['reason'] in ('missing', 'assessment_incomplete', 'history_changed',
                                                       'assessment_identity_conflict', 'history_unavailable') for r in health))
            public['status'] = ('healthy' if runtime_ok and assessment_ok and not gap
                and not saturated and coverage == len(self.workloads) and not public['collection_error']
                else 'degraded')
            private = dict(checked_at=stamp(now), health=public, readers=health, runtime_cursor=cursor,
                           runtime_cursors=cursors, sources=sources, workload_coverage=self.sources.coverage,
                           agents=[dict(node=r.node, pod=r.pod_name, pod_uid=r.pod_uid,
                                        daemonset_uid=r.daemonset_uid,
                                        observed=cursors[r.node]['agent'] if cursors[r.node] else None)
                                   for r in self.sources.readers],
                           retained_sources=retained_sources,
                           database_bytes=pages * page_size, pending_bytes=pending[1],
                           public_writes_enabled=False, automatic_closure_enabled=False)
            # Previews never become an unauthenticated HTTP endpoint or log output.
            atomic(self.state / 'previews.json', [render_private(i, self.publications[i['workload_key']])
                   for i in self.c.all() if i['workload_key'] in self.publications])
            atomic(self.state / 'status.json', private)
            with self.c.db:
                self.c.db.execute('CREATE TABLE IF NOT EXISTS service_status (id INTEGER PRIMARY KEY, data TEXT)')
                self.c.db.execute('INSERT OR REPLACE INTO service_status VALUES (1,?)',
                                 (json.dumps(private),))
            self.snapshot = public
        except (Rejected, sqlite3.Error, OSError, ValueError, KeyError, TypeError):
            self.snapshot = dict(ready=False, status='degraded', collection_error=True,
                                 error='collection_or_storage_failed')
        self.updated = self.clock()
        return self.snapshot

    def response(self, path):
        age = self.clock() - (self.updated if self.updated is not None else self.started)
        live = age <= 300
        ready = live and self.updated is not None and self.snapshot['ready']
        if path == '/livez':
            return (200 if live else 503), {'alive': live}
        if path == '/readyz':
            return (200 if ready else 503), {'ready': ready}
        if path == '/healthz':
            value = dict(self.snapshot)
            value['telemetry_stale'] = not live
            if not ready:
                value.update(status='degraded', ready=False)
            if not live:
                value.update(runtime_read_ok=False, assessment_qualified=False, healthy_agents=0,
                             covered_workloads=0, uncovered_workloads=len(self.workloads))
            return (200 if ready and value['status'] == 'healthy' else 503), value
        return 404, {'error': 'not_found'}


def server_for(service, host='0.0.0.0', port=8080):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, value = service.response(self.path)
            payload = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(2)

    return HTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/config/collector.json')
    parser.add_argument('--state', default='/state/private')
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(Path(args.config).read_text())
    config['database_lock'] = json.loads(Path('/assessment-config/grype-database.json').read_text())
    require(config['api_origin'] == 'https://kubernetes.default.svc', 'api_origin_invalid')
    api = KubernetesAPI(config['api_origin'], '/var/run/collector-api/ca.crt', '/var/run/collector-api/token')
    service = Service(config, args.state, api)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    server = server_for(service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        while not stop.is_set():
            service.tick(datetime.now(timezone.utc))
            stop.wait(30)
    finally:
        server.shutdown()
        server.server_close()
        service.c.close()


if __name__ == '__main__':
    main()
