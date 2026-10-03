import copy
import datetime as dt
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("idle", Path(__file__).resolve().parents[1] / "longhorn-resume-idle-snapshot-purge.py")
idle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idle)


def claims():
    return [{"kind": "PersistentVolume", "metadata": {"name": "original-pv", "uid": "pv-uid"},
             "spec": {"csi": {"driver": "driver.longhorn.io", "volumeHandle": "test"},
                      "claimRef": {"namespace": "app", "name": "data", "uid": "claim-uid"}}, "status": {"phase": "Bound"}},
            {"kind": "PersistentVolumeClaim", "metadata": {"namespace": "app", "name": "data", "uid": "claim-uid"},
             "spec": {"volumeName": "original-pv"}, "status": {"phase": "Bound"}}]


def pending_snapshots():
    return [{"metadata": {"name": name, "uid": name + "-uid"},
             "status": {"readyToUse": True, "markRemoved": False, "creationTime": when}}
            for name, when in [("backup", "2026-10-02T05:20:00Z"), ("maintenance", "2026-10-02T07:20:00Z")]] + [
        {"metadata": {"name": "old", "uid": "old-uid", "deletionTimestamp": "2026-10-02T07:21:00Z"},
         "status": {"readyToUse": True, "markRemoved": False, "creationTime": "2026-10-01T05:20:00Z"}}]


class IdleSnapshotGuards(unittest.TestCase):
    def test_unmarked_points_require_prior_deletion_and_older_capture(self):
        snapshots = pending_snapshots()
        self.assertEqual(idle.pending_snapshot_plan(snapshots, "backup")[0], {"backup", "maintenance"})
        for mutate in [lambda s: s[-1]["metadata"].pop("deletionTimestamp"),
                       lambda s: s[-1]["status"].update(readyToUse=False),
                       lambda s: s[-1]["status"].update(creationTime="2026-10-02T05:21:00Z"),
                       lambda s: s[0]["metadata"].update(deletionTimestamp="now")]:
            changed = copy.deepcopy(snapshots); mutate(changed)
            with self.assertRaises(idle.guards.UnsafeCleanup): idle.pending_snapshot_plan(changed, "backup")
        snapshots[-1]["status"].update(markRemoved=True, readyToUse=False)
        idle.pending_snapshot_plan(snapshots, "backup")
        snapshots[-1]["metadata"].pop("deletionTimestamp")
        with self.assertRaises(idle.guards.UnsafeCleanup): idle.pending_snapshot_plan(snapshots, "backup")

    def test_snapshot_identity_changes_stop_before_any_further_action(self):
        snapshots = pending_snapshots(); expected = idle.pending_snapshot_plan(snapshots, "backup")[2]
        for index in [0, 1, 2]:
            changed = copy.deepcopy(snapshots); changed[index]["metadata"]["uid"] = "replacement"
            with self.assertRaises(idle.guards.UnsafeCleanup): idle.pending_snapshot_plan(changed, "backup", expected)
        with self.assertRaises(idle.guards.UnsafeCleanup): idle.pending_snapshot_plan(snapshots[:2], "backup", expected)
        self.assertEqual(idle.pending_snapshot_plan(snapshots[:2], "backup", expected, True)[1], [])

    def test_active_and_terminating_consumers_block_maintenance(self):
        pod = {"kind": "Pod", "metadata": {"namespace": "app", "name": "consumer"},
               "spec": {"volumes": [{"persistentVolumeClaim": {"claimName": "data"}}]}, "status": {"phase": "Running"}}
        for phase in ["Pending", "Running", "Unknown"]:
            pod["status"]["phase"] = phase
            with self.assertRaises(idle.guards.UnsafeCleanup): idle.unused_claim_identity(claims() + [pod], "test")
        pod["metadata"]["deletionTimestamp"] = "now"
        with self.assertRaises(idle.guards.UnsafeCleanup): idle.unused_claim_identity(claims() + [pod], "test")
        pod["status"]["phase"] = "Succeeded"
        self.assertEqual(idle.unused_claim_identity(claims() + [pod], "test"), ("pv-uid", "app", "data", "claim-uid"))

    def test_csi_attachment_or_replaced_claim_blocks_maintenance(self):
        attachment = {"kind": "VolumeAttachment", "spec": {"source": {"persistentVolumeName": "original-pv"}}}
        with self.assertRaises(idle.guards.UnsafeCleanup): idle.unused_claim_identity(claims() + [attachment], "test")
        changed = claims()
        changed[1]["metadata"]["uid"] = "replacement"
        with self.assertRaises(idle.guards.UnsafeCleanup): idle.unused_claim_identity(changed, "test")
        changed = claims()
        changed[0]["status"]["phase"] = "Released"
        with self.assertRaises(idle.guards.UnsafeCleanup): idle.unused_claim_identity(changed, "test")

    def test_foreign_native_attachment_owner_blocks_maintenance(self):
        volume = {"metadata": {"name": "test", "uid": "volume-uid"}}
        attachment = {"spec": {"volume": "test"}, "metadata": {"ownerReferences": [{"kind": "Volume", "name": "test", "uid": "volume-uid"}]}}
        idle.check_attachment(attachment, volume)
        attachment["metadata"]["ownerReferences"][0]["uid"] = "foreign"
        with self.assertRaises(idle.guards.UnsafeCleanup): idle.check_attachment(attachment, volume)

    def test_native_flow_disables_frontend_and_removes_only_its_ticket(self):
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        volume = {"kind": "Volume", "metadata": {"name": "test", "uid": "volume-uid"},
                  "spec": {"image": "engine", "size": str(2**30), "dataEngine": "v1", "numberOfReplicas": 3, "accessMode": "rwo"},
                  "status": {"state": "detached", "robustness": "unknown", "currentImage": "engine", "lastBackup": "backup"}}
        engine = {"kind": "Engine", "metadata": {"name": "engine"}, "spec": {"active": True, "volumeName": "test", "image": "engine"},
                  "status": {"currentState": "stopped", "snapshots": {name: {} for name in ["backup", "maintenance", "parent", "volume-head"]},
                             "replicaModeMap": {"r0": "RW", "r1": "RW", "r2": "RW"}}}
        replicas = [{"kind": "Replica", "metadata": {"name": "r"+str(i)},
                     "spec": {"active": True, "volumeName": "test", "nodeID": "metal"+str(i), "diskID": "disk", "healthyAt": now, "failedAt": ""}} for i in range(3)]
        backup = {"kind": "Backup", "metadata": {"name": "backup"}, "status": {"state": "Completed", "progress": 100, "volumeName": "test", "url": "s3://recovery", "snapshotName": "backup", "snapshotCreatedAt": now}}
        snapshots = [{"kind": "Snapshot", "metadata": {"name": name, "uid": name + "-uid"}, "spec": {"volume": "test"},
                      "status": {"readyToUse": True, "markRemoved": False, "creationTime": now}} for name in ["backup", "maintenance"]]
        snapshots.append({"kind": "Snapshot", "metadata": {"name": "parent", "uid": "parent-uid", "deletionTimestamp": now}, "spec": {"volume": "test"},
                          "status": {"markRemoved": False, "readyToUse": True, "creationTime": "2026-01-01T00:00:00Z"}})
        attachment = {"metadata": {"uid": "attachment-uid", "ownerReferences": [{"kind": "Volume", "name": "test", "uid": "volume-uid"}]},
                      "spec": {"volume": "test", "attachmentTickets": {}}}
        nodes = [{"metadata": {"name": "metal"+str(i)}, "status": {"conditions": [{"type": "Ready", "status": "True"}],
                  "diskStatus": {"disk": {"diskUUID": "disk", "storageAvailable": 100*2**30, "conditions": [{"type": "Ready", "status": "True"}]}}}} for i in range(3)]
        def state(): return copy.deepcopy([volume, engine, backup] + replicas + snapshots)
        def get(resource, name=None):
            if resource == "daemonset": return {"metadata": {"generation": 1}, "status": {"desiredNumberScheduled": 3, "numberReady": 3, "updatedNumberScheduled": 3, "observedGeneration": 1}}
            if resource == "settings.longhorn.io": return {"value": "engine"}
            if resource == "volumes.longhorn.io": return copy.deepcopy(volume)
            if resource == "volumeattachments.longhorn.io": return copy.deepcopy(attachment)
            if resource == "volumesnapshotcontents.snapshot.storage.k8s.io": return {"items": []}
            if resource == "nodes.longhorn.io": return {"items": nodes}
            self.fail(resource)
        def action(name, operation, body):
            self.assertEqual(name, "test")
            if operation == "attach":
                self.assertTrue(body["disableFrontend"])
                self.assertEqual(body["attachmentID"], idle.TICKET)
                self.assertEqual(body["attacherType"], "longhorn-api")
                volume["status"].update(state="attached", robustness="healthy")
                engine["status"].update(currentState="running")
                del engine["status"]["snapshots"]["parent"]
                snapshots.pop()
                attachment["spec"]["attachmentTickets"][idle.TICKET] = {"type": "longhorn-api", "nodeID": "metal0", "parameters": {"disableFrontend": "true"}}
            elif operation == "detach":
                self.assertFalse(body["forceDetach"])
                self.assertEqual(body["attachmentID"], idle.TICKET)
                attachment["spec"]["attachmentTickets"].clear()
                volume["status"].update(state="detached", robustness="unknown")
                engine["status"].update(currentState="stopped")
            else: self.fail("Unexpected native operation: " + operation)
        with patch.object(idle, "cluster_resources", side_effect=lambda: claims()), patch.object(idle.guards, "state", side_effect=state), \
                patch.object(idle.guards, "get", side_effect=get), patch.object(idle.guards, "action", side_effect=action) as native, \
                patch.object(idle.sys, "argv", ["idle", "test", "--node", "metal0", "--apply"]):
            idle.main()
        self.assertEqual([c.args[1] for c in native.call_args_list], ["attach", "detach"])
        self.assertEqual(volume["metadata"]["uid"], "volume-uid")
        self.assertEqual(volume["status"]["state"], "detached")
        self.assertEqual(set(engine["status"]["snapshots"]), {"backup", "maintenance", "volume-head"})
        self.assertEqual(len(replicas), 3)


if __name__ == "__main__": unittest.main()
