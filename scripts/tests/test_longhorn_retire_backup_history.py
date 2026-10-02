import copy
import datetime as dt
import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location("retention", pathlib.Path(__file__).resolve().parents[1] / "longhorn-retire-backup-history.py")
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)

VOLUME = "pvc-11111111-1111-1111-1111-111111111111"

def fixtures():
    now = dt.datetime.now(dt.timezone.utc)
    def timestamp(offset): return (now - dt.timedelta(hours=offset)).isoformat().replace("+00:00", "Z")
    backup_volume = {"metadata": {"name": "backup-volume", "uid": "bv-uid"},
                     "spec": {"volumeName": VOLUME, "backupTargetName": "default"},
                     "status": {"lastBackupName": "newest"}}
    backups = []
    for name, age in [("old", 48), ("newest", 1)]:
        backups.append({"kind": "Backup", "metadata": {"name": name, "uid": name + "-uid", "resourceVersion": "1",
            "labels": {"backup-target": "default", "backup-volume": VOLUME},
            "ownerReferences": [{"kind": "BackupVolume", "name": "backup-volume", "uid": "bv-uid"}]},
            "status": {"state": "Completed", "progress": 100, "volumeName": VOLUME, "error": "",
                "backupCreatedAt": timestamp(age), "snapshotCreatedAt": timestamp(age),
                "url": retention.DESTINATION + "?backup=" + name + "&volume=" + VOLUME}})
    return backup_volume, backups

class FakeClient:
    def __init__(self):
        self.backup_volume, self.backups = fixtures()
        self.deleted = []
        image = "docker.io/longhornio/longhorn-engine:v1.11.1"
        self.volume = {"kind": "Volume", "metadata": {"name": VOLUME},
            "spec": {"dataEngine": "v1", "image": image, "numberOfReplicas": 3},
            "status": {"state": "attached", "robustness": "healthy", "currentImage": image, "lastBackup": "newest"}}
        self.engine = {"kind": "Engine", "metadata": {"name": "engine"}, "spec": {"volumeName": VOLUME, "active": True, "image": image},
            "status": {"currentState": "running", "currentImage": image, "replicaModeMap": {"r0": "RW", "r1": "RW", "r2": "RW"}}}
        self.replicas = [{"kind": "Replica", "metadata": {"name": "r" + str(i)},
            "spec": {"volumeName": VOLUME, "active": True, "nodeID": "node" + str(i), "healthyAt": "now", "failedAt": "", "image": image},
            "status": {"currentState": "running", "currentImage": image}} for i in range(3)]
        self.system = {"metadata": {"name": "system", "uid": "system-uid", "creationTimestamp": self.backups[-1]["status"]["backupCreatedAt"]},
            "spec": {"volumeBackupPolicy": "always"}, "status": {"state": "Ready", "version": "v1.11.1"}}
        self.image = image

    def csi_snapshot_contents(self):
        return []

    def request(self, path, body=None, missing_ok=False):
        if body is not None:
            self.deleted.append((path, body))
            raise AssertionError("Dry run must never issue deletion")
        return {"metadata": {"generation": 1}, "status": {"desiredNumberScheduled": 3,
            "numberReady": 3, "updatedNumberScheduled": 3, "observedGeneration": 1}}

    def get(self, resource, name=None, missing_ok=False):
        if resource == "settings": return {"value": "false" if name == "auto-cleanup-when-delete-backup" else self.image}
        if resource == "backuptargets": return {"spec": {"backupTargetURL": retention.DESTINATION, "credentialSecret": "longhorn-b2-credentials"}, "status": {"available": True}}
        if resource == "backupvolumes": return self.backup_volume if name else {"items": [self.backup_volume]}
        return {"items": {"systembackups": [self.system], "volumes": [self.volume], "engines": [self.engine],
            "replicas": self.replicas, "backups": self.backups}[resource]}

class RetentionGuards(unittest.TestCase):
    def test_current_and_legacy_csi_backup_references_block_retirement(self):
        _, backups = fixtures()
        for location, scheme in [("spec", "bak"), ("status", "bs")]:
            content = {"spec": {"driver": "driver.longhorn.io"}}
            handle = scheme + "://" + VOLUME + "/old"
            if location == "spec": content["spec"]["source"] = {"snapshotHandle": handle}
            else: content["status"] = {"snapshotHandle": handle}
            with self.assertRaises(retention.UnsafeRetention):
                retention.guard_backup_references(backups[:-1], [content])

    def test_foreign_volume_and_protected_latest_csi_backups_remain_allowed(self):
        _, backups = fixtures()
        for handle in ["bak://different-volume/old", "bak://" + VOLUME + "/newest", "snap://" + VOLUME + "/old"]:
            retention.guard_backup_references(backups[:-1], [{"spec": {"driver": "driver.longhorn.io", "source": {"snapshotHandle": handle}}}])

    def test_unresolved_csi_snapshot_stops_before_native_delete(self):
        class ReferencedClient(FakeClient):
            def csi_snapshot_contents(self):
                return [{"spec": {"driver": "driver.longhorn.io", "source": {"volumeHandle": VOLUME}}}]
        client = ReferencedClient()
        with self.assertRaises(retention.UnsafeRetention):
            retention.retire_volume(client, retention.load_guards(), VOLUME, "newest", True)
        self.assertEqual(client.deleted, [])

    def test_process_image_drift_blocks_retention_before_deletion(self):
        client = FakeClient()
        client.engine["status"]["currentImage"] = "docker.io/longhornio/longhorn-engine:v1.10.1"
        with self.assertRaises(retention.UnsafeRetention):
            retention.retire_volume(client, retention.load_guards(), VOLUME, "newest", True)
        self.assertEqual(client.deleted, [])

    def test_metadata_only_probe_cannot_authorize_retention(self):
        client = FakeClient()
        client.system["spec"]["volumeBackupPolicy"] = "disabled"
        with self.assertRaises(retention.UnsafeRetention):
            retention.retire_volume(client, retention.load_guards(), VOLUME, "newest", True)
        self.assertEqual(client.deleted, [])

    def test_bucket_or_local_cleanup_changes_block_retention(self):
        for change in ["destination", "unavailable", "local-cleanup"]:
            class ChangedClient(FakeClient):
                def get(self, resource, name=None, missing_ok=False):
                    obj = super().get(resource, name, missing_ok)
                    if resource == "backuptargets":
                        if change == "destination": obj["spec"]["backupTargetURL"] = "s3://foreign-bucket/longhorn"
                        if change == "unavailable": obj["status"]["available"] = False
                    if change == "local-cleanup" and resource == "settings" and name == "auto-cleanup-when-delete-backup":
                        obj["value"] = "true"
                    return obj
            client = ChangedClient()
            with self.assertRaises(retention.UnsafeRetention):
                retention.retire_volume(client, retention.load_guards(), VOLUME, "newest", True)
            self.assertEqual(client.deleted, [])

    def test_native_system_retention_preserves_latest_archive(self):
        class NativeSystems(FakeClient):
            def __init__(self):
                super().__init__()
                old = copy.deepcopy(self.system)
                old["metadata"].update(name="old-system", uid="old-system-uid", resourceVersion="2", creationTimestamp="2026-09-01T00:00:00Z")
                self.systems = [old, self.system]

            def get(self, resource, name=None, missing_ok=False):
                if resource == "systembackups":
                    if name: return next((s for s in self.systems if s["metadata"]["name"] == name), None)
                    return {"items": self.systems}
                return super().get(resource, name, missing_ok)

            def request(self, path, body=None, missing_ok=False):
                if body is None: return super().request(path, body, missing_ok)
                self.deleted.append((path, body))
                self.systems = [s for s in self.systems if s["metadata"]["name"] != path.rsplit("/", 1)[-1]]
                return {"kind": "Status", "status": "Success"}

        client = NativeSystems()
        retention.retire_systems(client, retention.load_guards(), "system", True)
        self.assertEqual([s["metadata"]["name"] for s in client.systems], ["system"])
        self.assertEqual(len(client.deleted), 1)
        self.assertEqual(client.deleted[0], (retention.BASE + "systembackups/old-system", {
            "apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": "old-system-uid", "resourceVersion": "2"}}))
    def test_native_apply_deletes_only_old_point_with_exact_preconditions(self):
        class NativeClient(FakeClient):
            def request(self, path, body=None, missing_ok=False):
                if body is None:
                    return super().request(path, body, missing_ok)
                self.deleted.append((path, body))
                name = path.rsplit("/", 1)[-1]
                self.backups = [b for b in self.backups if b["metadata"]["name"] != name]
                return {"kind": "Status", "status": "Success"}

            def get(self, resource, name=None, missing_ok=False):
                if resource == "backups" and name:
                    return next((b for b in self.backups if b["metadata"]["name"] == name), None)
                return super().get(resource, name, missing_ok)
        client = NativeClient()
        retention.retire_volume(client, retention.load_guards(), VOLUME, "newest", True)
        self.assertEqual([b["metadata"]["name"] for b in client.backups], ["newest"])
        self.assertEqual(len(client.deleted), 1)
        path, body = client.deleted[0]
        self.assertEqual(path, retention.BASE + "backups/old")
        self.assertEqual(body["preconditions"], {"uid": "old-uid", "resourceVersion": "1"})

    def test_changed_retained_identity_stops_before_native_deletion(self):
        class ChangedClient(FakeClient):
            def get(self, resource, name=None, missing_ok=False):
                if resource == "backupvolumes" and name:
                    changed = copy.deepcopy(self.backup_volume)
                    changed["metadata"]["uid"] = "replacement-volume"
                    return changed
                return super().get(resource, name, missing_ok)
        client = ChangedClient()
        with self.assertRaises(retention.UnsafeRetention):
            retention.retire_volume(client, retention.load_guards(), VOLUME, "newest", True)
        self.assertEqual(client.deleted, [])

    def test_native_failure_stops_before_another_history_deletion(self):
        class ErrorClient(FakeClient):
            def request(self, path, body=None, missing_ok=False):
                if body is None:
                    return super().request(path, body, missing_ok)
                self.deleted.append((path, body))
                return {"kind": "Status", "status": "Success"}

            def get(self, resource, name=None, missing_ok=False):
                if resource == "backups" and name:
                    point = copy.deepcopy(next(b for b in self.backups if b["metadata"]["name"] == name))
                    if self.deleted:
                        point["status"].update(state="Error", error="native cleanup failed")
                    return point
                return super().get(resource, name, missing_ok)
        client = ErrorClient()
        older = copy.deepcopy(client.backups[0])
        older["metadata"].update(name="oldest", uid="oldest-uid")
        older["status"].update(backupCreatedAt="2026-01-01T00:00:00Z", snapshotCreatedAt="2026-01-01T00:00:00Z",
                              url=retention.DESTINATION + "?backup=oldest&volume=" + VOLUME)
        client.backups.insert(0, older)
        with self.assertRaises(retention.UnsafeRetention):
            retention.retire_volume(client, retention.load_guards(), VOLUME, "newest", True)
        self.assertEqual(len(client.deleted), 1)
        self.assertTrue(client.deleted[0][0].endswith("/oldest"))

    def test_weekly_retention_requires_this_run_to_capture_every_volume(self):
        client = FakeClient()
        client.system["metadata"]["labels"] = {"recurring-job.longhorn.io/system-backup": "default-weekly-system-backup"}
        guards = retention.load_guards()
        retention.after_weekly_backup(client, guards)
        self.assertEqual(client.deleted, [])
        client.backups[-1]["status"]["snapshotCreatedAt"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        with self.assertRaises(retention.UnsafeRetention): retention.after_weekly_backup(client, guards, True)
        self.assertEqual(client.deleted, [])
        client.system["status"]["state"] = "Error"
        with self.assertRaises(retention.UnsafeRetention): retention.after_weekly_backup(client, guards, True)
        self.assertEqual(client.deleted, [])

    def test_stale_or_previous_version_archive_blocks_retention(self):
        client = FakeClient()
        client.system["status"]["version"] = "v1.10.1"
        with self.assertRaises(retention.UnsafeRetention): retention.global_state(client, retention.load_guards())
        client.system["status"]["version"] = "v1.11.1"
        client.system["metadata"]["creationTimestamp"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat().replace("+00:00", "Z")
        with self.assertRaises(retention.UnsafeRetention): retention.global_state(client, retention.load_guards())

    def test_keep_newest_and_exact_identity_preconditions(self):
        volume, backups = fixtures()
        keep, older = retention.retention_plan(backups, volume, "newest")
        self.assertEqual(keep["metadata"]["name"], "newest")
        self.assertEqual([b["metadata"]["name"] for b in older], ["old"])
        self.assertEqual(retention.delete_options(older[0])["preconditions"], {"uid": "old-uid", "resourceVersion": "1"})

    def test_refuse_incomplete_foreign_and_changed_recovery_identity(self):
        volume, backups = fixtures()
        for field, value in [("state", "InProgress"), ("progress", 90), ("error", "failed"), ("volumeName", "another")]:
            changed = copy.deepcopy(backups)
            changed[-1]["status"][field] = value
            with self.assertRaises(retention.UnsafeRetention): retention.retention_plan(changed, volume)
        for url in [retention.DESTINATION + "?backup=old&volume=" + VOLUME,
                    "s3://another-bucket/longhorn?backup=newest&volume=" + VOLUME]:
            changed = copy.deepcopy(backups)
            changed[-1]["status"]["url"] = url
            with self.assertRaises(retention.UnsafeRetention): retention.retention_plan(changed, volume)
        with self.assertRaises(retention.UnsafeRetention): retention.retention_plan(backups, volume, "old")
        backups[-1]["metadata"]["ownerReferences"][0]["uid"] = "foreign-uid"
        with self.assertRaises(retention.UnsafeRetention): retention.retention_plan(backups, volume)

    def test_legacy_import_still_requires_exact_remote_identity(self):
        volume, backups = fixtures()
        del backups[0]["metadata"]["ownerReferences"]
        retention.retention_plan(backups, volume)
        backups[0]["metadata"]["labels"]["backup-volume"] = "foreign"
        with self.assertRaises(retention.UnsafeRetention): retention.retention_plan(backups, volume)

    def test_system_archive_must_include_completed_volume_data(self):
        systems = [{"metadata": {"name": "old", "creationTimestamp": "2026-09-01T00:00:00Z"}, "spec": {"volumeBackupPolicy": "disabled"}, "status": {"state": "Ready"}}, FakeClient().system]
        self.assertEqual(retention.system_plan(systems)[0]["metadata"]["name"], "system")
        systems[-1]["status"]["state"] = "CreatingVolumeBackups"
        with self.assertRaises(retention.UnsafeRetention): retention.system_plan(systems)
        systems[-1]["status"]["state"] = "Ready"
        systems[-1]["spec"]["volumeBackupPolicy"] = "disabled"
        with self.assertRaises(retention.UnsafeRetention): retention.system_plan(systems)

    def test_dry_run_never_deletes_and_degraded_volume_blocks_retention(self):
        client = FakeClient()
        guards = retention.load_guards()
        retention.retire_volume(client, guards, VOLUME, "newest")
        self.assertEqual(client.deleted, [])
        client.volume["status"]["robustness"] = "degraded"
        with self.assertRaises(retention.UnsafeRetention): retention.retire_volume(client, guards, VOLUME, "newest", True)
        self.assertEqual(client.deleted, [])

if __name__ == "__main__": unittest.main()
