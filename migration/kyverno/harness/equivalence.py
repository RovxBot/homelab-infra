"""Compare each authored validation rule with real Kubernetes admission.

One unchanged legacy rule is selected at a time to prevent another rule from
masking a missing control. Exceptions retain their original complete scopes.
"""

import copy
import json
import time
from corpus import cases
from rehearse import (
    ROOT,
    EVIDENCE,
    WORK,
    OWNER,
    apply,
    delete,
    load,
    ready,
    admission,
    get,
    record,
    k,
)


def suite(version, engine):
    index = json.loads((ROOT / "migration/kyverno/policy-map.json").read_text())
    draft = []
    for path in ["namespace.yaml", "workloads.yaml", "runners.yaml"]:
        draft += load("migration/kyverno/policies/" + path)
    inventory = json.loads((ROOT / "docs/kyverno/migration-inventory.json").read_text())
    exceptions = (
        load("migration/kyverno/exceptions/exceptions.yaml")
        if engine == "cel"
        else [load(e["path"])[0] for e in inventory["exceptions"]]
    )
    for ex in exceptions:
        apply(ex)
    time.sleep(2)
    output = {}
    for item in index:
        if item["source_policy"] == "platform-flux-guardrails":
            continue
        key = item["source_policy"] + "/" + item["source_rule"]
        original = next(
            p for p in inventory["policies"] if p["name"] == item["source_policy"]
        )
        old = load(original["path"])[0]
        old["spec"]["rules"] = [
            r for r in old["spec"]["rules"] if r["name"] == item["source_rule"]
        ]
        policy = (
            old
            if engine == "legacy"
            else next(p for p in draft if p["metadata"]["name"] == item["name"])
        )
        rows = cases(item["source_policy"], item["source_rule"])
        # Store two objects before activating this rule. nodeName prevents every
        # runner Pod from scheduling or attempting a production registry pull.
        updates = []
        for state, raw in [("valid", rows[0][1]), ("invalid", rows[1][1])]:
            d = copy.deepcopy(raw)
            d["metadata"]["name"] = "existing-" + state
            if d["kind"] == "Pod":
                d["spec"]["nodeName"] = "rehearsal-unscheduled"
            if d["kind"] == "Namespace":
                d["metadata"]["name"] = "existing-" + state + "-ns"
            # A native-invalid fixture is not usable as an oldObject.
            p = k(
                "create", "-f", "-", data=__import__("yaml").safe_dump(d), check=False
            )
            if p.returncode:
                if "already exists" not in p.stderr:
                    continue
            resource = {
                "Pod": "pods",
                "Namespace": "namespaces",
                "Deployment": "deployments",
                "StatefulSet": "statefulsets",
                "DaemonSet": "daemonsets",
                "Job": "jobs",
                "CronJob": "cronjobs",
            }[d["kind"]]
            stored = get(
                resource, d["metadata"]["name"], d["metadata"].get("namespace", "")
            )
            mutated = copy.deepcopy(stored)
            mutated["metadata"].setdefault("annotations", {})[
                "rehearsal.cooked.beer/update"
            ] = "true"
            updates.append(("existing-" + state, mutated))
        successful = False
        try:
            apply(policy)
            ready(policy)
            # Ready is controller state; a known denial probe below is still
            # required before these decisions count as policy equivalence.
            time.sleep(1)
            for name, doc in rows:
                # System Namespace fixtures already exist; use real UPDATE.
                operation = "CREATE"
                if doc["kind"] == "Namespace" and doc["metadata"]["name"] in [
                    "default",
                    "kube-node-lease",
                    "kube-public",
                    "kube-system",
                    "sops",
                ]:
                    try:
                        stored = get("namespaces", doc["metadata"]["name"])
                        stored["metadata"]["labels"] = doc["metadata"].get("labels", {})
                        doc = stored
                        operation = "UPDATE"
                    except RuntimeError:
                        pass
                result = admission(doc, operation, check_policy=False)
                output[key + "::" + name] = result
            for name, doc in updates:
                output[key + "::UPDATE-" + name] = admission(
                    doc, "UPDATE", check_policy=False
                )
            successful = True
        except Exception as e:
            record(
                "rule-admission",
                status="failed",
                version=version,
                engine=engine,
                rule=key,
                error=str(e),
            )
        finally:
            delete(policy)
            for _, doc in updates:
                delete(doc)
        path = EVIDENCE / f"admission-{version}-{engine}.json"
        path.write_text(json.dumps(output, indent=2) + "\n")
        record(
            "rule-admission",
            status="verified" if successful else "failed",
            version=version,
            engine=engine,
            rule=key,
            cases=len(rows) + len(updates),
        )
    for ex in exceptions:
        delete(ex)
    path = EVIDENCE / f"admission-{version}-{engine}.json"
    path.write_text(json.dumps(output, indent=2) + "\n")
    if engine == "cel":
        baseline = json.loads((EVIDENCE / "admission-1.19.2-legacy.json").read_text())
        mismatches = [
            dict(case=k, reason="CEL decision unavailable")
            for k in baseline.keys() - output.keys()
        ]
        for key, new in output.items():
            if key not in baseline:
                mismatches.append(dict(case=key, reason="baseline unavailable"))
                continue
            old = baseline[key]
            if (old["allowed"], old["warning"]) != (new["allowed"], new["warning"]):
                mismatches.append(dict(case=key, legacy=old, cel=new))
        (EVIDENCE / f"differences-{version}.json").write_text(
            json.dumps(mismatches, indent=2) + "\n"
        )
        record(
            "validation-equivalence",
            status="failed" if mismatches else "verified",
            version=version,
            cases=len(output),
            differences=len(mismatches),
        )
    return output


def whole_runners(version, engine):
    """Re-use both existing runner acceptance suites with every rule active."""
    from rehearse import run

    for pool, path, namespace in [
        ("core", "apps/gitea-runners/job-security.yaml", "gitea-jobs"),
        (
            "wowdecomp",
            "apps/gitea-runners/wowdecomp/job-security.yaml",
            "wowdecomp-ci-jobs",
        ),
    ]:
        policies = (
            load(path)
            if engine == "legacy"
            else [
                p
                for p in load("migration/kyverno/policies/runners.yaml")
                if p["metadata"]["namespace"] == namespace
            ]
        )
        try:
            for p in policies:
                apply(p)
                ready(p)
            time.sleep(2)
            result = run(
                [
                    "python3",
                    "ops/gitea/check-job-policy.py",
                    "--server",
                    "--pool",
                    pool,
                ],
                check=False,
                timeout=120,
            )
            (EVIDENCE / f"whole-runner-{version}-{engine}-{pool}.log").write_text(
                result.stdout + result.stderr
            )
            record(
                "whole-runner-admission",
                status="verified" if result.returncode == 0 else "failed",
                version=version,
                engine=engine,
                pool=pool,
                cases=len(result.stdout.splitlines()),
            )
        finally:
            delete(policies)
