#!/usr/bin/env python3
"""Update the registration image tag and digest together, or leave the file alone."""
import argparse
from pathlib import Path
import re


IMAGE = re.compile(
    r"(?m)^(?P<prefix>[ \t]*image:[ \t]+)"
    r"(?P<repository>ghcr\.io/[^/\s]+/grimguzzler-registration)"
    r"(?:[:@][^ \t\r\n#]+)?(?P<suffix>[ \t]*(?:#[^\r\n]*)?)$"
)


def update_image(manifest: str, tag: str, digest: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        raise ValueError("Invalid image tag")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Missing or invalid build digest")
    if len(list(IMAGE.finditer(manifest))) != 1:
        raise ValueError("Expected exactly one registration image")
    return IMAGE.sub(
        lambda match: f"{match['prefix']}{match['repository']}:{tag}@{digest}{match['suffix']}",
        manifest,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--digest", required=True)
    args = parser.parse_args()
    updated = update_image(args.manifest.read_text(), args.tag, args.digest)
    args.manifest.write_text(updated)


if __name__ == "__main__":
    main()
