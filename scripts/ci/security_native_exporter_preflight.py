#!/usr/bin/env python3
"""Non-deploying proof for Kubescape 1.42.0 native HTTP exporter configuration.

Uses the exact checksum-pinned upstream Helm chart and a synthetic Helm render.
Constructs an *unapplied candidate* Flux Helm post-render JSON patch to the
upstream-documented node-agent config.json only. No Kubernetes API/Secret reads,
no live HelmRelease modifications, no Synchronizer activation.

This preflight intentionally cannot establish in-cluster extra-CA root trust,
TokenReview success, or delivery/retry acceptance; those require the later
reviewed, two-node runtime canary.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / "scripts" / "ci"
sys.path.insert(0, str(CI))
from check_kubescape import CHART_URL, CHART_SHA256, VERSION

EXPORTER = {
    "url": "https://security-receiver.kubescape.svc:8443",
    "method": "POST",
    "tokenFile": "/var/run/secrets/kubescape.io/api-token/token",
    "timeoutSeconds": 8,
    "maxAlertsPerMinute": 30,
    "enableAlertBulking": False,
}
EXPORTER_PATH = "/v1/runtimealerts"
SCOPE = ["metal3", "metal6"]
NAMESPACES = ["homepage", "memos", "kubescape-runtime-test"]


def unique_pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate_chart_json_key")
        out[k] = v
    return out


def load_config(raw):
    if not isinstance(raw, str):
        raise ValueError("invalid_config_type")
    result = json.loads(raw, object_pairs_hook=unique_pairs)
    if not isinstance(result, dict) or not isinstance(result.get("exporters"), dict):
        raise ValueError("chart_exporters_not_present")
    return result


def candidate(raw):
    baseline = load_config(raw)
    exporters = baseline["exporters"]
    if "httpExporterConfig" in exporters:
        raise ValueError("chart_now_emits_http_exporter_review_required")
    if exporters.get("stdoutExporter") is not True:
        raise ValueError("stdout_exporter_must_remain_active")
    if exporters.get("syslogExporterURL") not in ("", None):
        raise ValueError("unexpected_syslog_exporter")
    if exporters.get("alertManagerExporterUrls") not in ([], None):
        raise ValueError("unexpected_alertmanager_exporter")
    proposed = deepcopy(baseline)
    proposed["exporters"]["httpExporterConfig"] = deepcopy(EXPORTER)
    changed = {key for key in baseline if baseline[key] != proposed.get(key)}
    if changed != {"exporters"}:
        raise ValueError("unapproved_configuration_drift")
    original_exporters = deepcopy(proposed["exporters"])
    del original_exporters["httpExporterConfig"]
    if original_exporters != exporters:
        raise ValueError("non_http_exporter_modified")
    # Keep upstream-provided config keys exactly; only a documented HTTP
    # exporter field is inserted. One ConfigMap string must be patched atomically.
    return json.dumps(proposed, indent=4, ensure_ascii=False) + "\n"


def configmap_patch(raw, new):
    # Native Flux Helm post-render JSON6902 patch, tested against exact
    # render. Test op guards EVERY byte of the rendered original ConfigMap.
    # Revisit chart upgrades deliberately; never quietly override chart drift.
    return {
        "target": {"version": "v1", "kind": "ConfigMap",
                   "name": "node-agent", "namespace": "kubescape"},
        "patch": yaml.safe_dump([
            {"op": "test", "path": "/data/config.json", "value": raw},
            {"op": "replace", "path": "/data/config.json", "value": new},
        ], sort_keys=False),
    }


def one(resources, kind, name, namespace="kubescape"):
    choices = [r for r in resources if isinstance(r, dict)
               and r.get("kind") == kind
               and r.get("metadata", {}).get("name") == name
               and r.get("metadata", {}).get("namespace") == namespace]
    if len(choices) != 1:
        raise ValueError("unexpected_pinned_chart_identity")
    return choices[0]


def verify_baseline(raw_yaml, values):
    resources = [r for r in yaml.safe_load_all(raw_yaml) if isinstance(r, dict)]
    cfg = one(resources, "ConfigMap", "node-agent")["data"]["config.json"]
    current = load_config(cfg)
    if values["global"]["apiAuth"]["enabled"] is not True:
        raise ValueError("api_auth_disabled")
    if values["nodeAgent"]["config"]["stdoutExporter"] is not True:
        raise ValueError("stdout_disabled")
    ds = one(resources, "DaemonSet", "node-agent")
    pod = ds["spec"]["template"]["spec"]
    if pod.get("serviceAccountName") != "node-agent":
        raise ValueError("unexpected_node_agent_sa")
    token = [v for v in pod.get("volumes", ()) if v.get("name") == "kubescape-api-token"]
    if len(token) != 1 or token[0].get("projected", {}).get("sources") != [{
        "serviceAccountToken": {
            "audience": "kubescape.io", "expirationSeconds": 3600, "path": "token"}
    }]:
        raise ValueError("bound_kubescape_io_token_missing")
    container = pod["containers"]
    if len(container) != 1 or container[0].get("name") != "node-agent":
        raise ValueError("unexpected_node_agent_containers")
    mounts = [m for m in container[0].get("volumeMounts", ())
              if m.get("name") == "kubescape-api-token"]
    if len(mounts) != 1 or mounts[0].get("mountPath") != "/var/run/secrets/kubescape.io/api-token":
        raise ValueError("bound_token_mount_missing")
    if current.get("runtimeDetectionEnabled") is not True:
        raise ValueError("runtime_detection_off")
    # Synchronizer and alternate transports remain disabled; HTTP exporter
    # is a separately supported, documented node-agent feature.
    caps = json.loads(one(resources, "ConfigMap", "ks-capabilities")["data"]["capabilities"],
                      object_pairs_hook=unique_pairs)
    if caps["components"]["synchronizer"]["enabled"] is not False:
        raise ValueError("synchronizer_not_disabled")
    return cfg


def run(output, helm):
    release = yaml.safe_load((ROOT / "infra/kubescape/helmrelease.yaml").read_text())
    if release["spec"]["chart"]["spec"]["version"] != VERSION:
        raise ValueError("chart_changed")
    values = release["spec"]["values"]
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"kubescape-operator-{VERSION}.tgz"
    if not archive.is_file():
        with urllib.request.urlopen(CHART_URL, timeout=60) as resp:
            archive.write_bytes(resp.read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != CHART_SHA256:
        raise ValueError("pinned_chart_checksum_mismatch")
    values_path = output / "chart-values.yaml"
    values_path.write_text(yaml.safe_dump(values))
    raw_yaml = subprocess.check_output([
        helm, "template", "kubescape", str(archive), "--namespace",
        "kubescape", "--kube-version", "1.34.0", "--values", str(values_path),
    ], text=True)
    raw = verify_baseline(raw_yaml, values)
    new = candidate(raw)
    patch = configmap_patch(raw, new)
    # Keep a machine-reviewable off-by-default artifact. Nothing generated
    # here is installed, applied or added to the live HelmRelease.
    (output / "node-agent-base.json").write_text(raw)
    (output / "node-agent-native-candidate.json").write_text(new)
    (output / "helm-post-render-candidate.yaml").write_text(
        yaml.safe_dump(patch, sort_keys=False))
    result = {
        "chart": VERSION,
        "chart_sha256": CHART_SHA256,
        "original_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "proposed_sha256": hashlib.sha256(new.encode()).hexdigest(),
        "only_addition": "exporters.httpExporterConfig",
        "token_audience": "kubescape.io",
        "agent_scope": SCOPE,
        "namespaces": NAMESPACES,
        "sync_enabled": False,
        "upstream_agent_changed": False,
        "active_manifests_changed": False,
    }
    (output / "proof.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--helm", default="helm")
    args = parser.parse_args()
    run(args.output, args.helm)
