import copy
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

from test_longhorn_retire_backup_history import FakeClient, VOLUME, fixtures

spec = importlib.util.spec_from_file_location("retired", Path(__file__).resolve().parents[1] / "longhorn-retire-retired-volumes.py")
retired = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retired)
RETIRE = "pvc-22222222-2222-2222-2222-222222222222"


class NativeClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.volume["metadata"]["uid"] = "volume-uid"
        self.backups = self.backups[1:]
        self.backup_volume["metadata"].update(resourceVersion="7", finalizers=["longhorn.io"])
        bv, points = fixtures()
        bv["spec"]["volumeName"] = RETIRE
        bv["metadata"].update(name="retired-bv", uid="retired-bv-uid", resourceVersion="9", finalizers=["longhorn.io"])
        bv["status"]["lastBackupName"] = "retired-backup"
        point = points[-1]
        point["metadata"].update(name="retired-backup", uid="retired-backup-uid")
        point["metadata"]["labels"]["backup-volume"] = RETIRE
        point["metadata"]["ownerReferences"] = [{"apiVersion": "longhorn.io/v1beta2", "kind": "BackupVolume", "name": "retired-bv", "uid": "retired-bv-uid"}]
        point["status"].update(volumeName=RETIRE, url=retired.retention.DESTINATION + "?backup=retired-backup&volume=" + RETIRE)
        self.backups.append(point)
        self.backup_volumes = [self.backup_volume, bv]
        self.claims = [{"kind": "PersistentVolume", "metadata": {"name": "current-pv", "uid": "pv-uid"}, "status": {"phase": "Bound"}},
                       {"kind": "PersistentVolumeClaim", "metadata": {"namespace": "app", "name": "data", "uid": "pvc-uid"}, "status": {"phase": "Bound"}}]
        self.contents = []

    def get(self, resource, name=None, missing_ok=False):
        if resource == "backuptargets":
            return {"metadata": {}, **super().get(resource, name, missing_ok)}
        if resource in ["backupvolumes", "backups"]:
            values = self.backup_volumes if resource == "backupvolumes" else self.backups
            return next((o for o in values if o["metadata"]["name"] == name), None) if name else {"items": values}
        return super().get(resource, name, missing_ok)

    def request(self, path, body=None, missing_ok=False):
        if path in ["/api/v1/persistentvolumes", "/api/v1/persistentvolumeclaims"]:
            kind = "PersistentVolume" if path.endswith("persistentvolumes") else "PersistentVolumeClaim"
            return {"items": [{k: copy.deepcopy(v) for k, v in o.items() if k != "kind"}
                              for o in self.claims if o["kind"] == kind]}
        if body is None:
            return super().request(path, body, missing_ok)
        self.deleted.append((path, body))
        self.backup_volumes = [v for v in self.backup_volumes if path != retired.retention.BASE + "backupvolumes/" + v["metadata"]["name"]]
        self.backups = [b for b in self.backups if b["status"]["volumeName"] != RETIRE]
        return {"kind": "Status", "status": "Success"}

    def csi_snapshot_contents(self):
        return self.contents


class RetiredVolumeGuards(unittest.TestCase):
    def setUp(self):
        self.client = NativeClient()
        self.guards = retired.retention.load_guards()
        self.point = {"volume": RETIRE, "backup": "retired-backup", "backupUID": "retired-backup-uid",
                      "backupVolume": "retired-bv", "backupVolumeUID": "retired-bv-uid"}
        self.plan = {"deletionAuthorized": True, "authorization": "Delete the reviewed retired restore point",
            "retiredRecoveryPoints": [self.point], "originalVolumes": {VOLUME: "volume-uid"},
            "originalClaimsVerified": 2, "originalClaims": copy.deepcopy(self.client.claims),
            "plans": [{"volume": VOLUME, "live": True, "backupVolumeName": "backup-volume", "backupVolumeUID": "bv-uid", "keep": "newest", "keepUID": "newest-uid", "older": {}},
                      {"volume": RETIRE, "live": False, "backupVolumeName": "retired-bv", "backupVolumeUID": "retired-bv-uid", "keep": "retired-backup", "keepUID": "retired-backup-uid", "older": {}}],
            "system": {"name": "system", "uid": "system-uid"}, "olderSystems": {},
            "restoreVerification": {"verificationCopyRemoved": True, "cloneHealthyCopies": 3,
                "originalClaimsVerified": 2, "sqliteIntegrity": "SQLite integrity: ok", "sourceVolume": VOLUME,
                "sourceVolumeUID": "volume-uid", "sourceBackup": "newest", "sourceBackupUID": "newest-uid"}}

    def check(self):
        return retired.check_plan(self.client, self.guards, self.plan)

    def test_dry_run_and_apply_preserve_current_recovery_and_use_exact_delete(self):
        retired.retire(self.client, self.guards, self.plan)
        self.assertEqual(self.client.deleted, [])
        retired.retire(self.client, self.guards, self.plan, True)
        retired.retire(self.client, self.guards, self.plan, True)
        self.assertEqual(self.client.deleted, [(retired.retention.BASE + "backupvolumes/retired-bv",
            {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": "retired-bv-uid", "resourceVersion": "9"}})])
        self.assertEqual([b["metadata"]["name"] for b in self.client.backups], ["newest"])

    def test_missing_authorization_and_changed_approved_identities_block(self):
        for field, value in [("deletionAuthorized", False), ("authorization", ""), ("backupUID", "different"), ("backupVolumeUID", "different")]:
            self.setUp()
            if field in self.point: self.point[field] = value
            else: self.plan[field] = value
            with self.assertRaises(retired.UnsafeRetention): self.check()
            self.assertEqual(self.client.deleted, [])

    def test_live_volume_and_replica_reference_block(self):
        for item in [self.client.volume, self.client.replicas[0]]:
            original = item["metadata"]["name"]
            item["metadata"]["name"] = RETIRE
            with self.assertRaises(retired.UnsafeRetention): self.check()
            item["metadata"]["name"] = original

    def test_current_claim_identity_and_bound_state_are_rechecked(self):
        for key, value in [("uid", "changed"), ("phase", "Lost")]:
            self.setUp()
            self.client.claims[0]["metadata" if key == "uid" else "status"][key] = value
            with self.assertRaises(retired.UnsafeRetention): self.check()

    def test_retired_volume_pv_and_csi_backup_references_block(self):
        self.client.claims.append({"kind": "PersistentVolume", "metadata": {"name": "old-pv"}, "spec": {"csi": {"volumeHandle": RETIRE}}})
        with self.assertRaises(retired.UnsafeRetention): self.check()
        self.client.claims.pop()
        self.client.contents = [{"spec": {"driver": "driver.longhorn.io", "source": {"snapshotHandle": "bak://" + RETIRE + "/retired-backup"}}}]
        with self.assertRaises(retired.UnsafeRetention): self.check()

    def test_linked_clone_and_metadata_only_cleanup_label_block(self):
        for change in ["clone", "label", "prefixed-label", "finalizer", "owner"]:
            self.setUp()
            if change == "clone": self.client.backup_volume["status"]["linkedCloneSourceVolume"] = RETIRE
            if change == "label": self.client.backup_volumes[-1]["metadata"]["labels"] = {"delete-custom-resource-only": "false"}
            if change == "prefixed-label": self.client.backup_volumes[-1]["metadata"]["labels"] = {"longhorn.io/delete-custom-resource-only": "false"}
            if change == "finalizer": self.client.backup_volumes[-1]["metadata"]["finalizers"] = []
            if change == "owner": self.client.backups[-1]["metadata"]["ownerReferences"] = []
            with self.assertRaises(retired.UnsafeRetention): self.check()

    def test_changed_current_keeper_and_expired_capture_block(self):
        for change in ["keeper", "age"]:
            self.setUp()
            if change == "keeper": self.client.backups[0]["metadata"]["uid"] = "changed"
            else: self.client.system["metadata"]["creationTimestamp"] = "2020-01-01T00:00:00Z"
            with self.assertRaises(retired.UnsafeRetention): self.check()

    def test_orphan_unreviewed_backup_does_not_allow_partial_resume(self):
        self.client.backup_volumes.pop()
        with self.assertRaises(retired.UnsafeRetention): self.check()

    def test_native_error_and_recreated_identity_stop_observation(self):
        for change in ["error", "uid"]:
            self.setUp()
            if change == "error": self.client.backup_volumes[-1]["status"]["messages"] = {"error": "failed"}
            else: self.client.backup_volumes[-1]["metadata"]["uid"] = "recreated"
            with self.assertRaises(retired.UnsafeRetention): retired.wait_native(self.client, self.point)
            self.assertEqual(self.client.deleted, [])

    def test_native_timeout_never_forces_finalizers(self):
        with mock.patch.object(retired.time, "sleep"):
            with self.assertRaises(retired.UnsafeRetention): retired.wait_native(self.client, self.point)
        self.assertEqual(self.client.deleted, [])
