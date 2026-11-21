#!/usr/bin/env python3
"""Real admission and controller lifecycle rehearsal, confined to one kind context."""
import copy
import json
import os
import pathlib
import subprocess
import time
import traceback
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
WORK = ROOT / ".work/cel"
EVIDENCE = WORK / "evidence"
CONTEXT = "kind-kyverno-cel-rehearsal"
RESULTS = []
OWNER = "homelab.cooked.beer/owner"


def run(args, data=None, check=True, timeout=90):
    p = subprocess.run(
        args, input=data, text=True, capture_output=True, timeout=timeout
    )
    if check and p.returncode:
        raise RuntimeError(f"{args}: {p.stdout}\n{p.stderr}")
    return p


def k(*args, data=None, check=True, timeout=90):
    return run(
        ["kubectl", "--context", CONTEXT, "--request-timeout=30s", *args],
        data,
        check,
        timeout,
    )


def apply(doc):
    return k(
        "apply",
        "--server-side",
        "--force-conflicts",
        "-f",
        "-",
        data=yaml.safe_dump_all(doc if isinstance(doc, list) else [doc]),
    )


def delete(doc):
    docs = doc if isinstance(doc, list) else [doc]
    # Fixture Pods deliberately reference a nonexistent node, so no kubelet can
    # acknowledge graceful deletion. They never start or pull an image.
    pod_flags = (
        ["--grace-period=0", "--force"] if any(d["kind"] == "Pod" for d in docs) else []
    )
    return k(
        "delete",
        "--wait=true",
        "--timeout=45s",
        "--ignore-not-found=true",
        *pod_flags,
        "-f",
        "-",
        data=yaml.safe_dump_all(doc if isinstance(doc, list) else [doc]),
    )


def get(kind, name="", ns=""):
    args = (
        ["get", kind]
        + ([name] if name else [])
        + (["-n", ns] if ns else ["-A"])
        + ["-o", "json"]
    )
    return json.loads(k(*args).stdout)


def record(name, **details):
    row = dict(name=name, **details)
    RESULTS.append(row)
    (EVIDENCE / "results.json").write_text(json.dumps(RESULTS, indent=2) + "\n")
    print(json.dumps(row), flush=True)


def wait(label, fn, seconds=75):
    end = time.monotonic() + seconds
    last = None
    while time.monotonic() < end:
        try:
            last = fn()
            if last:
                return last
        except (RuntimeError, KeyError, TypeError) as e:
            last = str(e)
        time.sleep(2)
    raise AssertionError(f"{label}: deadline exceeded; last={last}")


def ready(p):
    def check():
        obj = get(p["kind"], p["metadata"]["name"], p["metadata"].get("namespace", ""))
        status = obj.get("status", {})
        (
            EVIDENCE
            / ("policy-status-" + p["kind"] + "-" + p["metadata"]["name"] + ".json")
        ).write_text(json.dumps(obj, indent=2) + "\n")
        legacy_ready = any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in status.get("conditions", [])
        )
        return (status.get("ready") is True or legacy_ready) and all(
            c["status"] == "True" for c in status.get("conditions", [])
        )

    wait(p["metadata"]["name"], check)


def labels():
    return {
        OWNER: "test",
        **{
            "pod-security.kubernetes.io/" + k: v
            for k, v in {
                "enforce": "privileged",
                "enforce-version": "v1.34",
                "audit": "restricted",
                "audit-version": "v1.34",
                "warn": "restricted",
                "warn-version": "v1.34",
            }.items()
        },
    }


def namespace(name):
    return dict(
        apiVersion="v1", kind="Namespace", metadata=dict(name=name, labels=labels())
    )


def load(path):
    return list(yaml.safe_load_all((ROOT / path).read_text()))


def install(version):
    chart = {"1.19.2": "3.9.2", "1.20.0": "3.10.0"}[version]
    chartfile = WORK / f"kyverno-{chart}.tgz"
    run(
        [
            "curl",
            "-fsSL",
            "--connect-timeout",
            "15",
            "--max-time",
            "120",
            f"https://kyverno.github.io/kyverno/kyverno-{chart}.tgz",
            "-o",
            str(chartfile),
        ]
    )
    import hashlib

    digest = hashlib.sha256(chartfile.read_bytes()).hexdigest()
    if version == "1.19.2":
        assert (
            digest == "f6fe7184544d5e2ee33a77808c09e67aa603ce6560cc197038fde31d727c861e"
        )
    if version == "1.20.0":
        assert (
            digest == "55961392dc0821eb367323f0e9585c061dd4407ef7ada921bd56ea1642c2a301"
        )
    values = load("infra/kyverno/helmrelease.yaml")[0]["spec"]["values"]
    values["admissionController"]["replicas"] = 1
    values["features"]["backgroundScan"] = {"backgroundScanInterval": "20s"}
    # Additional isolated permissions are scoped to these three output kinds.
    values["backgroundController"]["rbac"] = {
        "clusterRole": {
            "extraResources": [
                {
                    "apiGroups": [""],
                    "resources": ["limitranges", "resourcequotas"],
                    "verbs": [
                        "get",
                        "list",
                        "watch",
                        "create",
                        "update",
                        "patch",
                        "delete",
                    ],
                },
                {
                    "apiGroups": ["networking.k8s.io"],
                    "resources": ["networkpolicies"],
                    "verbs": [
                        "get",
                        "list",
                        "watch",
                        "create",
                        "update",
                        "patch",
                        "delete",
                    ],
                },
            ]
        }
    }
    if version == "1.20.0":
        for component, image in [
            ("admissionController", "kyverno"),
            ("backgroundController", "background-controller"),
            ("cleanupController", "cleanup-controller"),
            ("reportsController", "reports-controller"),
        ]:
            sig = json.loads((WORK / f"trust/{image}.signature.json").read_text())
            target = (
                values[component]["container"]["image"]
                if component == "admissionController"
                else values[component]["image"]
            )
            target["tag"] = (
                "v1.20.0@" + sig[0]["critical"]["image"]["docker-manifest-digest"]
            )
        for target, image in [
            (values["admissionController"]["initContainer"]["image"], "kyvernopre"),
            (values["crds"]["migration"]["image"], "kyverno-cli"),
        ]:
            target["tag"] = (
                "v1.20.0@"
                + json.loads((WORK / f"trust/{image}.signature.json").read_text())[0][
                    "critical"
                ]["image"]["docker-manifest-digest"]
            )
        cli_digest = json.loads(
            (WORK / "trust/kyverno-cli.signature.json").read_text()
        )[0]["critical"]["image"]["docker-manifest-digest"]
        ready_digest = json.loads(
            (WORK / "trust/readiness-checker.signature.json").read_text()
        )[0]["critical"]["image"]["docker-manifest-digest"]
        values["global"] = {"image": {"registry": "ghcr.io"}}
        values["upgrade"] = {
            "legacyPolicyCheck": {
                "image": {"registry": "ghcr.io", "tag": "v1.20.0@" + cli_digest}
            }
        }
        values["webhooksCleanup"] = {
            "image": {"registry": "ghcr.io", "tag": "v1.20.0@" + ready_digest}
        }
        values["test"] = {
            "image": {"registry": "ghcr.io", "tag": "v1.20.0@" + ready_digest}
        }
    vp = WORK / "values.yaml"
    vp.write_text(yaml.safe_dump(values))
    run(
        [
            "helm",
            "upgrade",
            "--install",
            "kyverno",
            str(chartfile),
            "--namespace",
            "kyverno",
            "--create-namespace",
            "--kube-context",
            CONTEXT,
            "--values",
            str(vp),
            "--wait",
            "--timeout",
            "4m",
        ],
        timeout=270,
    )
    observed = [
        dict(
            pod=p["metadata"]["name"],
            images=[
                c["image"]
                for c in p["spec"].get("initContainers", []) + p["spec"]["containers"]
            ],
            runtime_images=[
                s.get("imageID", "")
                for s in p.get("status", {}).get("containerStatuses", [])
            ],
        )
        for p in get("pods", ns="kyverno")["items"]
    ]
    if version == "1.20.0":
        assert all(
            "@sha256:" in image for p in observed for image in p["images"]
        ), observed
    record(
        "install",
        status="verified",
        version=version,
        chart=chart,
        chart_sha256=digest,
        images=observed,
    )


def admission(doc, operation="CREATE", check_policy=True, user=None):
    args = [
        {"CREATE": "create", "UPDATE": "replace", "DELETE": "delete"}[operation],
        "--dry-run=server",
        "-f",
        "-",
        "-o",
        "json",
    ]
    if user:
        args += ["--as", user]
    p = k(*args, data=yaml.safe_dump(doc), check=False)
    allowed = p.returncode == 0
    if (
        not allowed
        and check_policy
        and not any(
            x in p.stderr for x in ["kyverno", "policy-exception-accountability"]
        )
    ):
        raise AssertionError(f"Non-policy denial: {p.stderr}")
    if not allowed and any(
        x in p.stderr.lower()
        for x in [
            "failed calling webhook",
            "no endpoints",
            "context deadline",
            "invalid expression",
            "undeclared reference",
            "no such key",
        ]
    ):
        raise AssertionError(
            f"Infrastructure/evaluation error is not a policy denial: {p.stderr}"
        )
    return dict(allowed=allowed, warning="Warning:" in p.stderr, stderr=p.stderr)


def smoke():
    for name in [
        "gitea-jobs",
        "wowdecomp-ci-jobs",
        "cel-tests",
        "longhorn-system",
        "gpu-operator",
        "wireguard",
        "wotlk",
        "home-assistant",
        "cloudflared",
        "homepage",
    ]:
        apply(namespace(name))


def governance(version):
    old = load("infra/kyverno/policies/policy-exception-accountability.yaml")[0]
    if version == "1.19.2":
        apply(old)
        ready(old)
    native = load("migration/kyverno/policies/governance-native.yaml")
    for mode in ["original", "native"] if version == "1.19.2" else ["native"]:
        if mode == "native":
            apply(native)
            time.sleep(3)
        for group, api in [
            ("legacy", "kyverno.io/v2"),
            ("cel", "policies.kyverno.io/v1"),
        ]:
            if version == "1.20.0" and group == "legacy":
                continue
            base = (
                load("infra/kyverno/exceptions/home-assistant.yaml")[0]
                if group == "legacy"
                else load("migration/kyverno/exceptions/exceptions.yaml")[1]
            )
            base = copy.deepcopy(base)
            base["apiVersion"] = api
            base["metadata"]["name"] = "accountability-probe"
            cases = [("valid", base)]
            url = copy.deepcopy(base)
            url["metadata"]["annotations"].pop("github.cooked.beer/issue-create", None)
            url["metadata"]["annotations"][
                "github.cooked.beer/issue-url"
            ] = "https://github.com/RovxBot/homelab-infra/issues/2133"
            cases.append(("valid-issue-url", url))
            invalid_url = copy.deepcopy(url)
            invalid_url["metadata"]["annotations"][
                "github.cooked.beer/issue-url"
            ] = "https://example.invalid/issues/2133"
            cases.append(("invalid-issue-url", invalid_url))
            false_marker = copy.deepcopy(base)
            false_marker["metadata"]["annotations"][
                "github.cooked.beer/issue-create"
            ] = "false"
            cases.append(("false-issue-marker", false_marker))
            for field, container in [
                (OWNER, "labels"),
                ("kyverno.cooked.beer/reason", "annotations"),
                ("kyverno.cooked.beer/expires-at", "annotations"),
                ("github.cooked.beer/issue-create", "annotations"),
            ]:
                for mutation in ["missing", "empty"]:
                    d = copy.deepcopy(base)
                    if mutation == "missing":
                        d["metadata"][container].pop(field, None)
                    else:
                        d["metadata"][container][field] = ""
                    cases.append((field + "-" + mutation, d))
            for name, doc in cases:
                result = admission(doc)
                expected = mode == "original" or name in ["valid", "valid-issue-url"]
                assert result["allowed"] == expected, (
                    version,
                    mode,
                    group,
                    name,
                    result,
                )
                record(
                    "governance-create",
                    status="verified",
                    version=version,
                    mode=mode,
                    api=api,
                    case=name,
                    **result,
                )
            # A valid stored object exercises update admission, not just dry-run CREATE.
            apply(base)
            stored = get(
                "policyexceptions." + api.split("/")[0],
                base["metadata"]["name"],
                "kyverno",
            )
            for name, doc in cases:
                d = copy.deepcopy(stored)
                d["metadata"]["labels"] = doc["metadata"]["labels"]
                d["metadata"]["annotations"] = doc["metadata"]["annotations"]
                result = admission(d, "UPDATE")
                expected = mode == "original" or name in ["valid", "valid-issue-url"]
                assert result["allowed"] == expected, (
                    version,
                    mode,
                    group,
                    name,
                    result,
                )
                record(
                    "governance-update",
                    status="verified",
                    version=version,
                    mode=mode,
                    api=api,
                    case=name,
                    **result,
                )
            delete(base)
        if mode == "native":
            # Native governance applies only to exception APIs. A stopped Kyverno
            # cannot create a dependency for the controller's own Deployment.
            k(
                "scale",
                "deployment/kyverno-admission-controller",
                "-n",
                "kyverno",
                "--replicas=0",
            )
            wait(
                "admission controller stops",
                lambda: get("deployment", "kyverno-admission-controller", "kyverno")
                .get("status", {})
                .get("availableReplicas", 0)
                == 0,
            )
            from corpus import workload

            bootstrap = workload(name="kyverno-bootstrap-probe", ns="kyverno")
            result = admission(bootstrap)
            assert result["allowed"], result
            record(
                "native-governance-bootstrap",
                status="verified",
                version=version,
                **result,
            )
            k(
                "scale",
                "deployment/kyverno-admission-controller",
                "-n",
                "kyverno",
                "--replicas=1",
            )
            k(
                "rollout",
                "status",
                "deployment/kyverno-admission-controller",
                "-n",
                "kyverno",
                "--timeout=90s",
                timeout=100,
            )
            delete(native)
    if version == "1.19.2":
        delete(old)


def main():
    assert os.environ.get("REHEARSAL_CONTEXT") == CONTEXT
    config = json.loads(k("config", "view", "--minify", "-o", "json").stdout)
    endpoint = config["clusters"][0]["cluster"]["server"]
    assert endpoint.startswith("https://127.0.0.1:"), endpoint
    assert get("nodes")["items"][0]["status"]["nodeInfo"]["kubeletVersion"] == "v1.34.0"
    assert not get("gitrepositories.source.toolkit.fluxcd.io")["items"]
    install("1.19.2")
    smoke()
    governance("1.19.2")
    from equivalence import suite, whole_runners

    suite("1.19.2", "legacy")
    suite("1.19.2", "cel")
    whole_runners("1.19.2", "legacy")
    whole_runners("1.19.2", "cel")
    from reporting import run as reports

    for engine in ["legacy", "cel"]:
        try:
            reports("1.19.2", engine)
        except Exception as e:
            record(
                "reporting",
                status="failed",
                version="1.19.2",
                engine=engine,
                error=str(e),
            )
    from generation import before_upgrade

    try:
        generation_state = before_upgrade()
    except Exception as e:
        generation_state = {"passed": False, "error": str(e)}
        record("generator", status="failed", error=str(e))
        # Abandon this isolated fixture so the chart safety check can still gate
        # a real upgrade and independent validation work can finish.
        for kind in ["clusterpolicy", "generatingpolicy"]:
            k(
                "delete",
                kind,
                "platform-namespace-bootstrap",
                "--ignore-not-found=true",
                check=False,
            )
    record("integration-baseline", status="verified")
    if os.environ.get("CANDIDATE_TRUST") != "success":
        record("candidate-upgrade", status="not run", reason="artifact trust failed")
        return
    install("1.20.0")
    smoke()
    governance("1.20.0")
    suite("1.20.0", "cel")
    whole_runners("1.20.0", "cel")
    try:
        reports("1.20.0", "cel")
    except Exception as e:
        record(
            "reporting", status="failed", version="1.20.0", engine="cel", error=str(e)
        )
    if generation_state["passed"]:
        from generation import after_upgrade

        after_upgrade(generation_state)
    assert not any(
        r.get("status") == "failed" for r in RESULTS
    ), "One or more activation gates failed; see results.json"


if __name__ == "__main__":
    import sys

    sys.modules["rehearse"] = sys.modules[__name__]
    try:
        main()
    except Exception as e:
        record("rehearsal", status="failed", error=str(e))
        traceback.print_exc()
        raise
