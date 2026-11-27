#!/usr/bin/env python3
"""Repository Kustomize inputs and owner-preserving production discovery.

Renders stay in memory for this invocation only; diagnostics never include YAML.
Retained roots and negative fixtures are rendered, never added to production.
"""
from pathlib import Path
import subprocess

import yaml

FILENAMES = {"kustomization.yaml", "kustomization.yml", "Kustomization"}
# Required retained inputs, not a fixed count of roots. Removing these requires
# an explicit coverage-contract review; ordinary additions need no quota change.
REQUIRED_ROOTS = {
    "clusters/home", "secrets", "ops/security-collector/acceptance",
    "ops/security-collector/acceptance/base", "ops/security-collector/acceptance/trigger",
}
FLUX_API = "kustomize.toolkit.fluxcd.io/v1"


class InputError(ValueError):
    """A safe, payload-free validation diagnostic suitable for CI logs."""


class ResourceLoader(yaml.SafeLoader):
    """Duplicate YAML keys are invalid input, never a last-document-wins merge."""
    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        try:
            if len(keys) != len(set(keys)):
                raise InputError("Duplicate YAML keys (payload suppressed)")
        except TypeError:
            raise InputError("Invalid YAML mapping keys (payload suppressed)") from None
        return super().construct_mapping(node, deep=deep)


def resource_documents(raw, context):
    try:
        docs = list(yaml.load_all(raw, Loader=ResourceLoader))
    except (yaml.YAMLError, ValueError):
        raise InputError(f"{context}: invalid YAML (payload suppressed)") from None
    # Empty separators are YAML syntax, but empty streams and non-resources fail.
    docs = [doc for doc in docs if doc is not None]
    if not docs:
        raise InputError(f"{context}: no resource documents")
    for doc in docs:
        if (not isinstance(doc, dict)
                or not isinstance(doc.get("apiVersion"), str) or not doc["apiVersion"]
                or not isinstance(doc.get("kind"), str) or not doc["kind"]
                or not isinstance(doc.get("metadata"), dict)
                or not isinstance(doc["metadata"].get("name"), str) or not doc["metadata"]["name"]
                or ("namespace" in doc["metadata"] and
                    (not isinstance(doc["metadata"]["namespace"], str) or not doc["metadata"]["namespace"]))):
            raise InputError(f"{context}: invalid resource document (payload suppressed)")
    return docs


def discover_roots(root):
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, capture_output=True, check=True,
    )
    roots = {}
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = Path(raw.decode("utf-8"))
        if path.name not in FILENAMES:
            continue
        if not (root/path).is_file() or not (root/path).resolve().is_relative_to(root.resolve()):
            raise InputError(f"Missing or external Kustomize input: {path}")
        directory = path.parent.as_posix()
        if directory in roots and roots[directory] != path:
            raise InputError(f"Multiple Kustomize filenames in {directory}")
        roots[directory] = path
    missing = REQUIRED_ROOTS - roots.keys()
    if missing or not roots:
        raise InputError("Missing retained Kustomize roots: " + ", ".join(sorted(missing)))
    return sorted(roots)


def render_roots(root):
    from kyverno_gate import render_kustomization
    return {path: render_kustomization(root, path) for path in discover_roots(root)}


def production_entries(root, rendered=None):
    """Follow rendered Flux declarations recursively, retaining every occurrence.

    Bootstrap's self-owning clusters/home reconciliation is included. Isolated
    fixtures are excluded by reachability, regardless of the source-root list.
    A supplied per-invocation render map must cover every reachable owner path.
    """
    from kyverno_gate import render_kustomization
    local = {} if rendered is None else dict(rendered)

    def render(path):
        if path not in local:
            if rendered is not None:
                raise InputError(f"Production root absent from discovery: {path}")
            local[path] = render_kustomization(root, path)
        return local[path]

    queue = list(render("clusters/home"))
    definitions = {}
    entries = []
    for doc in queue:
        if doc.get("kind") != "Kustomization" or not doc.get("apiVersion", "").startswith("kustomize.toolkit.fluxcd.io/"):
            continue
        if doc["apiVersion"] != FLUX_API:
            raise InputError("Unsupported Flux Kustomization API version")
        meta = doc["metadata"]
        key = (meta.get("namespace", "default"), meta["name"])
        if key in definitions:
            if definitions[key] != doc:
                raise InputError(f"Conflicting Flux Kustomization definitions: {key}")
            continue  # declarations recur through the bootstrap self-reference
        definitions[key] = doc
        spec = doc.get("spec") or {}
        path = spec.get("path")
        if (not isinstance(path, str) or not path.startswith("./")
                or ".." in Path(path).parts or Path(path).is_absolute()):
            raise InputError(f"Invalid Flux source path for {key}")
        path = Path(path).as_posix()
        source = spec.get("sourceRef", {})
        if (source.get("kind") != "GitRepository" or source.get("name") != "flux-system"
                or source.get("namespace", key[0]) != "flux-system"):
            raise InputError(f"Unqualified Flux source for {key}")
        # Source renders are equivalent today. New controller transformations or
        # remote targets require qualification rather than an optimistic render.
        transforms = ("postBuild", "patches", "images", "components", "namePrefix",
                      "nameSuffix", "kubeConfig")
        if any(spec.get(field) for field in transforms):
            raise InputError(f"Unqualified Flux render transformations for {key}")
        docs = render(path)
        entries.append((doc, docs))
        queue.extend(docs)
    if not entries:
        raise InputError("No production Flux Kustomizations discovered")
    known = set(definitions)
    for ks, _ in entries:
        ns = ks["metadata"].get("namespace", "default")
        for dep in ks["spec"].get("dependsOn", []):
            if (dep.get("namespace", ns), dep["name"]) not in known:
                raise InputError(f"Missing Flux dependency for {ns}/{ks['metadata']['name']}")
    return entries
