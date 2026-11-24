"""Bounded, pure normalization of Kubescape node-agent v0.3.310 HTTP alerts.

NOT a network listener, authentication service, or production event sink.
VerifiedSource must come from an authenticated, bound Kubernetes source.
TrustedPod must come from independent Deployment -> ReplicaSet -> Pod inventory.
Neither object may be populated from the HTTP body or untrusted headers.

Pinned sources: kubescape/node-agent v0.3.310 pkg/exporters/http_exporter.go
and armosec/armoapi-go armotypes/runtimeincidents.go (flattened JSON).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

MAX_BODY = 512 * 1024
AGENT_SA = "system:serviceaccount:kubescape:node-agent"
AGENT_VERSION = "v0.3.310"
AGENT_PIN = "v0.3.310@sha256:99152dbefd07e524418f682882ad300cb74821aaa4e42e6477c42a9fa4686e60"
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
RULE = re.compile(r"R[0-9]{4}\Z")
IDENTITY = re.compile(r"[a-zA-Z0-9_.:/-]{1,255}\Z")


class Rejected(ValueError):
    """Static rejection codes only. Never include source payloads."""


def require(value, code):
    if not value:
        raise Rejected(code)


def utc(value):
    require(isinstance(value, str) and len(value) <= 40, "timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Rejected("timestamp_invalid") from None
    require(parsed.tzinfo is not None, "timestamp_invalid")
    return parsed.astimezone(timezone.utc)


def decode(body):
    require(isinstance(body, bytes) and 0 < len(body) <= MAX_BODY, "payload_limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    try:
        result = json.loads(body, object_pairs_hook=unique,
                            parse_constant=lambda _: require(False, "invalid_constant"))
    except (ValueError, UnicodeError, RecursionError):
        raise Rejected("payload_invalid") from None
    require(isinstance(result, dict), "payload_invalid")
    return result


@dataclass(frozen=True)
class VerifiedSource:
    service_account: str
    agent_pod_uid: str
    node: str


@dataclass(frozen=True)
class TrustedPod:
    cluster: str
    namespace: str
    deployment: str
    deployment_uid: str
    pod_name: str
    pod_uid: str
    container: str
    container_id: str
    node: str
    started_at: str
    running_digest: str


@dataclass(frozen=True)
class SafeEvent:
    event_id: str
    rule: str
    observed_at: str
    severity: str
    workload_key: str
    running_digest: str
    digest_relation: str
    node: str
    agent_pod_uid: str
    pod_uid: str
    container_id: str
    deployment_uid: str


def normalize(body: bytes, source: VerifiedSource, pod: TrustedPod, now: datetime) -> SafeEvent:
    """Validate one official HTTP RuntimeAlerts envelope against trusted context.

    A different reported image digest is recorded as DIFFERENT, not silently
    accepted as equivalent, nor mistaken for the independent running image ID.
    The eventual receiver must commit this safe signal before acknowledging.
    """
    require(type(source) is VerifiedSource and type(pod) is TrustedPod,
            "trusted_context_required")
    require(source.service_account == AGENT_SA
            and isinstance(source.agent_pod_uid, str)
            and bool(IDENTITY.fullmatch(source.agent_pod_uid))
            and isinstance(source.node, str)
            and bool(IDENTITY.fullmatch(source.node)), "source_identity_invalid")
    require(source.node == pod.node, "source_node_mismatch")
    require(isinstance(pod.running_digest, str)
            and bool(DIGEST.fullmatch(pod.running_digest)), "trusted_image_invalid")
    require(isinstance(now, datetime) and now.tzinfo is not None, "clock_invalid")
    value = decode(body)
    require(value.get("kind") == "RuntimeAlerts"
            and value.get("apiVersion") == "kubescape.io/v1", "exporter_schema_invalid")
    spec = value.get("spec")
    require(isinstance(spec, dict) and isinstance(spec.get("alerts"), list)
            and len(spec["alerts"]) == 1, "exporter_batch_unsupported")
    alert = spec["alerts"][0]
    require(isinstance(alert, dict), "exporter_alert_invalid")
    # Go's embedded BaseRuntimeAlert and RuntimeAlertK8sDetails are flattened
    # and serialized using lower-camel-case JSON. Stdout JSON is DIFFERENT.
    rule = alert.get("ruleID")
    require(isinstance(rule, str) and bool(RULE.fullmatch(rule)), "rule_invalid")
    require(alert.get("agentVersion") in (AGENT_VERSION, AGENT_PIN),
            "agent_version_invalid")
    severity = alert.get("severity")
    require(type(severity) is int and 0 <= severity <= 10, "severity_invalid")
    at = alert.get("timestamp")
    observed = utc(at)
    require(timedelta(0) <= now.astimezone(timezone.utc) - observed <= timedelta(days=7)
            and utc(pod.started_at) <= observed, "event_time_invalid")
    require(alert.get("namespace") == pod.namespace
            and alert.get("containerName") == pod.container
            and alert.get("podName") == pod.pod_name
            and alert.get("podUID") == pod.pod_uid
            and alert.get("workloadKind") == "Deployment"
            and alert.get("workloadName") == pod.deployment
            and alert.get("workloadUID") == pod.deployment_uid
            and alert.get("nodeName") == pod.node, "workload_identity_invalid")

    def cid(v):
        require(isinstance(v, str) and bool(v), "container_identity_invalid")
        return v.removeprefix("containerd://").removeprefix("docker://")

    require(cid(alert.get("containerID")) == cid(pod.container_id),
            "container_identity_invalid")
    token = alert.get("uniqueID")
    require(isinstance(token, str) and 0 < len(token) <= 128, "event_id_invalid")
    reported = alert.get("imageDigest")
    relation = "absent"
    if reported:
        require(isinstance(reported, str)
                and bool(DIGEST.fullmatch(reported.rsplit("@", 1)[-1])),
                "reported_image_invalid")
        relation = ("equal" if reported.rsplit("@", 1)[-1] == pod.running_digest
                    else "different")
    workload_key = "/".join((pod.cluster, pod.namespace, "Deployment",
                             pod.deployment, pod.container))
    event_id = hashlib.sha256(json.dumps(
        (workload_key, rule, pod.pod_uid, cid(pod.container_id), token),
        separators=(",", ":")).encode()).hexdigest()
    level = ("Critical" if severity >= 9 else "High" if severity >= 7
             else "Medium" if severity >= 4 else "Low")
    return SafeEvent(event_id, rule, at, level, workload_key, pod.running_digest,
                     relation, source.node, source.agent_pod_uid, pod.pod_uid,
                     pod.container_id, pod.deployment_uid)
