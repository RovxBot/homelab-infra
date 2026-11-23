"""Private file adapters for verified local formats; no receiver or polling loop."""
import base64
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
from typing import Protocol

from .model import (Deployment, Observation, SEVERITIES, Workload, Rejected, digest,
                    fingerprint, require, stamp, timestamp)


class EventAdapter(Protocol):
    def read(self, payload: bytes, deployment: Deployment, now) -> Observation: ...


def decode(payload, limit):
    require(isinstance(payload, bytes) and len(payload) <= limit, 'payload_limit')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    try:
        result = json.loads(payload, object_pairs_hook=unique)
        require(isinstance(result, dict), 'payload_invalid')
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise Rejected('payload_invalid') from None


def age_ok(value, now, hours):
    return timedelta(0) <= now - timestamp(value) <= timedelta(hours=hours)


class RuntimeStderrAdapter:
    def read(self, payload, deployment, now):
        event = decode(payload, 1024 * 1024)
        try:
            base, k8s = event['BaseRuntimeMetadata'], event['RuntimeK8sDetails']
            w = deployment if isinstance(deployment, Workload) else deployment.workload
            # Resolve controllers using independently read inventory, never pod-name stripping.
            require(k8s['namespace'] == w.namespace and k8s['containerName'] == w.container,
                    'runtime_attribution_mismatch')
            if k8s.get('workloadName'):
                require(k8s['workloadName'] == w.name and k8s['workloadKind'] == w.kind,
                        'runtime_attribution_mismatch')
            else:
                require(w.kind == 'Pod' and k8s['podName'] == w.name, 'runtime_owner_missing')
            observed = base['timestamp']
            require(age_ok(observed, now, 168), 'event_outside_replay_window')
            require(base.get('agentVersion', 'v0.3.310') == 'v0.3.310', 'runtime_version_unsupported')
            severity = base['severity']
            require(type(severity) is int and 0 <= severity <= 10, 'severity_invalid')
            image = k8s.get('imageDigest')
            if image:
                # Can contain a registry reference; retain only the immutable suffix.
                image = digest(image.rsplit('@', 1)[-1])
            rule = event['RuleID']
            # Preserve original nanosecond timestamp in the replay identity.
            token = base.get('uniqueID') or [observed, k8s.get('containerID'), base.get('infectedPID')]
            require(len(json.dumps(token)) <= 1024, 'event_id_limit')
            return Observation('kubescape', 'runtime', w, image, (rule,),
                               'Critical' if severity >= 9 else 'High' if severity >= 7 else 'Medium' if severity >= 4 else 'Low',
                               observed, fingerprint((w.key, rule, token)), 'node-agent-v0.3.310-stderr',
                               'confirmed' if age_ok(observed, now, 24) else 'stale', 'detection', 'investigate')
        except (KeyError, TypeError, AttributeError, IndexError):
            raise Rejected('runtime_structure_invalid') from None


@dataclass(frozen=True)
class AssessmentContext:
    """Trusted reader inputs, not fields accepted from the report body.

    Stage B must obtain these from the recurring runner's qualified history,
    database lock and a fresh independent Kubernetes inventory reread.
    """
    database_checksum: str
    database_built: str
    database_schema: str
    request_started_at: str
    generation: int
    latest_attempt: str
    coverage_complete: bool


class VulnerabilityAdapter:
    def __init__(self, context):
        self.context = context

    def read(self, payload, deployment, now):
        bundle = decode(payload, 4 * 1024 * 1024)
        try:
            report, sbom, summary = (bundle[k] for k in ('report', 'sbom', 'summary'))
            spec, ctx, w = report['spec'], self.context, deployment.workload
            data = spec['payload']
            created = spec['metadata']['report']['createdAt']
            db = data['descriptor']['db']
            source = sbom['spec']['syft']['source']['metadata']
            annotations = summary['metadata']['annotations']
            require(report['metadata']['annotations']['kubescape.io/status'] == 'ready'
                    and sbom['metadata']['annotations']['kubescape.io/status'] == 'ready', 'assessment_incomplete')
            require(data['source']['target']['userInput'].rsplit('@', 1)[-1] == deployment.digest
                    and source['userInput'] == data['source']['target']['userInput'], 'assessment_image_mismatch')
            require(annotations['kubescape.io/image-id'] == data['source']['target']['userInput']
                    and annotations['kubescape.io/workload-container-name'] == w.container
                    and annotations['kubescape.io/wlid'] == f'wlid://cluster-{w.cluster}/namespace-{w.namespace}/{w.kind.lower()}-{w.name}'
                    and summary['spec']['vulnerabilitiesRef']['all']['name'] == report['metadata']['name'], 'summary_attribution_mismatch')
            require(source['architecture'] == 'amd64' and source['os'] == 'linux'
                    and bool(sbom['spec']['syft']['artifacts']), 'sbom_unsupported')
            for field, key in (('manifest', 'manifestDigest'), ('config', 'imageID')):
                encoded = base64.b64decode(source[field], validate=True)
                require('sha256:' + hashlib.sha256(encoded).hexdigest() == source[key], 'sbom_identity_invalid')
            require(db['valid'] is True and db['built'] == ctx.database_built
                    and db['schemaVersion'] == ctx.database_schema
                    and spec['metadata']['tool']['databaseVersion'] == digest(ctx.database_checksum).removeprefix('sha256:'), 'database_mismatch')
            require(timestamp(created) >= timestamp(ctx.request_started_at).replace(microsecond=0)
                    and timestamp(created) >= timestamp(deployment.deployed_at), 'assessment_predates_deployment')
            matches = data['matches']
            require(isinstance(matches, list) and len(matches) <= 5000, 'finding_limit')
            ids, severities, fixed = set(), [], False
            for match in matches:
                vuln = match['vulnerability']
                ids.add(vuln['id'])
                require(vuln['severity'] in SEVERITIES, 'severity_invalid')
                severities.append(vuln['severity'])
                fixed |= vuln.get('fix', {}).get('state') == 'fixed'
            qualified = (ctx.latest_attempt == 'healthy' and ctx.coverage_complete is True
                         and ctx.generation == deployment.generation
                         and age_ok(deployment.observed_at, now, 1)
                         and timestamp(deployment.observed_at) >= timestamp(created))
            fresh = age_ok(created, now, 72) and age_ok(ctx.database_built, now, 168)
            state = 'confirmed' if qualified and fresh else 'stale' if not fresh else 'unknown'
            return Observation('kubescape', 'vulnerability', w, deployment.digest, tuple(sorted(ids)),
                               max(severities, key=SEVERITIES.index, default='Unknown'), created,
                               fingerprint((w.key, deployment.digest, created, ctx.database_checksum)),
                               'storage-v0.0.382-grype', state, 'qualified' if qualified else 'unverified',
                               'fix_available' if fixed else 'no_known_fix', not matches and state == 'confirmed',
                               deployment.generation, stamp(min(timestamp(created) + timedelta(hours=72),
                                   timestamp(ctx.database_built) + timedelta(hours=168))))
        except (KeyError, TypeError, AttributeError, IndexError, ValueError) as exc:
            if isinstance(exc, Rejected):
                raise
            raise Rejected('assessment_structure_invalid') from None
