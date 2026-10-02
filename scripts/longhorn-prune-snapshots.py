#!/usr/bin/env python3
"""Retain two recent local restore points on one freshly backed-up V1 volume."""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time


class UnsafeCleanup(RuntimeError):
    pass


def run(argv, payload=None):
    result = subprocess.run(argv, input=payload, text=True, capture_output=True, timeout=45)
    if result.returncode:
        raise UnsafeCleanup(f"{argv[0]} {argv[1]} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


def get(resource, name=None):
    args = ["kubectl", "--request-timeout=20s", "-n", "longhorn-system", "get", resource]
    if name:
        args.append(name)
    return json.loads(run(args + ["-o", "json"]))


def guard_health(items):
    replicas = {o["metadata"]["name"]: o for o in items if o["kind"] == "Replica"}
    engines = [o for o in items if o["kind"] == "Engine" and o["spec"].get("active")]
    for volume in (o for o in items if o["kind"] == "Volume"):
        name, spec, status = volume["metadata"]["name"], volume["spec"], volume["status"]
        if spec.get("dataEngine") != "v1" or spec.get("dataSource") or spec.get("cloneMode") or status.get("restoreRequired") or spec.get("migrationNodeID"):
            raise UnsafeCleanup(f"Unsupported engine, clone, restore or migration on {name}")
        if spec["image"] != status["currentImage"]:
            raise UnsafeCleanup(f"An engine upgrade is in progress on {name}")
        if status["state"] == "detached":
            sources = [r for r in replicas.values() if r["spec"]["volumeName"] == name and r["spec"].get("active") and
                       r["spec"].get("healthyAt") and not r["spec"].get("failedAt")]
        else:
            if status["state"] != "attached" or status["robustness"] != "healthy":
                raise UnsafeCleanup(f"{name} is not attached and healthy")
            active = [e for e in engines if e["spec"]["volumeName"] == name]
            if len(active) != 1 or active[0]["status"].get("currentState") != "running":
                raise UnsafeCleanup(f"{name} lacks one running active engine")
            modes = active[0]["status"].get("replicaModeMap") or {}
            if any(mode != "RW" for mode in modes.values()):
                raise UnsafeCleanup(f"{name} has a rebuilding or failed replica")
            sources = [replicas[r] for r, mode in modes.items() if mode == "RW" and r in replicas]
        nodes = {r["spec"].get("nodeID") for r in sources if r["spec"].get("nodeID") and not r["spec"].get("failedAt")}
        if len(nodes) < max(3, spec["numberOfReplicas"]):
            raise UnsafeCleanup(f"{name} lacks its healthy copies on distinct nodes")


def guard_backups(items, now):
    backups = {o["metadata"]["name"]: o for o in items if o["kind"] == "Backup"}
    if any(b.get("status", {}).get("state") not in {"Completed", "Error"} for b in backups.values()):
        raise UnsafeCleanup("Wait for running backups before snapshot compaction")
    for volume in (o for o in items if o["kind"] == "Volume"):
        name, status = volume["metadata"]["name"], volume["status"]
        b = backups.get(status.get("lastBackup"), {}).get("status", {})
        if b.get("state") != "Completed" or b.get("progress") != 100 or b.get("error") or not b.get("url") or b.get("volumeName") != name:
            raise UnsafeCleanup(f"{name} lacks a completed recovery backup")
        when = dt.datetime.fromisoformat(b["snapshotCreatedAt"].replace("Z", "+00:00"))
        if not dt.timedelta(0) <= now - when <= dt.timedelta(hours=24):
            raise UnsafeCleanup(f"{name}'s recovery backup is older than 24 hours")


def snapshot_health_stable(items, name, initially_detached):
    """Wait for native snapshot attachment; never authorize work in transition."""
    volume = next(o for o in items if o["kind"] == "Volume" and o["metadata"]["name"] == name)
    status = volume["status"]
    starting = status["state"] == "attached" and status.get("robustness") == "unknown"
    if not initially_detached or (status["state"] not in {"attaching", "detaching"} and not starting):
        guard_health(items)
        return True
    guard_health([o for o in items if o.get("spec", {}).get("volumeName", o["metadata"]["name"]) != name])
    spec, status = volume["spec"], volume["status"]
    if spec.get("dataEngine") != "v1" or spec.get("dataSource") or spec.get("cloneMode") or \
            status.get("restoreRequired") or spec.get("migrationNodeID") or spec["image"] != status["currentImage"]:
        raise UnsafeCleanup("Unexpected restore, migration or engine change during snapshot attachment")
    copies = {r["spec"].get("nodeID") for r in items if r["kind"] == "Replica" and
              r["spec"]["volumeName"] == name and r["spec"].get("active") and
              r["spec"].get("healthyAt") and not r["spec"].get("failedAt")}
    copies.discard(None)
    copies.discard("")
    if len(copies) < max(3, spec["numberOfReplicas"]):
        raise UnsafeCleanup("The transitioning volume lost its healthy retained copies")
    return False


def select_snapshots(snapshots, protected_backup_snapshot):
    visible = [s for s in snapshots if s.get("status", {}).get("readyToUse") and not s["status"].get("markRemoved")]
    visible.sort(key=lambda s: (s["status"]["creationTime"], s["metadata"]["name"]), reverse=True)
    keep = {s["metadata"]["name"] for s in visible[:2]}
    if protected_backup_snapshot not in keep:
        raise UnsafeCleanup("The latest completed backup snapshot must be one of the two retained points; refresh the backup first")
    # Deleting an already-marked parent CR lets the native snapshot controller
    # attach an idle volume and finish physical purging before it detaches.
    return keep, visible[2:] + [s for s in snapshots if s.get("status", {}).get("markRemoved")]


def guard_snapshot_references(targets, contents):
    names = {s["metadata"]["name"] for s in targets}
    for content in contents:
        handles = [content.get("spec", {}).get("source", {}).get("snapshotHandle", ""),
                   content.get("status", {}).get("snapshotHandle", "")]
        if any(name in handle for name in names for handle in handles):
            raise UnsafeCleanup("A CSI VolumeSnapshotContent still references a selected snapshot")


def physical_compaction_complete(engine, keep):
    purge = engine["status"].get("purgeStatus") or {}
    if any(p.get("isPurging") or p.get("error") for p in purge.values()):
        return False
    snapshots = engine["status"].get("snapshots") or {}
    return bool(snapshots) and set(snapshots) == set(keep) | {"volume-head"}


def guard_compaction_space(volume, replicas, nodes, parent_sizes=None):
    # V1 FoldFile writes child extents into the existing removed parent, then
    # replaces the child with that parent. Missing parent extents plus a 10%
    # volume-size margin bound temporary allocation. Without measured sizes,
    # conservatively allow a whole additional volume.
    size = int(volume["spec"]["size"])
    margin = (size + 9) // 10
    node_map = {n["metadata"]["name"]: n for n in nodes}
    for replica in replicas:
        spec = replica["spec"]
        if spec["volumeName"] != volume["metadata"]["name"] or not spec.get("active"):
            continue
        node = node_map.get(spec.get("nodeID"), {})
        disk = next((d for d in node.get("status", {}).get("diskStatus", {}).values()
                     if d.get("diskUUID") == spec.get("diskID")), {})
        ready = any(c["type"] == "Ready" and c["status"] == "True" for c in disk.get("conditions", []))
        measured = (parent_sizes or {}).get(replica["metadata"]["name"])
        additional = size if measured is None else max([0] + [max(0, size - n) for n in measured])
        needed = additional + margin
        if not ready or disk.get("storageAvailable", 0) < needed:
            raise UnsafeCleanup(f"{spec.get('nodeID')} lacks ready disk space for temporary snapshot coalescing")


def check_compaction_space(volume, items, nodes, retiring):
    replicas = [r for r in items if r["kind"] == "Replica"]
    engines = [e for e in items if e["kind"] == "Engine" and e["spec"]["volumeName"] == volume["metadata"]["name"] and e["spec"].get("active") and e["status"].get("currentState") == "running"]
    node_map = {n["metadata"]["name"]: n for n in nodes}
    measured = {}
    full_allowance = (int(volume["spec"]["size"]) * 11 + 9) // 10
    for replica in replicas:
        spec = replica["spec"]
        if spec["volumeName"] != volume["metadata"]["name"] or not spec.get("active"):
            continue
        disk = next((d for d in node_map.get(spec.get("nodeID"), {}).get("status", {}).get("diskStatus", {}).values() if d.get("diskUUID") == spec.get("diskID")), {})
        if disk.get("storageAvailable", 0) >= full_allowance or not engines:
            continue
        snapshots = engines[0]["status"].get("snapshots") or {}
        if not snapshots:
            raise UnsafeCleanup("Cannot measure compaction allocation without the physical snapshot tree")
        parents = [name for name, s in snapshots.items() if name != "volume-head" and
                   (s.get("removed") or name in retiring or not s.get("usercreated")) and s.get("children")]
        directory = spec["dataDirectoryName"]
        disk_path = spec["diskPath"].rstrip("/")
        if disk_path not in {"/var/lib/longhorn", "/var/lib/longhorn/extra"} or not re.fullmatch(r"[a-z0-9-]+", directory) or any(not re.fullmatch(r"[a-z0-9-]+", name) for name in parents):
            raise UnsafeCleanup("Unexpected replica path or snapshot name")
        pod = get("pods", replica["status"]["instanceManagerName"])
        if pod["spec"]["nodeName"] != spec["nodeID"] or not any(c["type"] == "Ready" and c["status"] == "True" for c in pod["status"].get("conditions", [])):
            raise UnsafeCleanup("The replica instance manager is not ready on its recorded node")
        sizes = []
        if parents:
            paths = ["/host" + disk_path + "/replicas/" + directory + "/volume-snap-" + name + ".img" for name in parents]
            output = run(["kubectl", "--request-timeout=20s", "-n", "longhorn-system", "exec", pod["metadata"]["name"], "--", "stat", "-c", "%b %B"] + paths)
            for line in output.splitlines():
                blocks, block_size = map(int, line.split())
                if blocks < 0 or block_size <= 0:
                    raise UnsafeCleanup("Invalid snapshot allocation measurement")
                sizes.append(blocks * block_size)
            if len(sizes) != len(parents):
                raise UnsafeCleanup("Incomplete snapshot allocation measurements")
        measured[replica["metadata"]["name"]] = sizes
    guard_compaction_space(volume, replicas, nodes, measured)


def action(volume, name, body):
    path = "/api/v1/namespaces/longhorn-system/services/longhorn-backend:9500/proxy/v1/volumes/" + volume + "?action=" + name
    run(["kubectl", "--request-timeout=20s", "create", "--raw", path, "-f", "-"], json.dumps(body))


def state(with_backups=True):
    resources = "volumes.longhorn.io,engines.longhorn.io,replicas.longhorn.io,snapshots.longhorn.io"
    if with_backups:
        resources += ",backups.longhorn.io"
    return get(resources)["items"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("volume", help="Process exactly one volume")
    parser.add_argument("--snapshot-name", required=True, help="Unique name for a fresh maintenance restore point")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--retire-older-history", action="store_true", help="Acknowledge retirement of old local points, including unbacked versions")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,61}[a-z0-9]", args.snapshot_name):
        raise UnsafeCleanup("Use a unique DNS label of at most 63 characters for the maintenance snapshot")
    ds = get("daemonset", "longhorn-manager")
    if ds["status"].get("numberReady") != ds["status"].get("desiredNumberScheduled") or \
            ds["status"].get("updatedNumberScheduled") != ds["status"].get("desiredNumberScheduled") or \
            ds["status"].get("observedGeneration") != ds["metadata"]["generation"]:
        raise UnsafeCleanup("Finish the manager rollout before compaction")
    items = state()
    guard_health(items)
    guard_backups(items, dt.datetime.now(dt.timezone.utc))
    image = get("settings.longhorn.io", "default-engine-image")["value"]
    if any(v["spec"]["image"] != image for v in items if v["kind"] == "Volume"):
        raise UnsafeCleanup("Finish engine upgrades before compaction")
    volume = next(v for v in items if v["kind"] == "Volume" and v["metadata"]["name"] == args.volume)
    initially_detached = volume["status"]["state"] == "detached"
    backup = next(b for b in items if b["kind"] == "Backup" and b["metadata"]["name"] == volume["status"]["lastBackup"])
    snapshot_name = backup["status"]["snapshotName"]
    snapshots = [s for s in items if s["kind"] == "Snapshot" and s["spec"]["volume"] == args.volume]
    existing = next((s for s in items if s["kind"] == "Snapshot" and s["metadata"]["name"] == args.snapshot_name), None)
    if existing and existing["spec"]["volume"] != args.volume:
        raise UnsafeCleanup("The requested maintenance snapshot name belongs to another volume")
    if not args.apply:
        retiring = {s["metadata"]["name"] for s in snapshots if s["metadata"]["name"] not in {snapshot_name, args.snapshot_name}}
        check_compaction_space(volume, items, get("nodes.longhorn.io")["items"], retiring)
        print(f"Dry run: create/reuse {args.snapshot_name}, retain it and backup snapshot {snapshot_name}; retire older local history on {args.volume}.")
        print("Current visible points:", [(s["metadata"]["name"], s["status"].get("creationTime")) for s in snapshots if s["status"].get("readyToUse") and not s["status"].get("markRemoved")])
        return
    if not args.retire_older_history:
        raise UnsafeCleanup("Apply requires --retire-older-history; no old snapshots will be archived")
    if not existing:
        action(args.volume, "snapshotCRCreate", {"name": args.snapshot_name, "labels": {"maintenance": "longhorn-upgrade-20261001"}})
    for _ in range(90):
        created = get("snapshots.longhorn.io", args.snapshot_name)
        status = created.get("status", {})
        if status.get("readyToUse") and not status.get("markRemoved"):
            break
        time.sleep(2)
    else:
        raise UnsafeCleanup("The maintenance snapshot did not become ready; no older history was removed")
    for _ in range(90):
        current = state()
        if snapshot_health_stable(current, args.volume, initially_detached):
            break
        time.sleep(2)
    else:
        raise UnsafeCleanup("Native snapshot attachment did not settle; no older history was removed")
    guard_backups(current, dt.datetime.now(dt.timezone.utc))
    snapshots = [s for s in current if s["kind"] == "Snapshot" and s["spec"]["volume"] == args.volume]
    keep, targets = select_snapshots(snapshots, snapshot_name)
    check_compaction_space(volume, current, get("nodes.longhorn.io")["items"], {s["metadata"]["name"] for s in targets})
    crd = run(["kubectl", "get", "crd", "volumesnapshotcontents.snapshot.storage.k8s.io", "--ignore-not-found", "-o", "json"])
    contents = get("volumesnapshotcontents.snapshot.storage.k8s.io")["items"] if crd.strip() else []
    physical_removed = [{"metadata": {"name": name}} for e in current if e["kind"] == "Engine" and e["spec"]["volumeName"] == args.volume and e["spec"].get("active") for name, s in (e["status"].get("snapshots") or {}).items() if name != "volume-head" and (s.get("removed") or not s.get("usercreated"))]
    guard_snapshot_references(targets + physical_removed, contents)
    print(f"Retain {sorted(keep)}; retire {len(targets)} older points", flush=True)
    for target in targets:
        latest = get("snapshots.longhorn.io", target["metadata"]["name"])
        if latest["metadata"]["uid"] != target["metadata"]["uid"] or latest["spec"]["volume"] != args.volume or latest["status"]["creationTime"] != target["status"]["creationTime"]:
            raise UnsafeCleanup("Snapshot identity changed; stop before deletion")
        action(args.volume, "snapshotCRDelete", {"name": target["metadata"]["name"]})
    # Snapshot controllers may retire a long chain over several native passes.
    # Keep checking health while allowing up to three hours on this one volume.
    deadline = time.monotonic() + 3 * 60 * 60
    for attempt in range(2160):
        if time.monotonic() >= deadline:
            break
        current = state(with_backups=False)
        if not snapshot_health_stable(current, args.volume, initially_detached):
            time.sleep(5)
            continue
        remaining = [s for s in current if s["kind"] == "Snapshot" and s["spec"]["volume"] == args.volume]
        final_keep, extra = select_snapshots(remaining, snapshot_name)
        targets_present = any(s["metadata"]["name"] in {t["metadata"]["name"] for t in targets} for s in remaining)
        active = [e for e in current if e["kind"] == "Engine" and e["spec"]["volumeName"] == args.volume and e["spec"].get("active") and e["status"].get("currentState") == "running"]
        stopped = [e for e in current if e["kind"] == "Engine" and e["spec"]["volumeName"] == args.volume and e["spec"].get("active")]
        if not active and not targets_present and not extra and keep == final_keep and \
                len(stopped) == 1 and physical_compaction_complete(stopped[0], keep):
            guard_backups(state(), dt.datetime.now(dt.timezone.utc))
            print("Verified: detached volume retains two recent points and its healthy copies; selected old points are gone", flush=True)
            return
        if active:
            engine = active[0]
            purge = engine["status"].get("purgeStatus") or {}
            if any(p.get("error") for p in purge.values()):
                raise UnsafeCleanup("Snapshot purge reported an error; stop before changing another volume")
            if attempt and not initially_detached and not targets_present and physical_compaction_complete(engine, keep):
                if not extra and keep == final_keep:
                    guard_backups(state(), dt.datetime.now(dt.timezone.utc))
                    print("Verified: two recent local points retained; older selected points purged; healthy replicas and B2 backups preserved", flush=True)
                    return
            if not any(p.get("isPurging") for p in purge.values()) and attempt % 12 == 0:
                action(args.volume, "snapshotPurge", {})
        if attempt % 12 == 0:
            print(f"Waiting for native compaction on {args.volume}; original backup {backup['metadata']['name']} retained", flush=True)
        time.sleep(5)
    raise UnsafeCleanup("Compaction has not finished; stop and investigate before changing another volume")


if __name__ == "__main__":
    try:
        main()
    except (UnsafeCleanup, KeyError, ValueError, StopIteration, subprocess.TimeoutExpired) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        sys.exit(1)
