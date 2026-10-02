#!/usr/bin/env python3
"""Resume pending native snapshot purging on one unused, backed-up V1 volume."""
import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time


spec = importlib.util.spec_from_file_location("snapshot_guards", Path(__file__).with_name("longhorn-prune-snapshots.py"))
guards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guards)
TICKET = "longhorn-idle-snapshot-maintenance"


def cluster_resources():
    return json.loads(guards.run(["kubectl", "--request-timeout=20s", "get", "pv,pvc,pods,volumeattachments.storage.k8s.io", "--all-namespaces", "-o", "json"]))["items"]


def unused_claim_identity(resources, name):
    pvs = [o for o in resources if o["kind"] == "PersistentVolume" and
           o["spec"].get("csi", {}).get("driver") == "driver.longhorn.io" and
           o["spec"]["csi"].get("volumeHandle") == name]
    if len(pvs) != 1 or pvs[0]["status"].get("phase") != "Bound":
        raise guards.UnsafeCleanup("Require one original Bound Longhorn PV")
    pv = pvs[0]
    ref = pv["spec"]["claimRef"]
    claims = [o for o in resources if o["kind"] == "PersistentVolumeClaim" and
              (o["metadata"]["namespace"], o["metadata"]["name"], o["metadata"]["uid"]) ==
              (ref["namespace"], ref["name"], ref["uid"]) and o["status"].get("phase") == "Bound" and
              o["spec"].get("volumeName") == pv["metadata"]["name"]]
    if len(claims) != 1:
        raise guards.UnsafeCleanup("The original PVC identity or binding changed")
    for o in resources:
        if o["kind"] == "VolumeAttachment" and o["spec"].get("source", {}).get("persistentVolumeName") == pv["metadata"]["name"]:
            raise guards.UnsafeCleanup("A Kubernetes CSI attachment still uses the volume")
        if o["kind"] == "Pod" and o["metadata"]["namespace"] == ref["namespace"] and o["status"].get("phase") not in {"Succeeded", "Failed"} and \
                any(v.get("persistentVolumeClaim", {}).get("claimName") == ref["name"] for v in o["spec"].get("volumes", [])):
            raise guards.UnsafeCleanup("An active or terminating workload uses the volume")
    return pv["metadata"]["uid"], ref["namespace"], ref["name"], ref["uid"]


def check_attachment(attachment, volume):
    if attachment["spec"]["volume"] != volume["metadata"]["name"] or not any(
            o.get("kind") == "Volume" and o.get("name") == volume["metadata"]["name"] and
            o.get("uid") == volume["metadata"]["uid"] for o in attachment["metadata"].get("ownerReferences", [])):
        raise guards.UnsafeCleanup("Native attachment ownership changed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("volume")
    parser.add_argument("--node", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    ds = guards.get("daemonset", "longhorn-manager")
    s = ds["status"]
    if s.get("numberReady") != s.get("desiredNumberScheduled") or s.get("updatedNumberScheduled") != s.get("desiredNumberScheduled") or s.get("observedGeneration") != ds["metadata"]["generation"]:
        raise guards.UnsafeCleanup("Finish the manager rollout first")
    items = guards.state()
    guards.guard_health(items)
    guards.guard_backups(items, dt.datetime.now(dt.timezone.utc))
    image = guards.get("settings.longhorn.io", "default-engine-image")["value"]
    if any(o["spec"]["image"] != image for o in items if o["kind"] == "Volume"):
        raise guards.UnsafeCleanup("Finish all engine upgrades first")
    volume = next(o for o in items if o["kind"] == "Volume" and o["metadata"]["name"] == args.volume)
    if volume["status"]["state"] != "detached" or volume["spec"]["accessMode"] != "rwo":
        raise guards.UnsafeCleanup("Select an unused detached RWO volume")
    identity = unused_claim_identity(cluster_resources(), args.volume)
    attachment = guards.get("volumeattachments.longhorn.io", args.volume)
    check_attachment(attachment, volume)
    if attachment["spec"].get("attachmentTickets"):
        raise guards.UnsafeCleanup("The idle volume has another attachment request")
    snapshots = [o for o in items if o["kind"] == "Snapshot" and o["spec"]["volume"] == args.volume]
    backup = next(o for o in items if o["kind"] == "Backup" and o["metadata"]["name"] == volume["status"]["lastBackup"])
    keep, targets = guards.select_snapshots(snapshots, backup["status"]["snapshotName"])
    if len(keep) != 2 or not targets or any(not o["metadata"].get("deletionTimestamp") or not o["status"].get("markRemoved") for o in targets):
        raise guards.UnsafeCleanup("Require two protected points and only already-marked parents pending native deletion")
    guards.guard_snapshot_references(targets, guards.get("volumesnapshotcontents.snapshot.storage.k8s.io")["items"])
    nodes = guards.get("nodes.longhorn.io")["items"]
    node = next(o for o in nodes if o["metadata"]["name"] == args.node)
    if not any(c["type"] == "Ready" and c["status"] == "True" for c in node["status"]["conditions"]):
        raise guards.UnsafeCleanup("The maintenance node is not ready")
    guards.check_compaction_space(volume, items, nodes, {o["metadata"]["name"] for o in targets})
    print(f"Checked unused {args.volume}: two protected points; resume native purge with disabled frontend on {args.node}", flush=True)
    if not args.apply:
        print("Dry run: no changes made")
        return
    current = guards.get("volumes.longhorn.io", args.volume)
    latest_attachment = guards.get("volumeattachments.longhorn.io", args.volume)
    if current["metadata"]["uid"] != volume["metadata"]["uid"] or current["status"]["state"] != "detached" or \
            latest_attachment["metadata"]["uid"] != attachment["metadata"]["uid"] or latest_attachment["spec"].get("attachmentTickets") or \
            unused_claim_identity(cluster_resources(), args.volume) != identity:
        raise guards.UnsafeCleanup("Original idle-volume identities changed before attachment")
    guards.action(args.volume, "attach", {"hostId": args.node, "disableFrontend": True,
                  "attachedBy": TICKET, "attacherType": "longhorn-api", "attachmentID": TICKET})
    for _ in range(180):
        current_items = guards.state()
        if not guards.snapshot_health_stable(current_items, args.volume, True):
            time.sleep(2)
            continue
        current = next(o for o in current_items if o["kind"] == "Volume" and o["metadata"]["name"] == args.volume)
        if current["metadata"]["uid"] != volume["metadata"]["uid"]:
            raise guards.UnsafeCleanup("Original volume identity changed")
        engines = [o for o in current_items if o["kind"] == "Engine" and o["spec"]["volumeName"] == args.volume and o["spec"].get("active")]
        pending = [o for o in current_items if o["kind"] == "Snapshot" and o["spec"]["volume"] == args.volume and o["metadata"]["name"] not in keep]
        tickets = guards.get("volumeattachments.longhorn.io", args.volume)["spec"]["attachmentTickets"]
        if current["status"]["state"] == "attached" and len(engines) == 1 and not pending and \
                guards.physical_compaction_complete(engines[0], keep) and set(tickets) == {TICKET}:
            if tickets[TICKET].get("nodeID") != args.node or tickets[TICKET].get("type") != "longhorn-api" or tickets[TICKET].get("parameters", {}).get("disableFrontend") != "true":
                raise guards.UnsafeCleanup("The maintenance attachment parameters changed")
            guards.guard_backups(current_items, dt.datetime.now(dt.timezone.utc))
            if unused_claim_identity(cluster_resources(), args.volume) != identity:
                raise guards.UnsafeCleanup("Original claim identity or workload usage changed")
            break
        time.sleep(2)
    else:
        raise guards.UnsafeCleanup("Native purge is incomplete; leave the maintenance ticket for inspection")
    guards.action(args.volume, "detach", {"attachmentID": TICKET, "hostId": args.node, "forceDetach": False})
    for _ in range(180):
        final = guards.state()
        if guards.snapshot_health_stable(final, args.volume, True):
            current = next(o for o in final if o["kind"] == "Volume" and o["metadata"]["name"] == args.volume)
            if current["status"]["state"] == "detached":
                guards.guard_backups(final, dt.datetime.now(dt.timezone.utc))
                final_attachment = guards.get("volumeattachments.longhorn.io", args.volume)
                if current["metadata"]["uid"] != volume["metadata"]["uid"] or \
                        final_attachment["metadata"]["uid"] != attachment["metadata"]["uid"] or final_attachment["spec"].get("attachmentTickets") or \
                        unused_claim_identity(cluster_resources(), args.volume) != identity:
                    raise guards.UnsafeCleanup("Original idle-volume identities or attachments changed")
                print("Verified: native purge complete; original volume and claims preserved, three healthy copies, detached with no tickets", flush=True)
                return
        time.sleep(2)
    raise guards.UnsafeCleanup("Normal detach is incomplete; inspect without forcing it")


if __name__ == "__main__":
    try:
        main()
    except (guards.UnsafeCleanup, KeyError, ValueError, StopIteration, subprocess.SubprocessError) as error:
        print("STOP:", error, file=sys.stderr)
        sys.exit(1)
