import copy
import importlib.util
from pathlib import Path
import unittest


module_spec = importlib.util.spec_from_file_location("removal", Path(__file__).resolve().parents[1] / "longhorn-remove-stale-attachment.py")
removal = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(removal)


def attachment():
    return {"metadata": {"uid": "reviewed", "resourceVersion": "123", "deletionTimestamp": "2025-12-13T09:56:01Z",
                          "finalizers": ["external-attacher/driver-longhorn-io"]},
            "spec": {"attacher": "driver.longhorn.io", "nodeName": "metal7", "source": {"persistentVolumeName": "pvc-gone"}},
            "status": {"attached": False}}


class RemovalGuards(unittest.TestCase):
    def test_accepts_only_reviewed_deleted_unattached_identity(self):
        removal.guard(attachment(), [], "reviewed", "pvc-gone", "metal7")
        changes = [("metadata", "uid", "other"), ("metadata", "deletionTimestamp", None),
                   ("metadata", "finalizers", ["unrelated"]), ("status", "attached", True),
                   ("spec", "nodeName", "metal6"), ("spec", "attacher", "another.driver")]
        for section, key, value in changes:
            changed = attachment(); changed[section][key] = value
            with self.assertRaises(removal.UnsafeRemoval):
                removal.guard(changed, [], "reviewed", "pvc-gone", "metal7")

    def test_refuses_pv_claim_and_longhorn_references(self):
        references = [
            {"kind": "PersistentVolume", "metadata": {"name": "pvc-gone"}},
            {"kind": "PersistentVolume", "metadata": {"name": "alias"}, "spec": {"csi": {"volumeHandle": "pvc-gone"}}},
            {"kind": "PersistentVolumeClaim", "spec": {"volumeName": "pvc-gone"}},
            {"kind": "Volume", "metadata": {"name": "pvc-gone"}},
            {"kind": "Volume", "metadata": {"name": "alias"}, "status": {"kubernetesStatus": {"pvName": "pvc-gone"}}},
            {"kind": "Engine", "spec": {"volumeName": "pvc-gone"}},
            {"kind": "Replica", "spec": {"volumeName": "pvc-gone"}},
        ]
        for item in references:
            with self.assertRaises(removal.UnsafeRemoval):
                removal.guard(attachment(), [item], "reviewed", "pvc-gone", "metal7")

    def test_refuses_node_attached_or_in_use(self):
        for state in [{"volumesAttached": [{"name": "kubernetes.io/csi/driver.longhorn.io^pvc-gone"}]},
                      {"volumesInUse": ["kubernetes.io/csi/driver.longhorn.io^pvc-gone"]}]:
            with self.assertRaises(removal.UnsafeRemoval):
                removal.guard(attachment(), [{"kind": "Node", "status": state}], "reviewed", "pvc-gone", "metal7")

    def test_patch_rechecks_identity_and_only_removes_finalizer(self):
        operations = removal.patch(attachment())
        self.assertEqual([o["op"] for o in operations], ["test"] * 6 + ["remove"])
        self.assertEqual(operations[0]["value"], "reviewed")
        self.assertEqual(operations[1]["value"], "123")
        self.assertEqual(operations[-1]["path"], "/metadata/finalizers/0")


if __name__ == "__main__":
    unittest.main()
