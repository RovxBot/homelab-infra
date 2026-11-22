#!/usr/bin/env python3
"""Review the additive recurring resources without loosening chart-only guards."""
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/ci"))
import kubescape_recurring_bundle
import check_kubescape as chart


def check(resources):
    jobs = [r for r in resources if r["kind"] == "CronJob"]
    assert {r["metadata"]["name"] for r in jobs} == {"kubescape-assessment-vulnerability", "kubescape-assessment-posture"}
    config = json.loads((ROOT / "infra/kubescape/recurring/config.json").read_text())
    evidence = json.loads((ROOT / "infra/kubescape/recurring/resource-qualification.json").read_text())
    assert config["resourceBudgetQualified"] is True and evidence["status"] == "verified"
    selected = {t["identity"] for t in config["targets"]}
    assert "memos/Deployment/memos/memos" in selected
    assert selected <= {"memos/Deployment/memos/memos", "homepage/Deployment/homepage-assets/nginx"}, "coverage expansion needs review"
    if len(selected) > 1:
        acceptance = json.loads((ROOT / "infra/kubescape/recurring/acceptance.json").read_text())
        assert acceptance["initialMemosVulnerability"]["state"] == acceptance["initialMemosPosture"]["state"] == "healthy", "Memos must qualify before cohort expansion"
    assert config["scanTimeoutSeconds"] == 360 and config["maximumHistoryBytes"] <= 512 * 1024**2
    assert config["minimumDatabaseFreeBytes"] >= 3 * 1024**3
    assert config["minimumReportFreeBytes"] >= 1024**3
    assert config["maximumWorkingSetBytes"] <= 2 * 1024**3
    bundle = json.loads((ROOT / "infra/kubescape/recurring/client-bundle.json").read_text())
    for name, source in bundle.items():
        assert source == (ROOT / "scripts" / name).read_text(), "supported client bundle differs"
    release = yaml.safe_load((ROOT / "infra/kubescape/helmrelease.yaml").read_text())
    engine_image = release["spec"]["values"]["kubevuln"]["image"]
    if engine_image["repository"] != "quay.io/kubescape/kubevuln":
        import hashlib
        proof_root = ROOT / "infra/kubescape/recurring/scanner"
        proof = json.loads((proof_root / "build-evidence.json").read_text())
        assert proof["status"] == "verified"
        assert engine_image["repository"] == "ghcr.io/rovxbot/kubevuln"
        assert proof["image"] == engine_image["repository"] + "@" + engine_image["tag"].split("@")[-1]
        assert proof["upstreamCommit"] == "45d265e3b46af4d7fc1c896ecc22a93915f2d6fe"
        assert set(proof["inputs"]) == {"Dockerfile", "force-rescan.patch", "force_rescan_test.go"}
        for name, digest in proof["inputs"].items():
            assert hashlib.sha256((proof_root / name).read_bytes()).hexdigest() == digest, name
    for job in jobs:
        spec = job["spec"]
        assert spec["concurrencyPolicy"] == "Forbid" and spec["timeZone"] == "Etc/UTC"
        expected = "13 19 * * *" if job["metadata"]["name"].endswith("vulnerability") else "13 20 * * *"
        assert spec["schedule"] == expected and spec["startingDeadlineSeconds"] == 300
        jspec = spec["jobTemplate"]["spec"]
        assert jspec["backoffLimit"] == 0 and jspec["activeDeadlineSeconds"] == 1800
        pod = jspec["template"]["spec"]
        assert pod["restartPolicy"] == "Never" and pod["serviceAccountName"] == "kubescape-assessment"
        assert pod["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"] == [{
            "labelSelector": {"matchLabels": {"app.kubernetes.io/component": "kubevuln"}}, "topologyKey": "kubernetes.io/hostname"}]
        # Reuse all static restricted-Pod checks while preserving the chart workload allowlist.
        probe = json.loads(json.dumps(job))
        probe["metadata"]["name"] = "kubescape-scheduler"
        probe["spec"]["suspend"] = True
        assert not chart.findings([probe]), chart.findings([probe])
        assert all(c["image"] == "python:3.15-alpine@sha256:0f9552bb28711333da16915ec146f5e6bb44a7fdac9ced15dc17c2e0206346dd"
                   for c in pod["containers"] + pod["initContainers"])
        mounts = pod["containers"][0]["volumeMounts"]
        assert next(m for m in mounts if m["name"] == "history")["subPath"] == "assessments"
        assert next(v for v in pod["volumes"] if v["name"] == "history")["persistentVolumeClaim"]["claimName"] == "kubescape-kubevuln"
        assert next(v for v in pod["volumes"] if v["name"] == "api-token")["projected"]["sources"][0]["serviceAccountToken"]["audience"] == "kubescape.io"
    for resource in resources:
        for rule in resource.get("rules", []):
            assert not set(rule.get("resources", [])) & {"secrets", "nodes/proxy", "pods/exec", "pods/attach"}
            assert "*" not in rule.get("resources", []) + rule.get("verbs", []) + rule.get("apiGroups", [])
            if set(rule.get("verbs", [])) - {"get", "list"}:
                assert resource["kind"] == "Role" and resource["metadata"]["namespace"] == "kubescape"
                assert set(rule["resources"]) <= {"jobs", "configmaps", "scans", "scanresults", "vulnerabilityscans"}
    print("verified: recurring scope, budgets, authentication, restricted Pods and additive RBAC")


if __name__ == "__main__":
    source = subprocess.check_output(["kustomize", "build", "infra/kubescape"], cwd=ROOT, text=True)
    resources = chart.documents(source)
    check(resources)
