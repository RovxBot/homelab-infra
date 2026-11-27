#!/usr/bin/env python3
"""Qualify the staged or explicitly active, manually triggered Kubescape release.

Requires Helm 3, Kustomize, the verified proposed Kyverno CLI and PyYAML. Never contacts a Kubernetes cluster.
All modes require zero admission failures and a suspended scan scheduler.
Active releases require --active-review and Flux health waiting.
"""

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/ci"))
from grype_db import mirror_init
import kubescape_runtime as runtime
from security_native_exporter_preflight import candidate
VERSION = "1.42.0"
CHART_SHA256 = "be45f83398f28db8f2ff395e5d8ab411328041e9e2165a42e5fb92a4ba369079"
CHART_URL = ("https://github.com/kubescape/helm-charts/releases/download/"
             f"kubescape-operator-{VERSION}/kubescape-operator-{VERSION}.tgz")
DISABLED = set("""continuousScan nodeScan nodeSbomGeneration scanEmbeddedSBOMs
relevancy vexGeneration networkPolicyService
networkEventsStreaming malwareDetection nodeProfileService
admissionController httpDetection seccompProfileService sandboxSeccomp manageWorkloads
syncSBOM agentRuntimePosture riskAcceptance remediation autoUpgrading prometheusExporter""".split())
CERTGEN_NAMES = {"certgen-create", "certgen-patch"}
APISERVICE = "v1beta1.spdx.softwarecomposition.kubescape.io"
CANARY_NAMESPACE = "birdnet-go,cloudflared,gitea,gitea-runners,home-assistant,homepage,immich,invoiceninja,koito,media,memos,monitoring,mop,mqtt,security,stirling-pdf,wger,wireguard,wotlk"
CERTGEN_BUDGET = {"requests": {"cpu": "10m", "memory": "32Mi"},
                  "limits": {"cpu": "100m", "memory": "128Mi"}}
WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod"}


def documents(text):
    return [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]


def check_release(release, active=False):
    spec = release["spec"]
    assert spec.get("suspend") is (not active), "HelmRelease suspension does not match the explicit review mode"
    assert spec["chart"]["spec"]["version"] == VERSION, "chart version changed"
    values = spec["values"]
    caps = values["capabilities"]
    for key in DISABLED:
        assert caps.get(key) == "disable", f"capabilities.{key} must explicitly be disabled"
    for key in ("operator", "configurationScan", "vulnerabilityScan", "kubescapeOffline", "runtimeDetection", "runtimeObservability"):
        assert caps.get(key) == "enable", f"capabilities.{key} must be enabled"
    assert caps["seccompProfileBackend"] == "storage"
    assert values["certificates"]["strategy"] == "initContainer"
    assert not any(values.get(k) for k in ("server", "account", "accessKey"))
    assert not values["credentials"]["cloudSecret"]
    assert values["global"]["enableClusterWideSecretAccess"] is False
    runtime.check_values(values)
    assert spec["upgrade"]["crds"] == "Create"
    assert values["nodeAgent"]["privileged"] is False
    assert caps["testing"]["nodeAgentMultiplication"]["enabled"] is False
    assert values["storage"]["hostNetwork"] is False
    assert values["storage"]["mtls"]["enabled"] is True
    assert values["persistence"]["storageClass"] == "longhorn"
    for key in ("enabled", "createEgressRules"):
        assert values["global"]["networkPolicy"][key] is False
    for path in (("nodeAgent", "config", "hostSensor"), ("nodeAgent", "sbomScanner"),
                 ("nodeAgent", "autoscaler"), ("nodeAgent", "multipleDaemonSets"),
                 ("nodeAgent", "gVisorStart"), ("kubevuln", "sbomScanner"), ("serviceScanConfig",)):
        item = values
        for part in path:
            item = item[part]
        assert item["enabled"] is False, f"{'.'.join(path)} must be disabled"
    for component in ("kubescape", "operator", "storage", "kubescapeOpsTool", "kubevuln"):
        tag = values[component]["image"]["tag"]
        assert re.fullmatch(r"[0-9v][^@ :]*@sha256:[a-f0-9]{64}", tag), f"unpinned {component}"
    assert values["kubescape"]["downloadArtifacts"] is False
    assert values["kubescape"]["skipUpdateCheck"] is True
    assert values["operator"]["triggerSecurityFramework"] is False
    assert values["operator"]["rulesUpdateConfig"]["enabled"] is False
    assert values["includeNamespaces"] == CANARY_NAMESPACE, "reviewed posture scope differs"
    assert values["defaultFrameworks"] == ["nsa"], "canary framework changed"
    assert values["persistence"]["size"]["backingStorage"] == "5Gi"
    assert values["persistence"]["accessMode"] == "ReadWriteOnce"
    assert values["persistence"]["size"]["kubevuln"] == "10Gi"
    db = values["grypeOfflineDB"]
    assert db["enabled"] is True and db["image"]["tag"] == ""
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", db["image"]["sha"]), "unpinned DB mirror"
    assert values["kubevuln"]["podAnnotations"]["homelab.cooked.beer/grype-index"] == db["image"]["sha"], "DB change must roll the RWO engine"
    assert values["kubevuln"]["config"] == {
        "grypeDbPersistence": True, "maxImageSize": 536870912,
        "maxSBOMSize": 20971520, "scanTimeout": "5m", "cveMatchingMode": "off"}
    assert values["kubevuln"]["env"] == [
        {"name": "CA_MAX_VULN_SCAN_ROUTINES", "value": "1"},
        {"name": "MAXQUEUEDEPTH", "value": "1"},
        {"name": "MAXIMAGESIZE", "value": "2684354560"},
        {"name": "MAXSBOMSIZE", "value": "33554432"}]
    assert values["kubevuln"]["resources"] == {
        "requests": {"cpu": "300m", "memory": "1Gi", "ephemeral-storage": "3Gi"},
        "limits": {"cpu": "1000m", "memory": "3Gi", "ephemeral-storage": "4Gi"}}
    assert values["kubevuln"]["tmpDirPersistence"]["enabled"] is False
    assert values["kubescapeScheduler"]["requestBody"] == {"commands": [{
        "CommandName": "kubescapeScan", "args": {"scanV1": {
            "targetType": "framework", "targetNames": ["nsa"],
            "hostScanner": False, "keepLocal": True}}}]}, "canary request changed"


def check_child(child, active):
    spec = child["spec"]
    assert spec.get("wait") is active, "Flux health waiting must match the review mode"
    assert not spec.get("suspend"), "Kubescape child must reconcile"
    assert spec["dependsOn"] == [{"name": "infrastructure"}, {"name": "kyverno-policies"}]


def pod_template(resource):
    if resource["kind"] == "Pod":
        return resource
    spec = resource["spec"]
    if resource["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    return spec["template"]


def findings(resources):
    """Conservative static PSA/Kyverno checks; real Kyverno remains authoritative."""
    errors = []
    for resource in resources:
        kind, name = resource["kind"], resource["metadata"]["name"]
        identity = f"{kind}/{name}"
        if (kind, name) == ("DaemonSet", "node-agent"):
            errors.extend(runtime.findings(resource))
            continue
        if kind in {"MutatingWebhookConfiguration", "ValidatingWebhookConfiguration",
                    "NetworkPolicy", "ServiceMonitor", "DaemonSet"}:
            errors.append(f"{identity}: forbidden optional resource")
        if resource["metadata"].get("annotations", {}).get("helm.sh/hook"):
            errors.append(f"{identity}: unexpected Helm hook")
        if kind not in WORKLOADS:
            continue
        if (kind, name) not in {("Deployment", "kubescape"), ("Deployment", "operator"),
                                ("Deployment", "storage"), ("CronJob", "kubescape-scheduler"),
                                ("Deployment", "kubevuln"), ("Deployment", "grype-offline-db"),
                                ("CronJob", "kubevuln-scheduler")}:
            errors.append(f"{identity}: unexpected workload")
        if kind == "CronJob" and resource["spec"].get("suspend") is not True:
            errors.append(f"{identity}: scheduled scanning is active")
        template = pod_template(resource)
        pod = template["spec"]
        psc = pod.get("securityContext", {})
        if not template.get("metadata", {}).get("labels", {}).get("homelab.cooked.beer/owner"):
            errors.append(f"{identity}: missing owner label")
        if any(pod.get(k) for k in ("hostNetwork", "hostPID", "hostIPC")):
            errors.append(f"{identity}: host namespace access")
        if any("hostPath" in v for v in pod.get("volumes", [])):
            errors.append(f"{identity}: host mount")
        allowed_volumes = {"name", "configMap", "csi", "downwardAPI", "emptyDir", "ephemeral",
                           "persistentVolumeClaim", "projected", "secret"}
        if any(set(v) - allowed_volumes for v in pod.get("volumes", [])) or psc.get("sysctls"):
            errors.append(f"{identity}: volume or sysctl outside the restricted preflight subset")
        for where in ("containers", "initContainers", "ephemeralContainers"):
            for container in pod.get(where, []):
                cid = f"{identity} {where}/{container['name']}"
                sc = {**psc, **container.get("securityContext", {})}
                if (sc.get("runAsNonRoot") is not True or sc.get("runAsUser") == 0
                        or sc.get("privileged", False) or sc.get("allowPrivilegeEscalation") is not False
                        or sc.get("seccompProfile", {}).get("type") != "RuntimeDefault"
                        or "ALL" not in sc.get("capabilities", {}).get("drop", [])
                        or sc.get("capabilities", {}).get("add")
                        or sc.get("procMount", "Default") != "Default"
                        or sc.get("seLinuxOptions") or sc.get("windowsOptions", {}).get("hostProcess")
                        or sc.get("appArmorProfile", {}).get("type", "RuntimeDefault") != "RuntimeDefault"
                        or any(p.get("hostPort", 0) for p in container.get("ports", []))):
                    errors.append(f"{cid}: violates restricted security context")
                if not re.search(r"@sha256:[a-f0-9]{64}$", container["image"]):
                    errors.append(f"{cid}: image lacks digest")
                budget = container.get("resources", {})
                if not all(budget.get(side, {}).get(unit) is not None
                           for side in ("requests", "limits") for unit in ("cpu", "memory")):
                    errors.append(f"{cid}: missing CPU/memory requests/limits")
    return sorted(errors)


def check_effective_config(resources):
    maps = {r["metadata"]["name"]: r["data"] for r in resources if r["kind"] == "ConfigMap"}
    cloud = json.loads(maps["ks-cloud-config"]["clusterData"])
    for key in ("serviceDiscovery", "continuousPostureScan",
                "relevantImageVulnerabilitiesEnabled", "scanEmbeddedSBOMs", "storeFilteredSbom"):
        assert cloud[key] is False, f"effective {key} enabled"
    assert cloud["keepLocal"] is True and cloud["nodeAgent"] == "true"
    assert cloud["imageVulnerabilitiesScanningEnabled"] is True
    assert cloud["listingURL"] == "http://grype-offline-db:80/databases"
    assert cloud["cveMatchingMode"] == "off"
    assert cloud["defaultFrameworks"] == ["nsa"]
    operator_config = json.loads(maps["operator"]["config.json"])
    assert operator_config["includeNamespaces"] == CANARY_NAMESPACE
    assert not operator_config["triggersecurityframework"]
    assert json.loads(maps["kubescape-scheduler"]["request-body.json"]) == {
        "commands": [{"CommandName": "kubescapeScan", "args": {"scanV1": {
            "targetType": "framework", "targetNames": ["nsa"],
            "hostScanner": False, "keepLocal": True}}}]}
    caps = json.loads(maps["ks-capabilities"]["capabilities"])
    assert caps["capabilities"]["vulnerabilityScan"] == "enable"
    for key in DISABLED:
        assert caps["capabilities"][key] == "disable", f"effective {key} enabled"
    for key in ("sbomScanner", "kubevuln", "kubevulnScheduler", "synchronizer",
                "autoUpdater", "prometheusExporter", "serviceDiscovery"):
        assert caps["components"][key]["enabled"] is False, f"effective {key} enabled"
    scanner = next(r for r in resources if r["kind"] == "Deployment" and r["metadata"]["name"] == "kubescape")
    env = {e["name"]: e.get("value") for e in pod_template(scanner)["spec"]["containers"][0]["env"]}
    for key, expected in {"KS_ENABLE_HOST_SCANNER": "false", "KS_DOWNLOAD_ARTIFACTS": "false",
                          "KS_SKIP_UPDATE_CHECK": "true", "KS_OFFLINE": "true",
                          "KS_INCLUDE_NAMESPACES": CANARY_NAMESPACE}.items():
        assert env[key] == expected, f"unexpected scanner {key}"
    operator = next(r for r in resources if r["kind"] == "Deployment" and r["metadata"]["name"] == "operator")
    env = {e["name"]: e.get("value") for e in pod_template(operator)["spec"]["containers"][0]["env"]}
    assert env["KS_SKIP_UPDATE_CHECK"] == "true" and env["WORKERCONCURRENCY"] == "1"
    # Only operator consumes ks-capabilities. Keep automatic image routing off;
    # the runtime agent watches independently and manual scans use authenticated Kubevuln.
    assert caps["components"]["kubescape"]["enabled"] is True
    assert caps["components"]["nodeAgent"]["enabled"] is True
    for key in ("runtimeDetection", "runtimeObservability"):
        assert caps["capabilities"][key] == "enable"
    runtime.check_config(resources)
    for name in ("kubescape-scheduler", "kubevuln-scheduler"):
        job = next(r for r in resources if r["kind"] == "CronJob" and r["metadata"]["name"] == name)
        assert job["spec"].get("suspend") is True
    for name in ("kubescape-storage", "kubescape-kubevuln"):
        pvc = next(r for r in resources if r["kind"] == "PersistentVolumeClaim" and r["metadata"]["name"] == name)
        assert pvc["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"


def inventory(resources):
    return {
        "databaseImage": next((pod_template(r)["spec"]["containers"][0]["image"] for r in resources
                               if r["kind"] == "Deployment" and r["metadata"]["name"] == "grype-offline-db"), None),
        "databaseRolloutIndex": next((pod_template(r)["metadata"]["annotations"]["homelab.cooked.beer/grype-index"]
                                      for r in resources if r["kind"] == "Deployment" and r["metadata"]["name"] == "kubevuln"), None),
        "databaseStartupGate": next((pod_template(r)["spec"]["initContainers"][0]
                                     for r in resources if r["kind"] == "Deployment" and r["metadata"]["name"] == "kubevuln"), None),
        "chart": {"version": VERSION, "url": CHART_URL, "sha256": CHART_SHA256, "kubernetes": "1.34.0"},
        "counts": dict(sorted(Counter(r["kind"] for r in resources).items())),
        "resources": sorted(f"{r['kind']}/{r['metadata'].get('namespace', '-')}/{r['metadata']['name']}"
                            for r in resources),
        "certificateInitBudgets": {
            c["name"]: c.get("resources", {}) for r in resources
            if (r["kind"], r["metadata"].get("namespace"), r["metadata"]["name"])
            == ("Deployment", "kubescape", "storage")
            for c in pod_template(r)["spec"].get("initContainers", [])},
        "certificateAPIServiceRules": [r["rules"] for r in resources
            if r["kind"] == "ClusterRole" and r["metadata"]["name"] == "storage-certgen-kubescape"],
        "admissionBlockers": findings(resources),
    }


def check_post_render(raw, rendered):
    """Compare every field, allowing only the reviewed manual-only additions."""
    def indexed(resources):
        result = {(r["apiVersion"], r["kind"], r["metadata"].get("namespace"), r["metadata"]["name"]): r
                  for r in resources}
        assert len(result) == len(resources), "duplicate rendered resource identity"
        return result

    expected = indexed(deepcopy(raw))
    # Kustomize omits these null fields emitted by this chart. Non-null
    # annotations, affinity and tolerations must still compare exactly.
    for resource in expected.values():
        if resource["metadata"].get("annotations") is None:
            resource["metadata"].pop("annotations", None)
    storage = expected[("apps/v1", "Deployment", "kubescape", "storage")]
    for field in ("affinity", "tolerations"):
        pod = pod_template(storage)["spec"]
        if pod.get(field) is None:
            pod.pop(field, None)
    inits = pod_template(storage)["spec"]["initContainers"]
    assert len(inits) == 2 and {c["name"] for c in inits} == CERTGEN_NAMES, "chart certgen names changed"
    for container in inits:
        assert not container.get("resources"), "chart now supplies certgen budgets; review the patch"
        container["resources"] = deepcopy(CERTGEN_BUDGET)
    expected[("batch/v1", "CronJob", "kubescape", "kubescape-scheduler")]["spec"]["suspend"] = True
    if ("batch/v1", "CronJob", "kubescape", "kubevuln-scheduler") in expected:
        engine = expected[("apps/v1", "Deployment", "kubescape", "kubevuln")]
        mirror = expected[("apps/v1", "Deployment", "kubescape", "grype-offline-db")]
        assert not pod_template(engine)["spec"].get("initContainers"), "chart now supplies DB initialization; review patch"
        pod_template(engine)["spec"]["initContainers"] = [mirror_init(pod_template(mirror)["spec"]["containers"][0]["image"])]
        assert engine["spec"]["strategy"] == {"type": "Recreate"}, "RWO engine must not surge"
        expected[("batch/v1", "CronJob", "kubescape", "kubevuln-scheduler")]["spec"]["suspend"] = True
        caps = expected[("v1", "ConfigMap", "kubescape", "ks-capabilities")]["data"]
        data = json.loads(caps["capabilities"])
        for key in ("kubevuln", "kubevulnScheduler"):
            assert data["components"][key]["enabled"] is True
            data["components"][key]["enabled"] = False
        caps["capabilities"] = json.dumps(data, separators=(",", ":"))
        for name in ("kubescape-storage", "kubescape-kubevuln"):
            pvc = expected[("v1", "PersistentVolumeClaim", "kubescape", name)]
            pvc["metadata"].setdefault("annotations", {})["helm.sh/resource-policy"] = "keep"
        operator_role = expected[("rbac.authorization.k8s.io/v1", "Role", "kubescape", "operator")]
        assert operator_role["rules"][1] == {"apiGroups": [""], "resources": ["secrets"],
            "verbs": ["create", "get", "update", "watch", "list", "patch", "delete"]}
        del operator_role["rules"][1]
    certgen = expected[("rbac.authorization.k8s.io/v1", "ClusterRole", None, "storage-certgen-kubescape")]
    assert certgen["rules"] == [{"apiGroups": ["apiregistration.k8s.io"],
                                 "resources": ["apiservices"], "verbs": ["get", "patch"]}], "chart certgen RBAC changed"
    certgen["rules"][0]["resourceNames"] = [APISERVICE]
    # Native agent HTTP exporting is a documented ConfigMap setting. The
    # pinned 1.42.0 chart omits it with Synchronizer disabled, so Flux's
    # supported postRenderer adds ONLY that field with a byte-exact test.
    # A chart update or change to any other config field fails here.
    node_key = ("v1", "ConfigMap", "kubescape", "node-agent")
    if node_key in expected:
        native = expected[node_key]["data"]
        native["config.json"] = candidate(native["config.json"])
    runtime.post_render(expected)
    actual = indexed(deepcopy(rendered))
    runtime.normalize_rules(expected)
    runtime.normalize_rules(actual)
    assert actual == expected, "post-render changed fields outside qualified edits"


def check_scope(changed_paths):
    protected = ("infra/kyverno/", "infra/kyverno-policies/", "infra/observability/", "infra/cilium/")
    # The publisher lives in its own static monitoring subtree and is
    # qualified separately; unrelated observability/kyverno changes remain blocked.
    allowed = {"infra/observability/gatus-helmrelease.yaml",
               "infra/kyverno-policies/kustomization.yaml",
               "infra/observability/kustomization.yaml",
               "infra/observability/security-incident-publisher/kustomization.yaml",
               "infra/observability/security-incident-publisher/workload.yaml",
               "infra/observability/security-incident-publisher/publisher.py"}
    return [p for p in changed_paths if p.startswith(protected) and p not in allowed]


def retired_namespaces(base_ref, paths):
    """Qualify deleted app Namespace sources against every remaining Flux owner."""
    removed = set()
    for path in paths:
        if not (path.startswith("apps/") and path.endswith((".yaml", ".yml"))
                and not (ROOT / path).exists()):
            continue
        source = subprocess.check_output(["git", "show", base_ref + ":" + path], cwd=ROOT, text=True)
        removed.update(d["metadata"]["name"] for d in documents(source) if d.get("kind") == "Namespace")
    if not removed:
        return set()
    from kustomize_inputs import production_entries
    remaining = set()
    for owner, docs in production_entries(ROOT):
        for doc in docs:
            meta, spec = doc["metadata"], doc.get("spec", {})
            remaining.add(meta["name"] if doc["kind"] == "Namespace" else
                          owner["spec"].get("targetNamespace") or spec.get("targetNamespace") or meta.get("namespace"))
    assert not removed & remaining, "Retired namespace still has a Flux consumer"
    return removed


def check_assessment_alert(before, after, retired=()):
    """Only exact reviewed assessment/collector health endpoints may change."""
    expected = {"name": "kubescape-assessments", "group": "cluster",
                "url": "http://pod-report.homepage.svc.cluster.local:80/kubescape-health.json",
                "interval": "1m", "conditions": ["[STATUS] == 200", "[BODY].status == healthy",
                    "[BODY].vulnerability.evidenceAgeSeconds <= 259200", "[BODY].posture.evidenceAgeSeconds <= 93600"],
                "alerts": [{"type": "github"}]}
    receiver_transport = {
        'name': 'security-receiver-transport',
        'group': 'cluster',
        'url': 'tcp://security-receiver.kubescape.svc.cluster.local:8443',
        'interval': '1m',
        'conditions': ['[CONNECTED] == true'],
        'alerts': [{'type': 'github'}],
    }
    reporting = {"name": "security-reporting", "group": "cluster",
                 "url": "http://pod-report.homepage.svc.cluster.local:80/security-reporting-health.json",
                 "interval": "1m", "conditions": ["[STATUS] == 200", "[BODY].status == healthy"],
                 "alerts": [{"type": "github"}]}
    cve_reporting = dict(reporting, name="security-cve-reporting",
                         conditions=["[STATUS] == 200", "[BODY].cvePublisher == healthy"])
    capacity_reporting = dict(reporting, name="security-receiver-capacity",
                              conditions=["[STATUS] == 200", "[BODY].receiverCapacity == healthy"])
    policy_assurance = dict(reporting, name="kyverno-policy-assurance",
                            conditions=["[STATUS] == 200", "[BODY].kyvernoAssurance == healthy"])
    allowed = [expected, receiver_transport, reporting, cve_reporting, capacity_reporting, policy_assurance] + [dict(name='security-collector-' + name, group='cluster',
        url='http://security-collector.kubescape.svc.cluster.local:8080/' + path,
        interval='1m', conditions=['[STATUS] == 200', condition], alerts=[])
        for name, path, condition in (('process', 'readyz', '[BODY].ready == true'),
                                      ('sources', 'healthz', '[BODY].status == healthy'))]
    before, after = json.loads(json.dumps(before)), json.loads(json.dumps(after))
    # Application retirement permits only removal of old endpoints whose exact
    # service namespace has no remaining desired resource. All retained
    # endpoints and every other Gatus setting keep the existing equality gate.
    from urllib.parse import urlsplit
    endpoints = before["spec"]["values"]["config"]["endpoints"]
    retained = after["spec"]["values"]["config"]["endpoints"]
    for endpoint in endpoints[:]:
        host = (urlsplit(endpoint.get("url", "")).hostname or "").split(".")
        if len(host) == 5 and host[2:] == ["svc", "cluster", "local"] and host[1] in retired:
            assert not any(e["name"] == endpoint["name"] for e in retained), "Retired endpoint still configured"
            endpoints.remove(endpoint)
    for document in (before, after):
        endpoints = document["spec"]["values"]["config"]["endpoints"]
        for reviewed in allowed:
            matching = [e for e in endpoints if e["name"] == reviewed["name"]]
            assert len(matching) <= 1
            if matching:
                multi = dict(reviewed, conditions=reviewed['conditions'] + [
                    '[BODY].expected_agents == 2', '[BODY].healthy_agents == 2', '[BODY].uncovered_workloads == 0'])
                assert matching[0] == reviewed or (reviewed["name"] == "kyverno-policy-assurance"
                                                   and matching[0] == dict(reviewed, alerts=[])) or (reviewed['name'] == 'security-collector-sources'
                                                   and matching[0] == multi), "assessment/collector alert differs"
                endpoints.remove(matching[0])
    assert before == after, "Gatus changed outside the assessment/collector endpoints"


def scope_guard_required(paths):
    return any(path.startswith("infra/kubescape/") or path == "clusters/home/infrastructure/kubescape-kustomization.yaml" for path in paths)


def policy_report(text):
    # CLI deprecation warnings precede the YAML report on stdout.
    start = re.search(r"^apiVersion:", text, re.MULTILINE)
    assert start is not None, "Kyverno did not return a policy report"
    return yaml.safe_load(text[start.start():])


def check_admission(args, work, resources):
    version = subprocess.check_output([args.kyverno, "version"], text=True)
    match = re.search(r"^Version: v?(\d+\.\d+\.\d+)$", version, re.MULTILINE)
    assert match and match[1] == args.expected_kyverno_version, "Executed CLI differs from verified proposed release"
    cel = tuple(map(int, match[1].split("."))) >= (1, 20, 0)
    namespace = yaml.safe_load((ROOT / "infra/kubescape/namespace.yaml").read_text())
    admission_resources = work / "admission-resources.yaml"
    admission_resources.write_text(yaml.safe_dump_all([namespace, *resources]))
    policy_root = ROOT / ("infra/kyverno-policies/policies" if cel else "infra/kyverno/policies")
    policies = [policy_root / name for name in (["workloads.yaml", "namespace.yaml"] if cel else ["platform-pod-standards.yaml", "platform-namespace-standards.yaml"])]
    policies.append(ROOT / "infra/kubescape/runtime/admission.yaml")
    evaluated = subprocess.run([
        args.kyverno, "apply", *(str(path) for path in policies),
        "--resource", str(admission_resources), "--policy-report"], text=True, stdout=subprocess.PIPE, check=False)
    report_text = evaluated.stdout
    (work / "kyverno-report.yaml").write_text(report_text)
    report = policy_report(report_text)
    failures = {(r["policy"], r.get("rule", "validation"), obj["kind"], obj["name"])
                for r in report["results"] if r["result"] in {"fail", "error"}
                for obj in r["resources"]}
    assert not failures, failures
    # CEL explicitly passes the non-service probe guard for the rendered Job;
    # the legacy engine omits that rule result. All other decisions agree.
    expected_passes = 54 + sum(r["kind"] in WORKLOADS for r in resources) if cel else 46
    assert report["summary"] == {"pass": expected_passes, "fail": 0, "error": 0, "skip": 1, "warn": 0}, report["summary"]
    skipped = [r for r in report["results"] if r["result"] == "skip"]
    assert len(skipped) == 1 and skipped[0]["policy"] == "platform-pod-standards-require-runtime-hardening"
    assert skipped[0]["resources"] == [{"apiVersion": "apps/v1", "kind": "DaemonSet", "name": "node-agent", "namespace": "kubescape"}]
    assert sum(r["result"] == "pass" for r in report["results"]) == expected_passes
    assert len(report["results"]) == expected_passes + 1, report["summary"]
    assert evaluated.returncode == 0, "Kyverno exit code contradicts its report"
    # Expose the exact Pod specs for subsequent PSA admission verification.
    # Static checks above are deliberately conservative; this is no live PSA claim.
    pods = work / "psa-pods.yaml"
    pods.write_text(yaml.safe_dump_all([
        {"apiVersion": "v1", "kind": "Pod", "metadata": {
            **pod_template(r).get("metadata", {}), "name": r["metadata"]["name"], "namespace": "kubescape"},
         "spec": pod_template(r)["spec"]} for r in resources if r["kind"] in WORKLOADS]))
    print(f"verified: Kyverno CLI {match[1]}: {expected_passes} passes, 0 failures/errors; exact agent hardening exemption; other Pod fields checked statically")


TLS_SECRET = "secrets/security-receiver-tls.enc.yaml"
CA_SECRET = "secrets/security-receiver-ca.enc.yaml"
RECEIVER_WORKLOAD = "infra/kubescape/receiver/workload.yaml"
TLS_MARKER = "homelab.cooked.beer/tls-leaf-reissue"


def check_leaf_ciphertext(before, after):
    """CI has no decryption key: enforce the exact ciphertext change boundary."""
    before, after = deepcopy(before), deepcopy(after)
    old, new = before["data"], after["data"]
    assert set(old) == set(new) == {"tls.crt", "tls.key"}, "unexpected TLS Secret fields"
    assert all(isinstance(v, str) and v.startswith("ENC[AES256_GCM,")
               for v in new.values()), "TLS Secret must remain encrypted"
    assert old["tls.key"] == new["tls.key"], "TLS private key ciphertext changed"
    assert old["tls.crt"] != new["tls.crt"], "encrypted leaf certificate did not change"
    old.pop("tls.crt")
    new.pop("tls.crt")
    for doc in (before, after):
        for field in ("lastmodified", "mac", "version"):
            doc["sops"].pop(field, None)
    assert before == after, "TLS Secret changed outside leaf and SOPS bookkeeping"


def check_receiver_tls_change(base_ref, paths):
    if not ({RECEIVER_WORKLOAD, TLS_SECRET} & set(paths)):
        return
    def prior(path):
        return subprocess.check_output(["git", "show", base_ref + ":" + path], cwd=ROOT)
    def annotations(source):
        receiver, = [obj for obj in documents(source)
                     if obj.get("kind") == "Deployment"
                     and obj["metadata"]["name"] == "security-receiver"]
        return receiver["spec"]["template"]["metadata"].get("annotations", {})
    old = annotations(prior(RECEIVER_WORKLOAD)).get(TLS_MARKER)
    new = annotations((ROOT / RECEIVER_WORKLOAD).read_text()).get(TLS_MARKER)
    if old == new and TLS_SECRET not in paths:
        return
    assert isinstance(new, str) and new and old != new, "TLS leaf change requires a new restart marker"
    assert TLS_SECRET in paths, "TLS leaf reissue must include the encrypted leaf Secret"
    assert CA_SECRET not in paths, "the existing trust CA must not be rotated"
    check_leaf_ciphertext(yaml.safe_load(prior(TLS_SECRET)),
                          yaml.safe_load((ROOT / TLS_SECRET).read_text()))


def run(args):
    release = yaml.safe_load((ROOT / "infra/kubescape/helmrelease.yaml").read_text())
    check_release(release, active=args.active_review)
    lock = json.loads((ROOT / "infra/kubescape/grype-database.json").read_text())
    assert lock["state"] == lock["publisher"]["status"] == "verified", "DB provenance not independently verified"
    assert lock["indexDigest"] == release["spec"]["values"]["grypeOfflineDB"]["image"]["sha"], "DB proof differs from GitOps"
    assert lock["image"] == "quay.io/kubescape/grype-offline-db@" + lock["indexDigest"]
    assert lock["metadata"] == lock["publisher"]["document"], "publisher identity mismatch"
    assert lock["database"]["built"] == lock["metadata"]["built"]
    assert lock["database"]["schemaVersion"] == lock["metadata"]["schemaVersion"] == "v6.1.10"
    assert lock["database"]["quickCheck"] == "ok"
    child = yaml.safe_load((ROOT / "clusters/home/infrastructure/kubescape-kustomization.yaml").read_text())
    check_child(child, active=args.active_review)
    if args.base_ref:
        changed = subprocess.check_output(["git", "diff", "--name-only", args.base_ref], cwd=ROOT, text=True)
        paths = changed.splitlines()
        check_receiver_tls_change(args.base_ref, paths)
        # The scope guard protects Kubescape infrastructure work. A Kyverno
        # compatibility-only tooling change does not activate that task scope.
        if scope_guard_required(paths):
            assert not check_scope(paths), "Protected infrastructure changed"
            policy_source = "infra/kyverno-policies/kustomization.yaml"
            if policy_source in paths:
                before = yaml.safe_load(subprocess.check_output(["git", "show", args.base_ref + ":" + policy_source], cwd=ROOT))
                before["resources"].append("../kubescape/runtime/admission.yaml")
                assert before == yaml.safe_load((ROOT / policy_source).read_text()), "policy composition changed outside the runtime boundary"
            publisher_parent = "infra/observability/kustomization.yaml"
            if publisher_parent in paths:
                before_parent = yaml.safe_load(subprocess.check_output(
                    ["git", "show", args.base_ref + ":" + publisher_parent], cwd=ROOT))
                after_parent = yaml.safe_load((ROOT / publisher_parent).read_text())
                assert after_parent["resources"] == before_parent["resources"] + [
                    "security-incident-publisher"], "publisher changed other monitoring resources"
                assert {k:v for k,v in after_parent.items() if k != "resources"} == {
                    k:v for k,v in before_parent.items() if k != "resources"}
            alert_path = "infra/observability/gatus-helmrelease.yaml"
            if alert_path in paths:
                before = yaml.safe_load(subprocess.check_output(["git", "show", args.base_ref + ":" + alert_path], cwd=ROOT))
                check_assessment_alert(before, yaml.safe_load((ROOT / alert_path).read_text()),
                                       retired_namespaces(args.base_ref, paths))
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    archive = work / f"kubescape-operator-{VERSION}.tgz"
    if not archive.exists():
        with urllib.request.urlopen(CHART_URL, timeout=60) as response:
            archive.write_bytes(response.read())
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == CHART_SHA256, "chart checksum mismatch"
    values = work / "values.yaml"
    values.write_text(yaml.safe_dump(release["spec"]["values"]))
    raw = subprocess.check_output([args.helm, "template", "kubescape", str(archive), "--namespace", "kubescape",
                                   "--kube-version", "1.34.0", "--include-crds", "--values", str(values)], text=True)
    (work / "raw.yaml").write_text(raw)
    renderer, = release["spec"]["postRenderers"]
    (work / "kustomization.yaml").write_text(yaml.safe_dump({
        "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
        "resources": ["raw.yaml"], **renderer["kustomize"]}))
    rendered = subprocess.check_output([args.kustomize, "build", str(work)], text=True)
    (work / "rendered.yaml").write_text(rendered)
    resources = documents(rendered)
    check_post_render(documents(raw), resources)
    check_effective_config(resources)
    result = inventory(resources)
    (work / "inventory.json").write_text(json.dumps(result, indent=2) + "\n")
    expected = json.loads((ROOT / "infra/kubescape/rendered-inventory.json").read_text())
    assert result == expected, "rendered inventory/blockers changed; review the manifests"
    check_admission(args, work, resources)
    runtime.check_direct_bindings(ROOT)
    runtime.check_boundary(args.kyverno, work, resources, ROOT)
    errors = result["admissionBlockers"]
    for error in errors:
        print(f"failed: {error}")
    print(f"verified: rendered {len(resources)} resources; inventory matches; automatic image routing disabled")
    if args.staged_review and not errors:
        print("verified: suspended staging; zero admission failures; activation still requires separate review")
    if args.active_review and not errors:
        print("verified: active manual assessment mode; Flux health waiting; both scan schedulers suspended")
    return 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helm", default="helm")
    parser.add_argument("--kustomize", default="kustomize")
    parser.add_argument("--kyverno", default="kyverno")
    parser.add_argument("--expected-kyverno-version", required=True, help="Application version resolved and verified by trust.sh")
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".work/kubescape-preflight")
    parser.add_argument("--base-ref", help="verify protected infrastructure and the scoped assessment alert")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged-review", action="store_true", help="verify suspended staging with the same strict admission checks")
    mode.add_argument("--active-review", action="store_true", help="qualify an active release with Flux health waiting and suspended scheduler")
    args = parser.parse_args()
    try:
        return run(args)
    except (AssertionError, KeyError, ValueError, OSError, yaml.YAMLError, subprocess.CalledProcessError) as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
