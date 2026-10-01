import copy
import datetime as dt
import importlib.util
from pathlib import Path
import unittest


path = Path(__file__).resolve().parents[1] / "longhorn-prune-snapshots.py"
module_spec = importlib.util.spec_from_file_location("prune", path)
prune = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(prune)


def snapshot(name, created, removed=False):
    return {"metadata": {"name": name}, "status": {"creationTime": created, "readyToUse": not removed, "markRemoved": removed}}


def fixture():
    volume = {"kind": "Volume", "metadata": {"name": "test"},
              "spec": {"dataEngine": "v1", "image": "engine", "numberOfReplicas": 3},
              "status": {"state": "attached", "robustness": "healthy", "currentImage": "engine",
                         "lastBackup": "backup", "lastBackupAt": "2026-10-01T08:00:00Z"}}
    replicas = [{"kind": "Replica", "metadata": {"name": f"r{i}"},
                 "spec": {"volumeName": "test", "nodeID": f"metal{i}", "active": True,
                          "healthyAt": "2026-09-30T00:00:00Z", "failedAt": ""}} for i in range(3)]
    engine = {"kind": "Engine", "metadata": {"name": "engine"}, "spec": {"volumeName": "test", "active": True},
              "status": {"currentState": "running", "replicaModeMap": {f"r{i}": "RW" for i in range(3)}}}
    backup = {"kind": "Backup", "metadata": {"name": "backup"},
              "status": {"state": "Completed", "progress": 100, "url": "s3://test", "volumeName": "test",
                         "snapshotCreatedAt": "2026-10-01T07:00:00Z"}}
    return [volume, engine, backup] + replicas


class SnapshotCleanupGuards(unittest.TestCase):
    def test_keep_newest_two_and_protect_recovery_backup(self):
        snapshots = [snapshot("old", "2026-05-10T00:00:00Z"), snapshot("backup", "2026-10-01T07:00:00Z"),
                     snapshot("maintenance", "2026-10-01T08:00:00Z"), snapshot("removed", "2026-10-01T09:00:00Z", True)]
        keep, retire = prune.select_snapshots(snapshots, "backup")
        self.assertEqual(keep, {"backup", "maintenance"})
        self.assertEqual([s["metadata"]["name"] for s in retire], ["old"])
        with self.assertRaises(prune.UnsafeCleanup):
            prune.select_snapshots(snapshots, "old")

    def test_refuse_incomplete_or_stale_snapshot_backup(self):
        items = fixture(); now = dt.datetime(2026, 10, 1, 8, tzinfo=dt.timezone.utc)
        prune.guard_backups(items, now)
        for field, value in [("state", "InProgress"), ("state", "Error"), ("volumeName", "other"), ("progress", 10), ("url", ""), ("snapshotCreatedAt", "2025-12-01T00:00:00Z")]:
            changed = copy.deepcopy(items); changed[2]["status"][field] = value
            with self.assertRaises(prune.UnsafeCleanup):
                prune.guard_backups(changed, now)

    def test_refuse_rebuilding_colocated_or_upgrading_volumes(self):
        items = fixture(); prune.guard_health(items)
        changed = copy.deepcopy(items); changed[1]["status"]["replicaModeMap"]["r0"] = "WO"
        with self.assertRaises(prune.UnsafeCleanup):
            prune.guard_health(changed)
        changed = copy.deepcopy(items); changed[3]["spec"]["nodeID"] = "metal1"
        with self.assertRaises(prune.UnsafeCleanup):
            prune.guard_health(changed)
        changed = copy.deepcopy(items); changed[0]["spec"]["image"] = "new"
        with self.assertRaises(prune.UnsafeCleanup):
            prune.guard_health(changed)
        changed = copy.deepcopy(items); changed[0]["spec"]["cloneMode"] = "linked-clone"
        with self.assertRaises(prune.UnsafeCleanup):
            prune.guard_health(changed)

    def test_refuse_referenced_csi_snapshot(self):
        targets = [snapshot("old", "2026-05-10T00:00:00Z")]
        for content in [{"spec": {"source": {"snapshotHandle": "snap://test/old"}}},
                        {"status": {"snapshotHandle": "snap://test/old"}}]:
            with self.assertRaises(prune.UnsafeCleanup):
                prune.guard_snapshot_references(targets, [content])
        prune.guard_snapshot_references(targets, [{"status": {"snapshotHandle": "snap://test/keep"}}])

    def test_requires_temporary_coalescing_space_on_every_replica_disk(self):
        items = fixture(); volume, replicas = items[0], items[3:]
        volume["spec"]["size"] = "10"
        nodes = []
        for i, replica in enumerate(replicas):
            replica["spec"]["diskID"] = f"uuid{i}"
            nodes.append({"metadata": {"name": f"metal{i}"}, "status": {"diskStatus": {
                "default": {"diskUUID": f"uuid{i}", "storageAvailable": 11,
                            "conditions": [{"type": "Ready", "status": "True"}]}}}})
        prune.guard_compaction_space(volume, replicas, nodes)
        for field, value in [("storageAvailable", 10), ("diskUUID", "another"), ("conditions", [])]:
            changed = copy.deepcopy(nodes)
            changed[1]["status"]["diskStatus"]["default"][field] = value
            with self.assertRaises(prune.UnsafeCleanup):
                prune.guard_compaction_space(volume, replicas, changed)
        with self.assertRaises(prune.UnsafeCleanup):
            prune.guard_compaction_space(volume, replicas, nodes[:2])


if __name__ == "__main__":
    unittest.main()
