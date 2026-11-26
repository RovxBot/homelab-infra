#!/usr/bin/env python3
"""Offline, read-only correlation of reviewed GitOps image replacements.

Compare immutable base/head commits already present in the local checkout.
Outputs candidate metadata only, never reads scanner findings, credentials,
issue bodies or private Gitea. A candidate is not a verified vulnerability fix.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "candidate_contract", ROOT / "infra/observability/security-incident-publisher/publisher.py")
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
SHA = re.compile(r"[0-9a-f]{40}")
REF = re.compile(r"([a-z0-9][a-z0-9._:/-]{0,300})(?::([A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?@(sha256:[a-f0-9]{64})")


def canonical_repository(value):
    # Docker Hub's default registry/library names are equivalent; explicit
    # registry ports and non-Hub repositories must never be conflated.
    parts = value.split("/")
    if len(parts) == 1:
        return "docker.io/library/" + value
    if parts[0] in ("docker.io", "index.docker.io"):
        rest = parts[1:]
        return "docker.io/" + ("library/" if len(rest) == 1 else "") + "/".join(rest)
    if "." not in parts[0] and ":" not in parts[0] and parts[0] != "localhost":
        return "docker.io/" + value
    return value


def image_identity(value):
    if not isinstance(value, str) or not REF.fullmatch(value):
        return None
    named, digest = value.rsplit("@", 1)
    last = named.rsplit("/", 1)[-1]
    repository = named.rsplit(":", 1)[0] if ":" in last else named
    return canonical_repository(repository), digest


def declared_image(read, workload, container):
    path, names = P.CVE_SOURCES[workload]
    ns, kind, name = workload.split("/")
    if not path or container not in names or workload not in P.CVE_DEPENDENCIES:
        return None
    # Helm image values require separate pinned-chart rendering. Do not
    # pretend a chart version or tag change proves a replacement digest.
    if path.endswith("-helmrelease.yaml"):
        return None
    source = read(path)
    if source is None:
        return None
    docs = [d for d in yaml.safe_load_all(source) if isinstance(d, dict)
            and d.get("apiVersion") == "apps/v1" and d.get("kind") == kind
            and d.get("metadata", {}).get("name") == name]
    if len(docs) != 1:
        return None
    doc = docs[0]
    declared_ns = doc["metadata"].get("namespace")
    if declared_ns is None:
        parent = str(Path(path).parent / "kustomization.yaml")
        content = read(parent)
        if content is None:
            return None
        kustomization = yaml.safe_load(content)
        if (not isinstance(kustomization, dict) or kustomization.get("namespace") != ns
                or Path(path).name not in kustomization.get("resources", [])):
            return None
    elif declared_ns != ns:
        return None
    containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    images = [c.get("image") for c in containers if c.get("name") == container]
    if len(images) != 1:
        return None
    identity = image_identity(images[0])
    if identity is None or identity[0] != canonical_repository(P.CVE_DEPENDENCIES[workload]):
        return None
    return images[0]


def correlate(before, after):
    candidates, unsupported = [], []
    for workload, (path, containers) in sorted(P.CVE_SOURCES.items()):
        for container in containers:
            old, new = (declared_image(reader, workload, container) for reader in (before, after))
            if old is None or new is None:
                unsupported.append({"workload":workload,"container":container,"path":path,
                                    "reason":"immutable_source_not_qualified"})
                continue
            old_identity, new_identity = image_identity(old), image_identity(new)
            if old_identity[0] == new_identity[0] and old_identity[1] != new_identity[1]:
                candidates.append({"workload":workload,"container":container,"path":path,
                                   "dependency":old_identity[0],"before":old,"after":new,
                                   "state":"candidate_not_assessed"})
    return {"schema":1,"candidates":candidates,"unsupported":unsupported}


def git_reader(commit):
    if not SHA.fullmatch(commit):
        raise ValueError("full immutable commit SHA required")
    resolved = subprocess.run(["git","rev-parse","--verify",commit+"^{commit}"],
                              cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    if resolved != commit:
        raise ValueError("commit mismatch")
    def read(path):
        result = subprocess.run(["git","show",commit+":"+path],cwd=ROOT,capture_output=True)
        if result.returncode:
            return None
        if len(result.stdout)>1024*1024:
            raise ValueError("manifest size bound")
        return result.stdout.decode("utf-8")
    return read


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base",required=True)
    parser.add_argument("--head",required=True)
    args=parser.parse_args()
    result=correlate(git_reader(args.base),git_reader(args.head))
    result.update(base=args.base,head=args.head)
    print(json.dumps(result,sort_keys=True,indent=2))


if __name__=="__main__":
    main()
