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
                       "ready": ready(pod) and status.get("ready", False)}
                key = row_key(row)
                if key in seen:
                    previous = next(r for r in rows if row_key(r) == key)
                    previous["podUIDs"].extend(row["podUIDs"])
                    previous["ready"] = previous["ready"] and row["ready"]
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
    return sorted(rows, key=row_key)


def target_key(row):
    return "/".join(row[k] for k in ("namespace", "kind", "workload", "container"))


def row_key(row):
    return target_key(row) + "/" + row["workloadUID"] + "/" + (row["runningDigest"] or "")


def targets(rows, config):
    result = []
    for target in config["targets"]:
        matches = [r for r in rows if target_key(r) == target["identity"] and r["containerType"] == "containers"]
        db.require(len(matches) == 1 and matches[0]["ready"] and matches[0]["runningDigest"], "workload_not_ready")
        db.require(matches[0]["kind"] == "Deployment", "unsupported_workload")
        result.append(matches[0])
    return result


def coverage(rows, records, config, now=None):
    now = now or db.utcnow()
    selected = {t["identity"] for t in config["targets"]}
    for row in rows:
        record = records.get(row_key(row), {})
        row.update(lastSuccessfulScan=record.get("createdAt"), database=record.get("database"),
                   scanAgeHours=None, severities=record.get("severities"), fixesAvailable=record.get("fixesAvailable"),
                   assessmentState="never_scanned", exclusionReason=None, lastAttempt=record.get("lastAttempt"))
        current, invalid = False, False
        if record.get("createdAt"):
            try:
                age = db.age_hours(record["createdAt"], now)
                row["scanAgeHours"] = round(age, 4)
                current = (age <= config["maximumScanAgeHours"]
                    and db.age_hours(record["database"]["built"], now) <= 72
                    and record["database"]["checksum"] == config["databaseChecksum"])
            except (db.Rejected, KeyError):
                invalid = True
        if target_key(row) not in selected:
            row.update(assessmentState="deliberately_excluded", exclusionReason="Outside the reviewed initial cohorts; critical infrastructure and other workloads await qualification")
        elif not row["runningDigest"] or row["containerType"] != "containers":
            row.update(assessmentState="unsupported", exclusionReason="No running registry digest or unsupported container type")
        elif record.get("lastAttempt", {}).get("state") in {"failed", "unknown"}:
            row["assessmentState"] = record["lastAttempt"]["state"]
        elif record.get("createdAt"):
            row["assessmentState"] = "unknown" if invalid else "healthy" if current else "stale"
    return {"schemaVersion": 1, "generatedAt": db.stamp(now),
            "validUntil": db.stamp(now + timedelta(hours=26)), "rows": rows,
            "counts": dict(Counter(r["assessmentState"] for r in rows)),
            "healthMeaning": "Assessment freshness and integrity, not absence of vulnerabilities"}


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


def check_posture(result, summaries, started):
    # Full scanner response is authoritative for Action Required / Not Evaluated.
    db.require(isinstance(result, dict) and result.get("summaryDetails"), "posture_result_incomplete")
    frameworks = result["summaryDetails"].get("frameworks", [])
    db.require(bool(frameworks), "posture_controls_empty")
    controls = []
    for framework in frameworks:
        values = framework.get("controls", {})
        controls.extend(values.values() if isinstance(values, dict) else values)
    db.require(bool(controls), "posture_controls_empty")
    statuses = Counter()
    for control in controls:
        info = control.get("statusInfo", {})
        substatus = info.get("subStatus", "")
        status = control.get("status", info.get("status", {}))
        status = status.get("status") if isinstance(status, dict) else status
        status = str(status).lower().replace(" ", "")
        translated = {"passed": "Passed", "failed": "Failed", "actionrequired": "Action Required",
                      "skipped": "Not Evaluated", "notimplemented": "Not Evaluated", "irrelevant": "Not Evaluated"}
        category = translated.get(status, "Not Evaluated")
        if substatus in {"notEvaluated", "incompleteCoverage", "irrelevant"}:
            category = "Not Evaluated"
        elif substatus in {"requires review", "manual review", "configuration", "integration"} and status != "failed":
            category = "Action Required"
        statuses[category] += 1
    db.require(statuses["Passed"] + statuses["Failed"] > 0, "posture_not_evaluated")
    db.require(bool(summaries), "posture_reports_missing")
    for summary in summaries:
        db.require(db.timestamp(summary["metadata"]["annotations"]["kubescape.io/report-created-at"]) >= db.timestamp(started), "posture_report_stale")
        db.require(bool(summary["spec"].get("controls")), "posture_controls_empty")
    db.require(any(s["metadata"].get("namespace") == "memos" and s["metadata"]["name"] == "deployment-memos" for s in summaries), "posture_memos_missing")
    db.require(any(s["metadata"].get("namespace") == NS for s in summaries), "posture_shared_rbac_missing")
    return {"framework": "nsa", "controls": dict(statuses), "storedSummaries": len(summaries),
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


class Runner:
    def __init__(self, api, config, expected, history, mode):
        self.api, self.config, self.expected, self.history, self.mode = api, config, expected, history, mode
        self.run_id = mode + "-" + db.utcnow().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.work = history / self.run_id
        self.result = {"schemaVersion": 1, "runID": self.run_id, "mode": mode, "state": "unknown",
                       "startedAt": db.stamp(db.utcnow()), "reasons": [], "assessments": []}
        self.records = json.loads((history / "records.json").read_text()) if (history / "records.json").exists() else {}

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
            path = CORE + "/namespaces/kubescape/configmaps/kubescape-assessment-status"
            status = self.api.call(path)
            status.setdefault("data", {})[self.mode + ".json"] = json.dumps(self.result, separators=(",", ":"))
            status["data"]["coverage.json"] = json.dumps(inventory, separators=(",", ":"))
            db.require(len(json.dumps(status)) < 900000, "inventory_too_large")
            self.api.call(path, status, "PUT")
        except Exception as error:
            self.result.update(state="unknown", reasons=self.result["reasons"] + ["status_publish_unavailable"])
            atomic(self.history / ("latest-" + self.mode + ".json"), self.result)
            self.save("result", self.result)
        print(json.dumps(self.result, sort_keys=True), flush=True)

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
        metrics = self.api.items("/apis/metrics.k8s.io/v1beta1", "pods", NS)
        metric = next(m for m in metrics if m["metadata"]["name"] == engine["metadata"]["name"])
        db.require(db.age_hours(metric["timestamp"]) <= 0.05, "resource_metrics_stale")
        usage = next(c["usage"] for c in metric["containers"] if c["name"] == "kubevuln")
        db.require(quantity(usage["memory"]) < self.config["maximumWorkingSetBytes"], "memory_budget_exhausted")
        db.require(quantity(usage["cpu"]) < self.config["maximumIdleCPUCores"], "scanner_busy")
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
        previous = existing("vulnerabilitymanifests")
        previous_sbom = existing("sbomsyfts")
        self.save("previous-" + row["container"], {"vulnerability": previous, "sbom": previous_sbom})
        job = "recurring-" + uuid.uuid4().hex
        started = db.stamp(db.utcnow())
        payload = {"imageTag": row["runningDigest"], "imageHash": row["runningDigest"], "jobID": job,
                   "wlid": f"wlid://cluster-home/namespace-{row['namespace']}/deployment-{row['workload']}",
                   "containerName": row["container"], "args": {"platform": "linux/amd64", "forceRescan": True}}
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
        self.save("terminal-" + job, status)
        db.require(status["state"] == "succeeded" and status["phase"] == "completed", "scan_failed")
        report, sbom = existing("vulnerabilitymanifests"), existing("sbomsyfts")
        db.require(report is not None and sbom is not None, "report_missing")
        result = check_report(report, sbom, row, self.expected, started, previous)
        self.save("reports-" + job, {"vulnerability": report, "sbom": sbom})
        # Independent named rereads, including the workload summary attribution.
        for kind, value in (("vulnerabilitymanifests", report), ("sbomsyfts", sbom)):
            db.require(self.api.get(STORAGE, kind, value["metadata"]["name"])["spec"] == value["spec"], "report_reread_mismatch")
        summary = self.api.get(STORAGE, "vulnerabilitymanifestsummaries", "deployment-" + row["workload"] + "-" + row["container"], row["namespace"])
        annotations = summary["metadata"]["annotations"]
        db.require(annotations["kubescape.io/image-id"] == row["runningDigest"]
                   and annotations["kubescape.io/workload-container-name"] == row["container"]
                   and annotations["kubescape.io/wlid"] == payload["wlid"]
                   and summary["spec"]["vulnerabilitiesRef"]["all"]["name"] == report["metadata"]["name"], "summary_attribution_mismatch")
        self.save("summary-" + job, summary)
        current = targets(workload_rows(self.api), self.config)
        db.require(any(row_key(r) == row_key(row) and r["generation"] == row["generation"] and r["podUIDs"] == row["podUIDs"] for r in current), "workload_image_changed")
        self.gate(audit)
        result.update(jobID=job, durationSeconds=(db.timestamp(status["finishedAt"]) - db.timestamp(status["startedAt"])).total_seconds(),
                      workloadUID=row["workloadUID"], imageDigest=row["runningDigest"], container=row["container"])
        self.records[row_key(row)] = result
        atomic(self.history / "records.json", self.records)
        self.result["assessments"].append(result)
        (self.history / "inflight.json").unlink()

    def posture(self, audit):
        self.gate(audit)  # Also prevents DB hydration / scan overlap.
        scanner = component_pod(self.api, "kubescape")
        before = targets(workload_rows(self.api), self.config)
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
        assessed = check_posture(result, summaries, started)
        self.save("persisted-posture", summaries)
        again = [self.api.get(STORAGE, "workloadconfigurationscansummaries", s["metadata"]["name"], s["metadata"]["namespace"]) for s in summaries]
        db.require(again == summaries, "posture_reread_mismatch")
        db.require(before == targets(workload_rows(self.api), self.config), "workload_image_changed")
        self.gate(audit)
        self.result["assessments"].append({**assessed, "scanID": scan_id, "createdAt": db.stamp(db.utcnow())})
        (self.history / "inflight.json").unlink()

    def run(self):
        with locked(self.history):
            try:
                selected = targets(workload_rows(self.api), self.config)
                self.selected = selected
                self.fence()
                digest = hashlib.sha256(json.dumps(self.config["targets"], sort_keys=True).encode()).hexdigest()[:12]
                day = db.utcnow().strftime("%Y-%m-%d")
                completed = self.history / (self.mode + "-" + day + "-" + digest + ".done")
                db.require(not completed.exists(), "duplicate_execution")
                if self.mode == "vulnerability":
                    for row in selected:
                        self.records.setdefault(row_key(row), {})["lastAttempt"] = {
                            "state": "unknown", "checkedAt": db.stamp(db.utcnow()), "reasons": ["assessment_in_progress"]}
                    atomic(self.history / "records.json", self.records)
                self.result["reasons"] = ["assessment_in_progress"]
                self.publish(terminal=False)
                self.result["reasons"] = []
                engine = self.preflight()
                audit = self.audit(engine)
                self.gate(audit)
                if self.mode == "vulnerability":
                    for row in selected:
                        self.vulnerability(row, audit)
                else:
                    self.posture(audit)
                self.result["state"] = "healthy"
                atomic(completed, {"runID": self.run_id})
            except db.Rejected as error:
                self.result.update(state="stale" if error.reason == "stale_database" else "unknown" if error.reason == "uncertain_previous_request" else "failed", reasons=[error.reason])
            except Exception as error:
                self.result.update(state="unknown", reasons=["evidence_unavailable"], errorType=type(error).__name__)
            if self.result["state"] != "healthy" and self.mode == "vulnerability":
                for row in getattr(self, "selected", []):
                    record = self.records.setdefault(row_key(row), {})
                    record["lastAttempt"] = {"state": "failed" if self.result["state"] == "failed" else "unknown", "checkedAt": db.stamp(db.utcnow()), "reasons": self.result["reasons"]}
                atomic(self.history / "records.json", self.records)
            self.publish()
        return EXIT[self.result["state"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("vulnerability", "posture", "inventory"))
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
        return Runner(api, config, expected, args.history, args.mode).run()
    except Exception as error:
        print(json.dumps({"state": "failed", "reasons": [getattr(error, "reason", "runner_unavailable")], "errorType": type(error).__name__}))
        return 4


if __name__ == "__main__":
    sys.exit(main())
