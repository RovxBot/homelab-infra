#!/usr/bin/env python3
"""Offline, read-only correlation of reviewed GitOps image replacements.

Compare immutable base/head commits already present in the local checkout.
Outputs candidate metadata only, never reads scanner findings, credentials,
issue bodies or private Gitea. A candidate is not a verified vulnerability fix.
"""
import argparse
from datetime import datetime, timezone
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
canonical_repository = P.canonical_repository
image_identity = P.candidate_image_identity


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


REPOSITORY = "RovxBot/homelab-infra"
MAX_OPEN_PRS = 50


def live_candidates(pulls, base, fetch_head, ancestor, reader=git_reader):
    """Only same-repository, ready, current-base PRs; never execute head code."""
    if not SHA.fullmatch(base) or not isinstance(pulls, list) or len(pulls) > MAX_OPEN_PRS:
        raise ValueError("PR inventory bound or base invalid")
    results, skipped, seen = [], {}, set()
    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1
    for pr in pulls:
        number = pr["number"]
        if type(number) is not int or not 0 < number < 10**9 or number in seen:
            raise ValueError("invalid PR identity")
        seen.add(number)
        if pr.get("state") != "open" or pr.get("draft") is not False:
            skip("not_ready")
            continue
        before, after = pr["base"], pr["head"]
        if (before.get("ref") != "main" or before.get("sha") != base
                or (before.get("repo") or {}).get("full_name") != REPOSITORY
                or (after.get("repo") or {}).get("full_name") != REPOSITORY):
            skip("unsupported_base_or_repository")
            continue
        head = after.get("sha")
        if not isinstance(head, str) or not SHA.fullmatch(head):
            raise ValueError("invalid PR head")
        if fetch_head(number) != head:
            skip("head_changed")
            continue
        if not ancestor(base, head):
            skip("branch_behind_main")
            continue
        candidate = correlate(reader(base), reader(head))
        results.extend(dict(item, pr=number, base=base, head=head)
                       for item in candidate["candidates"])
    return {"schema": 1, "repository": REPOSITORY, "base": base,
            "state": "observed", "openPrCount": len(pulls),
            "skipped": skipped, "candidates": results,
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def discover_live():
    def run(args):
        return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                              check=True, timeout=90).stdout.strip()
    base = run(["git", "rev-parse", "HEAD"])
    # Trusted checkout, no untrusted title/body, no head checkout or execution.
    pulls = json.loads(run(["gh", "api", "--method", "GET",
                           "repos/" + REPOSITORY + "/pulls",
                           "-f", "state=open", "-f", "per_page=51"]))
    def fetch(number):
        run(["git", "fetch", "--no-tags", "--no-recurse-submodules",
             "https://github.com/" + REPOSITORY + ".git", "refs/pull/" + str(number) + "/head"])
        return run(["git", "rev-parse", "FETCH_HEAD"])
    def ancestor(before, after):
        result = subprocess.run(["git", "merge-base", "--is-ancestor", before, after],
                                cwd=ROOT, capture_output=True, timeout=30)
        if result.returncode not in (0, 1):
            raise ValueError("ancestry unavailable")
        return result.returncode == 0
    return live_candidates(pulls, base, fetch, ancestor)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--live", action="store_true")
    args=parser.parse_args()
    if args.live:
        if args.base or args.head:
            parser.error("--live cannot be combined with explicit commits")
        result = discover_live()
    else:
        if not args.base or not args.head:
            parser.error("--base and --head are required")
        result=correlate(git_reader(args.base),git_reader(args.head))
        result.update(base=args.base,head=args.head)
    print(json.dumps(result,sort_keys=True,indent=2))


if __name__=="__main__":
    main()
