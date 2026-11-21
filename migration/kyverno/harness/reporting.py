"""Warning, Audit and background report checks against the API server."""

import copy
import json
import time
from corpus import workload
from rehearse import (
    EVIDENCE,
    OWNER,
    apply,
    delete,
    load,
    ready,
    admission,
    get,
    k,
    wait,
    record,
    namespace,
)


def policy_results(namespace, policy, uid=None):
    reports = get("policyreports", ns=namespace)["items"]
    return [
        r for p in reports for r in p.get("results", []) if r.get("policy") == policy
    ]


def run(version, engine):
    ns = "cel-reporting"
    apply(namespace(ns))
    if engine == "legacy":
        owner = load("infra/kyverno/policies/platform-pod-standards.yaml")[0]
        owner["spec"]["rules"] = [
            r for r in owner["spec"]["rules"] if r["name"] == "require-owner-label"
        ]
        flux = load("infra/kyverno/policies/platform-flux-guardrails.yaml")[0]
    else:
        owner = load("migration/kyverno/policies/workloads.yaml")[0]
        flux = load("migration/kyverno/policies/flux.yaml")[0]
    bad = workload(name="pre-existing-violation", ns=ns)
    bad["spec"]["template"]["metadata"]["labels"].pop(OWNER)
    apply(bad)
    bad_uid = get("deployment", "pre-existing-violation", ns)["metadata"]["uid"]
    apply(owner)
    ready(owner)
    try:
        wait(
            "background violation report",
            lambda: any(
                r.get("result") == "fail"
                for r in policy_results(ns, owner["metadata"]["name"], bad_uid)
            ),
            75,
        )
        record(
            "background-report",
            status="verified",
            version=version,
            engine=engine,
            results=policy_results(ns, owner["metadata"]["name"], bad_uid),
        )
        old = get("deployment", "pre-existing-violation", ns)
        old["metadata"].setdefault("annotations", {})["test"] = "update"
        result = admission(old, "UPDATE")
        assert result["allowed"], result
        record(
            "existing-violation-update",
            status="verified",
            version=version,
            engine=engine,
            **result
        )
        good = workload(name="new-violation", ns=ns)
        apply(good)
        obj = get("deployment", "new-violation", ns)
        obj["spec"]["template"]["metadata"]["labels"].pop(OWNER)
        result = admission(obj, "UPDATE")
        assert not result["allowed"], result
        record(
            "introduce-new-violation",
            status="verified",
            version=version,
            engine=engine,
            **result
        )
        delete(good)
    finally:
        delete(owner)
        delete(bad)
    apply(flux)
    ready(flux)
    time.sleep(1)
    try:
        flux_boundaries(version, engine)
        cm = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "flux-probe",
                "namespace": ns,
                "labels": {"kustomize.toolkit.fluxcd.io/name": "rehearsal"},
            },
            "data": {"test": "before"},
        }
        for user in [
            None,
            "system:serviceaccount:flux-system:helm-controller",
            "system:serviceaccount:flux-system:kustomize-controller",
        ]:
            p = admission(cm, user=user)
            assert p["allowed"] and p["warning"] == (user is None), (user, p)
            record(
                "flux-create",
                status="verified",
                version=version,
                engine=engine,
                user=user or "manual",
                **p
            )
        apply(cm)
        cm_uid = get("configmap", "flux-probe", ns)["metadata"]["uid"]
        for operation in ["UPDATE", "DELETE"]:
            obj = get("configmap", "flux-probe", ns)
            if operation == "UPDATE":
                obj["data"]["test"] = "after"
            for user in [
                None,
                "system:serviceaccount:flux-system:kustomize-controller",
            ]:
                p = admission(obj, operation, user=user)
                assert p["allowed"] and p["warning"] == (user is None), (
                    operation,
                    user,
                    p,
                )
                record(
                    "flux-" + operation.lower(),
                    status="verified",
                    version=version,
                    engine=engine,
                    user=user or "manual",
                    **p
                )
        wait(
            "admission Audit report",
            lambda: any(
                r.get("result") == "fail"
                for r in policy_results(ns, flux["metadata"]["name"], cm_uid)
            ),
            75,
        )
        record(
            "flux-audit-report",
            status="verified",
            version=version,
            engine=engine,
            results=policy_results(ns, flux["metadata"]["name"], cm_uid),
        )
        delete(cm)
    finally:
        delete(flux)


def flux_boundaries(version, engine):
    """Real Audit/warning decisions, including the exact Longhorn conjunction."""
    versions = [
        {
            "name": v,
            "served": True,
            "storage": v == "v1beta2",
            "schema": {
                "openAPIV3Schema": {
                    "type": "object",
                    "properties": {
                        "spec": {
                            "type": "object",
                            "x-kubernetes-preserve-unknown-fields": True,
                        }
                    },
                }
            },
        }
        for v in ["v1beta1", "v1beta2"]
    ]
    crd = {
        "apiVersion": "apiextensions.k8s.io/v1",
        "kind": "CustomResourceDefinition",
        "metadata": {"name": "backuptargets.longhorn.io"},
        "spec": {
            "group": "longhorn.io",
            "names": {
                "plural": "backuptargets",
                "singular": "backuptarget",
                "kind": "BackupTarget",
            },
            "scope": "Namespaced",
            "versions": versions,
        },
    }
    apply(crd)
    k(
        "wait",
        "--for=condition=Established",
        "crd/backuptargets.longhorn.io",
        "--timeout=30s",
    )
    users = ["longhorn-service-account", "adjacent-controller"]
    for name in users:
        apply(
            {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {"name": name, "namespace": "longhorn-system"},
            }
        )
    role = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRole",
        "metadata": {"name": "rehearsal-longhorn-probes"},
        "rules": [
            {
                "apiGroups": ["longhorn.io"],
                "resources": ["backuptargets"],
                "verbs": ["get", "create", "update", "delete"],
            },
            {
                "apiGroups": [""],
                "resources": ["configmaps"],
                "verbs": ["get", "create", "update", "delete"],
            },
        ],
    }
    binding = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRoleBinding",
        "metadata": {"name": "rehearsal-longhorn-probes"},
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": "rehearsal-longhorn-probes",
        },
        "subjects": [
            {"kind": "ServiceAccount", "name": n, "namespace": "longhorn-system"}
            for n in users
        ],
    }
    apply([role, binding])
    base = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "scope-probe",
            "namespace": "cel-reporting",
            "labels": {"kustomize.toolkit.fluxcd.io/name": "rehearsal"},
        },
    }
    rows = []
    absent = copy.deepcopy(base)
    absent["metadata"].pop("labels")
    rows.append(("no-label", absent, None, False))
    empty = copy.deepcopy(base)
    empty["metadata"]["labels"]["kustomize.toolkit.fluxcd.io/name"] = ""
    rows.append(("empty-label", empty, None, True))
    for ns in [
        "flux-system",
        "kube-node-lease",
        "kube-public",
        "kube-system",
        "kyverno",
    ]:
        d = copy.deepcopy(base)
        d["metadata"]["namespace"] = ns
        rows.append(("excluded-" + ns, d, None, False))
    sa = "system:serviceaccount:longhorn-system:longhorn-service-account"
    other = "system:serviceaccount:longhorn-system:adjacent-controller"
    for api, ns, user, expected, name in [
        ("v1beta2", "longhorn-system", sa, False, "exact"),
        ("v1beta2", "longhorn-system", None, True, "manual"),
        ("v1beta2", "longhorn-system", other, True, "other-user"),
        ("v1beta2", "cel-reporting", sa, True, "other-namespace"),
        ("v1beta1", "longhorn-system", sa, True, "other-version"),
    ]:
        d = copy.deepcopy(base)
        d.update(apiVersion="longhorn.io/" + api, kind="BackupTarget")
        d["metadata"]["namespace"] = ns
        rows.append(("longhorn-" + name, d, user, expected))
    d = copy.deepcopy(base)
    d["metadata"]["namespace"] = "longhorn-system"
    rows.append(("longhorn-other-kind", d, sa, True))
    for kind, api, extra in [
        ("Endpoints", "v1", {"subsets": []}),
        (
            "EndpointSlice",
            "discovery.k8s.io/v1",
            {"addressType": "IPv4", "endpoints": []},
        ),
        ("PolicyReport", "wgpolicyk8s.io/v1alpha2", {"results": []}),
        ("ClusterPolicyReport", "wgpolicyk8s.io/v1alpha2", {"results": []}),
    ]:
        d = copy.deepcopy(base)
        d.update(kind=kind, apiVersion=api, **extra)
        if kind == "ClusterPolicyReport":
            d["metadata"].pop("namespace")
        rows.append(("excluded-kind-" + kind, d, None, False))
    for name, doc, user, warning in rows:
        result = admission(doc, user=user)
        policy_warning = "Flux-managed resources should be changed" in result["stderr"]
        assert result["allowed"] and policy_warning == warning, (name, result)
        record(
            "flux-boundary",
            status="verified",
            version=version,
            engine=engine,
            case=name,
            policy_warning=policy_warning,
            **result
        )
    delete([binding, role])
