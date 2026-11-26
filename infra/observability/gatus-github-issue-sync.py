import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def api_request(url, method="GET", payload=None, headers=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.length == 0:
            return None
        return json.load(response)


def github_headers():
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "User-Agent": "gatus-github-issue-sync",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def result_time(timestamp):
    if not isinstance(timestamp, str) or not timestamp:
        return None
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return value if value.tzinfo is not None else None
    except ValueError:
        return None


def is_recent(timestamp, max_age=None):
    observed = result_time(timestamp)
    max_age = MAX_RESULT_AGE_SECONDS if max_age is None else max_age
    return observed is not None and 0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= max_age


def cluster_recovery_policy():
    defaults = {
        "CLUSTER_SUCCESS_THRESHOLD": 20,
        "CLUSTER_RECOVERY_MIN_SECONDS": 1140,
        "CLUSTER_MAX_SAMPLE_GAP_SECONDS": 90,
        "CLUSTER_MAX_RESULT_AGE_SECONDS": 120,
        "CLUSTER_MAX_HEALTH_AGE_SECONDS": 300,
    }
    try:
        policy = {key: int(os.getenv(key, str(value))) for key, value in defaults.items()}
    except ValueError:
        return None
    # Configuration may strengthen recovery, but must not weaken its floor
    # or allow a missing one-minute sample. Impossible windows fail closed.
    count = policy["CLUSTER_SUCCESS_THRESHOLD"]
    duration = policy["CLUSTER_RECOVERY_MIN_SECONDS"]
    gap = policy["CLUSTER_MAX_SAMPLE_GAP_SECONDS"]
    if (count < 20 or duration < 1140 or not 60 <= gap < 120
            or duration > (count - 1) * gap
            or any(value <= 0 for value in policy.values())):
        return None
    return policy


def cluster_endpoint_is_stably_healthy(endpoint):
    policy = cluster_recovery_policy()
    results = endpoint.get("results")
    if policy is None or not isinstance(results, list) or len(results) < policy["CLUSTER_SUCCESS_THRESHOLD"]:
        return False
    now = datetime.now(timezone.utc)
    observations = []
    for item in results:
        if not isinstance(item, dict) or type(item.get("success")) is not bool:
            return False
        timestamp = result_time(item.get("timestamp"))
        if timestamp is None or timestamp > now:
            return False
        observations.append((timestamp, item["success"]))
    # SQLite and memory adapters can expose opposite orders. Accept either
    # strict chronology, never reorder malformed/shuffled or duplicate data.
    if observations[0][0] > observations[-1][0]:
        observations.reverse()
    if any(a[0] >= b[0] for a, b in zip(observations, observations[1:])):
        return False
    latest = observations[-policy["CLUSTER_SUCCESS_THRESHOLD"]:]
    return (all(success for _, success in latest)
            and 0 <= (now - latest[-1][0]).total_seconds() <= policy["CLUSTER_MAX_RESULT_AGE_SECONDS"]
            and (latest[-1][0] - latest[0][0]).total_seconds() >= policy["CLUSTER_RECOVERY_MIN_SECONDS"]
            and all((b[0] - a[0]).total_seconds() <= policy["CLUSTER_MAX_SAMPLE_GAP_SECONDS"]
                    for a, b in zip(latest, latest[1:])))


def endpoint_is_stably_healthy(endpoint):
    results = endpoint.get("results", [])
    if not isinstance(results, list) or len(results) < SUCCESS_THRESHOLD:
        return False
    if any(not isinstance(item, dict) or result_time(item.get("timestamp")) is None for item in results):
        return False
    # Storage adapters may return either ordering. Only the newest samples can
    # establish recovery; old successes must not hide a newer failure.
    latest = sorted(results, key=lambda item: result_time(item["timestamp"]), reverse=True)[:SUCCESS_THRESHOLD]
    return all(item.get("success") is True and is_recent(item["timestamp"]) for item in latest)


def endpoint_snapshot():
    endpoints = api_request(GATUS_API_URL)
    if isinstance(endpoints, dict):
        endpoints = endpoints.get("endpoints", [])
    if not isinstance(endpoints, list):
        raise RuntimeError("Gatus returned an unexpected endpoint-status response")

    snapshot = {}
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        name = endpoint.get("name")
        group = endpoint.get("group")
        if not isinstance(name, str) or not isinstance(group, str):
            continue
        title = f"alert(gatus): {group}/{name}"
        # Duplicate identities are ambiguous, including three or more copies.
        snapshot[title] = None if title in snapshot else endpoint
    return snapshot


def healthy_alert_titles(snapshot=None):
    snapshot = endpoint_snapshot() if snapshot is None else snapshot
    return {title for title, endpoint in snapshot.items() if endpoint is not None
            and (cluster_endpoint_is_stably_healthy(endpoint) if title == CLUSTER_TITLE
                 else endpoint_is_stably_healthy(endpoint))}


def list_open_gatus_issues(username):
    issues = []
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {
                "creator": username,
                "page": page,
                "per_page": 100,
                "state": "open",
            }
        )
        batch = api_request(f"{GITHUB_REPOSITORY_API_URL}/issues?{query}", headers=github_headers())
        if not isinstance(batch, list):
            raise RuntimeError("GitHub returned an unexpected issue-list response")
        issues.extend(
            issue
            for issue in batch
            if isinstance(issue.get("title"), str)
            and issue["title"].startswith("alert(gatus): ")
            and "pull_request" not in issue
        )
        if len(batch) < 100:
            return issues
        page += 1


def close_issue(issue):
    issue_url = issue.get("url")
    if not isinstance(issue_url, str) or not issue_url:
        raise RuntimeError(f"Issue {issue.get('number')} has no API URL")
    return api_request(
        issue_url,
        method="PATCH",
        payload={"state": "closed", "state_reason": "completed"},
        headers=github_headers(),
    )


CHECKS = {"nodes": "Nodes", "workloads": "Workloads", "pvcs": "PVCs", "flux": "Flux", "longhorn": "Longhorn", "recentJobs": "Recent jobs"}
DIAGNOSTIC_START = "<!-- gatus-cluster-health:start -->"
DIAGNOSTIC_END = "<!-- gatus-cluster-health:end -->"
CLUSTER_TITLE = "alert(gatus): cluster/cluster-health"
INCIDENT_START = "<!-- gatus-incident-diagnostics:start -->"
INCIDENT_END = "<!-- gatus-incident-diagnostics:end -->"

# Public identities and selectors are deliberately static. These are validated
# against the owning manifests and pinned chart renders, never endpoint input.
def workload(namespace, name, selector=None):
    return (namespace, "deployments", name, selector or f"app={name}")


KUBESCAPE_WORKLOADS = tuple(workload("kubescape", name,
    "app.kubernetes.io/name=kubescape-operator,app.kubernetes.io/instance=kubescape,"
    f"app.kubernetes.io/component={name}")
    for name in ("kubescape", "kubevuln", "operator", "storage", "grype-offline-db"))
TARGETS = {
    "alert(gatus): internal/gitea": {
        "workloads": (workload("gitea", "gitea", "app.kubernetes.io/name=gitea,app.kubernetes.io/instance=gitea"),
                      workload("gitea", "gitea-postgres")),
        "flux": (("flux-system", "kustomizations", "apps-gitea"), ("gitea", "helmreleases", "gitea")),
        "guidance": "Check Gitea health and its PostgreSQL readiness; inspect the apps-gitea reconciliation.",
        "recovery": "Gitea /api/healthz returns HTTP 200 and status=pass for two fresh successful Gatus samples.",
    },
    "alert(gatus): internal/homepage": {
        "workloads": (workload("homepage", "homepage"),),
        "flux": (("flux-system", "kustomizations", "apps-homepage"),),
        "guidance": "Check Homepage readiness, Service endpoints and the apps-homepage reconciliation.",
        "recovery": "Homepage returns HTTP 2xx for two fresh successful Gatus samples.",
    },
    "alert(gatus): internal/koito": {
        "workloads": (workload("koito", "koito"),),
        "flux": (("flux-system", "kustomizations", "apps-koito"),),
        "guidance": "Check Koito readiness, its data volume and the apps-koito reconciliation.",
        "recovery": "Koito /apis/web/v1/health returns HTTP 200 for two fresh successful Gatus samples.",
    },
    "alert(gatus): internal/koito-lastfm-bridge": {
        "workloads": (workload("koito", "koito-lastfm-bridge"),),
        "flux": (("flux-system", "kustomizations", "apps-koito"),),
        "guidance": "Inspect bridge readiness and Service endpoints; verify its Koito and Last.fm dependencies locally. "
                    "Never copy authentication responses, configuration secrets or raw logs to the issue.",
        "recovery": "The bridge /api/health returns HTTP 200 for two fresh successful Gatus samples.",
    },
    "alert(gatus): internal/gitea-backup": {
        "workloads": (workload("gitea", "gitea-protection"),),
        "flux": (("flux-system", "kustomizations", "apps-gitea"),),
        "guidance": "Check the protection endpoint, latest gitea-cold-backup Job and remote backup verification locally. "
                    "An available protection Deployment alone does not prove a successful backup.",
        "recovery": "Gitea protection /backupz returns HTTP 200 for two fresh successful Gatus samples.",
    },
    "alert(gatus): cluster/security-cve-reporting": {
        "workloads": (), "flux": (),
        "security_field": "cvePublisher",
        "guidance": "Inspect the latest security incident publisher Job and assessment coverage locally. "
                    "Resolve failed or missing assessments, stale projections and transport errors. "
                    "Review findings exclusively in private security tracking.",
        "recovery": "security-reporting-health.json returns HTTP 200 and cvePublisher=healthy for two fresh successful Gatus samples.",
    },
    "alert(gatus): cluster/kyverno-policy-assurance": {
        "workloads": (), "flux": (),
        "security_field": "kyvernoAssurance",
        "guidance": "Inspect the policy assurance Job and private policy review locally. "
                    "Resolve the reviewed control or exception through an approved GitOps change; do not weaken admission. "
                    "The public alert creation route remains retired.",
        "recovery": "security-reporting-health.json returns HTTP 200 and kyvernoAssurance=healthy for two fresh successful Gatus samples.",
    },
    "alert(gatus): cluster/kubescape-assessments": {
        "workloads": (workload("homepage", "pod-report"),) + KUBESCAPE_WORKLOADS,
        "flux": (("flux-system", "kustomizations", "infrastructure"),
                 ("flux-system", "kustomizations", "kubescape"), ("kubescape", "helmreleases", "kubescape")),
        "guidance": "Inspect pod-report readiness and kubescape-assessment-vulnerability/posture CronJobs. "
                    "Check retained assessment timestamps and database freshness before considering a bounded rerun.",
        "recovery": "kubescape-health.json returns HTTP 200 and status=healthy, vulnerability evidence <=259200s "
                    "and posture evidence <=93600s, for two fresh successful Gatus samples.",
    },
}
REASONS = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError",
           "CreateContainerError", "RunContainerError", "ContainerCreating", "PodInitializing",
           "OOMKilled", "Error", "Completed", "Evicted", "DeadlineExceeded", "Unschedulable",
           "SchedulingGated", "FailedScheduling", "FailedMount", "FailedAttachVolume", "BackOff",
           "Unhealthy", "Failed", "FailedCreate", "ProgressDeadlineExceeded", "ReplicaFailure",
           "InstallFailed", "UpgradeFailed", "ReconciliationFailed", "DependencyNotReady",
           "HealthCheckFailed", "ArtifactFailed", "BuildFailed", "ReconciliationSucceeded",
           "InstallSucceeded", "UpgradeSucceeded", "Succeeded"}


def category(value):
    return value if isinstance(value, str) and value in REASONS else "other"


def counter(value):
    return value if type(value) is int and 0 <= value <= 2147483647 else None


def timestamp(value):
    parsed = result_time(value)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds") if parsed else "unavailable"


class KubernetesReader:
    """Bounded reads; no GitHub headers, redirects, retries or response logging."""
    def __init__(self):
        self.cache = {}
        self.remaining = 40

    def get(self, path):
        if path in self.cache:
            return self.cache[path]
        self.cache[path] = None
        if self.remaining <= 0:
            return None
        self.remaining -= 1
        try:
            root = "/var/run/secrets/kubernetes.io/serviceaccount"
            with open(root + "/token") as token_file:
                token = token_file.read().strip()
            context = ssl.create_default_context(cafile=root + "/ca.crt")
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=context))
            request = urllib.request.Request("https://kubernetes.default.svc" + path,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
            with opener.open(request, timeout=5) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) <= 2 * 1024 * 1024:
                value = json.loads(raw)
                if isinstance(value, dict):
                    self.cache[path] = value
        except (OSError, ValueError):
            pass  # Do not print API errors, payloads or credentials.
        return self.cache[path]


def list_items(value):
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        return None, "unavailable"
    return value["items"], "partial" if value.get("metadata", {}).get("continue") else "collected"


def workload_observation(reader, target):
    namespace, resource, name, selector = target
    identity = f"{namespace}/{resource}/{name}"
    obj = reader.get(f"/apis/apps/v1/namespaces/{namespace}/{resource}/{name}")
    if not isinstance(obj, dict):
        return [f"- `{identity}`: workload unavailable."], {"unknown"}
    status, spec = obj.get("status", {}), obj.get("spec", {})
    desired = counter(spec.get("replicas"))
    available = counter(status.get("availableReplicas" if resource == "deployments" else "readyReplicas", 0))
    generation = obj.get("metadata", {}).get("generation")
    fresh = counter(generation) is not None and status.get("observedGeneration") == generation
    flags = set()
    if not fresh or desired is None or available is None:
        flags.add("unknown")
    elif available < desired or desired == 0:
        flags.add("availability")
    lines = [f"- `{identity}`: desired={desired}, available={available}; "
             f"controller={'current' if fresh else 'stale/unknown'}."]
    query = urllib.parse.urlencode({"labelSelector": selector, "limit": 20})
    pods, state = list_items(reader.get(f"/api/v1/namespaces/{namespace}/pods?{query}"))
    if pods is None:
        return lines + ["  Pods: unavailable."], flags | {"unknown"}
    if state != "collected" or not pods:
        flags.add("unknown")
    labels = dict(item.split("=", 1) for item in selector.split(","))
    ready, containers, restarts, pending, terminating, matched = 0, 0, 0, 0, 0, 0
    reasons, uids = set(), set()
    for pod in pods[:20]:
        metadata, pod_status = pod.get("metadata", {}), pod.get("status", {})
        if not all(metadata.get("labels", {}).get(k) == v for k, v in labels.items()):
            flags.add("unknown")
            continue
        matched += 1
        uids.add(metadata.get("uid"))
        pending += pod_status.get("phase") == "Pending"
        terminating += bool(metadata.get("deletionTimestamp"))
        if pod_status.get("reason"):
            reasons.add(category(pod_status["reason"]))
        for condition in pod_status.get("conditions", []):
            if condition.get("type") == "PodScheduled" and condition.get("status") == "False":
                reasons.add(category(condition.get("reason")))
        statuses = pod_status.get("initContainerStatuses", []) + pod_status.get("containerStatuses", [])
        if not statuses or not pod_status.get("containerStatuses"):
            flags.add("unknown")
        for container in statuses:
            containers += 1
            ready += container.get("ready") is True
            count = counter(container.get("restartCount"))
            if count is None:
                flags.add("unknown")
            else:
                restarts += count
            for states in (container.get("state", {}), container.get("lastState", {})):
                for key in ("waiting", "terminated"):
                    if states.get(key, {}).get("reason"):
                        reasons.add(category(states[key]["reason"]))
    if ready < containers or pending or terminating:
        flags.add("pods")
    flags |= reasons
    lines.append(f"  Pods={matched} ({state}; max 20), pending={pending}, terminating={terminating}; "
                 f"containers ready={ready}/{containers}, restarts={restarts}; current/last reasons={', '.join(sorted(reasons)) or 'none observed'}.")
    query = urllib.parse.urlencode({"fieldSelector": "type=Warning,involvedObject.kind=Pod", "limit": 100})
    events, state = list_items(reader.get(f"/api/v1/namespaces/{namespace}/events?{query}"))
    warnings = set()
    for event in (events or [])[:100]:
        involved = event.get("involvedObject", {})
        observed = (event.get("series", {}).get("lastObservedTime") or event.get("lastTimestamp")
                    or event.get("eventTime") or event.get("metadata", {}).get("creationTimestamp"))
        if (event.get("type") == "Warning" and involved.get("uid") in (uids - {None})
                and involved.get("kind") == "Pod" and is_recent(observed, 1800)):
            warnings.add(category(event.get("reason")))
    lines.append(f"  Warning categories (last 30m, max 100 namespace events, {state}): "
                 f"{', '.join(sorted(warnings)) or 'none observed' if events is not None else 'unknown'}.")
    return lines, flags | warnings


def flux_observation(reader, target):
    namespace, resource, name = target
    group = "kustomize.toolkit.fluxcd.io/v1" if resource == "kustomizations" else "helm.toolkit.fluxcd.io/v2"
    obj = reader.get(f"/apis/{group}/namespaces/{namespace}/{resource}/{name}")
    state, reason = "unavailable", "other"
    if isinstance(obj, dict):
        generation = obj.get("metadata", {}).get("generation")
        for condition in obj.get("status", {}).get("conditions", []):
            if condition.get("type") == "Ready":
                state = {"True": "True", "False": "False", "Unknown": "Unknown"}.get(condition.get("status"), "Unknown")
                if (counter(generation) is None or condition.get("observedGeneration") != generation
                        or obj.get("spec", {}).get("suspend") is True):
                    state = "stale/unknown"
                reason = category(condition.get("reason"))
    return f"- Flux `{namespace}/{resource}/{name}`: Ready={state}; reason={reason}.", state


def incident_block(endpoint, target, reader, security_health=None):
    lines = [INCIDENT_START, "", "**Observed:**",
             "Snapshot collected at: " + datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "Freshness below is evaluated at collection; this snapshot is not live telemetry."]
    results = endpoint.get("results", []) if isinstance(endpoint, dict) else []
    valid = [item for item in results if isinstance(item, dict) and result_time(item.get("timestamp"))] if isinstance(results, list) else []
    latest = max(valid, key=lambda item: result_time(item["timestamp"])) if valid else {}
    state = "unknown"
    if type(latest.get("success")) is bool:
        state = "success" if latest["success"] else "failure"
    lines += [f"- Gatus latest result: {state}; freshness={'fresh' if is_recent(latest.get('timestamp')) else 'stale/unavailable'}.",
              "- Gatus sample at: " + timestamp(latest.get("timestamp"))]
    flags = set()
    for item in target["workloads"]:
        try:
            observed, signals = workload_observation(reader, item)
            lines.extend(observed)
            flags |= signals
        except Exception:
            lines.append(f"- `{item[0]}/{item[1]}/{item[2]}`: observation unavailable.")
            flags.add("unknown")
    for item in target["flux"]:
        try:
            observed, ready = flux_observation(reader, item)
            lines.append(observed)
            if ready == "False":
                flags.add("flux")
            elif ready != "True":
                flags.add("unknown")
        except Exception:
            lines.append(f"- Flux `{item[0]}/{item[1]}/{item[2]}`: unavailable.")
            flags.add("unknown")
    if "security_field" in target:
        # Only a single allowlisted operational state from the existing
        # public-safe health report. Never read private ConfigMaps,
        # policy objects, issue bodies, Job logs or arbitrary fields.
        field = target["security_field"]
        if not isinstance(security_health, dict) or not is_recent(security_health.get("observedAt"), 300):
            lines.append("- Security reporting health: stale/unavailable; state unknown.")
            flags.add("unknown")
        else:
            value = security_health.get(field)
            state = value if isinstance(value, str) and value in ("healthy", "failed", "stale", "unknown") else "unknown"
            lines.append("- Security reporting observation at: " + timestamp(security_health.get("observedAt")))
            lines.append(f"- Security reporting {field}: {state}.")
            if state in ("failed", "stale"):
                flags.add("security_reporting")
            elif state == "unknown":
                flags.add("unknown")
    if target is TARGETS["alert(gatus): cluster/kubescape-assessments"]:
        try:
            health = api_request(ASSESSMENT_HEALTH_URL)
            if not isinstance(health, dict) or not is_recent(health.get("observedAt"), 300):
                raise ValueError("Unavailable assessment telemetry")
            lines.append("- Assessment report at: " + timestamp(health.get("observedAt")))
            state = health.get("status")
            state = state if isinstance(state, str) and state in {"healthy", "stale", "unknown"} else "unknown"
            lines.append(f"- Assessment report: status={state}; report fresh at collection.")
            if state == "unknown":
                flags.add("unknown")
            for mode, maximum in (("vulnerability", 259200), ("posture", 93600), ("database", 1209600)):
                sample = health.get(mode, {})
                age = counter(sample.get("evidenceAgeSeconds"))
                freshness = "unknown" if age is None else "fresh" if age <= maximum else "stale"
                attempt = sample.get("lastAttemptState")
                attempt = attempt if isinstance(attempt, str) and attempt in {"healthy", "stale", "failed", "unknown"} else "unknown"
                lines.append(f"- Assessment {mode}: evidence={freshness}; last attempt={attempt}.")
                if freshness == "stale":
                    flags.add("assessment")
                elif freshness == "unknown":
                    flags.add("unknown")
                if attempt == "failed":
                    flags.add("assessment_attempt")
        except Exception:
            lines.append("- Assessment report: stale/unavailable; freshness unknown.")
            flags.add("unknown")
    causes = []
    for signal, hypothesis in (("OOMKilled", "A container may have exceeded its memory limit."),
            ("Unschedulable", "Scheduling constraints or capacity may be preventing placement."),
            ("FailedScheduling", "Scheduling constraints or capacity may be preventing placement."),
            ("FailedMount", "A volume mount may be blocking startup."),
            ("ImagePullBackOff", "An image pull may be blocking startup."),
            ("CrashLoopBackOff", "A repeated container startup failure may affect availability."),
            ("flux", "A failed reconciliation may be preventing the desired rollout."),
            ("availability", "Insufficient available replicas may explain the failed endpoint check."),
            ("assessment", "Assessment or database evidence may have exceeded its freshness limit."),
            ("assessment_attempt", "An assessment attempt failed; retained successful evidence can still be fresh."),
            ("security_reporting", "The monitored security reporting stage is failed or stale; inspect its private operational evidence.")):
        if signal in flags and hypothesis not in causes:
            causes.append(hypothesis)
    if "unknown" in flags:
        causes.append("Missing or stale observations limit diagnosis; they do not establish health.")
    lines += ["", "**Possible cause:**", "Hypotheses, not confirmed root causes."]
    lines += ["- " + cause for cause in causes] or ["- No specific cause established. Check Service routing and application dependencies."]
    lines += ["", "**Next investigation:**", target["guidance"]]
    for namespace, resource, name, _ in target["workloads"]:
        lines.append(f"- `kubectl -n {namespace} get {resource} {name}`; inspect readiness and events locally.")
    lines += ["", "**Recovery condition:**", target["recovery"],
              "Kubernetes diagnostics are context; Gatus remains the service-health authority.", "", INCIDENT_END]
    return "\n".join(lines)


def managed_body(body, block, start=INCIDENT_START, end=INCIDENT_END):
    # Incomplete/ambiguous markers can include user text. Leave them alone.
    if body.count(start) != body.count(end) or body.count(start) > 1:
        return body
    pattern = re.escape(start) + r".*?" + re.escape(end)
    match = re.search(pattern, body, flags=re.DOTALL)
    def material(text):
        return re.sub(r"^(?:Snapshot collected at:|- Gatus sample at:|- Assessment report at:).*\n?", "", text, flags=re.MULTILINE)
    if match:
        if material(match[0]) == material(block):
            return body
        return body[:match.start()] + block + body[match.end():]
    if start in body or end in body:
        return body
    return body + ("\n\n" if body else "") + block + "\n"


def cluster_is_healthy(health):
    policy = cluster_recovery_policy()
    return (policy is not None and isinstance(health, dict) and health.get("status") == "healthy"
            and is_recent(health.get("observedAt"), policy["CLUSTER_MAX_HEALTH_AGE_SECONDS"])
            and isinstance(health.get("checks"), dict)
            and all(health["checks"].get(key) == "ok" for key in CHECKS)
            and isinstance(health.get("failures"), dict)
            and all(isinstance(entries, list) and not entries for entries in health["failures"].values())
            and isinstance(health.get("collectionErrors"), dict)
            and not health["collectionErrors"])


def diagnostic_body(body, health):
    checks = health.get("checks", {})
    failures = health.get("failures", {})
    status = health.get("status") if health.get("status") in {"healthy", "unhealthy"} else "unknown"
    policy = cluster_recovery_policy()
    fresh = policy is not None and is_recent(health.get("observedAt"), policy["CLUSTER_MAX_HEALTH_AGE_SECONDS"])
    lines = [DIAGNOSTIC_START, "", "**Observed:**", f"Cluster health: {status}",
             f"Collector freshness at sync: {'fresh' if fresh else 'stale/unknown'}. "
             "Last failure snapshot is retained during pending recovery.", ""]
    for key, label in CHECKS.items():
        state = {"ok": "OK", "failed": "FAILED"}.get(checks.get(key), "UNKNOWN")
        lines.append(f"{label}: {state}")
        entries = failures.get(key, []) if isinstance(failures, dict) else []
        if state == "FAILED" and isinstance(entries, list):
            for entry in sorted(set(item for item in entries if isinstance(item, str)))[:10]:
                # Only identities, numeric counts and fixed states from the
                # generator. Never forward arbitrary messages or HTTP bodies.
                identity = r"[a-z0-9][a-z0-9.-]*/(?:HelmRelease/|Kustomization/)?[a-z0-9][a-z0-9.-]*"
                suffix = {"workloads": r" desired=[0-9]{1,10} available=[0-9]{1,10}",
                          "flux": r" Ready=(?:True|False|Unknown)",
                          "pvcs": r" phase=(?:Pending|Bound|Lost)",
                          "recentJobs": r" failed-attempts=[0-9]{1,10}"}.get(key)
                if suffix and len(entry) <= 220 and re.fullmatch(identity + suffix, entry):
                    lines.append(f"- `{entry}`")
    lines += ["", "**Possible cause:**", "Failed categories above may explain the cluster-health failure; root cause is unconfirmed.",
              "", "**Next investigation:**", "Inspect the failed category locally with `kubectl get` and `flux get kustomizations -A`; "
              "check pod-report collection errors before treating unknown checks as failures.",
              "", "**Recovery condition:**", "At least 20 consecutive successful Gatus samples over at least 19 minutes, "
              "with no sample gap above 90s and the newest sample <=120s old, plus healthy collector evidence <=300s old "
              "for all six categories and no collection errors (or a stronger configured recovery policy).",
              "", DIAGNOSTIC_END]
    # Preserve the last failure snapshot while recovery is pending. Update
    # its timestamp only when a category or failure identity changes.
    lines.insert(3, "Snapshot collected at: " + timestamp(health.get("observedAt")))
    return managed_body(body, "\n".join(lines), DIAGNOSTIC_START, DIAGNOSTIC_END)


def main():
    snapshot = endpoint_snapshot()
    healthy_titles = healthy_alert_titles(snapshot)
    current_user = api_request(f"{GITHUB_API_ROOT}/user", headers=github_headers())
    username = current_user.get("login") if isinstance(current_user, dict) else None
    if not isinstance(username, str) or not username:
        raise RuntimeError("GitHub token did not return a username")
    issues = list_open_gatus_issues(username)
    health = None
    if any(issue["title"] == CLUSTER_TITLE for issue in issues):
        try:
            health = api_request(CLUSTER_HEALTH_URL)
            if not isinstance(health, dict) or not isinstance(health.get("checks"), dict) or not is_recent(health.get("observedAt")):
                health = None
        except (OSError, ValueError):
            # Missing evidence keeps the cluster incident open. Other endpoint
            # recoveries can still proceed using their own Gatus samples.
            health = None
        if not cluster_is_healthy(health):
            healthy_titles.discard(CLUSTER_TITLE)

    closed, updated, failed = [], [], []
    reader = KubernetesReader()
    diagnostics, seen = {}, set()
    security_health = None
    if any(issue["title"] in TARGETS
           and "security_field" in TARGETS[issue["title"]]
           and issue["title"] not in healthy_titles for issue in issues):
        try:
            security_health = api_request(SECURITY_REPORTING_HEALTH_URL)
        except (OSError, ValueError):
            pass
    for issue in issues:
        if issue.get("url") in seen:
            continue
        seen.add(issue.get("url"))
        try:
            title = issue["title"]
            if title in healthy_titles:
                if not DRY_RUN:
                    close_issue(issue)
                closed.append({"number": issue.get("number"), "title": title})
                continue
            body = issue.get("body") or ""
            if title == CLUSTER_TITLE and health is not None and not cluster_is_healthy(health):
                body = diagnostic_body(body, health)
            elif title in TARGETS:
                if title not in diagnostics:
                    diagnostics[title] = incident_block(snapshot.get(title), TARGETS[title], reader, security_health)
                body = managed_body(body, diagnostics[title])
            if body != (issue.get("body") or ""):
                if not DRY_RUN:
                    # Re-read immediately before PATCH to preserve edits made
                    # since listing. Never touch comments or other fields.
                    current = api_request(issue["url"], headers=github_headers())
                    if not isinstance(current, dict) or current.get("state") != "open":
                        continue
                    current_body = current.get("body") or ""
                    if title == CLUSTER_TITLE:
                        body = diagnostic_body(current_body, health)
                    else:
                        body = managed_body(current_body, diagnostics[title])
                    if body == current_body:
                        continue
                    api_request(issue["url"], method="PATCH", payload={"body": body}, headers=github_headers())
                updated.append(issue.get("number"))
        except Exception:
            # A malformed observation or failed enrichment/PATCH must not
            # stop another endpoint's recovery. Never log exception payloads.
            failed.append(issue.get("number"))
    print(json.dumps({"closed" if not DRY_RUN else "wouldClose": closed,
                      "updated" if not DRY_RUN else "wouldUpdate": updated,
                      "failed": failed, "healthyEndpointCount": len(healthy_titles), "mode": "dry-run" if DRY_RUN else "live"}))
    return 1 if failed else 0


if __name__ == "__main__":
    GATUS_API_URL = os.getenv(
        "GATUS_API_URL",
        "http://gatus.monitoring.svc.cluster.local/api/v1/endpoints/statuses",
    )
    CLUSTER_HEALTH_URL = os.getenv("CLUSTER_HEALTH_URL", "http://pod-report.homepage.svc.cluster.local:80/health.json")
    ASSESSMENT_HEALTH_URL = "http://pod-report.homepage.svc.cluster.local:80/kubescape-health.json"
    SECURITY_REPORTING_HEALTH_URL = "http://pod-report.homepage.svc.cluster.local:80/security-reporting-health.json"
    GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
    GITHUB_API_ROOT = os.getenv("GITHUB_API_ROOT", "https://api.github.com")
    GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "RovxBot/homelab-infra")
    GITHUB_REPOSITORY_API_URL = f"{GITHUB_API_ROOT}/repos/{GITHUB_REPOSITORY}"
    DRY_RUN = env_bool("DRY_RUN", False)
    MAX_RESULT_AGE_SECONDS = int(os.getenv("MAX_RESULT_AGE_SECONDS", "900"))
    SUCCESS_THRESHOLD = int(os.getenv("SUCCESS_THRESHOLD", "2"))
    sys.exit(main())
