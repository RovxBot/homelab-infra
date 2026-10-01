#!/usr/bin/env python3
"""Select PR checks from the diff; scheduled and manual runs check everything."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess


TERRAFORM_STACKS = ("oci-free-tier", "oci-matrix-free-tier")
FULL_CHECK_PATHS = {
    ".github/workflows/ci.yml",
    "scripts/ci/detect_changes.py",
    "scripts/ci/tests/test_detect_changes.py",
}
MANIFEST_CHECK_PATHS = {
    ".github/kyverno-baseline.yaml",
    "scripts/ci/check_flux_bootstrap_digests.py",
    "scripts/ci/check_secret_refs.py",
    "scripts/ci/kyverno_gate.py",
}
EDGE_ROOT = "terraform/oci-free-tier/ops/wireguard/"


def classify_changes(paths: list[str], *, full: bool = False) -> dict[str, str]:
    full = full or any(path in FULL_CHECK_PATHS for path in paths)
    flags = {
        "manifests": full or any(
            path.startswith(("apps/", "infra/", "clusters/", "secrets/"))
            or path in MANIFEST_CHECK_PATHS
            for path in paths
        ),
        "public_edge": full or f"{EDGE_ROOT}Caddyfile" in paths,
        "shell": full or any(
            path.endswith(".sh") and path.startswith(("scripts/", EDGE_ROOT))
            for path in paths
        ),
        "workflows": full or any(
            path.startswith(".github/workflows/") or path == ".yamllint.yml"
            for path in paths
        ),
        "renovate": full or "renovate.json" in paths,
    }
    stacks = [
        stack for stack in TERRAFORM_STACKS
        if full or any(
            path.startswith(f"terraform/{stack}/")
            and path.endswith((".tf", ".tf.json", ".hcl", ".tftpl"))
            for path in paths
        )
    ]
    return {
        **{name: str(value).lower() for name, value in flags.items()},
        "terraform_matrix": json.dumps({"stack": stacks}, separators=(",", ":")),
        "terraform": str(bool(stacks)).lower(),
    }


def changed_paths(base: str, head: str) -> list[str]:
    # Compare the actual PR commits, not the synthetic merge commit checked out
    # by Actions. Base-only changes must not wake unrelated checks. Disable
    # rename detection so both the removed and added paths select their checks.
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}", "--"],
        check=True,
        capture_output=True,
    )
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    args = parser.parse_args()
    if args.event == "pull_request":
        if not args.base or not args.head:
            parser.error("pull_request requires --base and --head")
        paths = changed_paths(args.base, args.head)
        full = False
    elif args.event in {"schedule", "workflow_dispatch"}:
        paths = []
        full = True
    else:
        parser.error(f"unsupported event: {args.event}")

    outputs = classify_changes(paths, full=full)
    print(json.dumps({"changed_paths": paths, "checks": outputs}, indent=2))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
        for name, value in outputs.items():
            handle.write(f"{name}={value}\n")


if __name__ == "__main__":
    main()
