#!/usr/bin/env python3
"""Retire older remote recovery points through Longhorn's guarded native deletion."""
import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

class UnsafeRetention(RuntimeError):
    pass

NAMESPACE = "longhorn-system"
BASE = "/apis/longhorn.io/v1beta2/namespaces/" + NAMESPACE + "/"
DESTINATION = "s3://cooked-k8s@us-east-005/longhorn"
CSI_CONTENTS = "/apis/snapshot.storage.k8s.io/v1/volumesnapshotcontents"

def load_guards():
    path = Path(__file__).with_name("longhorn-upgrade-engines.py")
    spec = importlib.util.spec_from_file_location("engine_guards", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class Client:
    def __init__(self):
        directory = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        self.token_path = directory / "token"
        self.in_cluster = self.token_path.exists()
        if self.in_cluster:
            self.context = ssl.create_default_context(cafile=str(directory / "ca.crt"))

    def request(self, path, body=None, missing_ok=False):
        method = "GET" if body is None else "DELETE"
        if self.in_cluster:
            request = urllib.request.Request("https://kubernetes.default.svc" + path,
                headers={"Authorization": "Bearer " + self.token_path.read_text().strip(),
                         "Content-Type": "application/json"},
                data=None if body is None else json.dumps(body).encode(), method=method)
            try:
                with urllib.request.urlopen(request, context=self.context, timeout=30) as result:
                    return json.load(result)
            except urllib.error.HTTPError as error:
                if missing_ok and error.code == 404:
                    return None
                raise UnsafeRetention(f"Kubernetes {method} failed with HTTP {error.code}") from None
        command = ["kubectl", "--request-timeout=20s", "get" if body is None else "delete", "--raw", path]
        if body is not None:
            command += ["-f", "-"]
        result = subprocess.run(command, input=None if body is None else json.dumps(body),
                                capture_output=True, text=True, timeout=45)
        if result.returncode:
            if missing_ok and "(NotFound)" in result.stderr:
                return None
            raise UnsafeRetention(f"Kubernetes {method} failed (exit {result.returncode})")
        return json.loads(result.stdout)

    def get(self, resource, name=None, missing_ok=False):
        return self.request(BASE + resource + ("/" + name if name else ""), missing_ok=missing_ok)

    def csi_snapshot_contents(self):
        result = self.request(CSI_CONTENTS, missing_ok=True)
        return [] if result is None else result["items"]

def guard_backup_references(targets, contents):
    identities = {(b["status"]["volumeName"], b["metadata"]["name"]) for b in targets}
    volumes = {v for v, _ in identities}
    urls = {b["status"]["url"] for b in targets}
    for content in contents:
        spec = content.get("spec", {})
        if spec.get("driver") != "driver.longhorn.io":
            continue
        source = spec.get("source", {})
        handles = [source.get("snapshotHandle"), content.get("status", {}).get("snapshotHandle")]
        if not any(handles) and source.get("volumeHandle") in volumes:
            raise UnsafeRetention("An unresolved CSI snapshot still uses the selected volume")
        for handle in filter(None, handles):
            parsed = urllib.parse.urlparse(handle)
            identity = (parsed.netloc, parsed.path.lstrip("/"))
            # bak is current; bs is Longhorn's supported legacy backup handle.
            if handle in urls or parsed.scheme in {"bak", "bs"} and identity in identities:
                raise UnsafeRetention("A CSI VolumeSnapshotContent still references an older backup")

def complete_backup(backup, volume):
    status = backup.get("status", {})
    if backup["metadata"].get("deletionTimestamp") or status.get("state") != "Completed" or \
            status.get("progress") != 100 or status.get("error") or status.get("volumeName") != volume or \
            backup["metadata"].get("labels", {}).get("backup-target") != "default" or \
            backup["metadata"].get("labels", {}).get("backup-volume") != volume:
        raise UnsafeRetention("Backup identity, target or completion is not verified")
    url = urllib.parse.urlparse(status.get("url", ""))
    destination = urllib.parse.urlparse(DESTINATION)
    if (url.scheme, url.netloc, url.path) != (destination.scheme, destination.netloc, destination.path) or \
            urllib.parse.parse_qs(url.query) != {"backup": [backup["metadata"]["name"]], "volume": [volume]}:
        raise UnsafeRetention("Backup URL does not match its exact volume, name and destination")
    return status

def retention_plan(backups, backup_volume, expected_latest=None):
    volume = backup_volume["spec"]["volumeName"]
    if backup_volume["spec"]["backupTargetName"] != "default" or backup_volume["metadata"].get("deletionTimestamp"):
        raise UnsafeRetention("Backup volume target changed or the backup volume is being deleted")
    candidates = [b for b in backups if b.get("status", {}).get("volumeName") == volume]
    if not candidates:
        raise UnsafeRetention("No completed recovery point exists for the backup volume")
    for backup in candidates:
        complete_backup(backup, volume)
        owners = backup["metadata"].get("ownerReferences", [])
        # Legacy imported backup CRs may have no owner reference. Their exact
        # canonical URL, target and volume labels remain mandatory above.
        if any(owner.get("kind") != "BackupVolume" or owner.get("uid") != backup_volume["metadata"]["uid"] or
               owner.get("name") != backup_volume["metadata"]["name"] for owner in owners):
            raise UnsafeRetention("Backup owner identity does not match the backup volume")
    candidates.sort(key=lambda b: (b["status"]["backupCreatedAt"], b["status"]["snapshotCreatedAt"], b["metadata"]["name"]))
    keep = candidates[-1]
    if keep["metadata"]["name"] != backup_volume["status"].get("lastBackupName") or \
            expected_latest and keep["metadata"]["name"] != expected_latest:
        raise UnsafeRetention("The latest recovery point changed; review the retention plan again")
    return keep, candidates[:-1]

def delete_options(obj):
    return {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions":
            {"uid": obj["metadata"]["uid"], "resourceVersion": obj["metadata"]["resourceVersion"]}}

def global_state(client, guards):
    manager = client.request("/apis/apps/v1/namespaces/longhorn-system/daemonsets/longhorn-manager")
    status = manager["status"]
    if status.get("numberReady") != status.get("desiredNumberScheduled") or \
            status.get("updatedNumberScheduled") != status.get("desiredNumberScheduled") or \
            status.get("observedGeneration") != manager["metadata"]["generation"]:
        raise UnsafeRetention("The manager rollout is incomplete")
    target = client.get("backuptargets", "default")
    if target["spec"]["backupTargetURL"] != DESTINATION or target["spec"]["credentialSecret"] != "longhorn-b2-credentials" or \
            target.get("status", {}).get("available") is not True or \
            any(c["type"] == "Unavailable" and c["status"] == "True" for c in target.get("status", {}).get("conditions", [])):
        raise UnsafeRetention("The reviewed backup destination is unavailable or changed")
    if client.get("settings", "auto-cleanup-when-delete-backup")["value"] != "false":
        raise UnsafeRetention("Remote retention must not automatically delete local snapshots")
    systems = client.get("systembackups")["items"]
    if not systems:
        raise UnsafeRetention("A completed system recovery archive is required")
    newest_system = max(systems, key=lambda s: s["metadata"]["creationTimestamp"])
    if newest_system.get("status", {}).get("state") != "Ready" or \
            newest_system["spec"].get("volumeBackupPolicy") != "always" or newest_system["metadata"].get("deletionTimestamp"):
        raise UnsafeRetention("The newest system backup must complete its volume data and archive first")
    image = client.get("settings", "default-engine-image")["value"]
    now = dt.datetime.now(dt.timezone.utc)
    created = dt.datetime.fromisoformat(newest_system["metadata"]["creationTimestamp"].replace("Z", "+00:00"))
    manager_version = "v" + ".".join(map(str, guards.version(image)))
    if not dt.timedelta(0) <= now - created <= dt.timedelta(hours=24) or \
            newest_system["status"].get("version") != manager_version:
        raise UnsafeRetention("A fresh system archive of the current Longhorn version is required")
    items = []
    for resource in ["volumes", "engines", "replicas", "backups"]:
        items.extend(client.get(resource)["items"])
    try:
        guards.health(items)
    except guards.UnsafeUpgrade as error:
        raise UnsafeRetention(str(error)) from None
    backups = {o["metadata"]["name"]: o for o in items if o["kind"] == "Backup"}
    if any(b["metadata"].get("deletionTimestamp") or b.get("status", {}).get("state") != "Completed" for b in backups.values()):
        raise UnsafeRetention("Finish all backup creation or deletion operations first")
    for volume in (o for o in items if o["kind"] == "Volume"):
        if not guards.upgrade_complete(items, volume["metadata"]["name"], image):
            raise UnsafeRetention("Complete all engine upgrades before remote retention")
        backup = backups.get(volume["status"].get("lastBackup"))
        if not backup:
            raise UnsafeRetention("A current volume has no completed recovery point")
        complete_backup(backup, volume["metadata"]["name"])
        try:
            guards.fresh_backup(volume, backup, now)
        except guards.UnsafeUpgrade as error:
            raise UnsafeRetention(str(error)) from None
    return items

def retire_volume(client, guards, volume, expected_latest, apply=False):
    items = global_state(client, guards)
    backup_volumes = [v for v in client.get("backupvolumes")["items"] if v["spec"]["volumeName"] == volume]
    if len(backup_volumes) != 1:
        raise UnsafeRetention("Expected one reviewed backup volume")
    backup_volume = backup_volumes[0]
    keep, older = retention_plan([o for o in items if o["kind"] == "Backup"], backup_volume, expected_latest)
    live = next((o for o in items if o["kind"] == "Volume" and o["metadata"]["name"] == volume), None)
    if live and live["status"].get("lastBackup") != keep["metadata"]["name"]:
        raise UnsafeRetention("The current volume does not reference the retained recovery point")
    print(f"{volume}: keep {keep['metadata']['name']}; retire {len(older)} older remote recovery points", flush=True)
    guard_backup_references(older, client.csi_snapshot_contents())
    if not apply:
        return
    for backup in older:
        # Every deletion is guarded anew; a changed keeper or unrelated operation
        # stops the series. Controllers alone handle locks and shared-block GC.
        current_items = global_state(client, guards)
        current_volume = client.get("backupvolumes", backup_volume["metadata"]["name"])
        latest, _ = retention_plan([o for o in current_items if o["kind"] == "Backup"], current_volume, keep["metadata"]["name"])
        if latest["metadata"]["uid"] != keep["metadata"]["uid"] or current_volume["metadata"]["uid"] != backup_volume["metadata"]["uid"]:
            raise UnsafeRetention("Retained recovery identity changed")
        old = client.get("backups", backup["metadata"]["name"])
        if old["metadata"]["uid"] != backup["metadata"]["uid"]:
            raise UnsafeRetention("Historical backup identity changed")
        complete_backup(old, volume)
        guard_backup_references([old], client.csi_snapshot_contents())
        client.request(BASE + "backups/" + old["metadata"]["name"], delete_options(old))
        for _ in range(180):
            pending = client.get("backups", old["metadata"]["name"], missing_ok=True)
            if pending is None:
                break
            if pending.get("status", {}).get("error") or pending.get("status", {}).get("state") == "Error":
                raise UnsafeRetention("Native backup deletion reported an error; stop")
            time.sleep(5)
        else:
            raise UnsafeRetention("Native deletion is incomplete; inspect it before further changes")
        print("Native deletion complete:", old["metadata"]["name"], flush=True)
    final_items = global_state(client, guards)
    final_volume = client.get("backupvolumes", backup_volume["metadata"]["name"])
    final_keep, remaining = retention_plan([o for o in final_items if o["kind"] == "Backup"], final_volume, keep["metadata"]["name"])
    if remaining or final_keep["metadata"]["uid"] != keep["metadata"]["uid"]:
        raise UnsafeRetention("Retention did not converge to the protected recovery point")
    print("Verified: newest recovery point preserved; older backups retired by native controllers", flush=True)

def system_plan(systems, expected_latest=None):
    if not systems:
        raise UnsafeRetention("No system recovery archive exists")
    ordered = sorted(systems, key=lambda s: s["metadata"]["creationTimestamp"])
    if any(s.get("status", {}).get("state") != "Ready" or s["metadata"].get("deletionTimestamp") for s in ordered):
        raise UnsafeRetention("Finish system backup creation or deletion first")
    keep = ordered[-1]
    if keep["spec"].get("volumeBackupPolicy") != "always" or expected_latest and keep["metadata"]["name"] != expected_latest:
        raise UnsafeRetention("The newest complete data-and-configuration archive changed")
    return keep, ordered[:-1]

def retire_systems(client, guards, expected_latest, apply=False):
    global_state(client, guards)
    keep, older = system_plan(client.get("systembackups")["items"], expected_latest)
    print(f"Keep system archive {keep['metadata']['name']}; retire {len(older)} older archives", flush=True)
    if not apply:
        return
    for system in older:
        global_state(client, guards)
        latest, _ = system_plan(client.get("systembackups")["items"], keep["metadata"]["name"])
        if latest["metadata"]["uid"] != keep["metadata"]["uid"]:
            raise UnsafeRetention("Retained system recovery identity changed")
        old = client.get("systembackups", system["metadata"]["name"])
        if old["metadata"]["uid"] != system["metadata"]["uid"]:
            raise UnsafeRetention("Historical system archive identity changed")
        client.request(BASE + "systembackups/" + old["metadata"]["name"], delete_options(old))
        for _ in range(180):
            pending = client.get("systembackups", old["metadata"]["name"], missing_ok=True)
            if pending is None:
                break
            if pending.get("status", {}).get("state") == "Error":
                raise UnsafeRetention("Native system archive deletion reported an error")
            time.sleep(5)
        else:
            raise UnsafeRetention("Native system archive deletion is incomplete; stop")
    final_keep, remaining = system_plan(client.get("systembackups")["items"], keep["metadata"]["name"])
    if remaining or final_keep["metadata"]["uid"] != keep["metadata"]["uid"]:
        raise UnsafeRetention("System archive retention did not converge")
    global_state(client, guards)
    print("Verified: newest system archive and current volume recovery points preserved", flush=True)

def after_weekly_backup(client, guards, apply=False):
    # Wait on Kubernetes status only. No bucket inventory, secret access or
    # backup-data reads occur here. The native system backup owns uploads.
    selected = None
    for _ in range(2400):
        systems = client.get("systembackups")["items"]
        candidates = [s for s in systems if s["metadata"].get("labels", {}).get(
            "recurring-job.longhorn.io/system-backup") == "default-weekly-system-backup"]
        if not candidates:
            raise UnsafeRetention("No weekly system backup exists")
        selected = max(candidates, key=lambda s: s["metadata"]["creationTimestamp"])
        created = dt.datetime.fromisoformat(selected["metadata"]["creationTimestamp"].replace("Z", "+00:00"))
        age = dt.datetime.now(dt.timezone.utc) - created
        if not dt.timedelta(0) <= age <= dt.timedelta(hours=24):
            raise UnsafeRetention("This week's fresh system backup has not started")
        if selected["spec"].get("volumeBackupPolicy") != "always" or selected["metadata"].get("deletionTimestamp"):
            raise UnsafeRetention("The weekly data-and-configuration recovery policy changed")
        state = selected.get("status", {}).get("state")
        if state == "Error":
            raise UnsafeRetention("Weekly backup failed; preserve all earlier recovery points")
        if state == "Ready":
            break
        time.sleep(30)
    else:
        raise UnsafeRetention("Weekly backup is incomplete; preserve all earlier recovery points")
    items = global_state(client, guards)
    newest_system = max(client.get("systembackups")["items"], key=lambda s: s["metadata"]["creationTimestamp"])
    if newest_system["metadata"]["uid"] != selected["metadata"]["uid"]:
        raise UnsafeRetention("Another maintenance backup superseded this week's archive; review manually")
    captured_after = selected["metadata"]["creationTimestamp"]
    backups = {b["metadata"]["name"]: b for b in items if b["kind"] == "Backup"}
    volumes = sorted([v for v in items if v["kind"] == "Volume"], key=lambda v: v["metadata"]["name"])
    for volume in volumes:
        backup = backups[volume["status"]["lastBackup"]]
        if backup["status"]["snapshotCreatedAt"] < captured_after:
            raise UnsafeRetention("A volume did not receive a fresh recovery point in this weekly backup")
    for volume in volumes:
        retire_volume(client, guards, volume["metadata"]["name"], volume["status"]["lastBackup"], apply)
    retire_systems(client, guards, selected["metadata"]["name"], apply)

def check_reviewed_plan(client, guards, plan):
    """Accept partial progress, but never a new identity or recovery point."""
    items = global_state(client, guards)
    volumes = {o["metadata"]["name"]: o for o in items if o["kind"] == "Volume"}
    if not volumes or {name: v["metadata"]["uid"] for name, v in volumes.items()} != plan["originalVolumes"]:
        raise UnsafeRetention("Original volume identities changed")
    proof = plan["restoreVerification"]
    if proof.get("verificationCopyRemoved") is not True or proof.get("cloneHealthyCopies") != 3 or \
            proof.get("originalClaimsVerified", 0) != plan["originalClaimsVerified"] or \
            plan["originalClaimsVerified"] <= 0 or "SQLite integrity: ok" not in proof.get("sqliteIntegrity", ""):
        raise UnsafeRetention("A successful isolated restore and original-claim verification are required")
    plans = {p["volume"]: p for p in plan["plans"]}
    if len(plans) != len(plan["plans"]) or {p["volume"] for p in plans.values() if p["live"]} != set(volumes):
        raise UnsafeRetention("The reviewed plan must cover all original live volumes exactly once")
    source = plans[proof["sourceVolume"]]
    if plan["originalVolumes"].get(proof["sourceVolume"]) != proof["sourceVolumeUID"] or \
            (source["keep"], source["keepUID"]) != (proof["sourceBackup"], proof["sourceBackupUID"]):
        raise UnsafeRetention("The retained source backup must match the successful restore")
    backup_volumes = client.get("backupvolumes")["items"]
    if len(backup_volumes) != len(plans) or {v["spec"]["volumeName"] for v in backup_volumes} != set(plans):
        raise UnsafeRetention("The reviewed backup-volume inventory changed")
    backups = [o for o in items if o["kind"] == "Backup"]
    covered = set()
    approved = {}
    targets = []
    for bv in backup_volumes:
        p = plans[bv["spec"]["volumeName"]]
        if (bv["metadata"]["name"], bv["metadata"]["uid"]) != (p["backupVolumeName"], p["backupVolumeUID"]):
            raise UnsafeRetention("Reviewed backup-volume identity changed")
        keep, older = retention_plan(backups, bv, p["keep"])
        if keep["metadata"]["uid"] != p["keepUID"] or \
                p["volume"] in volumes and volumes[p["volume"]]["status"].get("lastBackup") != p["keep"]:
            raise UnsafeRetention("Reviewed protected recovery identity changed")
        covered.add(keep["metadata"]["name"])
        for old in older:
            name = old["metadata"]["name"]
            if p["older"].get(name) != old["metadata"]["uid"]:
                raise UnsafeRetention("An older recovery point was not included in the reviewed plan")
            covered.add(name)
            approved[BASE + "backups/" + name] = old["metadata"]["uid"]
        targets.extend(older)
    if covered != {b["metadata"]["name"] for b in backups}:
        raise UnsafeRetention("An unreviewed backup exists outside the plan")
    guard_backup_references(targets, client.csi_snapshot_contents())
    keep_system, older_systems = system_plan(client.get("systembackups")["items"], plan["system"]["name"])
    if keep_system["metadata"]["uid"] != plan["system"]["uid"]:
        raise UnsafeRetention("Reviewed system recovery identity changed")
    for old in older_systems:
        name = old["metadata"]["name"]
        if plan["olderSystems"].get(name) != old["metadata"]["uid"]:
            raise UnsafeRetention("An older system archive was not included in the reviewed plan")
        approved[BASE + "systembackups/" + name] = old["metadata"]["uid"]
    return approved

def retire_reviewed_plan(client, guards, plan, apply=False):
    # No new privileges: this proxy permits only native historical backup
    # deletions whose identities survive a fresh full-plan check at each DELETE.
    class ReviewedClient:
        def get(self, *args, **kwargs):
            return client.get(*args, **kwargs)

        def csi_snapshot_contents(self):
            return client.csi_snapshot_contents()

        def request(self, path, body=None, missing_ok=False):
            if body is not None:
                approved = check_reviewed_plan(client, guards, plan)
                if not apply or path not in approved or body.get("preconditions", {}).get("uid") != approved[path]:
                    raise UnsafeRetention("Deletion is outside the exact reviewed historical identities")
            return client.request(path, body, missing_ok)

    approved = check_reviewed_plan(client, guards, plan)
    print(f"Reviewed plan: protect {len(plan['plans'])} volume backups; retire {len(approved)} historical objects", flush=True)
    reviewed = ReviewedClient()
    for index, p in enumerate(plan["plans"], 1):
        check_reviewed_plan(client, guards, plan)
        print(f"Reviewed volume {index}/{len(plan['plans'])}: {p['volume']}", flush=True)
        retire_volume(reviewed, guards, p["volume"], p["keep"], apply)
    retire_systems(reviewed, guards, plan["system"]["name"], apply)
    remaining = check_reviewed_plan(client, guards, plan)
    if apply and remaining:
        raise UnsafeRetention("Reviewed retention has not converged")
    print(json.dumps({"reviewedPlanComplete": bool(apply), "protectedBackups": len(plan["plans"]),
                      "system": plan["system"], "restoreVerified": True}), flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("volume", nargs="?")
    parser.add_argument("--system-archives", action="store_true")
    parser.add_argument("--after-weekly-system-backup", action="store_true")
    parser.add_argument("--reviewed-plan", type=Path, help="Immutable reviewed identity plan with successful restore proof")
    parser.add_argument("--expected-latest")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if sum([bool(args.volume), args.system_archives, args.after_weekly_system_backup, bool(args.reviewed_plan)]) != 1:
        raise UnsafeRetention("Select exactly one volume or the system archives")
    if args.volume and not re.fullmatch(r"pvc-[a-f0-9-]{36}", args.volume):
        raise UnsafeRetention("Use the exact reviewed PVC-backed Longhorn volume identity")
    if args.apply and not args.expected_latest and not args.after_weekly_system_backup and not args.reviewed_plan:
        raise UnsafeRetention("Apply requires the latest backup name from a reviewed dry run")
    if args.reviewed_plan:
        retire_reviewed_plan(Client(), load_guards(), json.loads(args.reviewed_plan.read_text()), args.apply)
    elif args.after_weekly_system_backup:
        after_weekly_backup(Client(), load_guards(), args.apply)
    elif args.system_archives:
        retire_systems(Client(), load_guards(), args.expected_latest, args.apply)
    else:
        retire_volume(Client(), load_guards(), args.volume, args.expected_latest, args.apply)

if __name__ == "__main__":
    try:
        main()
    except (UnsafeRetention, KeyError, ValueError, StopIteration, subprocess.SubprocessError, OSError) as error:
        print("STOP:", error, file=sys.stderr)
        sys.exit(1)
