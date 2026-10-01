#!/usr/bin/env python3
"""Upgrade one healthy, freshly backed-up Longhorn V1 volume to the manager's engine."""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time


class UnsafeUpgrade(RuntimeError):
    pass


def run(argv, payload=None):
    result = subprocess.run(argv, input=payload, text=True, capture_output=True, timeout=45)
    if result.returncode:
        raise UnsafeUpgrade(f"{argv[0]} {argv[1]} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


def get(resource, name=None):
    args = ["kubectl", "--request-timeout=20s", "-n", "longhorn-system", "get", resource]
    if name:
        args.append(name)
    return json.loads(run(args + ["-o", "json"]))


def version(image):
    match = re.fullmatch(r"(?:docker.io/)?longhornio/longhorn-engine:v(\d+)\.(\d+)\.(\d+)", image)
    if not match:
        raise UnsafeUpgrade(f"Not a stable Longhorn engine image: {image}")
    return tuple(map(int, match.groups()))


def health(items, check_images=True):
    replicas = {o["metadata"]["name"]: o for o in items if o["kind"] == "Replica"}
    engines = [o for o in items if o["kind"] == "Engine" and o["spec"].get("active")]
    for volume in (o for o in items if o["kind"] == "Volume"):
        name, spec, status = volume["metadata"]["name"], volume["spec"], volume["status"]
        required = max(3, spec["numberOfReplicas"])
        if spec.get("dataEngine") != "v1" or spec.get("dataSource") or spec.get("cloneMode") or status.get("restoreRequired") or spec.get("migrationNodeID"):
            raise UnsafeUpgrade(f"Unsupported engine, clone, restore or migration on {name}")
        if check_images and version(spec["image"]) != version(status["currentImage"]):
            raise UnsafeUpgrade(f"An engine upgrade is already in progress on {name}")
        if status["state"] == "detached":
            sources = [r for r in replicas.values() if r["spec"]["volumeName"] == name and
                       r["spec"].get("active") and r["spec"].get("healthyAt") and not r["spec"].get("failedAt")]
            nodes = {r["spec"]["nodeID"] for r in sources if r["spec"].get("nodeID")}
        else:
            if status["state"] != "attached" or status["robustness"] != "healthy":
                raise UnsafeUpgrade(f"{name} is not attached and healthy")
            active = [e for e in engines if e["spec"]["volumeName"] == name]
            if len(active) != 1 or active[0]["status"]["currentState"] != "running":
                raise UnsafeUpgrade(f"{name} does not have one running active engine")
            modes = active[0]["status"].get("replicaModeMap") or {}
            if any(mode != "RW" for mode in modes.values()):
                raise UnsafeUpgrade(f"{name} has a rebuilding or failed replica")
            rw = [replicas[r] for r, mode in modes.items() if mode == "RW" and r in replicas]
            nodes = {r["spec"]["nodeID"] for r in rw if r["spec"].get("nodeID") and not r["spec"].get("failedAt")}
        if len(nodes) < required:
            raise UnsafeUpgrade(f"{name} lacks {required} healthy replicas on distinct nodes")


def fresh_backup(volume, backup, now):
    name = volume["metadata"]["name"]
    status = backup.get("status", {})
    if backup["metadata"]["name"] != volume["status"].get("lastBackup") or \
            status.get("volumeName") != name or status.get("state") != "Completed" or \
            status.get("progress") != 100 or status.get("error") or not status.get("url"):
        raise UnsafeUpgrade(f"{name} does not have a completed recovery backup")
    when = dt.datetime.fromisoformat(status["snapshotCreatedAt"].replace("Z", "+00:00"))
    if not dt.timedelta(0) <= now - when <= dt.timedelta(hours=24):
        raise UnsafeUpgrade(f"{name}'s recovery backup is older than 24 hours")


def preflight(items, name, target):
    health(items)
    volume = next(v for v in items if v["kind"] == "Volume" and v["metadata"]["name"] == name)
    old, new = version(volume["spec"]["image"]), version(target)
    if old[0] != new[0] or new < old or new[1] - old[1] not in (0, 1):
        raise UnsafeUpgrade("Downgrades, major changes and skipped minor versions are prohibited")
    if old == new:
        raise UnsafeUpgrade("This volume already uses the requested version")
    return volume


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("volume", help="Upgrade exactly this volume; no bulk option")
    parser.add_argument("--image", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    ds = get("daemonset", "longhorn-manager")
    s = ds["status"]
    if s.get("numberReady") != s.get("desiredNumberScheduled") or \
            s.get("updatedNumberScheduled") != s.get("desiredNumberScheduled") or \
            s.get("observedGeneration") != ds["metadata"]["generation"]:
        raise UnsafeUpgrade("Finish the manager rollout before upgrading engines")
    if get("settings.longhorn.io", "default-engine-image")["value"] != args.image or \
            get("settings.longhorn.io", "current-longhorn-version")["value"] != "v" + ".".join(map(str, version(args.image))):
        raise UnsafeUpgrade("The target must match the running manager and its default engine")
    if get("settings.longhorn.io", "concurrent-automatic-engine-upgrade-per-node-limit")["value"] != "0":
        raise UnsafeUpgrade("Disable automatic engine upgrades before serialized maintenance")
    items = get("volumes.longhorn.io,engines.longhorn.io,replicas.longhorn.io")["items"]
    volume = preflight(items, args.volume, args.image)
    backup_name = volume["status"].get("lastBackup")
    if not backup_name:
        raise UnsafeUpgrade("The volume has no recovery backup")
    backups = get("backups.longhorn.io")["items"]
    if any(b.get("status", {}).get("state") not in {"Completed", "Error"} for b in backups):
        raise UnsafeUpgrade("Finish running backups before engine maintenance")
    backup = next(b for b in backups if b["metadata"]["name"] == backup_name)
    fresh_backup(volume, backup, dt.datetime.now(dt.timezone.utc))
    image = next((e for e in get("engineimages.longhorn.io")["items"] if e["spec"]["image"] == args.image), None)
    nodes = {r["spec"]["nodeID"] for r in items if r["kind"] == "Replica" and r["spec"]["volumeName"] == args.volume and r["spec"].get("active")}
    nodes.add(volume["status"].get("currentNodeID"))
    nodes.discard(""); nodes.discard(None)
    if not image or image["status"].get("state") != "deployed" or \
            not all(image["status"].get("nodeDeploymentMap", {}).get(n) for n in nodes):
        raise UnsafeUpgrade("The target engine image is not deployed on every required node")
    print(f"Checked {args.volume}: {volume['spec']['image']} -> {args.image}; backup {backup_name}", flush=True)
    if not args.apply:
        print("Dry run: no changes made.")
        return
    path = "/api/v1/namespaces/longhorn-system/services/longhorn-backend:9500/proxy/v1/volumes/" + args.volume + "?action=engineUpgrade"
    run(["kubectl", "--request-timeout=20s", "create", "--raw", path, "-f", "-"], json.dumps({"image": args.image}))
    for attempt in range(150):
        current = get("volumes.longhorn.io,engines.longhorn.io,replicas.longhorn.io")["items"]
        v = next(o for o in current if o["kind"] == "Volume" and o["metadata"]["name"] == args.volume)
        if v["spec"]["image"] == args.image and v["status"].get("currentImage") == args.image:
            health(current)
            running = [o for o in current if o["kind"] in {"Engine", "Replica"} and o["spec"]["volumeName"] == args.volume and o["spec"].get("active") and o["status"].get("currentState") == "running"]
            if all(o["status"].get("currentImage") == args.image for o in running):
                print("Verified: engine upgrade completed; every attached volume retains required healthy replicas", flush=True)
                return
        # Do not initiate further changes if another volume loses its healthy copies.
        health([o for o in current if o["spec"].get("volumeName", o["metadata"]["name"]) != args.volume], check_images=False)
        if attempt % 10 == 0:
            print(f"Waiting for {args.volume}: {v['status']['state']}/{v['status']['robustness']}, image={v['status'].get('currentImage')}", flush=True)
        time.sleep(2)
    raise UnsafeUpgrade("Upgrade has not finished; stop and inspect it. Do not downgrade or upgrade another volume")


if __name__ == "__main__":
    try:
        main()
    except (UnsafeUpgrade, KeyError, ValueError, StopIteration, subprocess.TimeoutExpired) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        sys.exit(1)
