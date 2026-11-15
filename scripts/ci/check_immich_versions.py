#!/usr/bin/env python3
"""Reject partial Immich release updates; image digests remain image-specific."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def check_versions(root: Path) -> str:
    versions = {}
    for component, filename in [("server", "server.yaml"), ("machine-learning", "machine-learning.yaml")]:
        content = (root / "apps/immich" / filename).read_text()
        matches = re.findall(
            rf"^\s*image:\s*ghcr\.io/immich-app/immich-{component}:"
            r"(v\d+\.\d+\.\d+)@sha256:[a-f0-9]{64}\s*$",
            content,
            flags=re.MULTILINE,
        )
        if len(matches) != 1:
            raise ValueError(f"Immich {component} must have one stable version and SHA256 digest")
        versions[component] = matches[0]
    if len(set(versions.values())) != 1:
        raise ValueError(f"Immich application release mismatch: {versions}")
    return versions["server"]


if __name__ == "__main__":
    print(f"Immich server and machine-learning use {check_versions(ROOT)}")
