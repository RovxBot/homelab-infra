#!/usr/bin/env python3
"""Manually remove explicitly approved retired backup volumes via Longhorn."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time


def load_retention():
    spec = importlib.util.spec_from_file_location(
        "retention", Path(__file__).with_name("longhorn-retire-backup-history.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


retention = load_retention()
UnsafeRetention = retention.UnsafeRetention


def approved_points(plan):
    points = plan["retiredRecoveryPoints"]
    if plan.get("deletionAuthorized") is not True or not plan.get("authorization") or not points:
        raise UnsafeRetention("Explicit authorization for these last recovery points is required")
    if any(p["older"] for p in plan["plans"]) or plan["olderSystems"]:
        raise UnsafeRetention("Complete historical retention before retiring last recovery points")
    selected = {p["volume"]: p for p in points}
    reviewed = {p["volume"]: p for p in plan["plans"] if not p["live"]}
    if len(selected) != len(points) or set(selected) != set(reviewed):
        raise UnsafeRetention("Approval must cover the exact reviewed retired inventory")
    for volume, point in selected.items():
        original = reviewed[volume]
        if volume in plan["originalVolumes"] or any(point[k] != original[v] for k, v in [
                ("backup", "keep"), ("backupUID", "keepUID"),
                ("backupVolume", "backupVolumeName"), ("backupVolumeUID", "backupVolumeUID")]):
            raise UnsafeRetention("Approved retired recovery identity changed")
    return selected


def guard_claims(client, plan, selected):
    claims = []
    for resource, kind in [("persistentvolumes", "PersistentVolume"),
                           ("persistentvolumeclaims", "PersistentVolumeClaim")]:
        for obj in client.request("/api/v1/" + resource)["items"]:
            # Native list items omit TypeMeta; the typed API path supplies it.
            obj = copy.deepcopy(obj)
            obj.setdefault("kind", kind)
            claims.append(obj)
    def identity(obj):
        return obj["kind"], obj["metadata"].get("namespace", ""), obj["metadata"]["name"]
    current = {identity(o): o for o in claims}
    baseline = plan["originalClaims"]
    if len(baseline) != plan["originalClaimsVerified"] or len({identity(o) for o in baseline}) != len(baseline):
        raise UnsafeRetention("Original claim inventory is incomplete")
    for original in baseline:
        found = current.get(identity(original), {})
        if found.get("metadata", {}).get("uid") != original["metadata"]["uid"] or \
                found.get("status", {}).get("phase") != "Bound" or found["metadata"].get("deletionTimestamp"):
            raise UnsafeRetention("An original persistent claim changed or is not Bound")
    for claim in claims:
        if claim["metadata"]["name"] in selected or \
                claim.get("status", {}).get("volumeName") in selected or \
                claim.get("spec", {}).get("volumeName") in selected or \
                claim.get("spec", {}).get("csi", {}).get("volumeHandle") in selected:
            raise UnsafeRetention("A persistent claim still references a selected retired volume")


def check_plan(client, guards, plan):
    selected = approved_points(plan)
    if client.get("backuptargets", "default")["metadata"].get("deletionTimestamp"):
        raise UnsafeRetention("The backup target is being deleted")
    backup_volumes = client.get("backupvolumes")["items"]
    remaining = {v["spec"]["volumeName"] for v in backup_volumes}
    effective = copy.deepcopy(plan)
    effective["plans"] = [p for p in plan["plans"] if p["live"] or p["volume"] in remaining]
    retention.check_reviewed_plan(client, guards, effective)
    items = []
    for resource in ["volumes", "engines", "replicas"]:
        items.extend(client.get(resource)["items"])
    for item in items:
        if item["metadata"]["name"] in selected or item.get("spec", {}).get("volumeName") in selected:
            raise UnsafeRetention("A selected volume still has live storage resources")
        spec = item.get("spec", {})
        source = spec.get("dataSource") or {}
        if isinstance(source, dict) and source.get("volume") in selected or spec.get("cloneSourceVolume") in selected:
            raise UnsafeRetention("A current clone still references a retired volume")
    guard_claims(client, plan, selected)
    targets = []
    for volume in backup_volumes:
        status = volume.get("status", {})
        if status.get("linkedCloneSourceVolume") in selected:
            raise UnsafeRetention("A linked clone still references a retired backup volume")
        name = volume["spec"]["volumeName"]
        if name not in selected:
            continue
        if "longhorn.io" not in volume["metadata"].get("finalizers", []) or \
                any(key in volume["metadata"].get("labels", {}) for key in
                    ["delete-custom-resource-only", "longhorn.io/delete-custom-resource-only"]) or \
                status.get("messages"):
            raise UnsafeRetention("Native remote cleanup is not verified or reported a message")
        point = selected[name]
        backup = client.get("backups", point["backup"])
        if backup["metadata"].get("ownerReferences") != [{
                "apiVersion": "longhorn.io/v1beta2", "kind": "BackupVolume",
                "name": point["backupVolume"], "uid": point["backupVolumeUID"]}]:
            raise UnsafeRetention("Last backup must be owned by the exact retired backup volume")
        targets.append(backup)
    retention.guard_backup_references(targets, client.csi_snapshot_contents())
    return {v["spec"]["volumeName"]: v for v in backup_volumes if v["spec"]["volumeName"] in selected}


def wait_native(client, point):
    # Observing an already issued native operation never issues another DELETE.
    for _ in range(360):
        volume = client.get("backupvolumes", point["backupVolume"], missing_ok=True)
        backup = client.get("backups", point["backup"], missing_ok=True)
        for obj, uid in [(volume, point["backupVolumeUID"]), (backup, point["backupUID"])]:
            if obj and (obj["metadata"]["uid"] != uid or obj.get("status", {}).get("error") or
                        obj.get("status", {}).get("state") == "Error" or obj.get("status", {}).get("messages")):
                raise UnsafeRetention("Native retired-volume cleanup changed identity or reported an error")
        if volume is None and backup is None:
            print("Native retired-volume cleanup complete:", point["volume"], flush=True)
            return
        time.sleep(5)
    raise UnsafeRetention("Native cleanup is still pending; inspect or resume without forcing finalizers")


def retire(client, guards, plan, apply=False):
    selected = approved_points(plan)
    # Resume observation of an interrupted native deletion before full checks.
    for point in selected.values():
        volume = client.get("backupvolumes", point["backupVolume"], missing_ok=True)
        backup = client.get("backups", point["backup"], missing_ok=True)
        if volume and volume["metadata"].get("deletionTimestamp") or volume is None and backup:
            wait_native(client, point)
    pending = check_plan(client, guards, plan)
    print(f"Protect {len(plan['originalVolumes'])} current backups and the system archive; "
          f"retire {len(pending)} explicitly approved retired volumes", flush=True)
    if not apply:
        return
    for point in selected.values():
        pending = check_plan(client, guards, plan)
        volume = pending.get(point["volume"])
        if volume is None:
            continue
        # A fresh full identity, recovery, reference and claim check precedes
        # every exact UID/resourceVersion DELETE. Only Longhorn deletes objects.
        client.request(retention.BASE + "backupvolumes/" + point["backupVolume"], retention.delete_options(volume))
        wait_native(client, point)
    if check_plan(client, guards, plan):
        raise UnsafeRetention("Retired-volume cleanup has not converged")
    print(json.dumps({"retiredVolumesRemoved": len(selected), "protectedBackups": len(plan["originalVolumes"]),
                      "system": plan["system"], "originalClaimsVerified": plan["originalClaimsVerified"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewed-plan", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    retire(retention.Client(), retention.load_guards(), json.loads(args.reviewed_plan.read_text()), args.apply)


if __name__ == "__main__":
    try:
        main()
    except (UnsafeRetention, KeyError, ValueError, TypeError, subprocess.SubprocessError, OSError) as error:
        print("STOP:", error, file=sys.stderr)
        sys.exit(1)
