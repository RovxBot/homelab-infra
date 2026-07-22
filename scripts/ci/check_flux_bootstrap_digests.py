#!/usr/bin/env python3
"""Reject unpinned Flux controller images in the generated bootstrap manifest."""

from __future__ import annotations

import re
import sys
from pathlib import Path


FLUX_IMAGE = re.compile(r"^\s*image:\s*(?P<image>ghcr\.io/fluxcd/[^\s#]+)(?:\s+#.*)?$")
SHA256_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def main(path: Path) -> int:
    invalid: list[tuple[int, str]] = []
    image_count = 0

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = FLUX_IMAGE.match(line)
        if match is None:
            continue

        image_count += 1
        image = match.group("image")
        if SHA256_DIGEST.search(image) is None:
            invalid.append((line_number, image))

    if image_count == 0:
        print(f"No ghcr.io/fluxcd image references found in {path}.")
        return 1

    if invalid:
        print("Flux bootstrap controller images must use a lowercase 64-character SHA-256 digest:")
        for line_number, image in invalid:
            print(f"- {path}:{line_number}: {image}")
        return 1

    print(f"All {image_count} Flux bootstrap controller images are digest-pinned.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: check_flux_bootstrap_digests.py <gotk-components.yaml>")
        raise SystemExit(2)

    raise SystemExit(main(Path(sys.argv[1])))
