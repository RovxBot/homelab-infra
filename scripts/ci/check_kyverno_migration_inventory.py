#!/usr/bin/env python3
"""Check migration inventory coverage; this does not qualify policy behavior."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = Path("docs/kyverno/migration-inventory.json")
LEGACY_KINDS = {"ClusterPolicy", "Policy", "PolicyException", "CleanupPolicy", "ClusterCleanupPolicy"}
TARGET_KINDS = {
    "validate": {"ValidatingPolicy", "NamespacedValidatingPolicy"},
    "mutate": {"MutatingPolicy", "NamespacedMutatingPolicy"},
    "generate": {"GeneratingPolicy", "NamespacedGeneratingPolicy"},
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def identity(obj):
    meta = obj["metadata"]
    return obj["kind"], meta.get("namespace", ""), meta["name"]


def resource_contract(obj):
    meta = obj["metadata"]
    return {"spec": obj["spec"], "labels": meta.get("labels", {}), "annotations": meta.get("annotations", {})}


def discover(root):
    found = {}
    for directory in ("apps", "infra", "clusters"):
        for path in sorted((root / directory).rglob("*")):
            if path.suffix not in {".yaml", ".yml"}:
                continue
            raw = path.read_text()
            # App configuration can contain custom tags (e.g. Home Assistant
            # !include). BaseLoader identifies resources without executing tags;
            # only actual legacy manifests need typed SafeLoader construction.
            headers = list(yaml.load_all(raw, Loader=yaml.BaseLoader))
            if not any(isinstance(obj, dict) and str(obj.get("apiVersion", "")).startswith("kyverno.io/")
                       and obj.get("kind") in LEGACY_KINDS for obj in headers):
                continue
            for obj in yaml.safe_load_all(raw):
                if not isinstance(obj, dict) or not str(obj.get("apiVersion", "")).startswith("kyverno.io/"):
                    continue
                if obj.get("kind") not in LEGACY_KINDS:
                    continue
                key = identity(obj)
                if key in found:
                    raise ValueError(f"Duplicate legacy resource: {key}")
                found[key] = (path.relative_to(root).as_posix(), obj)
    return found


def check(root, inventory):
    found = discover(root)
    errors = []
    indexed = {}
    targets = {}
    covered_targets = {}
    for entry in inventory["policies"] + inventory["exceptions"]:
        key = entry["kind"], entry.get("namespace", ""), entry["name"]
        if key in indexed:
            errors.append(f"Duplicate inventory entry: {key}")
        indexed[key] = entry
    for key in sorted(found.keys() | indexed.keys()):
        if key not in indexed:
            errors.append(f"Unmapped legacy resource: {key}")
            continue
        if key not in found:
            errors.append(f"Inventoried resource missing: {key}")
            continue
        path, obj = found[key]
        entry = indexed[key]
        if path != entry["path"] or obj["apiVersion"] != entry["apiVersion"]:
            errors.append(f"Source identity changed: {key}")
        contract = resource_contract(obj)
        if fingerprint(contract) != entry["contract_sha256"]:
            errors.append(f"Source contract changed; review inventory: {key}")
        if key[0] == "PolicyException":
            continue
        actual_rules = {rule["name"]: rule for rule in obj["spec"].get("rules", [])}
        mapped_rules = {rule["name"]: rule for rule in entry["rules"]}
        if len(mapped_rules) != len(entry["rules"]) or actual_rules.keys() != mapped_rules.keys():
            errors.append(f"Rule coverage differs: {key}")
        for name in actual_rules.keys() & mapped_rules.keys():
            rule, mapped = actual_rules[name], mapped_rules[name]
            action = mapped["action"]
            target = mapped["target"]
            if action not in rule or target["kind"] not in TARGET_KINDS.get(action, set()):
                errors.append(f"Wrong target action/type: {key}/{name}")
            if (mapped["enforcement"] != rule.get("validate", {}).get("failureAction", "not applicable")
                    or mapped["background"] != obj["spec"].get("background", True)):
                errors.append(f"Mapped enforcement/background differs: {key}/{name}")
            if target.get("apiVersion") != "policies.kyverno.io/v1" or "*" in target.get("name", ""):
                errors.append(f"Invalid target reference: {key}/{name}")
            if target.get("namespace", "") != key[1] or target["kind"].startswith("Namespaced") != bool(key[1]):
                errors.append(f"Target scope changed: {key}/{name}")
            if not mapped.get("test_cases"):
                errors.append(f"Missing planned tests: {key}/{name}")
            targets[(key[1], key[2], name)] = (target["kind"], target["name"])
            covered_targets.setdefault((target["kind"], target.get("namespace", ""), target["name"]), set()).add((key[1], key[2], name))
    for entry in inventory["exceptions"]:
        key = entry["kind"], entry.get("namespace", ""), entry["name"]
        if key not in found:
            continue
        obj = found[key][1]
        expected = set()
        legacy_scope = {("", ref["policyName"], rule)
                        for ref in obj["spec"]["exceptions"] for rule in ref["ruleNames"]}
        if (entry["legacy_policy_refs"] != obj["spec"]["exceptions"] or entry["match"] != obj["spec"]["match"]
                or entry["governance"] != {"labels": obj["metadata"].get("labels", {}), "annotations": obj["metadata"].get("annotations", {})}):
            errors.append(f"Exception snapshot differs: {key}")
        for ref in obj["spec"]["exceptions"]:
            for rule in ref["ruleNames"]:
                target = targets.get(("", ref["policyName"], rule))
                if target is None:
                    errors.append(f"Exception references unmapped rule: {key}/{rule}")
                else:
                    expected.add(target)
                    if not covered_targets[(target[0], "", target[1])].issubset(legacy_scope):
                        errors.append(f"Exception would exempt additional rules: {key}/{rule}")
        actual = {(ref["kind"], ref["name"]) for ref in entry["target_policy_refs"]}
        if actual != expected or len(actual) != len(entry["target_policy_refs"]):
            errors.append(f"Exception target scope differs: {key}")
        if not entry.get("test_cases"):
            errors.append(f"Missing exception tests: {key}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    inventory = json.loads((root / INVENTORY).read_text())
    errors = check(root, inventory)
    if errors:
        parser.exit(1, "\n".join(errors) + "\n")
    print(f"verified inventory coverage: {len(inventory['policies'])} policies, "
          f"{sum(len(p['rules']) for p in inventory['policies'])} rules, {len(inventory['exceptions'])} exceptions")
    print("Preflight only: policy behavior, ownership transfer and rollout are NOT qualified.")


if __name__ == "__main__":
    main()
