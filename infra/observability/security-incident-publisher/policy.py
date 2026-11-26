"""Bounded private policy reporting; no raw admission payloads or public destinations."""
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler

from publisher import PrivateGitea, DurablePublisher, NoRedirect, WriteBudgetDeferred, marker, revision, require

IGNORED_SCOPE_KINDS = {"Pod", "ReplicaSet", "Job"}
IGNORED_POLICIES = {"platform-flux-guardrails"}
EXCLUDE_NAMESPACE_RE = re.compile(r"^(flux-system|kube-.*|kyverno|sops)$")
EXCEPTIONS = {"chart-managed-workloads", "gpu-operator-workloads", "home-assistant",
              "mutable-image-tags", "privileged-namespaces"}

WARNING_DAYS = 14
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}")

def parse_result_time(result, fallback):
    timestamp = result.get("timestamp")
    if isinstance(timestamp, dict):
        seconds = timestamp.get("seconds")
        if seconds is not None:
            return datetime.fromtimestamp(int(seconds), timezone.utc)
    for candidate in (
        result.get("timestamp"),
        result.get("timestamp", {}).get("nanos") if isinstance(result.get("timestamp"), dict) else None,
        fallback,
    ):
        if isinstance(candidate, str) and candidate:
            normalized = candidate.replace("Z", "+00:00")
            try:
                value = datetime.fromisoformat(normalized)
                return value if value.tzinfo is not None else None
            except ValueError:
                pass
    return None

# BEGIN GENERATED POLICY IDENTITIES (render-report-identities.py)
POLICY_IDENTITIES = {'gitea-job-security-bound-job-environment': ('gitea-job-security', 'bound-job-environment'),
 'gitea-job-security-bounded-sanitizer-identity': ('gitea-job-security',
                                                   'bounded-sanitizer-identity'),
 'gitea-job-security-drop-all-and-only-allow-ptrace': ('gitea-job-security',
                                                       'drop-all-and-only-allow-ptrace'),
 'gitea-job-security-no-extra-job-credentials': ('gitea-job-security', 'no-extra-job-credentials'),
 'gitea-job-security-one-job-container': ('gitea-job-security', 'one-job-container'),
 'gitea-job-security-ordinary-job-identity': ('gitea-job-security', 'ordinary-job-identity'),
 'gitea-job-security-ptrace-only-in-sanitizer-jobs': ('gitea-job-security',
                                                      'ptrace-only-in-sanitizer-jobs'),
 'gitea-job-security-restricted-with-sanitizer-capability': ('gitea-job-security',
                                                             'restricted-with-sanitizer-capability'),
 'gitea-jobs/gitea-job-security-bound-job-environment': ('gitea-job-security',
                                                         'bound-job-environment'),
 'gitea-jobs/gitea-job-security-bounded-sanitizer-identity': ('gitea-job-security',
                                                              'bounded-sanitizer-identity'),
 'gitea-jobs/gitea-job-security-drop-all-and-only-allow-ptrace': ('gitea-job-security',
                                                                  'drop-all-and-only-allow-ptrace'),
 'gitea-jobs/gitea-job-security-no-extra-job-credentials': ('gitea-job-security',
                                                            'no-extra-job-credentials'),
 'gitea-jobs/gitea-job-security-one-job-container': ('gitea-job-security', 'one-job-container'),
 'gitea-jobs/gitea-job-security-ordinary-job-identity': ('gitea-job-security',
                                                         'ordinary-job-identity'),
 'gitea-jobs/gitea-job-security-ptrace-only-in-sanitizer-jobs': ('gitea-job-security',
                                                                 'ptrace-only-in-sanitizer-jobs'),
 'gitea-jobs/gitea-job-security-restricted-with-sanitizer-capability': ('gitea-job-security',
                                                                        'restricted-with-sanitizer-capability'),
 'longhorn-csi-registrar-lifecycle': ('longhorn-csi-registrar-lifecycle',
                                      'use-native-registrar-shutdown'),
 'platform-flux-guardrails': ('platform-flux-guardrails',
                              'audit-manual-edits-to-flux-managed-resources'),
 'platform-namespace-standards': ('platform-namespace-standards', 'require-psa-and-owner-labels'),
 'platform-pod-standards-disallow-host-network': ('platform-pod-standards',
                                                  'disallow-host-network'),
 'platform-pod-standards-disallow-latest-tags': ('platform-pod-standards', 'disallow-latest-tags'),
 'platform-pod-standards-require-digest-pinned-images': ('platform-pod-standards',
                                                         'require-digest-pinned-images'),
 'platform-pod-standards-require-owner-label': ('platform-pod-standards', 'require-owner-label'),
 'platform-pod-standards-require-probes': ('platform-pod-standards', 'require-probes'),
 'platform-pod-standards-require-requests-and-limits': ('platform-pod-standards',
                                                        'require-requests-and-limits'),
 'platform-pod-standards-require-runtime-hardening': ('platform-pod-standards',
                                                      'require-runtime-hardening'),
 'wowdecomp-ci-jobs/wowdecomp-linux-security-bound-job-environment': ('wowdecomp-linux-security',
                                                                      'bound-job-environment'),
 'wowdecomp-ci-jobs/wowdecomp-linux-security-bounded-toolchain-capabilities': ('wowdecomp-linux-security',
                                                                               'bounded-toolchain-capabilities'),
 'wowdecomp-ci-jobs/wowdecomp-linux-security-no-extra-job-credentials': ('wowdecomp-linux-security',
                                                                         'no-extra-job-credentials'),
 'wowdecomp-ci-jobs/wowdecomp-linux-security-no-mounted-registry-credential': ('wowdecomp-linux-security',
                                                                               'no-mounted-registry-credential'),
 'wowdecomp-ci-jobs/wowdecomp-linux-security-no-registry-credential-in-environment': ('wowdecomp-linux-security',
                                                                                      'no-registry-credential-in-environment'),
 'wowdecomp-ci-jobs/wowdecomp-linux-security-one-job-container': ('wowdecomp-linux-security',
                                                                  'one-job-container'),
 'wowdecomp-ci-jobs/wowdecomp-linux-security-restricted-with-sanitizer-capability': ('wowdecomp-linux-security',
                                                                                     'restricted-with-sanitizer-capability'),
 'wowdecomp-linux-security-bound-job-environment': ('wowdecomp-linux-security',
                                                    'bound-job-environment'),
 'wowdecomp-linux-security-bounded-toolchain-capabilities': ('wowdecomp-linux-security',
                                                             'bounded-toolchain-capabilities'),
 'wowdecomp-linux-security-no-extra-job-credentials': ('wowdecomp-linux-security',
                                                       'no-extra-job-credentials'),
 'wowdecomp-linux-security-no-mounted-registry-credential': ('wowdecomp-linux-security',
                                                             'no-mounted-registry-credential'),
 'wowdecomp-linux-security-no-registry-credential-in-environment': ('wowdecomp-linux-security',
                                                                    'no-registry-credential-in-environment'),
 'wowdecomp-linux-security-one-job-container': ('wowdecomp-linux-security', 'one-job-container'),
 'wowdecomp-linux-security-restricted-with-sanitizer-capability': ('wowdecomp-linux-security',
                                                                   'restricted-with-sanitizer-capability')}
# END GENERATED POLICY IDENTITIES


def canonical_identity(result):
    policy = result.get("policy", "")
    rule = result.get("rule") or ""
    source = result.get("source", "")
    mapped = POLICY_IDENTITIES.get(policy)
    # This independently deployed CEL policy is not a migration alias.
    if policy == "kubescape-runtime-boundary" and source in {"KyvernoValidatingPolicy", "kyverno"}:
        if rule in {"", "validation", "exception", policy}:
            return policy, "validation"
        raise ValueError("Unrecognized runtime boundary rule")
    if source == "KyvernoGeneratingPolicy" and policy == "platform-namespace-bootstrap":
        if rule not in {"", "generation", "exception", policy}:
            raise ValueError("Unrecognized generator report rule: " + rule)
        return policy, "generation"
    if mapped and (source != "kyverno" or rule in {"", "validation", "mutation", "evaluation", "exception"}):
        allowed = {"", "mutation", "evaluation", "exception", mapped[1]} if policy == "longhorn-csi-registrar-lifecycle" else {"", "validation", "exception", mapped[1]}
        if rule not in allowed:
            raise ValueError("Unrecognized CEL validation rule: " + rule)
        return mapped
    if source not in {"", "kyverno"}:
        raise ValueError("Unmapped report identity: " + source + "/" + policy)
    if not policy or not rule:
        raise ValueError("Report has no stable policy/rule identity")
    return policy, rule


def load_observations(list_kubernetes_resource, cutoff, now):
    failures, passes, unknown = {}, {}, set()
    for path in (
        "/apis/wgpolicyk8s.io/v1alpha2/clusterpolicyreports",
        "/apis/wgpolicyk8s.io/v1alpha2/policyreports",
    ):
        response = list_kubernetes_resource(path)
        for report in response["items"]:
            meta, scope = report.get("metadata", {}), report.get("scope") or {}
            if scope.get("kind") in IGNORED_SCOPE_KINDS:
                continue
            for result in report.get("results", []):
                policy, rule = canonical_identity(result)
                if policy in IGNORED_POLICIES:
                    continue
                resources = result.get("resources") or result.get("subjects") or [scope]
                for resource in resources:
                    kind = resource.get("kind") or scope.get("kind")
                    name = resource.get("name") or scope.get("name")
                    namespace = resource.get("namespace") or scope.get("namespace") or meta.get("namespace") or "cluster"
                    if kind == "Namespace":
                        namespace = "cluster"
                    if not kind or not name:
                        raise ValueError("Report result has no resource identity")
                    if kind in IGNORED_SCOPE_KINDS:
                        continue
                    if EXCLUDE_NAMESPACE_RE and (EXCLUDE_NAMESPACE_RE.match(namespace) or kind == "Namespace" and EXCLUDE_NAMESPACE_RE.match(name)):
                        continue
                    key = (policy, rule, kind, namespace, name)
                    status = result.get("result")
                    observed = parse_result_time(result, meta.get("creationTimestamp") if status == "fail" else None)
                    if status == "fail":
                        failure = dict(persistent=observed is not None and observed <= cutoff and observed <= now)
                        # Legacy/CEL overlap is one finding. Any failure blocks closure,
                        # including findings younger than the incident threshold.
                        if key not in failures or failure["persistent"]:
                            failures[key] = failure
                    elif status == "pass" and observed:
                        passes[key] = max(observed, passes.get(key, observed))
                    elif status == "skip":
                        # An exception is not proof of remediation. Preserve history.
                        unknown.add(key)
                    elif status != "pass":
                        unknown.add(key)
    return failures, passes, unknown




def stable_item(category, identity, details):
    require(all(isinstance(value, str) and IDENTITY.fullmatch(value) for value in identity))
    key = hashlib.sha256(json.dumps(["kyverno", category, *identity], separators=(",", ":")).encode()).hexdigest()
    return {"key": key, "category": category, "identity": list(identity), "details": details}


def policy_items(list_resource, now):
    failures, _, _ = load_observations(list_resource, now - timedelta(hours=24), now)
    result = {}
    for key, failure in failures.items():
        if not failure["persistent"]:
            continue
        # The generator family cannot close a historical rule-specific incident.
        if key[0] == "platform-namespace-bootstrap":
            key = (key[0], "bootstrap", *key[2:])
        item = stable_item("violation", key, {"status": "Persistent failure"})
        result[item["key"]] = item
    return list(result.values())


def exception_items(list_resource, today):
    items = {}
    seen = set()
    for obj in list_resource("/apis/policies.kyverno.io/v1/policyexceptions")["items"]:
        meta = obj["metadata"]
        ns, name = meta["namespace"], meta["name"]
        identity = (ns, name)
        require(identity not in seen)
        seen.add(identity)
        annotations, labels = meta.get("annotations", {}), meta.get("labels", {})
        expires = annotations.get("kyverno.cooked.beer/expires-at")
        try:
            require(isinstance(expires, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", expires))
            days = (date.fromisoformat(expires) - today).days
            require(bool(labels.get("homelab.cooked.beer/owner")) and bool(annotations.get("kyverno.cooked.beer/reason")))
            require(annotations.get("github.cooked.beer/issue-create") == "true"
                    or bool(re.fullmatch(r"https://github[.]com/[^/]+/[^/]+/issues/[0-9]+",
                                         annotations.get("github.cooked.beer/issue-url", ""))))
            status = "Expired" if days < 0 else "Review due" if days <= WARNING_DAYS else "Current"
            details = {"status": status, "expires": expires}
        except (ValueError, TypeError):
            details = {"status": "Invalid accountability metadata"}
        if ns != "kyverno" or name not in EXCEPTIONS:
            details["inventory"] = "Unexpected exception"
        if details["status"] != "Current" or "inventory" in details:
            item = stable_item("exception", identity, details)
            items[item["key"]] = item
    for name in EXCEPTIONS - {name for ns, name in seen if ns == "kyverno"}:
        item = stable_item("exception", ("kyverno", name), {"status": "Expected exception missing"})
        items[item["key"]] = item
    return list(items.values())


# Reviewed guidance is code-owned, not report messages or annotation prose.
EXCEPTION_SOURCE = "infra/kyverno-policies/exceptions/exceptions.yaml"
EXCEPTION_GUIDANCE = {
    "chart-managed-workloads": "Review the chart values for owner labels, resource requests/limits and probes. Narrow the exception to only the settings the chart cannot support.",
    "gpu-operator-workloads": "Review the GPU Operator and device-discovery hardware-access requirements. Preserve necessary host access; narrow unrelated exceptions only after device and workload tests.",
    "home-assistant": "Review the host networking and runtime-hardening requirements for LAN discovery. Test discovery and device connectivity before removing an exception.",
    "mutable-image-tags": "Check whether affected workloads can use immutable digest pins and reviewed image updates instead of mutable tags.",
    "privileged-namespaces": "Review the storage, networking and game-workload host access requirements individually. Do not remove necessary privileges without compatibility tests.",
}

RULE_GUIDANCE = {
    ("platform-pod-standards", "require-owner-label"):
        ("infra/kyverno-policies/policies/workloads.yaml", "Set the required owner label on the affected workload/Pod template in GitOps; check the rendered labels."),
    ("platform-pod-standards", "require-requests-and-limits"):
        ("infra/kyverno-policies/policies/workloads.yaml", "Set CPU and memory requests and limits appropriate to measured application use. Check scheduling headroom and avoid arbitrary low limits."),
    ("platform-pod-standards", "require-runtime-hardening"):
        ("infra/kyverno-policies/policies/workloads.yaml", "Review the effective security context against the exact CEL rule; test compatible least-privilege settings, capabilities and seccomp before deploying."),
    ("platform-pod-standards", "disallow-host-network"):
        ("infra/kyverno-policies/policies/workloads.yaml", "Review whether hostNetwork is required. Prefer normal Pod networking when supported; preserve justified host access through a reviewed narrow exception."),
    ("platform-pod-standards", "require-digest-pinned-images"):
        ("infra/kyverno-policies/policies/workloads.yaml", "Pin the supported image to a verified immutable digest and use the reviewed dependency update workflow."),
    ("platform-pod-standards", "disallow-latest-tags"):
        ("infra/kyverno-policies/policies/workloads.yaml", "Replace the mutable latest tag with a reviewed version/digest, or review an explicitly justified narrow exception."),
    ("platform-pod-standards", "require-probes"):
        ("infra/kyverno-policies/policies/workloads.yaml", "Add application-appropriate health probes as required by the exact rule; test startup and failure behavior."),
    ("platform-namespace-standards", "require-psa-and-owner-labels"):
        ("infra/kyverno-policies/policies/namespace.yaml", "Review namespace ownership and PSA labels against the workload's reviewed security requirements. Do not lower PSA merely to clear the report."),
    ("kubescape-runtime-boundary", "validation"):
        ("infra/kubescape/runtime/admission.yaml", "Compare the runtime agent specification with its reviewed admission boundary and pinned chart contract. Restore the intended scope or submit a reviewed compatibility change."),
}


def render_policy(item):
    category, identity = item["category"], item["identity"]
    title = "Kyverno " + category + ": " + "/".join(identity)
    details = item["details"]
    lines = [marker(item["key"]), "", "## Summary",
             "- Status: " + details["status"]]
    if category == "exception":
        namespace, name = identity
        lines += ["- Exception: " + namespace + "/" + name]
        if "expires" in details:
            lines.append("- Review deadline: " + details["expires"])
        if "inventory" in details:
            lines.append("- Inventory: " + details["inventory"])
        reviewed = namespace == "kyverno" and name in EXCEPTION_GUIDANCE
        lines += ["- GitOps source: " + ("`" + EXCEPTION_SOURCE + "`" if reviewed else
                                         "not established; locate the actual managing manifest"),
                  "", "## Required action"]
        status = details["status"]
        if status in ("Review due", "Expired"):
            lines += ["This is an exception review, not proof of an active policy violation.",
                      "Determine whether the exception is still necessary before its deadline. "
                      "If no longer necessary, remediate the workload and remove the exception through GitOps. "
                      "If still necessary, document the reason, owner, narrow scope and approved new deadline in a reviewed PR."]
        elif status == "Invalid accountability metadata":
            lines += ["Verify the required owner, reason, expiry and approved tracking metadata in the managed exception. "
                      "Correct the metadata through a reviewed change; do not copy untrusted annotation text into this issue."]
        elif status == "Expected exception missing":
            lines += ["Compare live and desired exception inventory and check reconciliation. "
                      "Determine whether removal was intentional and qualified; do not blindly recreate it."]
        else:
            lines += ["Review whether this exception belongs in the approved inventory. "
                      "Validate its purpose and scope before changing it."]
        if "inventory" in details:
            lines.append("Reconcile the unexpected inventory against the approved desired state before accepting any renewal.")
        if reviewed:
            lines.append(EXCEPTION_GUIDANCE[name])
        lines += ["", "## Resolution checklist",
                  "- [ ] Confirm affected workloads, rule scope and accountable owner in GitOps.",
                  "- [ ] Review and merge an explicitly justified renewal or qualified removal; no automatic extension.",
                  "- [ ] Verify Flux reconciliation and the intended live exception/policy state.",
                  "- [ ] Confirm a valid renewed review window or positive compliance after remediation. "
                  "A renewed exception resolves the review reminder, not the underlying security exposure."]
    else:
        policy, rule, kind, namespace, name = identity
        source, guidance = RULE_GUIDANCE.get((policy, rule), (None,
            "Inspect the exact reviewed rule and affected GitOps resource locally. Determine the failing field and safe remediation; do not guess from the rule name."))
        lines += ["- Policy / rule: " + policy + " / " + rule,
                  "- Resource: " + namespace + "/" + kind + "/" + name,
                  "- Evidence: persistent failure under the 24-hour reporting threshold; live enforcement mode and exact failing field require confirmation.",
                  "- Policy source: " + ("`" + source + "`" if source else "not established by this report"),
                  "", "## Required action", guidance,
                  "Review the actual policy evaluation and workload manifest before proposing a change.",
                  "", "## Resolution checklist",
                  "- [ ] Confirm the live policy revision, enforcement mode and failing resource.",
                  "- [ ] Review, test and merge a safe GitOps remediation.",
                  "- [ ] Verify Flux reconciliation and fresh positive evaluation of the same control/resource."]
    lines += ["", "Automatic closure is not enabled yet. Missing reports, skipped evaluations and exception expiry do not prove remediation. Close only after verified recovery."]
    return title, "\n".join(lines)


def policy_revision(item):
    return revision({"formatVersion":2, "evidence":item})


def sync_policy_items(writer, items):
    outcomes = []
    for item in sorted(items, key=lambda item: (item["category"], item["key"])):
        try:
            outcomes.append(writer.sync(item))
        except WriteBudgetDeferred:
            outcomes.append("deferred")
    return outcomes


def kube_reader():
    root = Path("/var/run/secrets/kubernetes.io/serviceaccount")
    token = (root / "token").read_text().strip()
    opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(
        context=ssl.create_default_context(cafile=str(root / "ca.crt"))))
    allowed = {"/apis/wgpolicyk8s.io/v1alpha2/clusterpolicyreports",
               "/apis/wgpolicyk8s.io/v1alpha2/policyreports",
               "/apis/policies.kyverno.io/v1/policyexceptions"}
    def read(path):
        require(path in allowed)
        items, cursor = [], ""
        for _ in range(50):
            request = Request("https://kubernetes.default.svc" + path + "?" +
                              urlencode({"limit": 100, "continue": cursor}),
                              headers={"Authorization": "Bearer " + token})
            with opener.open(request, timeout=15) as response:
                body = response.read(8 * 1024**2 + 1)
                require(len(body) <= 8 * 1024**2)
                data = json.loads(body)
            require(isinstance(data.get("items"), list))
            items.extend(data["items"])
            cursor = data.get("metadata", {}).get("continue", "")
            if not cursor:
                return {"items": items}
        raise ValueError("inventory_bound")
    return read


def main():
    phase = "read"
    fd = None
    db = None
    try:
        read = kube_reader()
        now = datetime.now(timezone.utc)
        items = exception_items(read, now.date()) + policy_items(read, now)
        phase = "destination"
        client = PrivateGitea(os.environ.get("GITEA_TOKEN", ""))
        phase = "state"
        path = "/state/gitea-kyverno-publisher.sqlite"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = sqlite3.connect(path, timeout=5)
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        require(db.execute("PRAGMA quick_check").fetchone()[0] == "ok")
        writer = DurablePublisher(db, client, renderer=render_policy, fingerprint=policy_revision)
        phase = "sync"
        outcomes = sync_policy_items(writer, items)
        pending = db.execute("SELECT count(*) FROM issues WHERE pending_create != 0 OR pending_update IS NOT NULL").fetchone()[0]
        print(json.dumps({"policyFindings":len(items), "created":outcomes.count("created"),
                          "updated":outcomes.count("updated"),"pending":pending,
                          "deferred":outcomes.count("deferred")}), flush=True)
        return int(bool(pending) or "deferred" in outcomes)
    except Exception:
        print(json.dumps({"status":"unknown","phase":phase}), flush=True)
        return 1
    finally:
        if db is not None:
            db.close()
        if fd is not None:
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
