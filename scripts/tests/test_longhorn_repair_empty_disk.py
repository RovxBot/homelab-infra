import copy
import importlib.util
import pathlib
import unittest


module_path = pathlib.Path(__file__).resolve().parents[1] / "longhorn-repair-empty-disk.py"
spec = importlib.util.spec_from_file_location("repair_empty_disk", module_path)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


class DiskRepairSafetyTests(unittest.TestCase):
    def setUp(self):
        self.old = "a28f5144-b3da-41ae-bd29-51c039268f56"
        self.new = "afdfec59-cf63-4304-a7d1-a84e28dd749a"
        self.node = {
            "metadata": {"name": "metal0"},
            "spec": {"disks": {"default": {"diskType": "filesystem", "path": "/var/lib/longhorn/"}}},
            "status": {"diskStatus": {"default": {
                "diskUUID": self.old, "storageScheduled": 0, "scheduledReplica": {},
                "conditions": [{"type": "Ready", "status": "False", "reason": "DiskFilesystemChanged"}],
            }}},
        }

    def test_refuses_replica_references_to_either_identity_or_same_path(self):
        for spec in [{"diskID": self.old}, {"diskID": self.new},
                     {"nodeID": "metal0", "diskPath": "/var/lib/longhorn"}]:
            with self.subTest(spec=spec), self.assertRaises(repair.UnsafeRepair):
                repair.guard_references(self.node, "default", self.new, [{"kind": "Replica", "spec": spec}])

    def test_refuses_backing_image_data(self):
        for item in [
            {"kind": "BackingImage", "spec": {"diskFileSpecMap": {self.old: {}}}},
            {"kind": "BackingImageManager", "spec": {"diskUUID": self.new}},
            {"kind": "Orphan", "spec": {"parameters": {"DiskUUID": self.new}}},
        ]:
            with self.subTest(item=item), self.assertRaises(repair.UnsafeRepair):
                repair.guard_references(self.node, "default", self.new, [item])

    def test_refuses_scheduled_data_and_unexpected_disk_conditions(self):
        for field, value in [("storageScheduled", 1), ("scheduledReplica", {"copy": 1}),
                             ("scheduledBackingImage", {"image": 1}),
                             ("conditions", [{"type": "Ready", "status": "False", "reason": "NodeNotReady"}])]:
            node = copy.deepcopy(self.node)
            node["status"]["diskStatus"]["default"][field] = value
            with self.subTest(field=field), self.assertRaises(repair.UnsafeRepair):
                repair.guard_references(node, "default", self.new, [])

    def test_refuses_a_degraded_or_under_replicated_volume(self):
        volume = {"kind": "Volume", "metadata": {"name": "data"}, "spec": {"numberOfReplicas": 3},
                  "status": {"state": "attached", "robustness": "healthy"}}
        engine = {"kind": "Engine", "spec": {"volumeName": "data"},
                  "status": {"replicaModeMap": {"one": "RW", "two": "RW", "three": "WO"}}}
        with self.assertRaises(repair.UnsafeRepair):
            repair.guard_health([volume, engine])
        engine["status"]["replicaModeMap"]["three"] = "RW"
        volume["status"]["robustness"] = "degraded"
        with self.assertRaises(repair.UnsafeRepair):
            repair.guard_health([volume, engine])


if __name__ == "__main__":
    unittest.main()
