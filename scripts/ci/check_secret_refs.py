#!/usr/bin/env python3
import sys
from typing import Dict, Generator, Iterable, Optional, Set, Tuple

import yaml

# Manually provisioned or bootstrap secrets that are expected to be missing from render output.
ALLOWLIST_NAMES = {
    "flux-system",
    "sops-age",
    "wireguard-client-conf",
}


def walk(node: object) -> Generator[Dict, None, None]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def obj_namespace(doc: Dict) -> Optional[str]:
    metadata = doc.get("metadata") or {}
    ns = metadata.get("namespace")
    return ns if isinstance(ns, str) and ns else None


def refs_from_doc(doc: Dict, default_ns: Optional[str]) -> Set[Tuple[Optional[str], str]]:
    refs: Set[Tuple[Optional[str], str]] = set()
    for node in walk(doc):
        secret_key_ref = node.get("secretKeyRef")
        if isinstance(secret_key_ref, dict):
            name = secret_key_ref.get("name")
            if isinstance(name, str) and name:
                refs.add((default_ns, name))

        secret_ref = node.get("secretRef")
        if isinstance(secret_ref, dict):
            name = secret_ref.get("name")
            if isinstance(name, str) and name:
                refs.add((default_ns, name))

        secret_name = node.get("secretName")
        if isinstance(secret_name, str) and secret_name:
            refs.add((default_ns, secret_name))
    return refs


def main(path: str) -> int:
    declared: Set[Tuple[Optional[str], str]] = set()
    refs: Set[Tuple[Optional[str], str]] = set()

    with open(path, "r", encoding="utf-8") as f:
        docs = list(yaml.safe_load_all(f))

    for doc in docs:
        if not isinstance(doc, dict):
            continue
        namespace = obj_namespace(doc)

        if doc.get("kind") == "Secret":
            metadata = doc.get("metadata") or {}
            name = metadata.get("name")
            if isinstance(name, str) and name:
                declared.add((namespace, name))

        refs |= refs_from_doc(doc, namespace)

    missing = []
    for namespace, name in sorted(refs, key=lambda x: ((x[0] or ""), x[1])):
        if name in ALLOWLIST_NAMES:
            continue
        if (namespace, name) in declared:
            continue
        # Fallback for cluster-scoped docs that reference namespaced secrets.
        if (None, name) in declared:
            continue
        missing.append((namespace, name))

    if missing:
        print("Unresolved secret refs:")
        for namespace, name in missing:
            ns = namespace or "<cluster-scope>"
            print(f"- {ns}/{name}")
        return 1

    print("All secret references resolved (after allowlist).")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: check_secret_refs.py <rendered.yaml>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
