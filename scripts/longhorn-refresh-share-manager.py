#!/usr/bin/env python3
"""Refresh one healthy RWX export to its current Longhorn share-manager image."""
import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time

class UnsafeRefresh(RuntimeError):
    pass

def guards():
    spec = importlib.util.spec_from_file_location("engine_guards", Path(__file__).with_name("longhorn-upgrade-engines.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def run(command, payload=None):
    result = subprocess.run(command, input=payload, capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise UnsafeRefresh(f"{command[0]} {command[1]} failed (exit {result.returncode})")
    return result.stdout

def get(resource, name=None, namespace="longhorn-system"):
    command = ["kubectl", "--request-timeout=20s", "get", resource]
    command += ["-n", namespace] if namespace else ["--all-namespaces"]
    if name:
        command.append(name)
    return json.loads(run(command + ["-o", "json"]))

def ready(pod):
    return pod["status"].get("phase") == "Running" and not pod["metadata"].get("deletionTimestamp") and \
        any(c["type"] == "Ready" and c["status"] == "True" for c in pod["status"].get("conditions", []))

def image_version(image):
    match = re.fullmatch(r"(?:docker.io/)?longhornio/longhorn-share-manager:v(\d+)\.(\d+)\.(\d+)", image)
    if not match:
        raise UnsafeRefresh("Expected a stable official Longhorn share-manager image")
    return tuple(map(int, match.groups()))

def configured_share_image(daemonset):
    containers = [c for c in daemonset["spec"]["template"]["spec"]["containers"] if c["name"] == "longhorn-manager"]
    if len(containers) != 1:
        raise UnsafeRefresh("Require one configured Longhorn manager container")
    container = containers[0]
    command = container.get("command", []) + container.get("args", [])
    images = []
    for index, arg in enumerate(command):
        if arg == "--share-manager-image":
            if index + 1 >= len(command):
                raise UnsafeRefresh("The manager share-image argument has no value")
            images.append(command[index + 1])
        elif arg.startswith("--share-manager-image="):
            images.append(arg.split("=", 1)[1])
    if len(images) != 1:
        raise UnsafeRefresh("Require one explicit manager share-image argument")
    version = image_version(images[0])
    manager = re.fullmatch(r"(?:docker.io/)?longhornio/longhorn-manager:v(\d+)\.(\d+)\.(\d+)", container["image"])
    if not manager or tuple(map(int, manager.groups())) != version:
        raise UnsafeRefresh("Configured manager and share-manager versions differ")
    return images[0]

def check_export(volume, manager, pod, target):
    name = volume["metadata"]["name"]
    if volume["spec"]["accessMode"] != "rwx" or manager["metadata"]["name"] != name or manager["spec"].get("image") != target:
        raise UnsafeRefresh("The RWX volume or desired share-manager identity changed")
    endpoint = volume["status"].get("shareEndpoint")
    if manager["status"].get("state") != "running" or not endpoint or manager["status"].get("endpoint") != endpoint:
        raise UnsafeRefresh("The existing export must be running at the volume's stable endpoint")
    if not ready(pod) or pod["metadata"]["name"] != "share-manager-" + name or \
            not any(o.get("kind") == "ShareManager" and o.get("uid") == manager["metadata"]["uid"] and o.get("controller")
                    for o in pod["metadata"].get("ownerReferences", [])) or len(pod["spec"]["containers"]) != 1:
        raise UnsafeRefresh("The existing ready pod must be owned by this exact ShareManager")
    old = pod["spec"]["containers"][0]["image"]
    if image_version(old)[0] != image_version(target)[0] or image_version(old) >= image_version(target):
        raise UnsafeRefresh("No forward share-manager refresh is required")
    return endpoint

def consumers(volume, resources):
    name = volume["metadata"]["name"]
    pvs = [o for o in resources if o["kind"] == "PersistentVolume" and
           o.get("spec", {}).get("csi", {}).get("driver") == "driver.longhorn.io" and
           o["spec"]["csi"].get("volumeHandle") == name]
    if not pvs:
        raise UnsafeRefresh("The RWX volume has no original CSI PersistentVolume")
    claims = []
    for pv in pvs:
        if pv["status"].get("phase") != "Bound":
            raise UnsafeRefresh("The original PV is not Bound")
        ref = pv["spec"]["claimRef"]
        matches = [o for o in resources if o["kind"] == "PersistentVolumeClaim" and
                   o["metadata"]["namespace"] == ref["namespace"] and o["metadata"]["name"] == ref["name"] and
                   o["metadata"]["uid"] == ref["uid"] and o["spec"].get("volumeName") == pv["metadata"]["name"] and
                   o["status"].get("phase") == "Bound"]
        if len(matches) != 1:
            raise UnsafeRefresh("The original PVC identity or binding changed")
        claims.append((ref["namespace"], ref["name"], ref["uid"], pv["metadata"]["uid"]))
    clients = []
    for pod in (o for o in resources if o["kind"] == "Pod" and o["status"].get("phase") not in {"Succeeded", "Failed"}):
        names = {v.get("persistentVolumeClaim", {}).get("claimName") for v in pod["spec"].get("volumes", [])}
        if any(ns == pod["metadata"]["namespace"] and claim in names for ns, claim, _, _ in claims):
            if not ready(pod):
                raise UnsafeRefresh("An original NFS client is unready or terminating")
            clients.append((pod["metadata"]["namespace"], pod["metadata"]["name"], pod["metadata"]["uid"]))
    if not clients:
        raise UnsafeRefresh("The original RWX export has no ready workload clients")
    return set(claims), set(clients)

def check_nfs_mounts(volume, resources):
    claims, clients = consumers(volume, resources)
    probes = 0
    for pod in (p for p in resources if p["kind"] == "Pod" and
                (p["metadata"]["namespace"], p["metadata"]["name"], p["metadata"]["uid"]) in clients):
        namespace = pod["metadata"]["namespace"]
        names = {v["name"] for v in pod["spec"].get("volumes", []) if
                 (namespace, v.get("persistentVolumeClaim", {}).get("claimName")) in
                 {(ns, name) for ns, name, _, _ in claims}}
        mounted = False
        for container in pod["spec"]["containers"]:
            for mount in container.get("volumeMounts", []):
                if mount["name"] not in names:
                    continue
                output = run(["kubectl", "--request-timeout=20s", "exec", "-n", namespace,
                              pod["metadata"]["name"], "-c", container["name"], "--",
                              "stat", "-f", "-c", "%T", mount["mountPath"]])
                if output.strip() not in {"nfs", "nfs4"}:
                    raise UnsafeRefresh("An original client mount is not a responsive NFS filesystem")
                mounted = True
                probes += 1
        if not mounted:
            raise UnsafeRefresh("An original client has no verifiable NFS mount")
    print(f"Verified {probes} original NFS client mounts with read-only filesystem probes", flush=True)

def recovery_guard(items, backups, engine, default, now):
    engine.health(items)
    if any(b.get("status", {}).get("state") != "Completed" or b["metadata"].get("deletionTimestamp") for b in backups):
        raise UnsafeRefresh("Finish backup creation and deletion first")
    by_name = {b["metadata"]["name"]: b for b in backups}
    for volume in (o for o in items if o["kind"] == "Volume"):
        if not engine.upgrade_complete(items, volume["metadata"]["name"], default):
            raise UnsafeRefresh("Complete every volume engine and replica process upgrade before refreshing exports")
        backup = by_name.get(volume["status"].get("lastBackup"))
        if backup is None:
            raise UnsafeRefresh("Every original volume requires a fresh completed recovery backup")
        try:
            engine.fresh_backup(volume, backup, now)
        except engine.UnsafeUpgrade as error:
            raise UnsafeRefresh(str(error)) from None

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("volume")
    parser.add_argument("--image", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    engine = guards()
    manager_ds = get("daemonset", "longhorn-manager")
    s = manager_ds["status"]
    if s.get("numberReady") != s.get("desiredNumberScheduled") or s.get("updatedNumberScheduled") != s.get("desiredNumberScheduled") or \
            s.get("observedGeneration") != manager_ds["metadata"]["generation"]:
        raise UnsafeRefresh("Finish the manager rollout first")
    default = get("settings.longhorn.io", "default-engine-image")["value"]
    current = get("settings.longhorn.io", "current-longhorn-version")["value"]
    if configured_share_image(manager_ds) != args.image or \
            image_version(args.image) != engine.version(default) or current != "v" + ".".join(map(str, image_version(args.image))):
        raise UnsafeRefresh("The target must match the running managers and their default engine")
    items = get("volumes.longhorn.io,engines.longhorn.io,replicas.longhorn.io")["items"]
    volume = next(v for v in items if v["kind"] == "Volume" and v["metadata"]["name"] == args.volume)
    backups = get("backups.longhorn.io")["items"]
    recovery_guard(items, backups, engine, default, dt.datetime.now(dt.timezone.utc))
    manager = get("sharemanagers.longhorn.io", args.volume)
    pod_name = "share-manager-" + args.volume
    pod = get("pod", pod_name)
    endpoint = check_export(volume, manager, pod, args.image)
    original_resources = get("pv,pvc,pod", namespace=None)["items"]
    original_claims, original_clients = consumers(volume, original_resources)
    check_nfs_mounts(volume, original_resources)
    print(f"Checked {args.volume}: refresh {pod_name} to {args.image}; original clients {len(original_clients)}", flush=True)
    if not args.apply:
        print("Dry run: no changes made")
        return
    # Client probes can take time. Recheck recovery and the exact export and
    # client identities immediately before restarting the NFS server.
    latest_items = get("volumes.longhorn.io,engines.longhorn.io,replicas.longhorn.io")["items"]
    recovery_guard(latest_items, get("backups.longhorn.io")["items"], engine, default, dt.datetime.now(dt.timezone.utc))
    latest_volume = next(v for v in latest_items if v["kind"] == "Volume" and v["metadata"]["name"] == args.volume)
    latest_manager = get("sharemanagers.longhorn.io", args.volume)
    latest_pod = get("pod", pod_name)
    if latest_volume["metadata"]["uid"] != volume["metadata"]["uid"] or \
            latest_manager["metadata"]["uid"] != manager["metadata"]["uid"] or \
            latest_pod["metadata"]["uid"] != pod["metadata"]["uid"] or \
            check_export(latest_volume, latest_manager, latest_pod, args.image) != endpoint or \
            consumers(latest_volume, get("pv,pvc,pod", namespace=None)["items"]) != (original_claims, original_clients):
        raise UnsafeRefresh("The original export or client identities changed before the restart")
    options = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions":
               {"uid": pod["metadata"]["uid"], "resourceVersion": pod["metadata"]["resourceVersion"]}}
    run(["kubectl", "--request-timeout=20s", "delete", "--raw", "/api/v1/namespaces/longhorn-system/pods/" + pod_name, "-f", "-"], json.dumps(options))
    for _ in range(180):
        current_items = get("volumes.longhorn.io,engines.longhorn.io,replicas.longhorn.io")["items"]
        engine.health([o for o in current_items if o["spec"].get("volumeName", o["metadata"]["name"]) != args.volume])
        current_volume = next(v for v in current_items if v["kind"] == "Volume" and v["metadata"]["name"] == args.volume)
        if current_volume["metadata"]["uid"] != volume["metadata"]["uid"]:
            raise UnsafeRefresh("The original volume identity changed")
        manager_now = get("sharemanagers.longhorn.io", args.volume)
        if manager_now["metadata"]["uid"] != manager["metadata"]["uid"]:
            raise UnsafeRefresh("The original ShareManager identity changed")
        pods = get("pods")["items"]
        new = next((p for p in pods if p["metadata"]["name"] == pod_name and p["metadata"]["uid"] != pod["metadata"]["uid"]), None)
        if new and ready(new) and new["spec"]["containers"][0]["image"] == args.image and \
                any(o.get("uid") == manager["metadata"]["uid"] and o.get("controller") for o in new["metadata"].get("ownerReferences", [])) and \
                manager_now["status"].get("state") == "running" and manager_now["status"].get("endpoint") == endpoint and current_volume["status"].get("shareEndpoint") == endpoint:
            try:
                engine.health(current_items)
                resources_now = get("pv,pvc,pod", namespace=None)["items"]
                claims_now, clients_now = consumers(current_volume, resources_now)
            except (engine.UnsafeUpgrade, UnsafeRefresh):
                time.sleep(2)
                continue
            if claims_now != original_claims or clients_now != original_clients:
                raise UnsafeRefresh("An original claim, PV or client pod identity changed")
            check_nfs_mounts(current_volume, resources_now)
            backups_now = get("backups.longhorn.io")["items"]
            recovery_guard(current_items, backups_now, engine, default, dt.datetime.now(dt.timezone.utc))
            print("Verified: current share-manager image, original endpoint/volume/claims, ready clients and healthy replicas", flush=True)
            return
        time.sleep(2)
    raise UnsafeRefresh("Export refresh is incomplete; stop before changing another export")

if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError, ValueError, StopIteration, subprocess.SubprocessError) as error:
        print("STOP:", error, file=sys.stderr)
        sys.exit(1)
