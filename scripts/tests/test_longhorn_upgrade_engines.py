import copy
import datetime as dt
import importlib.util
from pathlib import Path
import unittest


path = Path(__file__).resolve().parents[1] / "longhorn-upgrade-engines.py"
module_spec = importlib.util.spec_from_file_location("upgrade", path)
upgrade = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(upgrade)


def fixture(detached=False):
    image = "longhornio/longhorn-engine:v1.10.1"
    volume = {"kind": "Volume", "metadata": {"name": "test"},
              "spec": {"dataEngine": "v1", "image": image, "numberOfReplicas": 3},
              "status": {"state": "detached" if detached else "attached", "robustness": "unknown" if detached else "healthy",
                         "currentImage": image, "lastBackup": "backup-test", "lastBackupAt": "2026-10-01T07:00:00Z"}}
    replicas = [{"kind": "Replica", "metadata": {"name": f"r{i}"},
                 "spec": {"volumeName": "test", "nodeID": f"metal{i}", "active": True,
                          "healthyAt": "2026-09-30T00:00:00Z", "failedAt": ""}} for i in range(3)]
    engine = {"kind": "Engine", "metadata": {"name": "e0"}, "spec": {"volumeName": "test", "active": True},
              "status": {"currentState": "running", "replicaModeMap": {f"r{i}": "RW" for i in range(3)}}}
    return [volume, engine] + replicas


class UpgradeGuards(unittest.TestCase):
    def test_refuse_unsupported_versions_and_in_progress_upgrade(self):
        items = fixture()
        upgrade.preflight(items, "test", "docker.io/longhornio/longhorn-engine:v1.11.1")
        for target in ["docker.io/longhornio/longhorn-engine:v1.12.1", "longhornio/longhorn-engine:v1.9.0", "longhornio/longhorn-engine:v2.0.0"]:
            with self.assertRaises(upgrade.UnsafeUpgrade):
                upgrade.preflight(items, "test", target)
        items[0]["spec"]["image"] = "longhornio/longhorn-engine:v1.11.1"
        with self.assertRaises(upgrade.UnsafeUpgrade):
            upgrade.health(items)
        for key, value in [("dataSource", "volume:test-source"), ("cloneMode", "full-copy"), ("migrationNodeID", "metal9")]:
            changed = fixture()
            changed[0]["spec"][key] = value
            with self.assertRaises(upgrade.UnsafeUpgrade):
                upgrade.health(changed)

    def test_refuse_degraded_and_colocated_replicas(self):
        items = fixture()
        for mode in ["WO", "ERR"]:
            changed = copy.deepcopy(items)
            changed[1]["status"]["replicaModeMap"]["r0"] = mode
            with self.assertRaises(upgrade.UnsafeUpgrade):
                upgrade.health(changed)
        items[2]["spec"]["nodeID"] = "metal1"
        with self.assertRaises(upgrade.UnsafeUpgrade):
            upgrade.health(items)

    def test_detached_requires_three_retained_healthy_copies(self):
        items = fixture(detached=True)
        upgrade.health(items)
        items[2]["spec"]["failedAt"] = "2026-10-01T06:00:00Z"
        with self.assertRaises(upgrade.UnsafeUpgrade):
            upgrade.health(items)

    def test_refuse_stale_incomplete_and_wrong_volume_backups(self):
        volume = fixture()[0]
        now = dt.datetime(2026, 10, 1, 8, tzinfo=dt.timezone.utc)
        backup = {"metadata": {"name": "backup-test"}, "status": {"volumeName": "test", "state": "Completed", "progress": 100, "url": "s3://test", "snapshotCreatedAt": "2026-10-01T07:00:00Z"}}
        upgrade.fresh_backup(volume, backup, now)
        for key, value in [("state", "Error"), ("volumeName", "other"), ("progress", 50), ("error", "upload failed"), ("url", "")]:
            changed = copy.deepcopy(backup); changed["status"][key] = value
            with self.assertRaises(upgrade.UnsafeUpgrade):
                upgrade.fresh_backup(volume, changed, now)
        stale = copy.deepcopy(backup)
        stale["status"]["snapshotCreatedAt"] = "2026-09-28T07:00:00Z"
        with self.assertRaises(upgrade.UnsafeUpgrade):
            upgrade.fresh_backup(volume, stale, now)
        with self.assertRaises(upgrade.UnsafeUpgrade):
            upgrade.fresh_backup(volume, backup, now + dt.timedelta(days=2))


if __name__ == "__main__":
    unittest.main()
