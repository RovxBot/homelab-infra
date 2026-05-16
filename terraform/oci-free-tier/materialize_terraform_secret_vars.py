#!/usr/bin/env python3

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path


def decrypt_secret(secret_path: Path) -> dict:
    result = subprocess.run(
        ["sops", "-d", "--output-type", "json", str(secret_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def extract_wireguard_peer_config(secret_doc: dict) -> str:
    string_data = secret_doc.get("stringData") or {}
    if "wireguard_peer_config" in string_data:
        return string_data["wireguard_peer_config"]

    data = secret_doc.get("data") or {}
    if "wireguard_peer_config" in data:
        return base64.b64decode(data["wireguard_peer_config"]).decode("utf-8")

    raise KeyError("wireguard_peer_config not found in stringData or data")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize Terraform sensitive vars from a SOPS-encrypted YAML secret."
    )
    parser.add_argument("--input", required=True, help="Path to the SOPS-encrypted YAML file")
    parser.add_argument("--output", required=True, help="Path to write the Terraform tfvars JSON")
    args = parser.parse_args()

    secret_path = Path(args.input)
    output_path = Path(args.output)

    secret_doc = decrypt_secret(secret_path)
    terraform_vars = {
        "wireguard_peer_config": extract_wireguard_peer_config(secret_doc),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(terraform_vars, indent=2) + "\n", encoding="utf-8")
    output_path.chmod(0o600)
    print(f"Wrote Terraform secret vars to {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())