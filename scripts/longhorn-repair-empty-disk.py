#!/usr/bin/env python3
"""Reconcile a stale Longhorn disk UUID only after proving the disk is empty."""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid


class UnsafeRepair(RuntimeError):
    pass


def run(argv):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=45)
    if result.returncode:
        raise UnsafeRepair(f"Command failed: {argv[0]} {argv[1]} (exit {result.returncode})")
    return result.stdout


def get(resource, name=None):
    argv = ["kubectl", "--request-timeout=20s", "-n", "longhorn-system", "get", resource]
    if name:
        argv.append(name)
    return json.loads(run(argv + ["-o", "json"]))


def guard_references(node, disk_name, actual_uuid, resources):
    spec = node["spec"]["disks"][disk_name]
    status = node["status"]["diskStatus"][disk_name]
    old_uuid = status["diskUUID"]
    uuid.UUID(actual_uuid)
    if old_uuid == actual_uuid:
        raise UnsafeRepair("The disk UUID already matches; no repair is needed")
    if spec.get("diskType") != "filesystem" or spec.get("evictionRequested"):
        raise UnsafeRepair("Only a non-evicting filesystem disk can be repaired")
    ready = next((c for c in status.get("conditions", []) if c["type"] == "Ready"), {})
    if ready.get("reason") != "DiskFilesystemChanged" or ready.get("status") != "False":
        raise UnsafeRepair("The disk does not have the expected UUID-mismatch condition")
    if status.get("storageScheduled", 0) or status.get("scheduledReplica") or status.get("scheduledBackingImage"):
        raise UnsafeRepair("The disk still has scheduled data")
    ids = {old_uuid, actual_uuid}
    for item in resources:
        kind, item_spec = item["kind"], item.get("spec", {})
        if kind == "Replica" and (item_spec.get("diskID") in ids or
                (item_spec.get("nodeID") == node["metadata"]["name"] and
                 item_spec.get("diskPath", "").rstrip("/") == spec["path"].rstrip("/"))):
            raise UnsafeRepair("A replica references the target disk")
        if kind == "BackingImage" and ids.intersection(item_spec.get("diskFileSpecMap", {})):
            raise UnsafeRepair("A backing image references the target disk")
        if kind == "BackingImageManager" and item_spec.get("diskUUID") in ids:
            raise UnsafeRepair("A backing-image manager references the target disk")
        if kind == "Orphan" and item_spec.get("parameters", {}).get("DiskUUID") in ids:
            raise UnsafeRepair("An orphan references the target disk")
    return old_uuid


def guard_health(resources):
    engines = {x["spec"].get("volumeName"): x for x in resources if x["kind"] == "Engine"}
    for volume in (x for x in resources if x["kind"] == "Volume"):
        status = volume["status"]
        if status.get("state") == "detached":
            continue
        if status.get("state") != "attached" or status.get("robustness") != "healthy":
            raise UnsafeRepair(f"Volume {volume['metadata']['name']} is not attached and healthy")
        engine = engines.get(volume["metadata"]["name"], {})
        modes = engine.get("status", {}).get("replicaModeMap") or {}
        if sum(mode == "RW" for mode in modes.values()) < max(3, volume["spec"]["numberOfReplicas"]):
            raise UnsafeRepair(f"Volume {volume['metadata']['name']} lacks its required RW replicas")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node")
    parser.add_argument("disk")
    parser.add_argument("--apply", action="store_true", help="Apply the checked status-only UUID repair")
    args = parser.parse_args()
    ds = get("daemonset", "longhorn-manager")
    if ds["status"].get("numberReady") != ds["status"].get("desiredNumberScheduled") or \
            ds["status"].get("updatedNumberScheduled") != ds["status"].get("desiredNumberScheduled") or \
            ds["status"].get("observedGeneration") != ds["metadata"]["generation"]:
        raise UnsafeRepair("The Longhorn manager rollout must be fully ready first")
    node = get("nodes.longhorn.io", args.node)
    kube_node = get("nodes", args.node)
    if not any(c["type"] == "Ready" and c["status"] == "True" for c in kube_node["status"]["conditions"]):
        raise UnsafeRepair("The Kubernetes node is not Ready")
    ip = next(a["address"] for a in kube_node["status"]["addresses"] if a["type"] == "InternalIP")
    spec = node["spec"]["disks"][args.disk]
    talos = ["talosctl", "--nodes", ip]
    if os.environ.get("TALOSCONFIG"):
        talos += ["--talosconfig", os.environ["TALOSCONFIG"]]
    cfg = json.loads(run(talos + ["read", spec["path"].rstrip("/") + "/longhorn-disk.cfg"]))
    if cfg.get("state") != "ready" or cfg.get("diskName") != args.disk:
        raise UnsafeRepair("The on-disk configuration does not describe the expected ready disk")
    listing = run(talos + ["ls", spec["path"].rstrip("/") + "/replicas"])
    entries = [line.split()[-1] for line in listing.splitlines()[1:] if line.strip()]
    if not entries or any(name not in {".", ".."} for name in entries):
        raise UnsafeRepair("The replica directory must be confirmed empty")
    resources = get("volumes.longhorn.io,engines.longhorn.io,replicas.longhorn.io,backingimages.longhorn.io,backingimagemanagers.longhorn.io,orphans.longhorn.io")["items"]
    guard_health(resources)
    old_uuid = guard_references(node, args.disk, cfg["diskUUID"], resources)
    pointer = args.disk.replace("~", "~0").replace("/", "~1")
    patch = [
        {"op": "test", "path": "/metadata/resourceVersion", "value": node["metadata"]["resourceVersion"]},
        {"op": "test", "path": f"/status/diskStatus/{pointer}/diskUUID", "value": old_uuid},
        {"op": "test", "path": f"/spec/disks/{pointer}/path", "value": spec["path"]},
        {"op": "replace", "path": f"/status/diskStatus/{pointer}/diskUUID", "value": cfg["diskUUID"]},
    ]
    print(f"Checked empty disk {args.node}/{args.disk}: {old_uuid} -> {cfg['diskUUID']}")
    if not args.apply:
        print("Dry run: no changes made. Use --apply to update controller status only.")
        return
    run(["kubectl", "--request-timeout=20s", "-n", "longhorn-system", "patch", "nodes.longhorn.io", args.node,
         "--subresource=status", "--type=json", "-p", json.dumps(patch)])
    for _ in range(30):
        current = get("nodes.longhorn.io", args.node)["status"]["diskStatus"][args.disk]
        conditions = {c["type"]: c["status"] for c in current.get("conditions", [])}
        if current.get("diskUUID") == cfg["diskUUID"] and all(conditions.get(k) == "True" for k in ["Ready", "Schedulable"]):
            guard_health(get("volumes.longhorn.io,engines.longhorn.io")["items"])
            print("Verified: disk Ready and Schedulable; attached volumes retain required healthy replicas")
            return
        time.sleep(2)
    raise UnsafeRepair("The status repair was applied but readiness did not recover; stop before any further repair")


if __name__ == "__main__":
    try:
        main()
    except (UnsafeRepair, KeyError, ValueError, StopIteration, subprocess.TimeoutExpired) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        sys.exit(1)
