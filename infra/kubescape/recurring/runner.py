#!/usr/bin/env python3
"""Bounded recurring assessments. Stdlib only; shares the qualified DB client.

The PVC lock serializes both scan types. An fsynced intent precedes each POST;
uncertain requests fence future scans until their saved ID is investigated.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import grype_db as db
import kubescape_db as client

NS = "kubescape"
STORAGE = "/apis/spdx.softwarecomposition.kubescape.io/v1beta1"
CORE = "/api/v1"
APPS = "/apis/apps/v1"
BATCH = "/apis/batch/v1"
EXIT = {"healthy": 0, "stale": 2, "unknown": 3, "failed": 4}
TERMINAL = {"succeeded", "failed", "abandoned"}
LABELS = {"homelab.cooked.beer/owner": "sam", "kubescape.io/ignore": "true"}
IDLE_WAIT_SECONDS = 120
IDLE_POLL_SECONDS = 5
INVENTORY_MAX_AGE_MINUTES = 65


class APIError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f"http_{code}")  # Never include response bodies or credentials.


class API:
    def __init__(self, local=False):
        self.local = local
        self.context = None if local else ssl.create_default_context(
            cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
        self.root = "https://kubernetes.default.svc"

    def call(self, path, value=None, method=None, raw=False):
        if self.local:
            db.require(value is None, "local_read_only")
            body = subprocess.check_output(["kubectl", "--request-timeout=20s", "get", "--raw=" + path], timeout=30)
        else:
            body = http(self.root + path, value, method, Path(
                "/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip(), self.context, raw=True)
        return body.decode() if raw else json.loads(body)

    def get(self, base, resource, name=None, namespace=NS, optional=False):
        path = base + ("/namespaces/" + namespace if namespace else "") + "/" + resource
        if name:
            path += "/" + urllib.parse.quote(name, safe="")
        try:
            return self.call(path)
        except APIError as error:
            if optional and error.code == 404:
                return None
            raise

    def items(self, base, resource, namespace=None):
        response = self.get(base, resource, namespace=namespace)
        for item in response["items"]:
            item.setdefault("kind", response["kind"].removesuffix("List"))
        return response["items"]


def http(url, value=None, method=None, token=None, context=None, raw=False):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=None if value is None else json.dumps(value).encode(),
                                     headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, context=context, timeout=25) as response:
            body = response.read(64 * 1024**2 + 1)
            db.require(len(body) <= 64 * 1024**2, "response_too_large")
    except urllib.error.HTTPError as error:
        raise APIError(error.code) from None
    return body if raw else json.loads(body)


def service(component, path, value=None):
    return http(f"http://{component}:8080" + path, value,
                token=Path("/var/run/assessment-api/token").read_text().strip())


def quantity(value):
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
             "n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1}
    match = re.fullmatch(r"([0-9.]+)(Ki|Mi|Gi|Ti|n|u|m)?", value)
    db.require(match is not None, "invalid_quantity")
    return float(match[1]) * units[match[2] or ""]


def ready(pod):
    return (not pod["metadata"].get("deletionTimestamp") and pod["status"].get("phase") == "Running"
            and any(c["type"] == "Ready" and c["status"] == "True" for c in pod["status"].get("conditions", [])))


def component_pod(api, component):
    pods = [p for p in api.items(CORE, "pods", NS)
            if p["metadata"].get("labels", {}).get("app.kubernetes.io/component") == component
            and not p["metadata"].get("deletionTimestamp")]
    db.require(len(pods) == 1 and ready(pods[0]), component + "_unavailable")
    return pods[0]


def deployment_current(workload, container):
    """Reject old ready replicas during a Deployment rollout."""
    spec, status = workload["spec"], workload.get("status", {})
    replicas = spec.get("replicas", 1)
    if (type(replicas) is not int or replicas < 1
            or status.get("observedGeneration") != workload["metadata"].get("generation")
            or any(status.get(key, 0) != replicas for key in
                   ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas"))):
        return False
    return any(c["name"] == container["name"] and c["image"] == container["image"]
               for c in spec["template"]["spec"].get("containers", []))


DIRECT_SCAN_KINDS = frozenset(("Deployment", "StatefulSet", "DaemonSet"))


def controller_current(workload, container, pod):
    """Require an observed, fully updated controller and matching Pod template."""
    kind = workload["kind"]
    if kind == "Deployment":
        return deployment_current(workload, container)
    spec, status = workload["spec"], workload.get("status", {})
    if status.get("observedGeneration") != workload["metadata"].get("generation"):
        return False
    if kind == "StatefulSet":
        count = spec.get("replicas", 1)
        if type(count) is not int or count < 1:
            return False
        if any(status.get(key, 0) != count for key in
               ("replicas", "updatedReplicas", "readyReplicas", "currentReplicas", "availableReplicas")):
            return False
        revision = status.get("updateRevision")
        if (not revision or status.get("currentRevision") != revision
                or pod["metadata"].get("labels", {}).get("controller-revision-hash") != revision):
            return False
    elif kind == "DaemonSet":
        count = status.get("desiredNumberScheduled", 0)
        if type(count) is not int or count < 1:
            return False
        if any(status.get(key, 0) != count for key in
               ("currentNumberScheduled", "updatedNumberScheduled", "numberReady", "numberAvailable")):
            return False
        if status.get("numberMisscheduled", 0) or status.get("numberUnavailable", 0):
            return False
    else:
        return False
    return any(c["name"] == container["name"] and c["image"] == container["image"]
               for c in spec["template"]["spec"].get("containers", []))


def native_summary_name(row):
    db.require(row["kind"] in DIRECT_SCAN_KINDS, "unsupported_workload")
    name = "-".join((row["kind"].lower(), row["workload"], row["container"]))
    db.require(len(name) <= 253 and all(re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", label)
               for label in name.split(".")), "unsupported_summary_name")
    return name


def workload_wlid(row):
    db.require(row["kind"] in DIRECT_SCAN_KINDS, "unsupported_workload")
    return f"wlid://cluster-home/namespace-{row['namespace']}/{row['kind'].lower()}-{row['workload']}"


# One reviewed external-service-dependent Deployment. Application readiness
# is not required to assess its stable running image; recovery still requires
# the original fully Ready controller and complete scope.
UNREADY_IMAGE_TARGET = "koito/Deployment/koito-lastfm-bridge/multi-scrobbler"


def stable_unready_image(workload, container, pod, status, digest):
    if (workload["kind"] != "Deployment"
            or "/".join((pod["metadata"]["namespace"], workload["kind"],
                         workload["metadata"]["name"], container["name"])) != UNREADY_IMAGE_TARGET
            or not digest or pod["metadata"].get("deletionTimestamp")
            or pod["status"].get("phase") != "Running"
            or not isinstance(status.get("state", {}).get("running"), dict)
            or status.get("started") is not True):
        return False
    spec, observed = workload["spec"], workload.get("status", {})
    # Deliberately single-replica only. Never accept a rollout, old replica,
    # scaled-zero workload, crashloop or absent container runtime identity.
    return (spec.get("replicas", 1) == 1
            and observed.get("observedGeneration") == workload["metadata"].get("generation")
            and observed.get("replicas") == 1 and observed.get("updatedReplicas") == 1
            and any(c["name"] == container["name"] and c["image"] == container["image"]
                    for c in spec["template"]["spec"].get("containers", [])))


def assessment_eligible(row):
    return row["ready"] or (target_key(row) == UNREADY_IMAGE_TARGET
                            and row.get("stableUnreadyImage") is True
                            and row["containerType"] == "containers")


def workload_rows(api):
    """Resolve actual controller UIDs; include init/ephemeral and nonrunning specs."""
    objects = {}
    for base, resources in ((APPS, ("deployments", "statefulsets", "daemonsets", "replicasets")),
                            (BATCH, ("jobs", "cronjobs"))):
        for resource in resources:
            for obj in api.items(base, resource):
                objects[obj["metadata"]["uid"]] = obj
    rows = []
    seen = set()
    for pod in api.items(CORE, "pods"):
        if pod["status"].get("phase") in {"Succeeded", "Failed"} or pod["metadata"].get("deletionTimestamp"):
            continue
        workload = pod
        while True:
            owners = [r for r in workload["metadata"].get("ownerReferences", []) if r.get("controller")]
            if not owners or owners[0]["uid"] not in objects:
                break
            workload = objects[owners[0]["uid"]]
        namespace = pod["metadata"]["namespace"]
        for field, statuses in (("containers", "containerStatuses"), ("initContainers", "initContainerStatuses"),
                                ("ephemeralContainers", "ephemeralContainerStatuses")):
            for container in pod["spec"].get(field, []):
                status = next((s for s in pod["status"].get(statuses, []) if s["name"] == container["name"]), {})
                digest = status.get("imageID", "").removeprefix("docker-pullable://")
                digest = digest if re.fullmatch(r"[^ @]+@sha256:[a-f0-9]{64}", digest) else None
                row = {"namespace": namespace, "kind": workload["kind"], "workload": workload["metadata"]["name"],
                       "workloadUID": workload["metadata"]["uid"], "generation": workload["metadata"].get("generation"),
                       "container": container["name"], "containerType": field, "runningDigest": digest,
                       "declaredImage": container["image"], "podUIDs": [pod["metadata"]["uid"]],
                       "ready": ready(pod) and status.get("ready", False)
                                and (workload["kind"] not in DIRECT_SCAN_KINDS or field != "containers"
                                     or controller_current(workload, container, pod))}
                row["stableUnreadyImage"] = (field == "containers" and
                    stable_unready_image(workload, container, pod, status, digest))
                key = row_key(row)
                if key in seen:
                    previous = next(r for r in rows if row_key(r) == key)
                    previous["podUIDs"].extend(row["podUIDs"])
                    previous["ready"] = previous["ready"] and row["ready"]
                    previous["stableUnreadyImage"] = False  # never multiple Pods
                else:
                    rows.append(row)
                    seen.add(key)
    # Workloads scaled to zero / unscheduled still belong in coverage.
    represented = {r["workloadUID"] for r in rows}
    for uid, obj in objects.items():
        if uid in represented or obj["kind"] in {"ReplicaSet", "Job"}:
            continue
        spec = obj["spec"]
        if obj["kind"] == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        for container in spec["template"]["spec"].get("containers", []):
            rows.append({"namespace": obj["metadata"]["namespace"], "kind": obj["kind"],
                         "workload": obj["metadata"]["name"], "workloadUID": uid,
                         "generation": obj["metadata"].get("generation"), "container": container["name"],
                         "containerType": "containers", "runningDigest": None, "declaredImage": container["image"],
                         "podUIDs": [], "ready": False})
    # Require all expected current Pods for each newly supported kind.
    for row in rows:
        if row["kind"] in ("StatefulSet", "DaemonSet") and row["containerType"] == "containers":
            obj = objects.get(row["workloadUID"], {})
            count = (obj.get("spec", {}).get("replicas", 1) if row["kind"] == "StatefulSet"
                     else obj.get("status", {}).get("desiredNumberScheduled", 0))
            row["ready"] = row["ready"] and type(count) is int and count > 0 and len(set(row["podUIDs"])) == count
        row["podUIDs"].sort()
        # Recovery is stricter than assessment reporting. Require the full
        # observed controller replica population and all main containers.
        obj = objects.get(row["workloadUID"], {})
        desired = (obj.get("status", {}).get("desiredNumberScheduled")
                   if row["kind"] == "DaemonSet" else obj.get("spec", {}).get("replicas", 1))
        row["recoveryScopeReady"] = (row["kind"] in DIRECT_SCAN_KINDS
            and row["containerType"] == "containers" and row["ready"]
            and type(desired) is int and desired > 0 and len(set(row["podUIDs"])) == desired)
    return sorted(rows, key=row_key)


def target_key(row):
    return "/".join(row[k] for k in ("namespace", "kind", "workload", "container"))


def row_key(row):
    return target_key(row) + "/" + row["workloadUID"] + "/" + (row["runningDigest"] or "")


def resolve_targets(rows, config):
    """Resolve each identity independently; missing workloads never gate another mode."""
    selected, outcomes = [], []
    for target in {t["identity"]: t for t in config["targets"]}.values():
        identity = target["identity"]
        matches = [r for r in rows if target_key(r) == identity and r["containerType"] == "containers"]
        outcome = {"identity": identity, "state": "unknown", "reasons": []}
        if len(matches) != 1:
            outcome["reasons"] = ["workload_missing" if not matches else "workload_rolling"]
        elif matches[0]["kind"] not in DIRECT_SCAN_KINDS:
            outcome.update(state="unsupported", reasons=["unsupported_workload"])
        elif not assessment_eligible(matches[0]):
            outcome.update(state="skipped", reasons=["workload_not_ready"])
        elif not matches[0]["runningDigest"]:
            outcome.update(state="unsupported", reasons=["running_digest_unavailable"])
        else:
            selected.append(matches[0])
        outcomes.append(outcome)
    return selected, outcomes


def targets(rows, config):
    return resolve_targets(rows, config)[0]


def select_batch(rows, records, config):
    # The most recent attempt rotates failures too, preventing starvation. A new
    # digest/UID has no record and comes first; DB changes invalidate old scans.
    def priority(row):
        record = records.get(row_key(row), {})
        attempted = record.get("lastAttempt", {}).get("checkedAt")
        successful = record.get("createdAt")
        stamp = max(filter(None, (attempted, successful)), default="")
        changed_db = record.get("database", {}).get("checksum") != config["databaseChecksum"]
        # An unsuccessful attempt on the current DB counts for fair rotation.
        attempted_current = record.get("lastAttempt", {}).get("databaseChecksum") == config["databaseChecksum"]
        return (bool(stamp) and (not changed_db or attempted_current), stamp)
    # The pinned engine applies workload-scoped exceptions to stored CVE
    # manifests. Equal image digests alone cannot establish equal findings.
    return sorted(rows, key=priority)[:config["maximumImagesPerRun"]]


def coverage(rows, records, config, now=None):
    now = now or db.utcnow()
    selected = {t["identity"] for t in config["targets"]}
    for row in rows:
        record = records.get(row_key(row), {})
        row.update(lastSuccessfulScan=record.get("createdAt"), database=record.get("database"),
                   scanAgeHours=None, severities=record.get("severities"), fixesAvailable=record.get("fixesAvailable"),
                   assessmentState="unknown", exclusionReason=None, lastAttempt=record.get("lastAttempt"),
                   evidenceKind=record.get("evidenceKind", "direct" if record.get("createdAt") else None))
        current, invalid = False, False
        if record.get("createdAt"):
            try:
                age = db.age_hours(record["createdAt"], now)
                row["scanAgeHours"] = round(age, 4)
                current = (age <= config["maximumScanAgeHours"]
                    and db.database_freshness(record["database"]["built"], now)["state"] != "expired"
                    and record["database"]["checksum"] == config["databaseChecksum"])
            except (db.Rejected, KeyError):
                invalid = True
        if target_key(row) not in selected:
            row.update(assessmentState="deliberately_excluded", exclusionReason="Outside the curated application targets")
        elif not assessment_eligible(row):
            row.update(assessmentState="skipped", exclusionReason="Workload not ready")
        elif not row["runningDigest"] or row["containerType"] != "containers":
            row.update(assessmentState="unsupported", exclusionReason="No running registry digest or unsupported container type")
        elif record.get("lastAttempt", {}).get("state") in {"failed", "unknown"}:
            row["assessmentState"] = record["lastAttempt"]["state"]
        elif record.get("evidenceKind") == "sharedImage":
            row.update(assessmentState="unknown", exclusionReason="Workload exception equivalence unverified")
        elif record.get("createdAt"):
            row["assessmentState"] = "unknown" if invalid else "healthy" if current else "stale"
    return {"schemaVersion": 1, "generatedAt": db.stamp(now),
            "validUntil": db.stamp(now + timedelta(minutes=INVENTORY_MAX_AGE_MINUTES)), "rows": rows,
            "counts": dict(Counter(r["assessmentState"] for r in rows)),
            "healthMeaning": "Assessment freshness and integrity, not absence of vulnerabilities"}


def cohort_health(inventory, config):
    """Require one current, healthy row for every selected target."""
    expected = {t["identity"] for t in config["targets"]}
    healthy = 0
    expiry = db.timestamp(inventory["validUntil"])
    for target in expected:
        matches = [row for row in inventory["rows"] if target_key(row) == target
                   and row["containerType"] == "containers"]
        if len(matches) != 1 or matches[0]["assessmentState"] != "healthy":
            continue
        healthy += 1
        expiry = min(expiry, db.timestamp(matches[0]["lastSuccessfulScan"])
                     + timedelta(hours=config["maximumScanAgeHours"]))
    return {"state": "healthy" if expected and healthy == len(expected) else "unknown",
            "expected": len(expected), "healthy": healthy, "validUntil": db.stamp(expiry)}


# Publication is a separate, explicit trust boundary. Only the named private
# projection may contain strictly validated package/version/image evidence.
# Never copy raw matches, report locations, metadata, URLs or arbitrary text.
LEGACY_PUBLIC_CVE_TARGETS = {
    "homepage/Deployment/homepage/homepage",
    "homepage/Deployment/homepage-assets/nginx",
    "memos/Deployment/memos/memos",
}
PRIVATE_CVE_TARGETS = {
    "gitea/Deployment/gitea-mcp/mcp",
    "homepage/Deployment/homepage-assets/nginx",
    "homepage/Deployment/homepage/homepage",
    "koito/Deployment/koito-lastfm-bridge/multi-scrobbler",
    "koito/Deployment/koito/koito",
    "memos/Deployment/memos/memos",
    "monitoring/Deployment/gatus/gatus",
    "openbrew/Deployment/openbrew/openbrew",
    "stirling-pdf/Deployment/stirling-pdf/stirling-pdf",
    "wotlk/StatefulSet/wotlk-mariadb/mysql",
    "kube-system/DaemonSet/kube-proxy/kube-proxy",
    "cloudflared/Deployment/cloudflared/cloudflared",
}
CVE_ID = re.compile(r"CVE-[0-9]{4}-[0-9]{4,10}\Z")
LEGACY_PRIVATE_CVE_LIMIT = 100
MAX_PRIVATE_CVES = 200
MAX_PRIVATE_DETAIL_MATCHES = 12
MAX_RECOVERY_CVES = 256
MAX_PRIVATE_PROJECTION_BYTES = 240 * 1024
PACKAGE_FIELD = re.compile(r"[A-Za-z0-9@][A-Za-z0-9._+:/@~=-]{0,127}\Z")
PRIVATE_IMAGE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,383}@sha256:[a-f0-9]{64}\Z")


def private_cve_findings(matches, *, limit=MAX_PRIVATE_CVES):
    findings, other = {}, 0
    for match in matches:
        vuln = match["vulnerability"]
        severity = vuln.get("severity")
        if severity not in ("High", "Critical"):
            continue
        identifier = vuln.get("id")
        if not isinstance(identifier, str) or not CVE_ID.fullmatch(identifier):
            other += 1
            continue
        existing = findings.setdefault(identifier, {"id": identifier, "severity": severity, "fixAvailable": False})
        if severity == "Critical":
            existing["severity"] = "Critical"
        existing["fixAvailable"] |= vuln.get("fix", {}).get("state") == "fixed"
    # Do not silently truncate a security finding set.
    if len(findings) > limit:
        return {"state": "overflow", "findings": [], "otherHighCriticalMatches": other}
    return {"state": "complete", "findings": sorted(findings.values(), key=lambda x: x["id"]),
            "otherHighCriticalMatches": other}


def private_cve_details(matches):
    """Qualified Grype 0.121.0 / kubevuln grypeToDomainMatch fields only.

    Keep the existing complete advisory summary independent of bounded
    package details. Unsupported/malformed/truncated details are explicit.
    """
    def safe(value):
        return isinstance(value, str) and PACKAGE_FIELD.fullmatch(value) and "://" not in value

    selected, omitted = {}, 0
    for match in matches:
        vuln = match["vulnerability"]
        identifier, severity = vuln.get("id"), vuln.get("severity")
        if severity not in ("High", "Critical") or not isinstance(identifier, str) or not CVE_ID.fullmatch(identifier):
            continue
        artifact = match.get("artifact")
        fix = vuln.get("fix") or {}
        if not isinstance(artifact, dict) or not isinstance(fix, dict):
            omitted += 1
            continue
        package, ecosystem, installed = (artifact.get(k) for k in ("name", "type", "version"))
        state, versions = fix.get("state", "unknown"), fix.get("versions", [])
        if versions is None:
            versions = []
        if (not all(safe(v) for v in (package, ecosystem, installed))
                or state not in ("fixed", "not-fixed", "wont-fix", "unknown")
                or not isinstance(versions, list) or len(versions) > 4
                or not all(safe(v) for v in versions)
                or (versions and state != "fixed")):
            omitted += 1
            continue
        detail = {"id":identifier, "severity":severity, "package":package,
                  "ecosystem":ecosystem, "installedVersion":installed,
                  "fixedVersions":sorted(set(versions)), "fixState":state}
        key = json.dumps(detail, sort_keys=True, separators=(",", ":"))
        selected[key] = detail
        # Keep only the best bounded candidates, independent of report order.
        if len(selected) > MAX_PRIVATE_DETAIL_MATCHES:
            worst = max(selected, key=lambda k: (selected[k]["severity"] != "Critical", k))
            del selected[worst]
            omitted += 1
    details = sorted(selected.values(), key=lambda m: (m["severity"] != "Critical", m["id"],
                     m["package"], m["ecosystem"], m["installedVersion"], m["fixState"], tuple(m["fixedVersions"])))
    return {"detailState":"partial" if omitted else "complete", "matches":details,
            "omittedMatches":omitted}


def private_cve_projection(inventory, records, diagnostics=None):
    """One bounded issue per workload, covering every reviewed container.

    All containers must have qualified evidence. Use the oldest assessment
    timestamp, merge CVEs by ID with highest severity and OR of fix flags,
    and fail closed on missing/ambiguous/overflow evidence.
    """
    rows = inventory["rows"]
    groups = {}
    for target in sorted(PRIVATE_CVE_TARGETS):
        groups.setdefault(target.rsplit("/", 1)[0], []).append(target)
    entries = []
    for workload, group in sorted(groups.items()):
        entry = {"workload": workload, "state": "unknown", "assessedAt": None,
                 "findings": [], "otherHighCriticalMatches": 0}
        findings, other, times = {}, 0, []
        reason = "complete"
        for target in group:
            selected = [row for row in rows if target_key(row) == target]
            record = records.get(row_key(selected[0]), {}) if len(selected) == 1 else {}
            evidence = record.get("privateCve")
            if evidence is None and target in LEGACY_PUBLIC_CVE_TARGETS:
                evidence = record.get("publicCve")
            if len(selected) != 1:
                reason = "inventory_missing" if not selected else "inventory_ambiguous"
                break
            assessment = selected[0]["assessmentState"]
            if assessment != "healthy":
                # Fixed codes only: never emit arbitrary retained record fields.
                reason = ("assessment_" + assessment if assessment in
                          {"unknown", "failed", "stale", "skipped", "unsupported", "deliberately_excluded"}
                          else "assessment_unknown")
                break
            if record.get("evidenceKind") == "sharedImage":
                reason = "shared_evidence_unqualified"
                break
            if not isinstance(evidence, dict):
                reason = "projection_missing"
                break
            if evidence.get("state") != "complete":
                reason = "projection_overflow" if evidence.get("state") == "overflow" else "projection_unqualified"
                break
            times.append(record["createdAt"])
            other += evidence["otherHighCriticalMatches"]
            for finding in evidence["findings"]:
                existing = findings.setdefault(finding["id"], dict(finding))
                if finding["severity"] == "Critical":
                    existing["severity"] = "Critical"
                existing["fixAvailable"] |= finding["fixAvailable"]
        else:
            if len(findings) <= MAX_PRIVATE_CVES and 0 <= other <= 1000000:
                entry.update(state="complete", assessedAt=min(times, key=db.timestamp),
                             findings=sorted(findings.values(), key=lambda x: x["id"]),
                             otherHighCriticalMatches=other)
            else:
                reason = "workload_projection_bound"
        if diagnostics is not None:
            diagnostics[reason] += 1
        entries.append(entry)
    return {"schema": 1, "source": "kubescape-cve", "generatedAt": inventory["generatedAt"],
            "entries": entries}


def private_recovery_proof(report, row, record):
    """Extract proof only from an independently qualified direct full report.

    Pinned storage GrypeDocument omits empty ignoredMatches; kubevuln
    grypeToDomain preserves ignored matches and security exceptions move
    suppressed matches there. Reject any ignored/relevancy-filtered report.
    An unresolved non-CVE advisory identity is not positive recovery proof.
    """
    try:
        payload = report["spec"]["payload"]
        if (record.get("evidenceKind") != "direct"
                or report["spec"]["metadata"].get("withRelevancy") is not False
                or payload.get("ignoredMatches", []) != []
                or payload["descriptor"]["db"].get("valid") is not True
                or not isinstance(payload["matches"], list)):
            return None
        ids = set()
        for match in payload["matches"]:
            vuln = match["vulnerability"]
            identifier = vuln.get("id")
            related = match.get("relatedVulnerabilities", [])
            if related is None:
                related = []
            if not isinstance(related, list):
                return None
            aliases = [v.get("id") for v in related if isinstance(v, dict)]
            if len(aliases) != len(related) or not isinstance(identifier, str) or not identifier:
                return None
            found = {v for v in [identifier, *aliases]
                     if isinstance(v, str) and CVE_ID.fullmatch(v)}
            if not found:
                return None
            ids.update(found)
            if len(ids) > MAX_RECOVERY_CVES:
                return None
        checksum = record["database"]["checksum"].removeprefix("sha256:")
        built = record["database"]["built"]
        if (not re.fullmatch(r"[a-f0-9]{64}", checksum)
                or db.timestamp(built) > db.timestamp(record["createdAt"])
                or record["workloadUID"] != row["workloadUID"]
                or record["imageDigest"] != row["runningDigest"]
                or record["container"] != row["container"]
                or not re.fullmatch(r"recurring-[a-f0-9]{32}", record["jobID"])):
            return None
        identity = json.dumps([record["jobID"], row_key(row), record["createdAt"],
                               checksum, record["manifestDigest"], record["configDigest"]],
                              separators=(",", ":"))
        return {"name":row["container"], "databaseBuilt":built,
                "databaseChecksum":checksum, "allCves":sorted(ids),
                "assessmentId":hashlib.sha256(identity.encode()).hexdigest()}
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def workload_recovery_proof(entry, inventory, records):
    if entry["state"] != "complete":
        return None
    rows = [r for r in inventory["rows"] if target_key(r).rsplit("/", 1)[0] == entry["workload"]]
    expected = {t.rsplit("/", 1)[1] for t in PRIVATE_CVE_TARGETS
                if t.rsplit("/", 1)[0] == entry["workload"]}
    # No unknown, unreviewed, init, ephemeral, mixed or missing container rows.
    if (not rows or len(rows) != len(expected)
            or {r["container"] for r in rows} != expected
            or any(r["containerType"] != "containers" or r["assessmentState"] != "healthy"
                   or r.get("recoveryScopeReady") is not True for r in rows)):
        return None
    owners = {r["workloadUID"] for r in rows}
    pods = {tuple(r["podUIDs"]) for r in rows}
    if len(owners) != 1 or len(pods) != 1 or not next(iter(pods)):
        return None
    proof = []
    generated = db.timestamp(inventory["generatedAt"])
    for row in sorted(rows, key=lambda r:r["container"]):
        record = records.get(row_key(row), {})
        item = record.get("privateRecoveryProof")
        if (not isinstance(item, dict) or item.get("name") != row["container"]
                or record.get("evidenceKind") != "direct"
                or record.get("workloadUID") != row["workloadUID"]
                or record.get("imageDigest") != row["runningDigest"]
                or record.get("container") != row["container"]):
            return None
        built, assessed = db.timestamp(item["databaseBuilt"]), db.timestamp(record["createdAt"])
        if not (built <= assessed <= generated and generated - built <= timedelta(days=7)):
            return None
        proof.append(item)
    identity = json.dumps([entry["workload"], next(iter(owners))], separators=(",", ":"))
    return {"workloadIdentity":hashlib.sha256(identity.encode()).hexdigest(), "containers":proof}


def rich_private_cve_projection(inventory, records, diagnostics=None):
    projection = private_cve_projection(inventory, records, diagnostics)
    projection["schema"] = 3
    for entry in projection["entries"]:
        entry["containers"] = []
        entry["recoveryProof"] = None
        if entry["state"] != "complete":
            continue
        for target in sorted(t for t in PRIVATE_CVE_TARGETS if t.rsplit("/", 1)[0] == entry["workload"]):
            row, = [r for r in inventory["rows"] if target_key(r) == target]
            record = records[row_key(row)]
            image = row["runningDigest"]
            # Do not invalidate existing advisory reporting for a reference
            # that cannot be represented safely in the richer contract.
            if not isinstance(image, str) or not PRIVATE_IMAGE_REF.fullmatch(image):
                return private_cve_projection(inventory, records)
            detail = record.get("privateCveDetails")
            if not isinstance(detail, dict):
                detail = {"detailState":"unavailable", "matches":[], "omittedMatches":None}
            entry["containers"].append({"name":row["container"], "image":image,
                                        "assessedAt":record["createdAt"], **detail})
        entry["recoveryProof"] = workload_recovery_proof(entry, inventory, records)
    return projection


def check_report(report, sbom, row, expected, started, previous=None):
    db.require(report["metadata"].get("annotations", {}).get("kubescape.io/status") == "ready", "report_incomplete")
    spec = report["spec"]
    created = spec["metadata"]["report"]["createdAt"]
    # Storage's metav1.Time serializes whole seconds; request time is fractional.
    # The previous report timestamp must still differ, independently of rounding.
    db.require(db.timestamp(created) >= db.timestamp(started).replace(microsecond=0), "report_not_new")
    db.require(previous is None or created != previous["spec"]["metadata"]["report"]["createdAt"], "report_not_new")
    db.require(isinstance(spec["payload"].get("matches"), list), "report_incomplete")
    descriptor = spec["payload"]["descriptor"]["db"]
    for key in ("built", "schemaVersion"):
        db.require(descriptor[key] == expected["metadata"][key], "report_database_mismatch")
    db.require(spec["metadata"]["tool"]["databaseVersion"] == expected["metadata"]["checksum"].removeprefix("sha256:"), "report_database_mismatch")
    db.require(sbom["metadata"].get("annotations", {}).get("kubescape.io/status") == "ready", "sbom_incomplete")
    source = sbom["spec"]["syft"]["source"]["metadata"]
    db.require(source["userInput"] == row["runningDigest"] and spec["payload"]["source"]["target"]["userInput"] == row["runningDigest"], "report_image_mismatch")
    db.require(source["architecture"] == "amd64" and source["os"] == "linux", "unsupported_platform")
    for encoded, digest in (("manifest", "manifestDigest"), ("config", "imageID")):
        db.require(db.digest_bytes(base64.b64decode(source[encoded])) == source[digest], "sbom_identity_invalid")
    db.require(bool(sbom["spec"]["syft"].get("artifacts")), "sbom_empty")
    matches = spec["payload"]["matches"]
    return {"createdAt": created, "database": expected["metadata"],
            "severities": dict(Counter(m["vulnerability"].get("severity", "Unknown") for m in matches)),
            "fixesAvailable": any(m["vulnerability"].get("fix", {}).get("state") == "fixed" for m in matches),
            "manifestDigest": source["manifestDigest"], "configDigest": source["imageID"],
            "lastAttempt": {"state": "healthy", "checkedAt": db.stamp(db.utcnow())}}


def check_posture(result, summaries, started, required_workloads=()):
    # Full scanner response is authoritative for Action Required / Not Evaluated.
    db.require(isinstance(result, dict) and result.get("summaryDetails"), "posture_result_incomplete")
    frameworks = result["summaryDetails"].get("frameworks", [])
    db.require(bool(frameworks), "posture_controls_empty")
    controls = []
    for framework in frameworks:
        values = framework.get("controls", {})
        db.require(isinstance(values, (dict, list)), "posture_controls_invalid")
        for key, control in (values.items() if isinstance(values, dict) else enumerate(values)):
            controls.append((key, control))
    db.require(bool(controls), "posture_controls_empty")
    statuses = Counter()
    not_evaluated = []
    incomplete_coverage = []
    for key, control in controls:
        db.require(isinstance(control, dict), "posture_controls_invalid")
        info = control.get("statusInfo") or {}
        db.require(isinstance(info, dict), "posture_status_invalid")
        # Kubescape v4.0.20: StatusInfo is authoritative; top-level status is
        # a compatibility fallback. Its printer counts skipped as Action Required.
        status = info.get("status") or control.get("status")
        status = status.get("status") if isinstance(status, dict) else status
        translated = {"passed": "Passed", "failed": "Failed",
                      "skipped": "Action Required", "actionRequired": "Action Required"}
        db.require(isinstance(status, str) and status in translated, "posture_status_unrecognised")
        statuses[translated[status]] += 1
        substatus = info.get("subStatus", "")
        identifier = control.get("controlID") or (key if isinstance(key, str) else None)
        if substatus == "notEvaluated":
            not_evaluated.append(identifier)
        elif substatus == "incompleteCoverage":
            incomplete_coverage.append(identifier)
    db.require(statuses["Passed"] + statuses["Failed"] > 0, "posture_not_evaluated")
    db.require(bool(summaries), "posture_reports_missing")
    for summary in summaries:
        db.require(db.timestamp(summary["metadata"]["annotations"]["kubescape.io/report-created-at"]) >= db.timestamp(started), "posture_report_stale")
        db.require(bool(summary["spec"].get("controls")), "posture_controls_empty")
    db.require(any(s["metadata"].get("namespace") == "memos" and s["metadata"]["name"] == "deployment-memos" for s in summaries), "posture_memos_missing")
    db.require(any(s["metadata"].get("namespace") == NS for s in summaries), "posture_shared_rbac_missing")
    observed = {s["metadata"].get("namespace", "") + "/" + s["metadata"]["name"] for s in summaries}
    db.require(set(required_workloads) <= observed, "posture_scope_incomplete")
    return {"framework": "nsa", "controls": dict(statuses), "storedSummaries": len(summaries),
            "requiredWorkloadCount": len(required_workloads),
            "coverage": {"notEvaluatedControls": len(not_evaluated), "notEvaluatedControlIDs": not_evaluated,
                         "incompleteCoverageControls": len(incomplete_coverage),
                         "incompleteCoverageControlIDs": incomplete_coverage},
            "limitations": ["Host/cloud checks remain unsupported or Action Required; no host collector", "Intentional RBAC exceptions retained; no remediation"]}


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as output:
        json.dump(value, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    temp.replace(path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def locked(history):
    with (history / "assessment.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise db.Rejected("duplicate_execution") from None
        yield


def status_safe(value):
    """Finding content is confined to retained history and the named projection."""
    if isinstance(value, dict):
        return {k: status_safe(v) for k, v in value.items()
                if k not in {"privateCve", "publicCve", "privateCveDetails", "privateRecoveryProof"}}
    if isinstance(value, list):
        return [status_safe(v) for v in value]
    return value


ARCHIVE_LIMIT = 64 * 1024**2  # Bound decompressed report + SBOM evidence.


def read_retained(path):
    db.require(path.is_file() and not path.is_symlink() and not path.parent.is_symlink(),
               "archive_unavailable")
    with gzip.open(path, "rb") as stream:
        body = stream.read(ARCHIVE_LIMIT + 1)
    db.require(len(body) <= ARCHIVE_LIMIT, "archive_budget")
    return json.loads(body)


def recover_private_projection(history, inventory, records, config):
    """Reuse only already-qualified, exact current retained reports; no new scan."""
    recovered = 0
    for row in inventory["rows"]:
        if (row["assessmentState"] != "healthy" or target_key(row) not in PRIVATE_CVE_TARGETS):
            continue
        record = records.get(row_key(row), {})
        previous = record.get("privateCve")
        if previous is None and target_key(row) in LEGACY_PUBLIC_CVE_TARGETS:
            previous = record.get("publicCve")
        overflow = isinstance(previous, dict) and previous.get("state") == "overflow"
        if record.get("evidenceKind") == "sharedImage" or (
                not overflow and "privateCveDetails" in record and "privateRecoveryProof" in record):
            continue
        job = record.get("jobID", "")
        if not isinstance(job, str) or not re.fullmatch(r"recurring-[a-f0-9]{32}", job):
            continue
        try:
            paths = list(history.glob("vulnerability-*/reports-" + job + ".json.gz"))
            db.require(len(paths) == 1, "archive_ambiguous")
            path = paths[0]
            db.require(record["workloadUID"] == row["workloadUID"]
                       and record["imageDigest"] == row["runningDigest"]
                       and record["container"] == row["container"]
                       and record["database"]["checksum"] == config["databaseChecksum"],
                       "archive_identity_mismatch")
            pair = read_retained(path)
            report, sbom = pair["vulnerability"], pair["sbom"]
            checked = check_report(report, sbom, row, {"metadata": record["database"]}, record["createdAt"])
            for key in ("createdAt","database","severities","fixesAvailable","manifestDigest","configDigest"):
                db.require(checked[key] == record[key], "archive_record_mismatch")
            summary = read_retained(path.parent / ("summary-" + job + ".json.gz"))
            annotations = summary["metadata"]["annotations"]
            db.require(annotations["kubescape.io/image-id"] == row["runningDigest"]
                       and annotations["kubescape.io/workload-container-name"] == row["container"]
                       and annotations["kubescape.io/wlid"] ==
                           workload_wlid(row)
                       and summary["spec"]["vulnerabilitiesRef"]["all"]["name"] == report["metadata"]["name"],
                       "archive_summary_mismatch")
            terminal_path = path.parent / ("terminal-" + job + ".json")
            db.require(terminal_path.is_file() and not terminal_path.is_symlink()
                       and terminal_path.stat().st_size <= 65536, "archive_terminal_missing")
            terminal = json.loads(terminal_path.read_text())
            db.require(terminal["state"] == "succeeded" and terminal["phase"] == "completed",
                       "archive_terminal_invalid")
            matches = report["spec"]["payload"]["matches"]
            findings = private_cve_findings(matches)
            if overflow:
                # Reproduce the old lossless boundary decision before replacing
                # it. Never promote an overflow above the new bound or trust an
                # archive that would not have produced the original verdict.
                if findings["state"] != "complete":
                    continue
                db.require(previous == private_cve_findings(matches, limit=LEGACY_PRIVATE_CVE_LIMIT),
                           "archive_projection_mismatch")
            else:
                db.require(previous is None or previous == findings, "archive_projection_mismatch")
            record["privateCve"] = findings
            record["privateCveDetails"] = private_cve_details(matches)
            record["privateRecoveryProof"] = private_recovery_proof(report, row, record)
            recovered += 1
        except (db.Rejected, OSError, ValueError, TypeError, KeyError, EOFError):
            # Never promote missing, malformed, oversized or inconsistent retained evidence.
            continue
    return recovered


def coverage_status(api, inventory, records, config):
    """Publish the dedicated private issue projection and prepare private status."""
    path = CORE + "/namespaces/kubescape/configmaps/kubescape-assessment-status"
    private_path = CORE + "/namespaces/kubescape/configmaps/kubescape-private-cve"
    projection = dict(api.call(private_path))
    diagnostics = Counter()
    rich = rich_private_cve_projection(inventory, records, diagnostics)
    def encode():
        projection["data"] = {"projection.json": json.dumps(rich, separators=(",", ":"))}
        return len(json.dumps(projection).encode("utf-8"))
    size = encode()
    if size > MAX_PRIVATE_PROJECTION_BYTES and rich["schema"] == 3:
        # Recovery proof is all-or-nothing. Never trim an all-severity ID set.
        for entry in rich["entries"]:
            entry["recoveryProof"] = None
        size = encode()
    if size > MAX_PRIVATE_PROJECTION_BYTES and rich["schema"] >= 2:
        # Keep all advisory IDs and qualified coverage. Detail display has a
        # separate bound; explicitly account for omitted matches, never
        # silently truncate the authoritative summary.
        for entry in rich["entries"]:
            for detail in entry["containers"]:
                if detail["matches"]:
                    detail["omittedMatches"] += len(detail["matches"])
                    detail["matches"] = []
                    detail["detailState"] = "partial"
        size = encode()
    db.require(size <= MAX_PRIVATE_PROJECTION_BYTES, "private_projection_too_large")
    api.call(private_path, projection, "PUT")
    status = api.call(path)
    data = status.setdefault("data", {})
    # Clean any older projection embedded in the general vulnerability status.
    if "vulnerability.json" in data:
        try:
            data["vulnerability.json"] = json.dumps(status_safe(json.loads(data["vulnerability.json"])), separators=(",", ":"))
        except (ValueError, TypeError):
            data.pop("vulnerability.json")
    data["coverage.json"] = json.dumps(inventory, separators=(",", ":"))
    cohort = cohort_health(inventory, config)
    data["coverage.state"] = cohort["state"]
    data["coverage.validUntil"] = cohort["validUntil"]
    data["coverage.expected"] = str(cohort["expected"])
    data["coverage.healthy"] = str(cohort["healthy"])
    data["coverage.observedAt"] = inventory["generatedAt"]
    # Aggregate projection eligibility only, not a publisher receipt or proof
    # of recovery. One fixed reason per reviewed workload, no identities.
    data["coverage.privateCveReasons"] = json.dumps(dict(diagnostics), sort_keys=True, separators=(",", ":"))
    return path, status


def scan_failure_reason(status):
    # Exact fixed codes from the pinned engine's terminal status contract.
    # Never copy arbitrary server error messages into general health or logs.
    reason = status.get("reason")
    allowed = ("image_too_large", "sbom_too_large", "sbom_incomplete",
               "image_auth_failed", "image_not_found", "image_schema_unsupported",
               "scanner_oom_killed", "scan_timeout", "rate_limit_exceeded",
               "sbom_generation_failed", "cve_matching_failed", "sbom_storage_failed",
               "result_storage_failed", "result_upload_failed", "unexpected_error")
    return reason if isinstance(reason, str) and reason in allowed else "scan_failed"


def refresh_inventory(api, config, history):
    """Revalidate current running digests without submitting any assessment."""
    with locked(history):
        db.require(not (history / "inflight.json").exists(), "uncertain_previous_request")
        source = history / "records.json"
        records = json.loads(source.read_text()) if source.exists() else {}
        inventory = coverage(workload_rows(api), records, config)
        recovered = recover_private_projection(history, inventory, records, config)
        if recovered:
            atomic(source, records)
        path, status = coverage_status(api, inventory, records, config)
        db.require(len(json.dumps(status)) < 900000, "inventory_too_large")
        api.call(path, status, "PUT")
        # Never publish raw inventory in logs. This is a successful observation,
        # not proof every workload has a current successful vulnerability scan.
        print(json.dumps({"mode": "refresh", "state": "observed",
                          "coverage": cohort_health(inventory, config),
                          "privateCveReasons": json.loads(status["data"]["coverage.privateCveReasons"]),
                          "recoveredProjections": recovered}), flush=True)
        return 0


def completion_markers(history, mode, now, digest):
    """One completed batch per UTC six-hour slot; posture remains daily."""
    day = now.strftime("%Y-%m-%d")
    legacy = history / (mode + "-" + day + "-" + digest + ".done")
    if mode != "vulnerability":
        return legacy, (legacy,)
    slot = now.hour // 6
    marker = history / (mode + "-" + day + "-slot" + str(slot) + "-" + digest + ".done")
    # A legacy daily marker may originate from a manually started Job.
    # Conservatively block all remaining slots on that UTC cutover day.
    return marker, (marker, legacy)


class Runner:
    def __init__(self, api, config, expected, history, mode):
        self.api, self.config, self.expected, self.history, self.mode = api, config, expected, history, mode
        self.deadline = time.monotonic() + 25 * 60
        self.run_id = mode + "-" + db.utcnow().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.work = history / self.run_id
        self.result = {"schemaVersion": 1, "runID": self.run_id, "mode": mode, "state": "unknown",
                       "startedAt": db.stamp(db.utcnow()), "reasons": [], "assessments": []}
        self.load_state()

    def load_state(self):
        path = self.history / "records.json"
        self.records = json.loads(path.read_text()) if path.exists() else {}
        previous_path = self.history / ("latest-" + self.mode + ".json")
        previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
        self.result["lastSuccessfulAssessmentAt"] = previous.get("lastSuccessfulAssessmentAt") or max(
            (a["createdAt"] for a in previous.get("assessments", []) if a.get("createdAt")), default=None)
        if not self.result["lastSuccessfulAssessmentAt"] and self.mode == "vulnerability":
            self.result["lastSuccessfulAssessmentAt"] = max(
                (r["createdAt"] for r in self.records.values() if r.get("createdAt")), default=None)

    def save(self, name, value):
        # Gzip full reports to keep retained evidence small; never overwrite a run.
        self.work.mkdir(exist_ok=True)
        path = self.work / (name + ".json.gz")
        with gzip.open(path, "wt") as output:
            json.dump(value, output, sort_keys=True)
        with path.open("rb") as source:
            os.fsync(source.fileno())

    def publish(self, terminal=True):
        if terminal:
            self.result["finishedAt"] = db.stamp(db.utcnow())
        atomic(self.history / ("latest-" + self.mode + ".json"), self.result)
        self.save("result", self.result)
        try:
            inventory = coverage(workload_rows(self.api), self.records, self.config)
            atomic(self.history / "coverage.json", inventory)
            path, status = coverage_status(self.api, inventory, self.records, self.config)
            status["data"][self.mode + ".json"] = json.dumps(status_safe(self.result), separators=(",", ":"))
            # Upgrade the already persisted result of the other mode to scalar
            # health fields without requesting another assessment during rollout.
            other = "posture" if self.mode == "vulnerability" else "vulnerability"
            if other + ".lastSuccessfulAssessmentAt" not in status["data"] and other + ".json" in status["data"]:
                try:
                    previous = json.loads(status["data"][other + ".json"])
                    success = previous.get("lastSuccessfulAssessmentAt") or max(
                        (a["createdAt"] for a in previous.get("assessments", []) if a.get("createdAt")), default="")
                    status["data"][other + ".lastSuccessfulAssessmentAt"] = success
                    status["data"][other + ".state"] = previous["state"]
                except (KeyError, TypeError, ValueError):
                    pass
            status["data"][self.mode + ".lastSuccessfulAssessmentAt"] = self.result["lastSuccessfulAssessmentAt"] or ""
            status["data"][self.mode + ".state"] = self.result["state"]
            if self.mode == "posture":
                # Only a newly verified report under this scope can satisfy it.
                status["data"]["posture.scopeCount"] = str(max(
                    (a.get("requiredWorkloadCount", 0) for a in self.result["assessments"]), default=0))
            if self.mode == "vulnerability":
                status["data"]["database.built"] = self.expected["metadata"]["built"]
            db.require(len(json.dumps(status)) < 900000, "inventory_too_large")
            self.api.call(path, status, "PUT")
        except Exception as error:
            self.result.update(state="unknown", reasons=self.result["reasons"] + ["status_publish_unavailable"])
            atomic(self.history / ("latest-" + self.mode + ".json"), self.result)
            self.save("result", self.result)
        print(json.dumps(status_safe(self.result), sort_keys=True), flush=True)

    def preflight(self):
        db.metadata(self.expected["metadata"])
        db.require(self.config["resourceBudgetQualified"] is True, "resource_budget_unqualified")
        db.require(self.config["databaseChecksum"] == self.expected["metadata"]["checksum"], "config_database_mismatch")
        engine = component_pod(self.api, "kubevuln")
        for component in ("storage", "kubescape", "grype-offline-db"):
            component_pod(self.api, component)
        release = self.api.get("/apis/helm.toolkit.fluxcd.io/v2", "helmreleases", "kubescape")
        db.require(release["status"].get("observedGeneration") == release["metadata"]["generation"]
                   and any(c["type"] == "Ready" and c["status"] == "True" and c.get("observedGeneration") == release["metadata"]["generation"]
                           for c in release["status"].get("conditions", [])), "database_refresh_in_progress")
        db.require(release["spec"]["values"]["grypeOfflineDB"]["image"]["sha"] == self.expected["indexDigest"], "gitops_digest_mismatch")
        fs = os.statvfs(self.history)
        db.require(fs.f_bavail * fs.f_frsize >= self.config["minimumDatabaseFreeBytes"], "database_pvc_low_space")
        history_bytes = sum(p.stat().st_size for p in self.history.rglob("*") if p.is_file())
        db.require(history_bytes < self.config["maximumHistoryBytes"], "history_budget_exhausted")
        self.result["historyBytesBefore"] = history_bytes
        self.result["database"] = {"built": self.expected["metadata"]["built"],
                                   **db.database_freshness(self.expected["metadata"]["built"])}
        usage = self.idle_resources(engine)
        self.result["resourceBefore"] = {"enginePodUID": engine["metadata"]["uid"], "usage": usage,
                                         "databaseFreeBytes": fs.f_bavail * fs.f_frsize}
        for node in self.api.items(CORE, "nodes"):
            db.require(any(c["type"] == "Ready" and c["status"] == "True" for c in node["status"]["conditions"]), "node_unhealthy")
            for condition in node["status"]["conditions"]:
                if condition["type"] in {"MemoryPressure", "DiskPressure", "PIDPressure"}:
                    db.require(condition["status"] == "False", "node_resource_pressure")
        for pod in self.api.items(CORE, "pods"):
            if pod["metadata"]["namespace"] in {"gitea-runners", "gitea", "gitea-jobs", "wowdecomp-ci-jobs"} and pod["status"].get("phase") in {"Running", "Pending"}:
                db.require(not any(owner["kind"] == "Job" for owner in pod["metadata"].get("ownerReferences", [])), "ci_build_active")
        node_metrics = self.api.items("/apis/metrics.k8s.io/v1beta1", "nodes")
        nodes = {n["metadata"]["name"]: n for n in self.api.items(CORE, "nodes")}
        involved = {component_pod(self.api, c)["spec"]["nodeName"] for c in ("kubevuln", "kubescape", "storage")}
        for name in involved:
            node_metric = next(m for m in node_metrics if m["metadata"]["name"] == name)
            db.require(db.age_hours(node_metric["timestamp"]) <= 0.05, "resource_metrics_stale")
            for unit in ("cpu", "memory"):
                db.require(quantity(node_metric["usage"][unit]) < quantity(nodes[name]["status"]["allocatable"][unit]) * 0.85, "node_load_high")
        self.storage_gate()
        service("kubevuln", "/v1/readiness")
        db.require(service("kubevuln", "/v1/diagnostics")["queueDepth"] == 0, "engine_not_idle")
        # The pinned scanner's no-ID status refers to its latest user scan.
        db.require(str(service("kubescape", "/v1/status").get("type", "")).lower() != "busy", "posture_scanner_busy")
        return engine

    def idle_resources(self, engine):
        # CPU is averaged over the metrics window, which can still include our
        # preceding completed scan. Wait only on this resource gate, never retry
        # a submission. All remaining preflight/freshness gates still run after it.
        deadline = time.monotonic() + IDLE_WAIT_SECONDS
        busy_timestamp = None
        while True:
            metrics = self.api.items("/apis/metrics.k8s.io/v1beta1", "pods", NS)
            metric = next(m for m in metrics if m["metadata"]["name"] == engine["metadata"]["name"])
            usage = next(c["usage"] for c in metric["containers"] if c["name"] == "kubevuln")
            self.result.setdefault("resourcePreflight", []).append({
                "observedAt": db.stamp(db.utcnow()), "enginePodUID": engine["metadata"]["uid"],
                "timestamp": metric["timestamp"], "window": metric.get("window"), "usage": usage})
            db.require(db.age_hours(metric["timestamp"]) <= 0.05, "resource_metrics_stale")
            db.require(quantity(usage["memory"]) < self.config["maximumWorkingSetBytes"], "memory_budget_exhausted")
            timestamp = db.timestamp(metric["timestamp"])
            if (quantity(usage["cpu"]) < self.config["maximumIdleCPUCores"]
                    and (busy_timestamp is None or timestamp > busy_timestamp)):
                return usage
            busy_timestamp = timestamp if busy_timestamp is None else max(timestamp, busy_timestamp)
            now = time.monotonic()
            remaining = deadline - now
            db.require(remaining > 0, "scanner_busy")
            budget = self.deadline - self.config["scanTimeoutSeconds"] - 60 - now
            db.require(budget > 0, "execution_time_budget")
            time.sleep(min(IDLE_POLL_SECONDS, remaining, budget))
            db.require(component_pod(self.api, "kubevuln")["metadata"]["uid"] == engine["metadata"]["uid"], "engine_restarted")

    def observe(self):
        metrics = self.api.items("/apis/metrics.k8s.io/v1beta1", "pods", NS)
        sample = {"observedAt": db.stamp(db.utcnow()), "components": [
            {"pod": m["metadata"]["name"], "timestamp": m["timestamp"], "containers": m["containers"]}
            for m in metrics if m["metadata"]["name"].startswith(("kubevuln-", "kubescape-", "storage-"))]}
        self.result.setdefault("resourceSamples", []).append(sample)
        engine = next(m for m in metrics if m["metadata"]["name"].startswith("kubevuln-"))
        usage = next(c["usage"] for c in engine["containers"] if c["name"] == "kubevuln")
        db.require(quantity(usage["memory"]) < self.config["maximumWorkingSetBytes"], "memory_budget_exhausted")

    def storage_gate(self):
        volumes = self.api.items("/apis/longhorn.io/v1beta2", "volumes", "longhorn-system")
        replicas = self.api.items("/apis/longhorn.io/v1beta2", "replicas", "longhorn-system")
        nodes = self.api.items("/apis/longhorn.io/v1beta2", "nodes", "longhorn-system")
        for claim in ("kubescape-kubevuln", "kubescape-storage"):
            pvc = self.api.get(CORE, "persistentvolumeclaims", claim)
            db.require(pvc["status"]["phase"] == "Bound", "storage_unavailable")
            volume = next(v for v in volumes if v["metadata"]["name"] == pvc["spec"]["volumeName"])
            db.require(volume["status"]["robustness"] == "healthy", "longhorn_unhealthy")
            live = [r for r in replicas if r["spec"]["volumeName"] == volume["metadata"]["name"]
                    and not r["metadata"].get("deletionTimestamp") and not r["spec"].get("failedAt")]
            db.require(len(live) == 3 and all(r["status"].get("currentState") == "running" for r in live), "longhorn_replica_unhealthy")
            for replica in live:
                node = next(n for n in nodes if n["metadata"]["name"] == replica["spec"]["nodeID"])
                disk = next(d for d in node["status"]["diskStatus"].values() if d["diskUUID"] == replica["spec"]["diskID"])
                db.require(disk["storageAvailable"] >= max(self.config["minimumReplicaDiskFreeBytes"],
                           disk["storageMaximum"] * self.config["minimumReplicaDiskFreeFraction"]), "replica_disk_low_space")

    def job(self, manifest, timeout=600):
        name = manifest["metadata"]["name"]
        # Audit jobs have zero retries, no credentials and read-only PVC mounts.
        self.api.call(BATCH + "/namespaces/kubescape/jobs?dryRun=All", manifest)
        self.api.call(BATCH + "/namespaces/kubescape/jobs", manifest)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.api.get(BATCH, "jobs", name).get("status", {})
            db.require(not status.get("failed"), "storage_audit_failed")
            if status.get("succeeded"):
                break
            time.sleep(3)
        else:
            raise db.Rejected("storage_audit_timeout")
        pods = [p for p in self.api.items(CORE, "pods", NS) if p["metadata"].get("labels", {}).get("job-name") == name]
        db.require(len(pods) == 1, "audit_pod_ambiguous")
        result = {}
        for container in manifest["spec"]["template"]["spec"]["containers"]:
            path = CORE + "/namespaces/kubescape/pods/" + pods[0]["metadata"]["name"] + "/log?container=" + container["name"]
            result[container["name"]] = self.api.call(path)
        return result

    def audit(self, engine):
        manifest = client.audit_manifest(engine, "assessment-db-audit-" + self.run_id[-21:])
        result = self.job(manifest)
        audit = {"checkedAt": db.stamp(db.utcnow()), "podUID": engine["metadata"]["uid"],
                 "sqlite": result["sqlite"], "checksum": result["checksum"]}
        self.save("cache-audit", audit)
        # Reports filesystem check is a separate short read-only Job on storage's node.
        storage = component_pod(self.api, "storage")
        manifest = client.audit_manifest(storage, "assessment-report-audit-" + self.run_id[-21:])
        spec = manifest["spec"]["template"]["spec"]
        spec["volumes"][0]["persistentVolumeClaim"]["claimName"] = "kubescape-storage"
        container = spec["containers"][1]
        container["command"] = ["python3", "-B", "-c", "import os,json; f=os.statvfs('/cache'); print(json.dumps({'freeBytes':f.f_bavail*f.f_frsize,'capacityBytes':f.f_blocks*f.f_frsize}))"]
        spec["containers"] = [container]
        reports = self.job(manifest, timeout=120)["sqlite"]
        db.require(reports["freeBytes"] >= self.config["minimumReportFreeBytes"], "reports_pvc_low_space")
        self.result["reportFilesystemBefore"] = reports
        return audit

    def gate(self, audit):
        engine = component_pod(self.api, "kubevuln")
        mirror = component_pod(self.api, "grype-offline-db")
        evidence = {"audit": audit, "enginePodUID": engine["metadata"]["uid"],
                    "diagnostics": service("kubevuln", "/v1/diagnostics"), "assessment": None,
                    "declaredDigest": self.api.get("/apis/helm.toolkit.fluxcd.io/v2", "helmreleases", "kubescape")["spec"]["values"]["grypeOfflineDB"]["image"]["sha"],
                    "mirrorImage": mirror["spec"]["containers"][0]["image"],
                    "mirrorImageID": mirror["status"]["containerStatuses"][0]["imageID"],
                    "mirrorMetadata": http("http://grype-offline-db:80/databases/v6/latest.json")}
        result = client.evaluate(evidence, self.expected)
        self.save("freshness-" + str(time.time_ns()), result)
        db.require(result["canAssess"] and result["integrity"] == "verified", result["reasons"][0])
        self.result["database"] = {"built": self.expected["metadata"]["built"],
                                   **db.database_freshness(self.expected["metadata"]["built"])}
        return result

    def fence(self):
        path = self.history / "inflight.json"
        if not path.exists():
            return
        previous = json.loads(path.read_text())
        if previous.get("jobID"):
            try:
                previous["observedStatus"] = service("kubevuln", "/v1/scanStatus/" + previous["jobID"])
            except Exception:
                previous["observedStatus"] = {"state": "unknown"}
        elif previous.get("scanID"):
            try:
                previous["observedStatus"] = service("kubescape", "/v1/status?id=" + previous["scanID"])
            except Exception:
                previous["observedStatus"] = {"state": "unknown"}
        self.save("previous-request", previous)
        raise db.Rejected("uncertain_previous_request")

    def vulnerability(self, row, audit):
        self.preflight()
        self.gate(audit)
        # Discover by actual immutable digest; the server's image-derived name is not a new-scan signal.
        def existing(kind):
            objects = self.api.items(STORAGE, kind, NS)
            matches = [o for o in objects if o["metadata"].get("annotations", {}).get("kubescape.io/image-id") == row["runningDigest"]]
            if not matches:
                digest = row["runningDigest"].split(":")[-1]
                matches = [o for o in objects if digest in o["metadata"]["name"]]
            db.require(len(matches) <= 1, "report_ambiguous")
            return self.api.get(STORAGE, kind, matches[0]["metadata"]["name"]) if matches else None
        native_summary_name(row)  # Validate the exact summary contract before POST.
        previous = existing("vulnerabilitymanifests")
        previous_sbom = existing("sbomsyfts")
        self.save("previous-" + row["container"], {"vulnerability": previous, "sbom": previous_sbom})
        # Keep only the previous report timestamp needed for freshness checks.
        # Do not hold an entire old SBOM and report while reading the new pair.
        if previous is not None:
            previous = {"spec": {"metadata": {"report": {"createdAt":
                previous["spec"]["metadata"]["report"]["createdAt"]}}}}
        del previous_sbom
        job = "recurring-" + uuid.uuid4().hex
        started = db.stamp(db.utcnow())
        # Direct evidence requires the scanner-generated WLID/container summary.
        # Any peer image-level attribution is separately labelled and validated.
        payload = {"imageTag": row["runningDigest"], "imageHash": row["runningDigest"], "jobID": job,
                   "wlid": workload_wlid(row),
                   "containerName": row["container"], "args": {"platform": "linux/amd64", "forceRescan": True}}
        db.require(time.monotonic() + self.config["scanTimeoutSeconds"] + 60 <= self.deadline, "execution_time_budget")
        atomic(self.history / "inflight.json", {"jobID": job, "enginePodUID": audit["podUID"], "workload": row, "submittedAt": started})
        self.save("request-" + job, payload)
        # POST is never retried, including a lost HTTP response.
        try:
            self.save("response-" + job, service("kubevuln", "/v1/scanImage", payload))
        except Exception:
            # An uncertain POST may have reached the server. Query its ID, never resubmit.
            self.save("uncertain-status-" + job, service("kubevuln", "/v1/scanStatus/" + job))
        deadline = time.monotonic() + self.config["scanTimeoutSeconds"]
        while time.monotonic() < deadline:
            db.require(component_pod(self.api, "kubevuln")["metadata"]["uid"] == audit["podUID"], "engine_restarted")
            self.observe()
            status = service("kubevuln", "/v1/scanStatus/" + job)
            if status["state"] in TERMINAL:
                break
            time.sleep(3)
        else:
            raise db.Rejected("scan_timeout")
        atomic(self.work / ("terminal-" + job + ".json"), status)
        db.require(component_pod(self.api, "kubevuln")["metadata"]["uid"] == audit["podUID"], "engine_restarted")
        db.require(status["state"] != "abandoned", "scan_abandoned")
        db.require(status["state"] == "failed" or status["phase"] == "completed", "scan_status_invalid")
        # Only an observed terminal status from this engine clears the durable
        # submission fence. Report validation can then fail without blocking peers.
        self.clear_fence()
        db.require(status["state"] == "succeeded" and status["phase"] == "completed", scan_failure_reason(status))
        report, sbom = existing("vulnerabilitymanifests"), existing("sbomsyfts")
        db.require(report is not None and sbom is not None, "report_missing")
        result = check_report(report, sbom, row, self.expected, started, previous)
        self.save("reports-" + job, {"vulnerability": report, "sbom": sbom})
        # Independent named rereads, including the workload summary attribution.
        for kind, value in (("vulnerabilitymanifests", report), ("sbomsyfts", sbom)):
            db.require(self.api.get(STORAGE, kind, value["metadata"]["name"])["spec"] == value["spec"], "report_reread_mismatch")
        summary = self.api.get(STORAGE, "vulnerabilitymanifestsummaries", native_summary_name(row), row["namespace"])
        annotations = summary["metadata"]["annotations"]
        db.require(annotations["kubescape.io/image-id"] == row["runningDigest"]
                   and annotations["kubescape.io/workload-container-name"] == row["container"]
                   and annotations["kubescape.io/wlid"] == payload["wlid"]
                   and summary["spec"]["vulnerabilitiesRef"]["all"]["name"] == report["metadata"]["name"], "summary_attribution_mismatch")
        self.save("summary-" + job, summary)
        current = targets(workload_rows(self.api), {**self.config, "targets": [{"identity": target_key(row)}]})
        db.require(any(row_key(r) == row_key(row) and r["generation"] == row["generation"] and r["podUIDs"] == row["podUIDs"] for r in current), "workload_image_changed")
        self.gate(audit)
        result.update(evidenceKind="direct", jobID=job, durationSeconds=(db.timestamp(status["finishedAt"]) - db.timestamp(status["startedAt"])).total_seconds(),
                      workloadUID=row["workloadUID"], imageDigest=row["runningDigest"], container=row["container"])
        # The report and workload attribution have now passed independent rereads.
        if target_key(row) in PRIVATE_CVE_TARGETS:
            matches = report["spec"]["payload"]["matches"]
            result["privateCve"] = private_cve_findings(matches)
            result["privateCveDetails"] = private_cve_details(matches)
            result["privateRecoveryProof"] = private_recovery_proof(report, row, result)
        self.records[row_key(row)] = result
        atomic(self.history / "records.json", self.records)
        self.result["assessments"].append(result)
        self.result["lastSuccessfulAssessmentAt"] = result["createdAt"]

    def clear_fence(self):
        (self.history / "inflight.json").unlink()
        fd = os.open(self.history, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def posture(self, audit):
        self.gate(audit)  # Also prevents DB hydration / scan overlap.
        scanner = component_pod(self.api, "kubescape")
        old = self.api.items(STORAGE, "workloadconfigurationscansummaries")
        self.save("previous-posture", [self.api.get(STORAGE, "workloadconfigurationscansummaries", s["metadata"]["name"], s["metadata"]["namespace"]) for s in old])
        started = db.stamp(db.utcnow())
        atomic(self.history / "inflight.json", {"mode": "posture", "scannerPodUID": scanner["metadata"]["uid"], "submittedAt": started})
        response = service("kubescape", "/v1/scan?keep=true", {"targetType": "framework", "targetNames": ["nsa"], "hostScanner": False, "keepLocal": True})
        scan_id = response.get("id") or response.get("ID")
        db.require(bool(scan_id), "posture_id_missing")
        atomic(self.history / "inflight.json", {"mode": "posture", "scanID": scan_id, "scannerPodUID": scanner["metadata"]["uid"], "submittedAt": started})
        deadline = time.monotonic() + self.config["scanTimeoutSeconds"]
        while time.monotonic() < deadline:
            db.require(component_pod(self.api, "kubescape")["metadata"]["uid"] == scanner["metadata"]["uid"], "posture_scanner_restarted")
            self.observe()
            response = service("kubescape", "/v1/results?id=" + scan_id + "&keep=true")
            if str(response.get("type", "")).lower() not in {"busy", ""}:
                break
            time.sleep(3)
        else:
            raise db.Rejected("posture_timeout")
        self.save("scanner-result", response)
        result = response.get("response")
        if isinstance(result, str):
            result = json.loads(result)
        summaries = [self.api.get(STORAGE, "workloadconfigurationscansummaries", s["metadata"]["name"], s["metadata"]["namespace"])
                     for s in self.api.items(STORAGE, "workloadconfigurationscansummaries")]
        assessed = check_posture(result, summaries, started, self.config["postureWorkloads"])
        self.save("persisted-posture", summaries)
        again = [self.api.get(STORAGE, "workloadconfigurationscansummaries", s["metadata"]["name"], s["metadata"]["namespace"]) for s in summaries]
        db.require(again == summaries, "posture_reread_mismatch")
        self.gate(audit)
        self.result["assessments"].append({**assessed, "scanID": scan_id, "createdAt": db.stamp(db.utcnow())})
        self.result["lastSuccessfulAssessmentAt"] = self.result["assessments"][-1]["createdAt"]
        self.clear_fence()

    def attempt(self, row, state, reasons):
        self.records.setdefault(row_key(row), {})["lastAttempt"] = {
            "state": state, "checkedAt": db.stamp(db.utcnow()), "reasons": reasons,
            "databaseChecksum": self.config["databaseChecksum"]}
        atomic(self.history / "records.json", self.records)

    def batch(self, selected, outcomes, audit):
        batch = select_batch(selected, self.records, self.config)
        deadline = self.deadline  # Leave room inside the 30-minute Job.
        for row in selected:
            outcome = next(o for o in outcomes if o["identity"] == target_key(row))
            previous = self.records.get(row_key(row), {})
            outcome.update(lastSuccessfulScan=previous.get("createdAt"))
            if row not in batch:
                outcome.update(state="skipped", reasons=["daily_batch_limit"])
        for row in batch:
            outcome = next(o for o in outcomes if o["identity"] == target_key(row))
            if time.monotonic() + self.config["scanTimeoutSeconds"] + 60 > deadline:
                outcome.update(state="skipped", reasons=["execution_time_budget"])
                continue
            self.attempt(row, "unknown", ["assessment_in_progress"])
            try:
                # A cache audit is valid for 15 minutes; refresh it only when needed.
                if db.age_hours(audit["checkedAt"]) > 0.2:
                    audit = self.audit(component_pod(self.api, "kubevuln"))
                self.vulnerability(row, audit)
                outcome.update(state="healthy", reasons=[], lastSuccessfulScan=self.records[row_key(row)]["createdAt"])
            except Exception as error:
                uncertain = (self.history / "inflight.json").exists()
                reason = getattr(error, "reason", "evidence_unavailable")
                state = "unknown" if uncertain or not isinstance(error, db.Rejected) else "skipped" if reason == "execution_time_budget" else "stale" if reason == "stale_database" else "failed"
                self.attempt(row, state, [reason])
                outcome.update(state=state, reasons=[reason])
                if uncertain:
                    # Preserve peers' results and do not issue another POST.
                    for pending in batch[batch.index(row) + 1:]:
                        next(o for o in outcomes if o["identity"] == target_key(pending)).update(
                            state="skipped", reasons=["uncertain_previous_request"])
                    raise db.Rejected("uncertain_previous_request") from None
            self.publish(terminal=False)  # Persist progress even if a later target fails.
        states = {o["state"] for o in outcomes if o["reasons"] != ["daily_batch_limit"]}
        self.result["state"] = "unknown" if "unknown" in states else "failed" if "failed" in states else "stale" if "stale" in states else "healthy" if self.result["assessments"] else "unknown"
        self.result["reasons"] = sorted({r for o in outcomes for r in o["reasons"] if r != "daily_batch_limit"})

    def run(self):
        with locked(self.history):
            try:
                self.load_state()  # Re-read under the lock before any record mutation.
                self.fence()
                digest = hashlib.sha256(json.dumps(self.config["targets"], sort_keys=True).encode()).hexdigest()[:12]
                completed, blockers = completion_markers(self.history, self.mode, db.utcnow(), digest)
                db.require(not any(path.exists() for path in blockers), "duplicate_execution")
                self.result["reasons"] = ["assessment_in_progress"]
                self.publish(terminal=False)
                self.result["reasons"] = []
                engine = self.preflight()
                audit = self.audit(engine)
                self.gate(audit)
                if self.mode == "vulnerability":
                    selected, outcomes = resolve_targets(workload_rows(self.api), self.config)
                    self.result["targets"] = outcomes
                    self.batch(selected, outcomes, audit)
                else:
                    self.posture(audit)
                    self.result["state"] = "healthy"
                # A completed partial batch is retried in the next scheduled rotation.
                atomic(completed, {"runID": self.run_id})
            except db.Rejected as error:
                self.result.update(state="stale" if error.reason == "stale_database" else "unknown" if error.reason == "uncertain_previous_request" else "failed", reasons=[error.reason])
            except Exception as error:
                self.result.update(state="unknown", reasons=["evidence_unavailable"], errorType=type(error).__name__)
            self.publish()
        return EXIT[self.result["state"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("vulnerability", "posture", "inventory", "refresh"))
    parser.add_argument("--config", type=Path, default=Path("/config/config.json"))
    parser.add_argument("--expected", type=Path, default=Path("/config/grype-database.json"))
    parser.add_argument("--history", type=Path, default=Path("/history"))
    parser.add_argument("--local", action="store_true", help="Read-only inventory through workstation kubectl")
    args = parser.parse_args()
    config, expected = json.loads(args.config.read_text()), json.loads(args.expected.read_text())
    config["databaseChecksum"] = expected["metadata"]["checksum"]
    api = API(args.local)
    try:
        if args.mode == "inventory":
            records = json.loads((args.history / "records.json").read_text()) if (args.history / "records.json").exists() else {}
            print(json.dumps(coverage(workload_rows(api), records, config), sort_keys=True))
            return 0
        db.require(not args.local, "local_read_only")
        if args.mode == "refresh":
            try:
                return refresh_inventory(api, config, args.history)
            except db.Rejected as error:
                if error.reason != "duplicate_execution":
                    raise
                # A concurrent qualified scan owns the lock. Do not renew
                # evidence or trigger a redundant Job failure for this skip.
                print(json.dumps({"mode": "refresh", "state": "skipped", "reason": "assessment_active"}))
                return 0
        return Runner(api, config, expected, args.history, args.mode).run()
    except Exception as error:
        print(json.dumps({"state": "failed", "reasons": [getattr(error, "reason", "runner_unavailable")], "errorType": type(error).__name__}))
        return 4


if __name__ == "__main__":
    sys.exit(main())
