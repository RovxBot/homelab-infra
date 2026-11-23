"""Bounded internal contracts. Raw telemetry never enters collector state."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re

SEVERITIES = ('Unknown', 'Negligible', 'Low', 'Medium', 'High', 'Critical')
STATES = ('confirmed', 'unknown', 'stale', 'failed', 'unsupported')
KINDS = ('Deployment', 'StatefulSet', 'DaemonSet', 'Job', 'CronJob', 'Pod')


class Rejected(ValueError):
    """Static reason codes only; never echo rejected payloads."""


def require(condition, code):
    if not condition:
        raise Rejected(code)


def timestamp(value):
    try:
        require(isinstance(value, str) and len(value) <= 40, 'timestamp_invalid')
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(result.tzinfo is not None, 'timestamp_invalid')
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise Rejected('timestamp_invalid') from None


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def identity(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,62}', value), 'identity_invalid')
    return value


def digest(value):
    require(isinstance(value, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', value), 'digest_invalid')
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class Workload:
    cluster: str
    namespace: str
    kind: str
    name: str
    container: str

    def __post_init__(self):
        for value in (self.cluster, self.namespace, self.name, self.container):
            identity(value)
        require(self.kind in KINDS, 'kind_unsupported')

    @property
    def key(self):
        return '/'.join((self.cluster, self.namespace, self.kind, self.name, self.container))


@dataclass(frozen=True)
class Deployment:
    workload: Workload
    digest: str
    generation: int
    observed_at: str
    deployed_at: str
    uid: str | None = None

    def __post_init__(self):
        digest(self.digest)
        require(type(self.generation) is int and self.generation > 0, 'generation_invalid')
        require(timestamp(self.deployed_at) <= timestamp(self.observed_at), 'deployment_time_invalid')
        if self.uid is not None:
            require(isinstance(self.uid, str) and re.fullmatch(r'[a-zA-Z0-9-]{1,64}', self.uid), 'uid_invalid')


@dataclass(frozen=True)
class Observation:
    source: str
    category: str
    workload: Workload
    digest: str | None
    identities: tuple[str, ...]
    severity: str
    observed_at: str
    event_id: str
    provenance: str
    freshness: str
    confidence: str
    remediation: str
    # Qualified clean assessments are separate from mere absence of findings.
    clean: bool = False
    generation: int | None = None
    valid_until: str | None = None
    workload_uid: str | None = None
    runtime_identity: dict | None = None

    def __post_init__(self):
        require(self.source == 'kubescape', 'source_unsupported')
        require(self.category in ('runtime', 'vulnerability'), 'category_unsupported')
        require(self.severity in SEVERITIES and self.freshness in STATES, 'state_invalid')
        require(self.confidence in ('detection', 'qualified', 'unverified'), 'confidence_invalid')
        require(self.remediation in ('investigate', 'fix_available', 'no_known_fix', 'reassess'), 'remediation_invalid')
        require(self.provenance in ('node-agent-v0.3.310-stderr', 'storage-v0.0.382-grype'), 'provenance_invalid')
        require(re.fullmatch('[a-f0-9]{64}', self.event_id) is not None, 'event_id_invalid')
        timestamp(self.observed_at)
        if self.valid_until is not None:
            timestamp(self.valid_until)
        if self.digest is not None:
            digest(self.digest)
        if self.workload_uid is not None:
            require(isinstance(self.workload_uid, str)
                    and re.fullmatch(r'[a-zA-Z0-9-]{1,64}', self.workload_uid), 'uid_invalid')
        if self.runtime_identity is not None:
            require(self.category == 'runtime' and isinstance(self.runtime_identity, dict)
                    and set(self.runtime_identity) == {'source', 'node', 'agent_pod_uid', 'deployment_uid',
                                                       'pod', 'pod_uid', 'container_id'}, 'runtime_identity_invalid')
            require(all(isinstance(v, str) and re.fullmatch(r'[a-zA-Z0-9_.:/-]{1,255}', v)
                        for v in self.runtime_identity.values()), 'runtime_identity_invalid')
        require(len(self.identities) <= 5000, 'finding_limit')
        for value in self.identities:
            require(isinstance(value, str) and re.fullmatch(r'(R[0-9]{4}|CVE-[0-9]{4}-[0-9]{4,10}|GHSA-[23456789cfghjmpqrvwx]{4}-[23456789cfghjmpqrvwx]{4}-[23456789cfghjmpqrvwx]{4})', value), 'finding_id_invalid')
        if self.category == 'runtime':
            require(len(self.identities) == 1 and self.identities[0].startswith('R') and not self.clean, 'runtime_invalid')
        else:
            require(all(not v.startswith('R') for v in self.identities), 'advisory_invalid')
            require(self.digest is not None, 'assessment_digest_missing')
            require(not self.clean or (not self.identities and self.freshness == 'confirmed'
                    and self.confidence == 'qualified'), 'clean_unqualified')

    @property
    def observation_hash(self):
        # The logical event ID locates an assessment; only its complete validated
        # normalized body proves an identical replay or fresh requalification.
        value = asdict(self)
        if self.runtime_identity is None:
            value.pop('runtime_identity')  # Preserve hashes of pre-upgrade assessments.
        return fingerprint(value)

    @property
    def incident_key(self):
        # Digests and CVEs intentionally do not split a workload/container backlog.
        rule = self.identities[0] if self.category == 'runtime' else 'image-backlog'
        return fingerprint((self.source, self.category, self.workload.key, rule))
