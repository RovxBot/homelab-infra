#!/usr/bin/env python3
"""Remove the finalizer from one deleted, unattached Longhorn VolumeAttachment."""

import argparse
import json
import subprocess
import sys


class UnsafeRemoval(RuntimeError):
    pass


def run(argv):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=45)
    if result.returncode:
        raise UnsafeRemoval(f"kubectl failed: {result.stderr.strip()}")
    return result.stdout


def get(resource, name=None, optional=False):
    argv = ["kubectl", "--request-timeout=20s", "get", resource]
    if name:
        argv.append(name)
    else:
        argv.append("-A")
    if optional:
        argv.append("--ignore-not-found")
    output = run(argv + ["-o", "json"])
    return json.loads(output) if output.strip() else None


def guard(attachment, items, uid, volume, node):
    metadata, spec, status = attachment["metadata"], attachment["spec"], attachment.get("status", {})
    if metadata["uid"] != uid or spec.get("attacher") != "driver.longhorn.io" or \
            spec.get("source") != {"persistentVolumeName": volume} or spec.get("nodeName") != node:
        raise UnsafeRemoval("Attachment identity differs from the reviewed orphan")
    if not metadata.get("deletionTimestamp") or status.get("attached") is not False or \
            metadata.get("finalizers") != ["external-attacher/driver-longhorn-io"]:
        raise UnsafeRemoval("Only an already deleted, unattached object with the expected finalizer qualifies")
    for item in items:
        kind, item_spec = item["kind"], item.get("spec", {})
        if kind == "PersistentVolume" and (item["metadata"]["name"] == volume or item_spec.get("csi", {}).get("volumeHandle") == volume):
            raise UnsafeRemoval("A persistent volume still exists or refers to this volume handle")
        if kind == "PersistentVolumeClaim" and item_spec.get("volumeName") == volume:
            raise UnsafeRemoval("A persistent volume claim still refers to this volume")
        if kind == "Volume" and (item["metadata"]["name"] == volume or item.get("status", {}).get("kubernetesStatus", {}).get("pvName") == volume):
            raise UnsafeRemoval("A Longhorn volume still exists or refers to this persistent volume")
        if kind in {"Engine", "Replica"} and item_spec.get("volumeName") == volume:
            raise UnsafeRemoval("A Longhorn engine or replica still refers to this volume")
        if kind == "Node":
            state = item.get("status", {})
            names = [v.get("name", "") for v in state.get("volumesAttached", [])] + state.get("volumesInUse", [])
            if any(volume in name for name in names):
                raise UnsafeRemoval("A Kubernetes node still reports this volume attached or in use")


def patch(attachment):
    metadata = attachment["metadata"]
    return [
        {"op": "test", "path": "/metadata/uid", "value": metadata["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion", "value": metadata["resourceVersion"]},
        {"op": "test", "path": "/metadata/deletionTimestamp", "value": metadata["deletionTimestamp"]},
        {"op": "test", "path": "/spec", "value": attachment["spec"]},
        {"op": "test", "path": "/status/attached", "value": False},
        {"op": "test", "path": "/metadata/finalizers", "value": metadata["finalizers"]},
        {"op": "remove", "path": "/metadata/finalizers/0"},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attachment")
    parser.add_argument("--uid", required=True)
    parser.add_argument("--volume", required=True, help="Reviewed missing PV and Longhorn volume name")
    parser.add_argument("--node", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    attachment = get("volumeattachments.storage.k8s.io", args.attachment)
    items = get("pv,pvc,nodes")["items"] + get("volumes.longhorn.io,engines.longhorn.io,replicas.longhorn.io")["items"]
    guard(attachment, items, args.uid, args.volume, args.node)
    print(f"Verified orphan {args.attachment}: absent PV/volume/claims/replicas and no node reports it in use")
    if not args.apply:
        print("Dry run: no changes made.")
        return
    run(["kubectl", "--request-timeout=20s", "patch", "volumeattachments.storage.k8s.io", args.attachment,
         "--type=json", "-p", json.dumps(patch(attachment))])
    if get("volumeattachments.storage.k8s.io", args.attachment, optional=True):
        raise UnsafeRemoval("The attachment remains; inspect before making another change")
    print("Verified: stale attachment removed; no persistent volume, Longhorn volume or replica was modified")


if __name__ == "__main__":
    try:
        main()
    except (UnsafeRemoval, KeyError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        sys.exit(1)
