#!/usr/bin/env python3
"""Read-only, fail-closed empty-state gate. Raw inventories remain private."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone

LABEL = "homelab.cooked.beer/bootstrap"
OUTPUTS = {
    "limitranges": "default-container-limits",
    "resourcequotas": "namespace-defaults",
    "networkpolicies.networking.k8s.io": "default-deny",
}


def summarize(objects):
    enabled = [n for n in objects["namespaces"]["items"]
               if n["metadata"].get("labels", {}).get(LABEL) == "enabled"]
    candidates = []
    for kind, name in OUTPUTS.items():
        for obj in objects[kind]["items"]:
            meta = obj["metadata"]
            labels = meta.get("labels", {})
            # Include unlabelled namesakes and either legacy or CEL provenance.
            if (meta["name"] == name or labels.get(LABEL) == "enabled"
                    or any("platform-namespace-bootstrap" in str(v)
                           for k, v in labels.items() if "kyverno" in k)):
                candidates.append(obj)
    urs = objects["updaterequests.kyverno.io"]["items"]
    return {"status": "verified" if not (enabled or candidates or urs) else "failed",
            "namespace_count": len(objects["namespaces"]["items"]),
            "enabled_namespaces": len(enabled), "bootstrap_output_candidates": len(candidates),
            "update_requests": len(urs),
            "resource_counts": {k: len(v["items"]) for k, v in objects.items()},
            "requirement": "all three counts must be zero; repeat under proven admission freeze"}


def collect(context, folder):
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    objects, hashes = {}, {}
    for kind in ["namespaces", *OUTPUTS, "updaterequests.kyverno.io"]:
        result = subprocess.run(["kubectl", "--context", context, "--request-timeout=20s",
                                 "get", kind, "-A", "-o", "json"],
                                text=True, capture_output=True, timeout=30, check=True)
        obj = json.loads(result.stdout)
        if not isinstance(obj.get("items"), list) or obj.get("metadata", {}).get("continue"):
            raise RuntimeError("Incomplete inventory: " + kind)
        objects[kind] = obj
        path = folder / (kind + ".json")
        path.write_text(result.stdout)
        path.chmod(0o600)
        hashes[kind] = hashlib.sha256(result.stdout.encode()).hexdigest()
    summary = {**summarize(objects), "observed_at": datetime.now(timezone.utc).isoformat(),
               "raw_sha256": hashes, "read_only": True}
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    result = collect(args.context, args.output)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "verified" else 1)
