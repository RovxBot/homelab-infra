#!/usr/bin/env python3
"""Collect/evaluate loaded Grype freshness and run the bounded manual Memos scan.

check is read-only except for the explicit --audit-cache transient, read-only PVC
audit Job and a short-lived API token. scan-memos runs exactly one image request.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
from urllib.parse import parse_qs, urlparse

import grype_db as db

ROOT = db.ROOT
REPORT = ("docker.io-neosmemo-memos-sha256-"
          "5dcd6e6b973a55b964f2fe2ccdaa33319463ab448a23404ebb5d2acbd19299ba-9299ba")
MEMOS_IMAGE = "docker.io/neosmemo/memos@sha256:5dcd6e6b973a55b964f2fe2ccdaa33319463ab448a23404ebb5d2acbd19299ba"
GRYPE_IMAGE = "anchore/grype:v0.121.0@sha256:621d56d1d68106b787022c81546f7e0a36202a25d1239f0c20195fab8c16f7ea"
PYTHON_IMAGE = "python:3.15-alpine@sha256:0f9552bb28711333da16915ec146f5e6bb44a7fdac9ced15dc17c2e0206346dd"
EXIT_CODES = {"healthy": 0, "stale": 2, "unknown": 3, "failed": 4}
CACHE_AUDIT = r'''
import datetime, hashlib, json, os, sqlite3
from pathlib import Path
p = Path('/cache/db/6/vulnerability.db')
with sqlite3.connect('file:' + str(p) + '?mode=ro', uri=True) as c:
    c.execute('PRAGMA cache_size=-131072')
    quick = c.execute('PRAGMA quick_check').fetchall()
    rows = c.execute('SELECT build_timestamp, model, revision, addition FROM db_metadata').fetchall()
if len(rows) != 1 or quick != [('ok',)]:
    raise SystemExit('SQLite integrity/metadata check failed')
built, model, revision, addition = rows[0]
with p.open('rb') as f:
    digest = 'sha256:' + hashlib.file_digest(f, 'sha256').hexdigest()
fs = os.statvfs('/cache')
print(json.dumps({'built': datetime.datetime.fromisoformat(built).isoformat().replace('+00:00', 'Z'),
    'schemaVersion': f'v{model}.{revision}.{addition}', 'quickCheck': 'ok', 'sha256': digest,
    'bytes': p.stat().st_size, 'import': json.loads(p.with_name('import.json').read_text()),
    'filesystem': {'availableBytes': fs.f_bavail * fs.f_frsize,
                   'capacityBytes': fs.f_blocks * fs.f_frsize,
                   'usedBytes': (fs.f_blocks - fs.f_bfree) * fs.f_frsize}}))
'''


def kubectl(*args, json_output=True, timeout=45):
    result = subprocess.check_output(["kubectl", "--request-timeout=30s", *args], timeout=timeout)
    return json.loads(result) if json_output else result.decode()


def named(kind, name, namespace="kubescape"):
    return kubectl("-n", namespace, "get", kind, name, "-o", "json")


def engine_pod():
    pods = kubectl("-n", "kubescape", "get", "pods", "-l", "app.kubernetes.io/component=kubevuln", "-o", "json")["items"]
    pods = [p for p in pods if not p["metadata"].get("deletionTimestamp")]
    db.require(len(pods) == 1, "engine_unavailable")
    pod, = pods
    db.require(pod["status"].get("phase") == "Running", "engine_unavailable")
    return pod


@contextmanager
def api():
    # Bind localhost only. Credentials never enter command arguments or files.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(["kubectl", "-n", "kubescape", "port-forward", "--address=127.0.0.1",
                             "svc/kubevuln", f"{port}:8080"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            db.require(proc.poll() is None, "api_unavailable")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.25)
        else:
            raise db.Rejected("api_unavailable")
        token = kubectl("-n", "kubescape", "create", "token", "operator", "--audience=kubescape.io",
                        "--duration=15m", json_output=False).strip()

        def request(path, value=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}" + path,
                                         data=None if value is None else json.dumps(value).encode(),
                                         headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as response:
                return json.load(response)
        yield request
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def audit_manifest(pod, name):
    security = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]}}
    def container(name, image, command):
        return {"name": name, "image": image, "command": command, "securityContext": security,
                "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                              "limits": {"cpu": "500m", "memory": "512Mi"}},
                "volumeMounts": [{"name": "cache", "mountPath": "/cache", "readOnly": True}]}
    grype = container("checksum", GRYPE_IMAGE, ["/grype", "db", "status", "-o", "json"])
    grype["env"] = [{"name": k, "value": v} for k, v in {
        "GRYPE_DB_CACHE_DIR": "/cache/db", "GRYPE_DB_AUTO_UPDATE": "false",
        "GRYPE_CHECK_FOR_APP_UPDATE": "false", "GRYPE_DB_VALIDATE_BY_HASH_ON_START": "true",
        "GRYPE_DB_VALIDATE_AGE": "false"}.items()]
    return {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "namespace": "kubescape"},
            "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 600, "ttlSecondsAfterFinished": 600,
                     "template": {"metadata": {"labels": {"homelab.cooked.beer/owner": "sam",
                                                          "kubescape.io/ignore": "true"}},
                                  "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False,
                                           "nodeSelector": {"kubernetes.io/os": "linux", "kubernetes.io/hostname": pod["spec"]["nodeName"]},
                                           "securityContext": {"runAsNonRoot": True, "runAsUser": 65532,
                                                               "runAsGroup": 65532, "seccompProfile": {"type": "RuntimeDefault"}},
                                           "containers": [grype, container("sqlite", PYTHON_IMAGE, ["python3", "-B", "-c", CACHE_AUDIT])],
                                           "volumes": [{"name": "cache", "persistentVolumeClaim": {
                                               "claimName": "kubescape-kubevuln", "readOnly": True}}]}}}}


def audit_cache(work):
    pod = engine_pod()
    name = "grype-cache-audit-" + db.utcnow().strftime("%Y%m%d%H%M%S")
    manifest = audit_manifest(pod, name)
    db.save_json(work / "cache-audit-job.json", manifest)
    # Admission checks happen before the only cluster workload mutation.
    subprocess.run(["kubectl", "create", "--dry-run=server", "-f", str(work / "cache-audit-job.json")], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["kubectl", "create", "-f", str(work / "cache-audit-job.json")], check=True, stdout=subprocess.DEVNULL)
    for _ in range(120):
        job = named("job", name)
        if job.get("status", {}).get("succeeded"):
            break
        db.require(not job.get("status", {}).get("failed"), "cache_audit_failed")
        time.sleep(5)
    else:
        raise db.Rejected("cache_audit_timeout")
    result = {"job": name, "podUID": pod["metadata"]["uid"], "checkedAt": db.stamp(db.utcnow())}
    for container in ("sqlite", "checksum"):
        result[container] = json.loads(kubectl("-n", "kubescape", "logs", "job/" + name, "-c", container, json_output=False))
    db.require(engine_pod()["metadata"]["uid"] == result["podUID"], "engine_changed_during_audit")
    db.save_json(work / "cache-audit.json", result)
    return result


def evaluate(evidence, expected, now=None):
    now = now or db.utcnow()
    result = {"state": "unknown", "databaseState": "unknown", "canAssess": False,
              "assessmentTrustworthy": False, "checkedAt": db.stamp(now), "maximumDatabaseAgeHours": 72,
              "reasons": [], "integrity": "unknown", "lastSuccessfulAssessmentTime": None}
    try:
        latest = expected["metadata"]
        result["expectedImage"] = expected["image"]
        diagnostics, audit = evidence["diagnostics"], evidence["audit"]
        sqlite, checksum = audit["sqlite"], audit["checksum"]
        result["loaded"] = {"archiveChecksum": diagnostics.get("cveDBVersion"), "built": sqlite["built"],
                            "schemaVersion": sqlite["schemaVersion"], "sqliteSHA256": sqlite["sha256"]}
        age = db.age_hours(sqlite["built"], now)
        result["databaseAgeHours"] = round(age, 6)
        # Expiry wins over readiness, old assessments and missing provenance.
        db.require(age <= 72, "stale_database")
        db.metadata(latest, now)
        db.require(expected.get("state") == "verified" and expected.get("publisher", {}).get("status") == "verified", "unverified_provenance")
        db.require(evidence["declaredDigest"] == expected["indexDigest"], "gitops_digest_mismatch")
        db.require(evidence["mirrorImage"] == expected["image"], "mirror_image_mismatch")
        db.require(evidence["mirrorImageID"].split("@")[-1] in {expected["indexDigest"], expected["manifestDigest"]}, "mirror_imageid_mismatch")
        db.require(evidence["mirrorMetadata"] == latest, "mirror_metadata_mismatch")
        db.require(audit["podUID"] == evidence["enginePodUID"], "cache_audit_pod_mismatch")
        audit_age = db.age_hours(audit["checkedAt"], now)
        db.require(audit_age <= 0.25, "cache_audit_expired")
        db.require(sqlite["quickCheck"] == "ok" and checksum.get("error") in (None, "") and checksum.get("valid") is True,
                   "cache_integrity_failed")
        db.require(sqlite["schemaVersion"] == latest["schemaVersion"] == checksum["schemaVersion"], "incompatible_schema")
        db.require(sqlite["built"] == latest["built"] == checksum["built"], "cache_build_mismatch")
        source = parse_qs(urlparse(checksum["from"]).query).get("checksum", [None])[0]
        db.require(source == latest["checksum"] and sqlite["import"]["source"] == checksum["from"], "cache_source_mismatch")
        db.require(diagnostics["cveDBVersion"] == latest["checksum"].removeprefix("sha256:"), "loaded_database_mismatch")
        db.require(diagnostics.get("storageEnabled") is True and diagnostics.get("queueDepth") == 0, "engine_not_idle")
        result.update(state="healthy", databaseState="healthy", canAssess=True, integrity="verified")
        report = evidence.get("assessment")
        if report is None:
            result.update(state="unknown", reasons=["assessment_unavailable"])
            return result
        spec = report["spec"]
        reported = spec["payload"]["descriptor"]["db"]
        created = spec["metadata"]["report"]["createdAt"]
        if report["metadata"].get("annotations", {}).get("kubescape.io/status") == "ready":
            result["lastSuccessfulAssessmentTime"] = created
        report_age = db.age_hours(created, now)
        if (report["metadata"].get("annotations", {}).get("kubescape.io/status") != "ready"
                or not isinstance(spec["payload"].get("matches"), list)):
            result.update(state="failed", reasons=["assessment_incomplete"])
        elif (spec["metadata"]["tool"]["databaseVersion"] != diagnostics["cveDBVersion"]
              or reported["built"] != sqlite["built"] or reported["schemaVersion"] != sqlite["schemaVersion"]):
            result.update(state="unknown", reasons=["assessment_previous_database"])
        elif report_age > 72:
            result.update(state="stale", reasons=["assessment_stale"])
        elif db.timestamp(created) < db.timestamp(sqlite["built"]):
            result.update(state="failed", reasons=["assessment_predates_database"])
        else:
            result["assessmentTrustworthy"] = True
        return result
    except db.Rejected as error:
        reason = error.reason
        unknown = {"unverified_provenance", "cache_audit_expired"}
        state = "stale" if reason == "stale_database" else "unknown" if reason in unknown else "failed"
    except (KeyError, TypeError, ValueError):
        state, reason = "unknown", "missing_or_invalid_evidence"
    result.update(state=state, databaseState=state, reasons=[reason])
    return result


def collect(request, audit):
    pod = engine_pod()
    mirror = named("deployment", "grype-offline-db")
    pods = kubectl("-n", "kubescape", "get", "pods", "-l", "app.kubernetes.io/component=grype-offline-db", "-o", "json")["items"]
    live = [p for p in pods if not p["metadata"].get("deletionTimestamp")]
    db.require(len(live) == 1, "mirror_unavailable")
    report = named("vulnerabilitymanifests", REPORT)
    return {"collectedAt": db.stamp(db.utcnow()), "enginePodUID": pod["metadata"]["uid"], "audit": audit,
            "diagnostics": request("/v1/diagnostics"), "assessment": report,
            "mirrorMetadata": kubectl("get", "--raw=/api/v1/namespaces/kubescape/services/grype-offline-db:80/proxy/databases/v6/latest.json"),
            "mirrorImage": mirror["spec"]["template"]["spec"]["containers"][0]["image"],
            "mirrorImageID": live[0]["status"]["containerStatuses"][0]["imageID"],
            "declaredDigest": named("helmrelease", "kubescape")["spec"]["values"]["grypeOfflineDB"]["image"]["sha"]}


def spec_hash(resource):
    return hashlib.sha256(json.dumps(resource["spec"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def scan_memos(request, expected, audit, work):
    before = collect(request, audit)
    db.save_json(work / "before.json", before)
    gate = evaluate(before, expected)
    db.save_json(work / "pre-scan-freshness.json", gate)
    db.require(gate["canAssess"], "freshness_gate_failed", str(gate["reasons"]))
    deployment = named("deployment", "memos", "memos")
    namespace = kubectl("get", "namespace", "memos", "-o", "json")
    pods = kubectl("-n", "memos", "get", "pods", "-l", "app=memos", "-o", "json")["items"]
    db.require(len(pods) == 1, "memos_workload_ambiguous")
    pod, = pods
    status, = pod["status"]["containerStatuses"]
    db.require(status["ready"] and status["name"] == "memos" and status["imageID"] == MEMOS_IMAGE, "memos_digest_changed")
    # Retain old results before the storage API updates the same named objects.
    for kind in ("vulnerabilitymanifests", "sbomsyfts"):
        db.save_json(work / ("previous-" + kind + ".json"), named(kind, REPORT))
    db.save_json(work / "previous-summary.json", named("vulnerabilitymanifestsummaries", "deployment-memos-memos", "memos"))
    job = "memos-db-refresh-" + db.utcnow().strftime("%Y%m%d%H%M%S")
    payload = {"imageTag": MEMOS_IMAGE, "imageHash": MEMOS_IMAGE,
               "wlid": "wlid://cluster-home/namespace-memos/deployment-memos", "jobID": job,
               "containerName": "memos", "args": {"platform": "linux/amd64"}}
    db.save_json(work / "scan-request.json", payload)
    db.save_json(work / "scan-response.json", request("/v1/scanImage", payload))
    for _ in range(72):
        status = request("/v1/scanStatus/" + job)
        db.save_json(work / "scan-status.json", status)
        if status["state"] in {"succeeded", "failed", "abandoned"}:
            break
        time.sleep(5)
    db.require(status["state"] == "succeeded" and status["phase"] == "completed", "assessment_failed")
    after = collect(request, audit)
    db.save_json(work / "after.json", after)
    gate = evaluate(after, expected)
    db.save_json(work / "freshness.json", gate)
    db.require(gate["assessmentTrustworthy"], "assessment_freshness_failed")
    db.require(db.timestamp(gate["lastSuccessfulAssessmentTime"]) >= db.timestamp(status["startedAt"]), "assessment_not_new")
    current = named("deployment", "memos", "memos")
    db.require(deployment["metadata"]["uid"] == current["metadata"]["uid"] and spec_hash(deployment) == spec_hash(current), "memos_workload_changed")
    sbom = named("sbomsyfts", REPORT)
    db.save_json(work / "sbom.json", sbom)
    db.require(sbom["metadata"]["annotations"]["kubescape.io/status"] == "ready", "sbom_incomplete")
    source = sbom["spec"]["syft"]["source"]["metadata"]
    original = json.loads((ROOT / "infra/kubescape/image-canary-evidence.json").read_text())
    db.require(source["manifestDigest"] == original["workload"]["manifestDigest"]
               and source["imageID"] == original["workload"]["configDigest"]
               and len(sbom["spec"]["syft"]["artifacts"]) > 0, "sbom_image_mismatch")
    db.save_json(work / "workload.json", {"deploymentUID": current["metadata"]["uid"], "namespaceUID": namespace["metadata"]["uid"],
                                          "podUID": pod["metadata"]["uid"], "imageID": MEMOS_IMAGE})
    return gate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "scan-memos", "evaluate", "audit-manifest"))
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".work/kubescape-db")
    parser.add_argument("--expected", type=Path, default=ROOT / "infra/kubescape/grype-database.json")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--audit-cache", action="store_true")
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    try:
        if args.command == "audit-manifest":
            print(json.dumps(audit_manifest(engine_pod(), "grype-cache-audit-preview"), indent=2))
            return 0
        expected = json.loads(args.expected.read_text())
        if args.command == "evaluate":
            result = evaluate(json.loads(args.evidence.read_text()), expected)
        else:
            audit = audit_cache(args.work_dir) if args.audit_cache else json.loads((args.work_dir / "cache-audit.json").read_text())
            with api() as request:
                if args.command == "scan-memos":
                    result = scan_memos(request, expected, audit, args.work_dir)
                else:
                    evidence = collect(request, audit)
                    db.save_json(args.work_dir / "evidence.json", evidence)
                    result = evaluate(evidence, expected)
    except db.Rejected as error:
        result = {"state": "failed", "canAssess": False, "assessmentTrustworthy": False, "reasons": [error.reason]}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        result = {"state": "unknown", "canAssess": False, "assessmentTrustworthy": False,
                  "reasons": ["evidence_unavailable"], "detail": type(error).__name__}
    db.save_json(args.work_dir / "freshness.json", result)
    print(json.dumps(result, sort_keys=True))
    return EXIT_CODES[result["state"]]


if __name__ == "__main__":
    raise SystemExit(main())
