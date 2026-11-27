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
    if doc.get("kind") == "Secret" and doc.get("apiVersion") == "v1":
        return refs  # Secret data is opaque, never a reference or diagnostic.
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


def production_refs_from_doc(doc: Dict, namespace: Optional[str]) -> Set[Tuple[Optional[str], str]]:
    """Known Secret reference shapes; explicit reference namespaces take priority.

    Ordinary local references use the resource's effective namespace. This does
    not discover additional chart-specific fields or controller-generated Secrets.
    The legacy bootstrap scanner and its fallback remain separate below.
    """
    refs = set()
    if doc.get("kind") == "Secret" and doc.get("apiVersion") == "v1":
        return refs
    for node in walk(doc):
        for field in ("secretKeyRef", "secretRef", "nodeStageSecretRef", "nodePublishSecretRef",
                      "controllerPublishSecretRef", "controllerExpandSecretRef", "nodeExpandSecretRef"):
            ref = node.get(field)
            if isinstance(ref, dict) and isinstance(ref.get("name"), str) and ref["name"]:
                refs.add((ref.get("namespace", namespace), ref["name"]))
        name = node.get("secretName")
        if isinstance(name, str) and name:
            refs.add((namespace, name))
        for ref in node.get("imagePullSecrets", []) or []:
            if isinstance(ref, dict) and isinstance(ref.get("name"), str) and ref["name"]:
                refs.add((namespace, ref["name"]))
    spec = doc.get("spec") or {}
    if doc.get("apiVersion", "").startswith("helm.toolkit.fluxcd.io/") and doc.get("kind") == "HelmRelease":
        for ref in spec.get("valuesFrom", []):
            if ref.get("kind", "Secret") == "Secret":
                refs.add((namespace, ref["name"]))
    if doc.get("apiVersion", "").startswith("kustomize.toolkit.fluxcd.io/") and doc.get("kind") == "Kustomization":
        for ref in spec.get("postBuild", {}).get("substituteFrom", []):
            if ref.get("kind") == "Secret":
                refs.add((namespace, ref["name"]))
    return refs


def main(path: str) -> int:
    declared: Set[Tuple[Optional[str], str]] = set()
    refs: Set[Tuple[Optional[str], str]] = set()

    from kustomize_inputs import resource_documents
    try:
        with open(path, encoding="utf-8") as handle:
            docs = resource_documents(handle.read(), "bootstrap render")
    except (OSError, ValueError):
        print("Invalid or missing bootstrap render (payload suppressed).")
        return 1

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
        # Legacy bootstrap-only fallback: an unnamespaced declaration matches
        # any reference namespace. Combined production validation never uses it.
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
