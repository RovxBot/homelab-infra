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


def policy_results(namespace, policy):
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
    bad["spec"]["template"]["metadata"]["labels"] = {}
    apply(bad)
    apply(owner)
    ready(owner)
    try:
        wait(
            "background violation report",
            lambda: any(
                r.get("result") == "fail"
                for r in policy_results(ns, owner["metadata"]["name"])
            ),
            75,
        )
        record(
            "background-report",
            status="verified",
            version=version,
            engine=engine,
            results=policy_results(ns, owner["metadata"]["name"]),
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
        obj["spec"]["template"]["metadata"]["labels"] = {}
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
                for r in policy_results(ns, flux["metadata"]["name"])
            ),
            75,
        )
        record(
            "flux-audit-report",
            status="verified",
            version=version,
            engine=engine,
            results=policy_results(ns, flux["metadata"]["name"]),
        )
        delete(cm)
    finally:
        delete(flux)
