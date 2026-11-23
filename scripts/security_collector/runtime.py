"""Pinned stdout-exporter ingestion through authenticated Kubernetes logs.

Kubelet logs are lossy: overlapping anchors detect observable gaps; neither a
successful poll nor quiet logs proves that the agent emitted every detection.
"""
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import re
import sqlite3

from .adapters import RuntimeStderrAdapter, decode
from .model import Rejected, identity, require, stamp, timestamp
from .sources import APPS, CORE, InventoryReader, log_path, owner, resource

AGENT_IMAGE = ('quay.io/kubescape/node-agent:v0.3.310@sha256:'
               '99152dbefd07e524418f682882ad300cb74821aaa4e42e6477c42a9fa4686e60')
# Previously qualified amd64 child of the pinned index, retained in
# infra/kubescape/runtime/prerequisites.json (not a new registry lookup).
AGENT_MANIFEST = 'sha256:bd0e305a2e59e443722e8cf61bbd89251d4275c6c615b00bf5096118471129b0'


class RuntimeLogReader:
    def __init__(self, api, intake, workloads, *, pod_name=None, pod_uid=None, daemonset_uid, node='metal6'):
        self.api, self.intake = api, intake
        self.workloads = tuple(workloads)
        require(0 < len(self.workloads) <= 100, 'runtime_scope_invalid')
        require(node in ('metal3', 'metal6'), 'runtime_scope_invalid')
        require(bool(pod_name) == bool(pod_uid), 'agent_configuration_invalid')
        if pod_name:
            identity(pod_name)
        require(isinstance(daemonset_uid, str) and 0 < len(daemonset_uid) <= 64,
                'agent_configuration_invalid')
        self.pod_name, self.pod_uid, self.daemonset_uid, self.node = pod_name, pod_uid, daemonset_uid, node
        self.key = 'runtime/' + node
        self.verified_identity = None

    def agent(self):
        try:
            return self._agent()
        except (KeyError, TypeError, IndexError, AttributeError):
            raise Rejected('agent_identity_invalid') from None

    def _agent(self):
        require(self.pod_name is not None, 'source_enrollment_pending')
        ds = self.api.get(resource(APPS, 'kubescape', 'daemonsets', 'node-agent'))
        pod = self.api.get(resource(CORE, 'kubescape', 'pods', self.pod_name))
        template = ds['spec']['template']['spec']
        refs = [r for r in pod['metadata'].get('ownerReferences', []) if r.get('controller') is True]
        require(ds['metadata']['uid'] == self.daemonset_uid
                and ds['metadata']['name'] == 'node-agent' and ds['metadata']['namespace'] == 'kubescape'
                and not ds['metadata'].get('deletionTimestamp')
                and template['serviceAccountName'] == 'node-agent'
                and len(template['containers']) == 1
                and template['containers'][0]['name'] == 'node-agent'
                and template['containers'][0]['image'] == AGENT_IMAGE
                and owner(pod, 'DaemonSet', self.daemonset_uid)
                and refs[0]['name'] == 'node-agent'
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
                and isinstance(states[0]['containerID'], str) and bool(states[0]['containerID'])
                and type(states[0]['restartCount']) is int and states[0]['restartCount'] >= 0
                and states[0]['imageID'].rsplit('@', 1)[-1] in (AGENT_IMAGE.rsplit('@', 1)[-1], AGENT_MANIFEST),
                'agent_identity_invalid')
        observed = dict(uid=pod['metadata']['uid'], container=states[0]['containerID'],
                        restarts=states[0]['restartCount'])
        self.verified_identity = dict(source=self.key, node=self.node, pod=self.pod_name,
            daemonset_uid=self.daemonset_uid, service_account='node-agent', image=AGENT_IMAGE,
            image_id=states[0]['imageID'], started=states[0]['state']['running']['startedAt'], **observed)
        return observed

    def poll(self, now, *, inventories=None, publish_status=True):
        self.intake.reserve_poll(self.key, now)
        reason, state = 'poll_ok', 'confirmed'
        invalid_identity = False
        try:
            previous = self.intake.cursor(self.key)
            agent = self.agent()
            proof = dict(self.verified_identity)
            require(timestamp(proof['started']) <= now, 'agent_identity_invalid')
            if inventories is None:
                inventories = [InventoryReader(self.api).read(w, now) for w in self.workloads]
            for inventory in inventories:
                self.intake.c.deployment(inventory.deployment)
            same = previous and previous['agent'] == agent
            boundary = now - timedelta(days=7)
            # Kubernetes can return the whole second containing sinceTime.
            # Share the rounded overlap bound with validation, never widening
            # the strict seven-day acceptance window.
            lower = (max(boundary, (timestamp(previous['at']) - timedelta(seconds=1)).replace(microsecond=0))
                     if same and previous['at'] else boundary)
            payload = self.api.raw(log_path('kubescape', self.pod_name, stamp(lower)))
            require(len(payload) <= 4 * 1024 * 1024 and (not payload or payload.endswith(b'\n')),
                    'log_snapshot_incomplete')
            lines = payload.splitlines()
            require(len(lines) <= 10000, 'log_snapshot_limit')
            events, anchors, observations, at = [], [], [], previous['at'] if same else None
            for line in lines:
                require(len(line) <= 1024 * 1024, 'log_line_limit')
                raw_time, separator, raw = line.partition(b' ')
                require(separator, 'log_line_invalid')
                log_time = raw_time.decode('ascii')
                parsed_time = timestamp(log_time)
                require(timedelta(0) <= now - parsed_time <= timedelta(days=7), 'log_time_invalid')
                # Retain the moving out-of-order limit at the same granularity.
                require(parsed_time >= lower and (at is None or parsed_time >=
                        (timestamp(at) - timedelta(seconds=1)).replace(microsecond=0)),
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
                known_pods = [p for i in inventories for p in i.pods
                              if i.deployment.workload.namespace == k8s['namespace']
                              and p['name'] == k8s['podName']]
                require(not known_pods or all(p['node'] == self.node for p in known_pods),
                        'runtime_attribution_invalid')
                candidates = [i for i in inventories if i.deployment.workload.namespace == k8s['namespace']
                              and i.deployment.workload.container == k8s['containerName']
                              and any(p['name'] == k8s['podName'] and p['node'] == self.node for p in i.pods)]
                if not candidates:
                    # No trusted current owner: retain only a typed source
                    # observation, never invent a workload or an incident.
                    rule = event.get('RuleID')
                    require(isinstance(rule, str) and re.fullmatch(r'R[0-9]{4}', rule),
                            'runtime_attribution_invalid')
                    observations.append(dict(id=anchors[-1], rule=rule, at=log_time,
                                             state='unknown', reason='attribution_unavailable'))
                    continue
                require(len(candidates) == 1, 'runtime_attribution_invalid')
                inventory = candidates[0]
                pods = [p for p in inventory.pods if p['name'] == k8s['podName'] and p['node'] == self.node]
                require(len(pods) == 1, 'runtime_attribution_invalid')
                pod = pods[0]
                container_id = lambda value: value.removeprefix('containerd://').removeprefix('docker://')
                require(k8s['imageDigest'].rsplit('@', 1)[-1] == inventory.deployment.digest,
                        'runtime_attribution_invalid')
                require(k8s['podUID'] == pod['uid']
                        and container_id(k8s['containerID']) == container_id(pod['container_id'])
                        and k8s.get('workloadUID', inventory.deployment.uid) == inventory.deployment.uid
                        and k8s.get('nodeName', self.node) == self.node,
                        'runtime_attribution_invalid')
                base = event['BaseRuntimeMetadata']
                require(timestamp(pod['started']) <= timestamp(base['timestamp']) <= timestamp(log_time),
                        'runtime_attribution_invalid')
                require(base['agentVersion'] in ('v0.3.310', AGENT_IMAGE.split(':', 1)[1]),
                        'runtime_version_unsupported')
                base['agentVersion'] = 'v0.3.310'
                # Verify any claimed controller, then supply the independently resolved owner.
                require(k8s.get('workloadName', inventory.deployment.workload.name) == inventory.deployment.workload.name
                        and k8s.get('workloadKind', 'Deployment') == 'Deployment', 'runtime_attribution_invalid')
                k8s.update(workloadName=inventory.deployment.workload.name, workloadKind='Deployment')
                observation = RuntimeStderrAdapter().read(json.dumps(event).encode(), inventory.deployment, now)
                events.append(replace(observation, runtime_identity=dict(source=self.key, node=self.node,
                    agent_pod_uid=agent['uid'], deployment_uid=inventory.deployment.uid,
                    pod=pod['name'], pod_uid=pod['uid'], container_id=pod['container_id'])))
            require(self.agent() == agent and self.verified_identity == proof, 'agent_changed_during_read')
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
            if observations:
                reason = 'attribution_unavailable'
            # Latch gaps until a separately reviewed reconciliation; subsequent
            # reads/replays do not certify the missing interval as complete.
            old_health = self.intake.source_status(self.key) or {}
            gap = reason != 'poll_ok' or bool(previous and previous.get('gap')) or old_health.get('gap', False)
            cursor = dict(agent=agent, at=at, anchor=anchors[-1] if anchors else
                          previous.get('anchor') if same else None, checked=stamp(now), gap=gap,
                          covered_workloads=sum(all(p['node'] == self.node for p in i.pods)
                                                for i in inventories),
                          observed_lines=len(lines), attributed_events=len(events),
                          unattributed_events=len(observations))
            self.intake.enqueue(events, now, cursor_key=self.key, cursor=cursor,
                                source_observations=observations, source_identity=proof)
            self.intake.drain(now)
            state = 'unknown' if gap else 'confirmed'
        except (Rejected, KeyError, TypeError, ValueError, UnicodeError, AttributeError, sqlite3.Error, OSError) as exc:
            code = str(exc) if isinstance(exc, Rejected) else ''
            invalid_identity = code in ('agent_identity_invalid', 'agent_changed_during_read')
            reason = ('saturated' if code in ('spool_capacity', 'ingest_rate_limit', 'receipt_capacity',
                      'incident_capacity', 'storage_unavailable', 'source_identity_capacity') or isinstance(exc, sqlite3.Error)
                      else 'source_enrollment_pending' if code == 'source_enrollment_pending'
                      else 'source_identity_invalid' if invalid_identity
                      else 'source_refresh_limited' if code == 'source_refresh_rate_limit'
                      else 'inventory_changed' if code == 'inventory_changed'
                      else 'read_failed' if code.startswith('api_') else 'invalid_input')
            state = 'unsupported' if code in ('runtime_unsupported', 'inventory_unsupported') else 'failed'
        self.intake.source_health(self.key, self.node, now, state, reason, identity_invalid=invalid_identity)
        if publish_status:
            for workload in self.workloads:
                self.intake.status(workload, 'runtime', now, state, reason)
        return state


class RuntimeSources:
    """Independent sources, one inventory view and replica-aware workload coverage."""
    def __init__(self, api, intake, workloads, agents):
        require(isinstance(agents, list) and 0 < len(agents) <= 2, 'runtime_scope_invalid')
        self.readers = tuple(RuntimeLogReader(api, intake, workloads, **a) for a in agents)
        require(len({r.node for r in self.readers}) == len(self.readers), 'runtime_scope_invalid')
        pinned = [r for r in self.readers if r.pod_name]
        require(len({r.pod_name for r in pinned}) == len({r.pod_uid for r in pinned}) == len(pinned)
                and len({r.daemonset_uid for r in self.readers}) == 1, 'agent_configuration_invalid')
        self.api, self.intake, self.workloads = api, intake, tuple(workloads)
        self.coverage = []

    def poll(self, now):
        inventories, errors = [], {}
        for workload in self.workloads:
            try:
                inventories.append(InventoryReader(self.api).read(workload, now))
            except (Rejected, KeyError, TypeError, ValueError, AttributeError, OSError):
                errors[workload.key] = 'inventory_changed'
        for reader in self.readers:
            try:
                reader.poll(now, inventories=inventories, publish_status=False)
            except Rejected:
                self.intake.source_health(reader.key, reader.node, now, 'failed', 'invalid_input')
        sources = self.health(now)
        by_node = {s['node']: s for s in sources}
        self.coverage = []
        for workload in self.workloads:
            inventory = next((i for i in inventories if i.deployment.workload == workload), None)
            if inventory is None:
                coverage = dict(workload=workload.key, covered=False, replicas=None, covered_replicas=0,
                                reason=errors[workload.key], digest=None, nodes=[])
            else:
                relevant = [by_node.get(p['node']) for p in inventory.pods]
                count = sum(bool(s and s['read_ok']) for s in relevant)
                covered = count == len(inventory.pods)
                gap = any(s and s['gap'] for s in relevant)
                coverage = dict(workload=workload.key, covered=covered, replicas=len(inventory.pods),
                    covered_replicas=count, digest=inventory.deployment.digest,
                    nodes=sorted({p['node'] for p in inventory.pods}), continuity_gap=bool(gap),
                    reason='poll_ok' if covered else 'uncovered_node')
            self.coverage.append(coverage)
            self.intake.status(workload, 'runtime', now,
                'failed' if not coverage['covered'] else 'unknown' if coverage.get('continuity_gap') else 'confirmed',
                coverage['reason'])
        return sources

    def health(self, now):
        result = []
        for reader in self.readers:
            value = dict(self.intake.source_status(reader.key) or dict(node=reader.node,
                checked_at=None, last_success=None, state='failed', reason='missing',
                read_ok=False, identity_invalid=False, gap=True, event_count=0, attributed_detections=0))
            if value['checked_at'] is None or not timedelta(0) <= now - timestamp(value['checked_at']) <= timedelta(minutes=5):
                value.update(state='stale', reason='heartbeat_expired', read_ok=False)
            result.append(value)
        return result
