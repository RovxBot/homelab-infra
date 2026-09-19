#!/usr/bin/env python3
"""Verify that a SoloCollections AddOn and embedded module share SC2 hashes."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


TYPE_KEY_BY_ID = {
    10: "mount",
    11: "companion",
    12: "toy",
    13: "appearance",
    14: "set",
    16: "mount",
    17: "companion",
}
LUA_HASH = re.compile(r'^\s*(?:([a-z]+)|\["([a-z-]+)"\])\s*=\s*"([0-9a-f]{64})",?\s*$')
CPP_HASH = re.compile(
    r'CollectionTypeId\(std::uint16_t\s*\{\s*(\d+)\s*\}\),\s*"([0-9a-f]{64})"'
)


def client_hashes(path: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    in_hashes = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if "typeMappingHashes = {" in line:
            in_hashes = True
            continue
        if in_hashes and line.strip() == "}":
            break
        if in_hashes:
            match = LUA_HASH.match(line)
            if match:
                hashes[match.group(1) or match.group(2)] = match.group(3)
    if not hashes:
        raise ValueError(f"no typeMappingHashes table found in {path}")
    return hashes


def module_hashes(path: Path) -> dict[int, str]:
    hashes = {int(type_id): digest for type_id, digest in CPP_HASH.findall(path.read_text(encoding="utf-8"))}
    if not hashes:
        raise ValueError(f"no SC2 category hashes found in {path}")
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("addon_catalog", type=Path)
    parser.add_argument("module_catalog", type=Path)
    args = parser.parse_args()

    client = client_hashes(args.addon_catalog)
    module = module_hashes(args.module_catalog)
    mismatches: list[str] = []
    for type_id, key in TYPE_KEY_BY_ID.items():
        expected = client.get(key)
        actual = module.get(type_id)
        if expected != actual:
            mismatches.append(
                f"type={type_id} key={key} client={expected or 'MISSING'} module={actual or 'MISSING'}"
            )
    if mismatches:
        raise SystemExit("SoloCollections SC2 catalog mismatch:\n" + "\n".join(mismatches))
    print("SoloCollections SC2 catalog hashes match for types 10,11,12,13,14,16,17.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
