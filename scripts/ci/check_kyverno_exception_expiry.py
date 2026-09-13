#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml


ANNOTATION = "kyverno.cooked.beer/expires-at"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Require every tracked Kyverno PolicyException to have a valid, unexpired expiry date."
    )
    parser.add_argument(
        "--exceptions-dir",
        default="infra/kyverno/exceptions",
        help="Directory containing PolicyException manifests (default: %(default)s).",
    )
    parser.add_argument(
        "--today",
        help="Override today's date as YYYY-MM-DD for deterministic local validation.",
    )
    return parser.parse_args()


def parse_today(value: str | None) -> date:
    if value is None:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise SystemExit(f"--today must be an ISO date (YYYY-MM-DD): {value}") from error


def load_documents(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as handle:
            return [document for document in yaml.safe_load_all(handle) if isinstance(document, dict)]
    except yaml.YAMLError as error:
        raise SystemExit(f"Unable to parse {path}: {error}") from error


def main() -> int:
    args = parse_args()
    exceptions_dir = Path(args.exceptions_dir)
    today = parse_today(args.today)
    errors: list[str] = []
    expirations: list[tuple[date, str]] = []

    for path in sorted(exceptions_dir.glob("*.yaml")):
        for document in load_documents(path):
            if document.get("kind") != "PolicyException":
                continue

            metadata = document.get("metadata") or {}
            name = str(metadata.get("name") or path.name)
            annotations = metadata.get("annotations") or {}
            raw_expiry = annotations.get(ANNOTATION)
            label = f"{path}:{name}"

            if raw_expiry is None or not str(raw_expiry).strip():
                errors.append(f"{label} is missing required {ANNOTATION} annotation")
                continue

            try:
                expiry = date.fromisoformat(str(raw_expiry))
            except ValueError:
                errors.append(f"{label} has invalid {ANNOTATION} value: {raw_expiry!r}")
                continue

            if expiry < today:
                errors.append(f"{label} expired on {expiry.isoformat()} (today: {today.isoformat()})")
                continue

            expirations.append((expiry, label))

    if errors:
        print("Kyverno PolicyException expiry check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    if not expirations:
        print(f"No PolicyException manifests found under {exceptions_dir}.", file=sys.stderr)
        return 1

    next_expiry, next_label = min(expirations)
    print(
        f"Validated {len(expirations)} Kyverno PolicyException expiry annotation(s); "
        f"next expiry is {next_expiry.isoformat()} ({next_label})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
