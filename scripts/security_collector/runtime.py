"""Pinned stdout-exporter ingestion through authenticated Kubernetes logs.

Kubelet logs are lossy: overlapping anchors detect observable gaps; neither a
successful poll nor quiet logs proves that the agent emitted every detection.
"""
from datetime import timedelta
import hashlib
import json
import sqlite3

from .adapters import RuntimeStderrAdapter, decode
from .model import Rejected, require, stamp, timestamp
from .sources import APPS, CORE, InventoryReader, log_path, owner, resource

AGENT_IMAGE = ('quay.io/kubescape/node-agent:v0.3.310@sha256:'
               '99152dbefd07e524418f682882ad300cb74821aaa4e42e6477c42a9fa4686e60')
# Previously qualified amd64 child of the pinned index, retained in
# infra/kubescape/runtime/prerequisites.json (not a new registry lookup).
AGENT_MANIFEST = 'sha256:bd0e305a2e59e443722e8cf61bbd89251d4275c6c615b00bf5096118471129b0'


class RuntimeLogReader:
    def __init__(self, api, intake, workloads, *, pod_name, pod_uid, daemonset_uid, node='metal6'):
        self.api, self.intake = api, intake
        self.workloads = tuple(workloads)
        require(0 < len(self.workloads) <= 100, 'runtime_scope_invalid')
        require(node == 'metal6', 'runtime_scope_invalid')
        self.pod_name, self.pod_uid, self.daemonset_uid, self.node = pod_name, pod_uid, daemonset_uid, node
        self.key = 'runtime/metal6'

    def agent(self):
        ds = self.api.get(resource(APPS, 'kubescape', 'daemonsets', 'node-agent'))
        pod = self.api.get(resource(CORE, 'kubescape', 'pods', self.pod_name))
        require(ds['metadata']['uid'] == self.daemonset_uid
                and owner(pod, 'DaemonSet', self.daemonset_uid)
                and pod['metadata']['namespace'] == 'kubescape'
                and pod['metadata']['name'] == self.pod_name
                and pod['metadata']['uid'] == self.pod_uid
                and not pod['metadata'].get('deletionTimestamp')
                and pod['spec']['nodeName'] == self.node
                and pod['spec']['serviceAccountName'] == 'node-agent'
                and pod['status']['phase'] == 'Running', 'agent_identity_invalid')
        containers = pod['spec']['containers']
        states = pod['status']['containerStatuses']
        require(len(containers) == len(states) == 1 and containers[0]['name'] == 'node-agent'
                and containers[0]['image'] == AGENT_IMAGE and states[0]['name'] == 'node-agent'
                and states[0]['ready'] is True and 'running' in states[0]['state']
                and states[0]['imageID'].rsplit('@', 1)[-1] in (AGENT_IMAGE.rsplit('@', 1)[-1], AGENT_MANIFEST),
                'agent_identity_invalid')
        return dict(uid=pod['metadata']['uid'], container=states[0]['containerID'],
                    restarts=states[0]['restartCount'])

    def poll(self, now):
        self.intake.reserve_poll(self.key, now)
        reason, state = 'poll_ok', 'confirmed'
        try:
            previous = self.intake.cursor(self.key)
            agent = self.agent()
            inventories = [InventoryReader(self.api).read(w, now) for w in self.workloads]
            for inventory in inventories:
                self.intake.c.deployment(inventory.deployment)
            same = previous and previous['agent'] == agent
            since = stamp(timestamp(previous['at']) - timedelta(seconds=1)) if same and previous['at'] else None
            payload = self.api.raw(log_path('kubescape', self.pod_name, since))
            require(len(payload) <= 4 * 1024 * 1024 and (not payload or payload.endswith(b'\n')),
                    'log_snapshot_incomplete')
            lines = payload.splitlines()
            require(len(lines) <= 10000, 'log_snapshot_limit')
            events, anchors, at = [], [], previous['at'] if same else None
            for line in lines:
                require(len(line) <= 1024 * 1024, 'log_line_limit')
                raw_time, separator, raw = line.partition(b' ')
                require(separator, 'log_line_invalid')
                log_time = raw_time.decode('ascii')
                require(timedelta(0) <= now - timestamp(log_time) <= timedelta(days=7), 'log_time_invalid')
                require(at is None or timestamp(log_time) >= timestamp(at) - timedelta(seconds=1),
                        'log_time_invalid')
                at = max(filter(None, (at, log_time)), key=timestamp)
                anchors.append(hashlib.sha256(line).hexdigest())
                # Ordinary Go logger lines are not runtime exporter evidence.
                if not raw.startswith(b'{'):
                    continue
                event = decode(raw, 1024 * 1024)
                if not ({'RuleID', 'BaseRuntimeMetadata', 'RuntimeK8sDetails'} & event.keys()):
                    require('event' not in event, 'runtime_unsupported')  # FIM has a different contract.
                    continue
                k8s = event['RuntimeK8sDetails']
                candidates = [i for i in inventories if i.deployment.workload.namespace == k8s['namespace']
                              and i.deployment.workload.container == k8s['containerName']
                              and any(p['name'] == k8s['podName'] and p['node'] == self.node for p in i.pods)]
                require(len(candidates) == 1, 'runtime_attribution_invalid')
                inventory = candidates[0]
                require(k8s['imageDigest'].rsplit('@', 1)[-1] == inventory.deployment.digest,
                        'runtime_attribution_invalid')
                base = event['BaseRuntimeMetadata']
                require(base['agentVersion'] in ('v0.3.310', AGENT_IMAGE.split(':', 1)[1]),
                        'runtime_version_unsupported')
                base['agentVersion'] = 'v0.3.310'
                events.append(RuntimeStderrAdapter().read(json.dumps(event).encode(), inventory.deployment, now))
            require(self.agent() == agent, 'agent_changed_during_read')
            require(all(InventoryReader(self.api).read(i.deployment.workload, now).signature == i.signature
                        for i in inventories), 'inventory_changed')
            if not previous:
                reason = 'initial_gap'
            elif not same:
                reason = 'agent_restarted'
            elif now - timestamp(previous['checked']) > timedelta(minutes=5):
                reason = 'disconnected'
            elif previous['anchor'] and previous['anchor'] not in anchors:
                reason = 'overlap_missing'
            # Latch gaps until a separately reviewed reconciliation; subsequent
            # reads/replays do not certify the missing interval as complete.
            gap = reason != 'poll_ok' or bool(previous and previous.get('gap'))
            cursor = dict(agent=agent, at=at, anchor=anchors[-1] if anchors else
                          previous.get('anchor') if same else None, checked=stamp(now), gap=gap)
            self.intake.enqueue(events, now, cursor_key=self.key, cursor=cursor)
            self.intake.drain(now)
            state = 'unknown' if gap else 'confirmed'
        except (Rejected, KeyError, TypeError, ValueError, UnicodeError, AttributeError, sqlite3.Error, OSError) as exc:
            code = str(exc) if isinstance(exc, Rejected) else ''
            reason = ('saturated' if code in ('spool_capacity', 'ingest_rate_limit', 'receipt_capacity',
                      'incident_capacity', 'storage_unavailable') or isinstance(exc, sqlite3.Error)
                      else 'inventory_changed' if code == 'inventory_changed'
                      else 'read_failed' if code.startswith('api_') else 'invalid_input')
            state = 'unsupported' if code in ('runtime_unsupported', 'inventory_unsupported') else 'failed'
        for workload in self.workloads:
            self.intake.status(workload, 'runtime', now, state, reason)
        return state
