#!/usr/bin/env python3
"""Build/check the byte-exact existing client bundle mounted by recurring Jobs."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "infra/kubescape/recurring/client-bundle.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    bundle = {name: (ROOT / "scripts" / name).read_text() for name in ("grype_db.py", "kubescape_db.py")}
    expected = json.dumps(bundle, indent=2, sort_keys=True) + "\n"
    if args.write:
        OUTPUT.write_text(expected)
    else:
        assert OUTPUT.read_text() == expected, "Run python3 scripts/kubescape_recurring_bundle.py --write"
    print("verified: recurring Jobs use the exact supported database client")


if __name__ == "__main__":
    main()
