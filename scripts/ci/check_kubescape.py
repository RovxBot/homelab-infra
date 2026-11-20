#!/usr/bin/env python3
"""Render the suspended Kubescape release and fail closed before activation.

Requires Helm 3, Kustomize, Kyverno CLI 1.19.2 and PyYAML. Never contacts a Kubernetes cluster.
Both the default preflight and --staged-review require zero admission failures.
Neither mode may unsuspend anything or allow policy exceptions.
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
VERSION = "1.42.0"
CHART_SHA256 = "be45f83398f28db8f2ff395e5d8ab411328041e9e2165a42e5fb92a4ba369079"
CHART_URL = ("https://github.com/kubescape/helm-charts/releases/download/"
             f"kubescape-operator-{VERSION}/kubescape-operator-{VERSION}.tgz")
DISABLED = set("""continuousScan nodeScan nodeSbomGeneration scanEmbeddedSBOMs
vulnerabilityScan relevancy vexGeneration runtimeObservability networkPolicyService
networkEventsStreaming runtimeDetection malwareDetection nodeProfileService
admissionController httpDetection seccompProfileService sandboxSeccomp manageWorkloads
syncSBOM agentRuntimePosture riskAcceptance remediation autoUpgrading prometheusExporter""".split())
CERTGEN_NAMES = {"certgen-create", "certgen-patch"}
CERTGEN_BUDGET = {"requests": {"cpu": "10m", "memory": "32Mi"},
                  "limits": {"cpu": "100m", "memory": "128Mi"}}
WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod"}


def documents(text):
    return [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]


def check_release(release):
    spec = release["spec"]
    assert spec.get("suspend") is True, "HelmRelease must remain suspended"
    assert spec["chart"]["spec"]["version"] == VERSION, "chart version changed"
    values = spec["values"]
    caps = values["capabilities"]
    for key in DISABLED:
        assert caps.get(key) == "disable", f"capabilities.{key} must explicitly be disabled"
    for key in ("operator", "configurationScan", "kubescapeOffline"):
        assert caps.get(key) == "enable", f"capabilities.{key} must be enabled"
    assert caps["seccompProfileBackend"] == "storage"
    assert values["certificates"]["strategy"] == "initContainer"
    assert not any(values.get(k) for k in ("server", "account", "accessKey"))
    assert not values["credentials"]["cloudSecret"]
    assert values["global"]["enableClusterWideSecretAccess"] is False
    assert values["nodeAgent"]["privileged"] is False
    assert caps["testing"]["nodeAgentMultiplication"]["enabled"] is False
    assert values["storage"]["hostNetwork"] is False
    assert values["storage"]["mtls"]["enabled"] is True
    assert values["persistence"]["storageClass"] == "longhorn"
    for key in ("enabled", "createEgressRules"):
        assert values["global"]["networkPolicy"][key] is False
    for path in (("nodeAgent", "config", "hostSensor"), ("nodeAgent", "sbomScanner"),
                 ("nodeAgent", "autoscaler"), ("nodeAgent", "multipleDaemonSets"),
                 ("nodeAgent", "gVisorStart"), ("grypeOfflineDB",), ("serviceScanConfig",)):
        item = values
        for part in path:
            item = item[part]
        assert item["enabled"] is False, f"{'.'.join(path)} must be disabled"
    for component in ("kubescape", "operator", "storage", "kubescapeOpsTool"):
        tag = values[component]["image"]["tag"]
        assert re.fullmatch(r"[0-9v][^@ :]*@sha256:[a-f0-9]{64}", tag), f"unpinned {component}"
    assert values["kubescape"]["downloadArtifacts"] is False
    assert values["kubescape"]["skipUpdateCheck"] is True
    assert values["operator"]["triggerSecurityFramework"] is False
    assert values["operator"]["rulesUpdateConfig"]["enabled"] is False
    assert values["includeNamespaces"] == "default", "canary scope expanded"


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
        if kind in {"MutatingWebhookConfiguration", "ValidatingWebhookConfiguration",
                    "NetworkPolicy", "ServiceMonitor", "DaemonSet"}:
            errors.append(f"{identity}: forbidden optional resource")
        if resource["metadata"].get("annotations", {}).get("helm.sh/hook"):
            errors.append(f"{identity}: unexpected Helm hook")
        if kind not in WORKLOADS:
            continue
        if (kind, name) not in {("Deployment", "kubescape"), ("Deployment", "operator"),
                                ("Deployment", "storage"), ("CronJob", "kubescape-scheduler")}:
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
    for key in ("serviceDiscovery", "imageVulnerabilitiesScanningEnabled", "continuousPostureScan",
                "relevantImageVulnerabilitiesEnabled", "scanEmbeddedSBOMs", "storeFilteredSbom"):
        assert cloud[key] is False, f"effective {key} enabled"
    assert cloud["keepLocal"] is True and cloud["nodeAgent"] == "false"
    caps = json.loads(maps["ks-capabilities"]["capabilities"])
    for key in DISABLED:
        assert caps["capabilities"][key] == "disable", f"effective {key} enabled"
    for key in ("nodeAgent", "sbomScanner", "kubevuln", "kubevulnScheduler", "synchronizer",
                "autoUpdater", "prometheusExporter", "serviceDiscovery"):
        assert caps["components"][key]["enabled"] is False, f"effective {key} enabled"
    scanner = next(r for r in resources if r["kind"] == "Deployment" and r["metadata"]["name"] == "kubescape")
    env = {e["name"]: e.get("value") for e in pod_template(scanner)["spec"]["containers"][0]["env"]}
    for key, expected in {"KS_ENABLE_HOST_SCANNER": "false", "KS_DOWNLOAD_ARTIFACTS": "false",
                          "KS_SKIP_UPDATE_CHECK": "true", "KS_OFFLINE": "true",
                          "KS_INCLUDE_NAMESPACES": "default"}.items():
        assert env[key] == expected, f"unexpected scanner {key}"
    operator = next(r for r in resources if r["kind"] == "Deployment" and r["metadata"]["name"] == "operator")
    env = {e["name"]: e.get("value") for e in pod_template(operator)["spec"]["containers"][0]["env"]}
    assert env["KS_SKIP_UPDATE_CHECK"] == "true" and env["WORKERCONCURRENCY"] == "1"


def inventory(resources):
    return {
        "chart": {"version": VERSION, "url": CHART_URL, "sha256": CHART_SHA256, "kubernetes": "1.34.0"},
        "counts": dict(sorted(Counter(r["kind"] for r in resources).items())),
        "resources": sorted(f"{r['kind']}/{r['metadata'].get('namespace', '-')}/{r['metadata']['name']}"
                            for r in resources),
        "certificateInitBudgets": {
            c["name"]: c.get("resources", {}) for r in resources
            if (r["kind"], r["metadata"].get("namespace"), r["metadata"]["name"])
            == ("Deployment", "kubescape", "storage")
            for c in pod_template(r)["spec"].get("initContainers", [])},
        "admissionBlockers": findings(resources),
    }


def check_post_render(raw, rendered):
    """Allow exactly certgen budgets and the existing scheduler suspension."""
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
    assert indexed(rendered) == expected, "post-render changed fields outside certgen budgets/scheduler suspension"


def check_scope(changed_paths):
    protected = ("infra/kyverno/", "infra/kyverno-policies/", "infra/observability/", "infra/cilium/")
    return [p for p in changed_paths if p.startswith(protected)]


def policy_report(text):
    # CLI deprecation warnings precede the YAML report on stdout.
    start = re.search(r"^apiVersion:", text, re.MULTILINE)
    assert start is not None, "Kyverno did not return a policy report"
    return yaml.safe_load(text[start.start():])


def check_admission(args, work, resources):
    version = subprocess.check_output([args.kyverno, "version"], text=True)
    assert re.search(r"^Version: 1\.19\.2$", version, re.MULTILINE), "Kyverno CLI must be 1.19.2"
    namespace = yaml.safe_load((ROOT / "infra/kubescape/namespace.yaml").read_text())
    admission_resources = work / "admission-resources.yaml"
    admission_resources.write_text(yaml.safe_dump_all([namespace, *resources]))
    policy_root = ROOT / "infra/kyverno/policies"
    evaluated = subprocess.run([
        args.kyverno, "apply", str(policy_root / "platform-pod-standards.yaml"),
        str(policy_root / "platform-namespace-standards.yaml"),
        "--resource", str(admission_resources), "--policy-report"], text=True, stdout=subprocess.PIPE, check=False)
    report_text = evaluated.stdout
    (work / "kyverno-report.yaml").write_text(report_text)
    report = policy_report(report_text)
    failures = {(r["policy"], r["rule"], obj["kind"], obj["name"])
                for r in report["results"] if r["result"] in {"fail", "error"}
                for obj in r["resources"]}
    assert not failures, failures
    assert report["summary"] == {"pass": 27, "fail": 0, "error": 0, "skip": 0, "warn": 0}, report["summary"]
    assert len(report["results"]) == 27 and all(r["result"] == "pass" for r in report["results"]), report["summary"]
    assert evaluated.returncode == 0, "Kyverno exit code contradicts its report"
    # Expose the exact Pod specs for subsequent PSA admission verification.
    # Static checks above are deliberately conservative; this is no live PSA claim.
    pods = work / "psa-pods.yaml"
    pods.write_text(yaml.safe_dump_all([
        {"apiVersion": "v1", "kind": "Pod", "metadata": {
            **pod_template(r).get("metadata", {}), "name": r["metadata"]["name"], "namespace": "kubescape"},
         "spec": pod_template(r)["spec"]} for r in resources if r["kind"] in WORKLOADS]))
    print("verified: Kyverno CLI 1.19.2: 27 passes, 0 failures/errors/skips; restricted Pod fields checked statically")


def run(args):
    release = yaml.safe_load((ROOT / "infra/kubescape/helmrelease.yaml").read_text())
    check_release(release)
    if args.base_ref:
        changed = subprocess.check_output(["git", "diff", "--name-only", args.base_ref], cwd=ROOT, text=True)
        assert not check_scope(changed.splitlines()), "Kyverno/Gatus/Cilium changed"
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
    errors = result["admissionBlockers"]
    for error in errors:
        print(f"failed: {error}")
    print(f"verified: rendered {len(resources)} resources; inventory matches; optional components disabled")
    if args.staged_review and not errors:
        print("verified: suspended staging; zero admission failures; activation still requires separate review")
    return 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helm", default="helm")
    parser.add_argument("--kustomize", default="kustomize")
    parser.add_argument("--kyverno", default="kyverno")
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".work/kubescape-preflight")
    parser.add_argument("--base-ref", help="also verify this task did not modify Kyverno, Gatus or Cilium")
    parser.add_argument("--staged-review", action="store_true", help="verify suspended staging with the same strict admission checks")
    args = parser.parse_args()
    try:
        return run(args)
    except (AssertionError, KeyError, ValueError, OSError, yaml.YAMLError, subprocess.CalledProcessError) as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
