#!/usr/bin/env bash
# Read-only guardrail for a single Longhorn replica relocation.
#
# It never patches a Longhorn resource. When volume, source, and target are
# supplied it validates whether the documented controller-managed eviction can
# be started safely; it does not start it.

set -euo pipefail

if ! command -v kubectl >/dev/null 2>&1; then
  printf '%s\n' 'kubectl is required.' >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'python3 is required.' >&2
  exit 1
fi

exec python3 - "$@" <<'PY'
import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Longhorn health and single-replica-relocation preflight. "
            "No cluster resource is changed."
        )
    )
    parser.add_argument("--volume", help="Longhorn Volume CR name")
    parser.add_argument("--source", help="Current replica node")
    parser.add_argument("--target", help="Requested destination node")
    args = parser.parse_args()
    chosen = (args.volume, args.source, args.target)
    if any(chosen) and not all(chosen):
        parser.error("--volume, --source, and --target must be supplied together")
    return args


def kubectl_json():
    command = [
        "kubectl",
        "-n",
        "longhorn-system",
        "get",
        "nodes.longhorn.io,volumes.longhorn.io,replicas.longhorn.io,settings.longhorn.io",
        "-o",
        "json",
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        stderr = error.stderr.strip() or "kubectl returned a non-zero status"
        raise RuntimeError(stderr) from error
    return json.loads(result.stdout)


def condition(conditions, name):
    for item in conditions or []:
        if item.get("type") == name:
            return item.get("status") == "True"
    return False


def gibibytes(value):
    return f"{value / (1024 ** 3):.1f} GiB"


def fail(message, failures):
    failures.append(message)


def main():
    args = parse_args()
    try:
        items = kubectl_json()["items"]
    except (RuntimeError, ValueError, KeyError) as error:
        print(f"PRE-FLIGHT ERROR: {error}", file=sys.stderr)
        return 2

    grouped = defaultdict(list)
    for item in items:
        grouped[item.get("kind")].append(item)

    nodes = {item["metadata"]["name"]: item for item in grouped["Node"]}
    volumes = {item["metadata"]["name"]: item for item in grouped["Volume"]}
    replicas = grouped["Replica"]
    settings = {item["metadata"]["name"]: item.get("value") for item in grouped["Setting"]}
    failures = []

    expected_settings = {
        "replica-auto-balance": "least-effort",
        "concurrent-replica-rebuild-per-node-limit": "1",
        "storage-minimal-available-percentage": "25",
    }
    for name, expected in expected_settings.items():
        actual = settings.get(name)
        if actual != expected:
            fail(f"Longhorn setting {name!r} is {actual!r}; expected {expected!r}", failures)

    for name, node in sorted(nodes.items()):
        status = node.get("status") or {}
        if not condition(status.get("conditions"), "Ready"):
            fail(f"node {name} is not Ready", failures)
        disks = status.get("diskStatus") or {}
        if not any(condition(disk.get("conditions"), "Schedulable") for disk in disks.values()):
            fail(f"node {name} has no Schedulable disk", failures)

    replicas_by_volume = defaultdict(list)
    replica_counts = Counter()
    eviction_requests = []
    non_running = []
    for replica in replicas:
        spec = replica.get("spec") or {}
        status = replica.get("status") or {}
        volume_name = spec.get("volumeName")
        replicas_by_volume[volume_name].append(replica)
        replica_counts[spec.get("nodeID", "<unscheduled>")] += 1
        if spec.get("evictionRequested"):
            eviction_requests.append(replica["metadata"]["name"])
        if status.get("currentState") != "running":
            non_running.append(
                f"{replica['metadata']['name']}={status.get('currentState', '<unset>')}"
            )

    if eviction_requests:
        fail("an eviction is already in progress: " + ", ".join(sorted(eviction_requests)), failures)
    if non_running:
        fail("non-running replica(s): " + ", ".join(sorted(non_running)), failures)

    for name, volume in sorted(volumes.items()):
        spec = volume.get("spec") or {}
        status = volume.get("status") or {}
        expected = int(spec.get("numberOfReplicas", 0))
        running = sum(
            (replica.get("status") or {}).get("currentState") == "running"
            for replica in replicas_by_volume[name]
        )
        if status.get("robustness") != "healthy":
            fail(f"volume {name} robustness is {status.get('robustness')!r}", failures)
        if running != expected:
            fail(f"volume {name} has {running}/{expected} running replicas", failures)

    print("Longhorn replica relocation preflight (read-only)")
    print("Replica distribution:")
    for name in sorted(nodes):
        print(f"  {name}: {replica_counts[name]}")
    print(
        "Settings: "
        + ", ".join(f"{key}={settings.get(key, '<missing>')}" for key in expected_settings)
    )

    if not args.volume:
        if failures:
            print("\nREFUSE: baseline is not safe for a relocation:")
            for message in failures:
                print(f"  - {message}")
            return 1
        print("\nPASS: all volumes are healthy with their expected running replicas; no eviction is active.")
        return 0

    volume = volumes.get(args.volume)
    source = nodes.get(args.source)
    target = nodes.get(args.target)
    if volume is None:
        fail(f"volume {args.volume!r} does not exist", failures)
    if source is None:
        fail(f"source node {args.source!r} does not exist", failures)
    if target is None:
        fail(f"target node {args.target!r} does not exist", failures)

    source_replicas = []
    if volume is not None:
        for replica in replicas_by_volume[args.volume]:
            spec = replica.get("spec") or {}
            status = replica.get("status") or {}
            if spec.get("nodeID") == args.source and status.get("currentState") == "running":
                source_replicas.append(replica)
        target_replicas = [
            replica
            for replica in replicas_by_volume[args.volume]
            if (replica.get("spec") or {}).get("nodeID") == args.target
        ]
        selector = (volume.get("spec") or {}).get("nodeSelector") or []
        if len(source_replicas) != 1:
            fail(
                f"volume {args.volume} has {len(source_replicas)} running replica(s) on {args.source}; expected exactly one",
                failures,
            )
        if target_replicas:
            fail(f"volume {args.volume} already has a replica on {args.target}", failures)
        if selector:
            fail(
                f"volume {args.volume} already has a nodeSelector ({', '.join(selector)})",
                failures,
            )

    suitable_disks = []
    if volume is not None and target is not None:
        target_spec = target.get("spec") or {}
        target_tags = target_spec.get("tags") or []
        if target_tags:
            fail(
                f"target node {args.target} already has tag(s) ({', '.join(target_tags)})",
                failures,
            )
        if not condition((target.get("status") or {}).get("conditions"), "Ready"):
            fail(f"target node {args.target} is not Ready", failures)

        minimum_free = int(settings.get("storage-minimal-available-percentage", "0")) / 100
        volume_size = int((volume.get("spec") or {}).get("size", 0))
        for disk_name, disk in ((target.get("status") or {}).get("diskStatus") or {}).items():
            if not condition(disk.get("conditions"), "Schedulable"):
                continue
            available = int(disk.get("storageAvailable", 0))
            maximum = int(disk.get("storageMaximum", 0))
            safe_headroom = available - int(maximum * minimum_free)
            if safe_headroom >= volume_size:
                suitable_disks.append((disk_name, safe_headroom))
        if not suitable_disks:
            fail(
                f"target node {args.target} has no Schedulable disk with {gibibytes(volume_size)} safe headroom",
                failures,
            )

    if failures:
        print("\nREFUSE: do not start a relocation:")
        for message in failures:
            print(f"  - {message}")
        return 1

    source_replica = source_replicas[0]["metadata"]["name"]
    print("\nPASS: controlled relocation can be prepared.")
    print(f"  volume: {args.volume}")
    print(f"  source replica: {source_replica} on {args.source}")
    print(f"  target: {args.target}")
    print("  eligible target disk(s):")
    for disk_name, headroom in suitable_disks:
        print(f"    - {disk_name}: {gibibytes(headroom)} safe headroom after the free-space reserve")
    print("\nThis command made no change. Follow docs/runbooks/longhorn-controlled-replica-relocation.md only after explicit approval.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY
