"""Kubernetes second-granularity overlap with durable private replay state."""
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security_collector.engine import Collector
from security_collector.intake import Intake
from security_collector.model import stamp, timestamp
from security_collector.runtime import RuntimeLogReader
from test_security_collector import W
from test_security_collector_multi import TwoNodes

NOW = timestamp('2026-11-23T09:09:00Z')
CURSOR_AT = '2026-11-23T09:08:34.591396741Z'
REQUESTED_SINCE = '2026-11-23T09:08:33.591396Z'
FIRST_LOG_AT = '2026-11-23T09:08:33.423783307Z'


def line(at, body=b'ordinary synthetic message'):
    return at.encode() + b' ' + body + b'\n'


class TimestampOverlapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'private.sqlite'
        self.c = Collector(self.database)
        self.addCleanup(lambda: self.c.close())
        self.i, self.api = Intake(self.c), TwoNodes()
        self.api.pod['spec']['nodeName'] = 'metal3'
        self.reader = RuntimeLogReader(self.api, self.i, [W], node='metal3',
            pod_name='node-agent-second', pod_uid='second-agent-uid', daemonset_uid='agent-ds-uid')
        self.other = RuntimeLogReader(self.api, self.i, [W], node='metal6',
            pod_name='node-agent-test', pod_uid='agent-pod-uid', daemonset_uid='agent-ds-uid')
        event = json.loads(self.api.logs.partition(b' ')[2])
        event['BaseRuntimeMetadata']['timestamp'] = FIRST_LOG_AT
        self.event = line(FIRST_LOG_AT, json.dumps(event).encode())
        self.anchor = line(CURSOR_AT, b'previous synthetic anchor')
        # Only timestamps come from production; all log bodies are synthetic.
        self.overlap = (self.event + line('2026-11-23T09:08:33.700000000Z')
            + line('2026-11-23T09:08:34.000000000Z')
            + line('2026-11-23T09:08:34.300000000Z') + self.anchor)

    def poll(self, payload, now=NOW):
        self.api.logs_by_pod['node-agent-second'] = payload
        return self.reader.poll(now)

    def since(self):
        path = next(p for p in reversed(self.api.calls) if '/node-agent-second/log?' in p)
        return parse_qs(urlsplit(path).query)['sinceTime'][0]

    def restart(self):
        self.c.close()
        self.c = Collector(self.database)
        self.i = Intake(self.c)
        self.reader.intake = self.other.intake = self.i

    def assert_one_event(self):
        self.assertEqual(len(self.c.all()), 1)
        self.assertEqual(self.c.all()[0]['count'], 1)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM runtime_evidence').fetchone()[0], 1)
        self.assertEqual(self.i.cursor(self.reader.key)['event_count'], 1)

    def test_exact_production_overlap_replays_after_restart_without_duplicates(self):
        self.assertEqual(stamp(timestamp(CURSOR_AT) - timedelta(seconds=1)), REQUESTED_SINCE)
        self.assertLess(timestamp(FIRST_LOG_AT), timestamp(REQUESTED_SINCE))
        self.assertEqual(self.poll(self.overlap), 'unknown')
        self.api.logs_by_pod['node-agent-test'] = line('2026-11-23T09:08:50Z')
        self.other.poll(NOW)
        before = self.i.cursor(self.reader.key)
        other_cursor, other_health = self.i.cursor(self.other.key), self.i.source_status(self.other.key)
        receipts = [tuple(r) for r in self.c.db.execute('SELECT * FROM receipts')]
        evidence = [tuple(r) for r in self.c.db.execute('SELECT * FROM runtime_evidence')]
        self.restart()
        self.assertEqual(self.poll(self.overlap, NOW + timedelta(seconds=30)), 'unknown')
        self.assertEqual(self.since(), '2026-11-23T09:08:33Z')
        after = self.i.cursor(self.reader.key)
        self.assertEqual((after['at'], after['anchor']), (CURSOR_AT, before['anchor']))
        self.assertEqual(after['observed_lines'], 5)
        self.assertTrue(after['gap'])
        health = self.i.source_status(self.reader.key)
        self.assertEqual(health['reason'], 'poll_ok')
        self.assertTrue(health['read_ok'])
        self.assertEqual(health['last_gap'], stamp(NOW))
        self.assertEqual(self.i.cursor(self.other.key), other_cursor)
        self.assertEqual(self.i.source_status(self.other.key), other_health)
        self.assertEqual([tuple(r) for r in self.c.db.execute('SELECT * FROM receipts')], receipts)
        self.assertEqual([tuple(r) for r in self.c.db.execute('SELECT * FROM runtime_evidence')], evidence)
        self.assert_one_event()

    def test_unseen_overlap_evidence_is_ingested_without_advancing_cursor(self):
        self.poll(self.anchor)
        self.assertEqual(self.poll(self.overlap, NOW + timedelta(seconds=30)), 'unknown')
        self.assertEqual(self.i.cursor(self.reader.key)['at'], CURSOR_AT)
        self.assertEqual(self.i.source_status(self.reader.key)['reason'], 'poll_ok')
        self.assert_one_event()

    def test_out_of_order_lines_inside_rounded_overlap_keep_maximum_cursor(self):
        self.poll(self.anchor)
        payload = b''.join(reversed(self.overlap.splitlines(keepends=True)))
        self.assertEqual(self.poll(payload, NOW + timedelta(seconds=30)), 'unknown')
        self.assertEqual(self.i.cursor(self.reader.key)['at'], CURSOR_AT)
        self.assertEqual(self.i.source_status(self.reader.key)['reason'], 'poll_ok')
        self.assert_one_event()

    def test_invalid_times_fail_atomically_without_advancing_or_skipping_evidence(self):
        self.poll(self.anchor)
        before = self.i.cursor(self.reader.key)
        for index, case in enumerate(('older', 'before_bound', 'out_of_order', 'future', 'expired'), 1):
            with self.subTest(case=case):
                now = NOW + timedelta(seconds=30 * index)
                bad = dict(older=line('2026-11-23T09:08:20Z'),
                    before_bound=line('2026-11-23T09:08:32.999999999Z'),
                    out_of_order=line('2026-11-23T09:08:40.500000000Z') + self.event,
                    future=line(stamp(now + timedelta(microseconds=1))),
                    expired=line(stamp(now - timedelta(days=7, microseconds=1))))[case]
                self.assertEqual(self.poll(self.event + bad + self.anchor, now), 'failed')
                self.assertEqual(self.i.cursor(self.reader.key), before)
                health = self.i.source_status(self.reader.key)
                self.assertEqual(health['reason'], 'invalid_input')
                self.assertTrue(health['gap'])
                self.assertFalse(health['read_ok'])
                self.assertEqual(self.c.all(), [])
                for table in ('pending', 'receipts', 'runtime_evidence'):
                    self.assertEqual(self.c.db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
        # Failed reads did not discard the previously unseen valid evidence.
        self.assertEqual(self.poll(self.overlap, NOW + timedelta(seconds=180)), 'unknown')
        self.assert_one_event()

    def test_fractional_seven_day_boundary_clamps_the_rounded_overlap(self):
        now = NOW + timedelta(microseconds=123456)
        boundary = now - timedelta(days=7)
        retained = line(stamp(boundary + timedelta(microseconds=500000)))
        self.poll(retained, now - timedelta(seconds=30))
        before = self.i.cursor(self.reader.key)
        self.assertEqual(self.poll(line(stamp(boundary - timedelta(microseconds=1))) + retained, now), 'failed')
        self.assertEqual(self.since(), stamp(boundary))
        self.assertEqual(self.i.cursor(self.reader.key), before)
        # The exact new boundary is accepted; the expired anchor remains a gap.
        current = boundary + timedelta(seconds=15)
        self.assertEqual(self.poll(line(stamp(current)), now + timedelta(seconds=15)), 'unknown')
        self.assertEqual(self.since(), stamp(current))
        self.assertEqual(self.i.cursor(self.reader.key)['at'], stamp(current))
        self.assertEqual(self.i.source_status(self.reader.key)['reason'], 'overlap_missing')

    def test_missing_anchor_retains_gap_even_after_successful_replay(self):
        self.poll(self.anchor)
        rotated = self.event + line(CURSOR_AT, b'different synthetic anchor')
        self.assertEqual(self.poll(rotated, NOW + timedelta(seconds=30)), 'unknown')
        self.assertEqual(self.i.source_status(self.reader.key)['reason'], 'overlap_missing')
        self.restart()
        self.assertEqual(self.poll(rotated, NOW + timedelta(seconds=60)), 'unknown')
        health = self.i.source_status(self.reader.key)
        self.assertEqual(health['reason'], 'poll_ok')
        self.assertTrue(health['gap'])
        self.assertEqual(health['last_gap'], stamp(NOW + timedelta(seconds=30)))
        self.assert_one_event()


if __name__ == '__main__':
    unittest.main()
