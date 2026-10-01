import copy
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "longhorn-b2-object-lifecycle.py"
spec = importlib.util.spec_from_file_location("lifecycle", path)
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


def fixture():
    rule = {"fileNamePrefix": "longhorn/", "daysFromHidingToDeleting": 1,
            "daysFromUploadingToHiding": None}
    return {"bucketName": "cooked-k8s", "bucketId": "bucket-id", "revision": 3,
            "lifecycleRules": []}, {"bucketName": "cooked-k8s", "lifecycleRules": [rule]}


class BucketLifecycleGuards(unittest.TestCase):
    def test_photo_rule_preserves_current_pack_and_snapshot_objects(self):
        bucket, policy = fixture()
        bucket["bucketName"] = policy["bucketName"] = "cooked-photos"
        policy["lifecycleRules"][0]["fileNamePrefix"] = "restic/immich-uploads/"
        self.assertEqual(lifecycle.desired_rules(bucket, policy), policy["lifecycleRules"])
        changed = copy.deepcopy(policy)
        changed["lifecycleRules"][0]["daysFromUploadingToHiding"] = 1
        with self.assertRaises(lifecycle.UnsafeLifecycle):
            lifecycle.desired_rules(bucket, changed)
        changed = copy.deepcopy(policy)
        changed["lifecycleRules"][0]["fileNamePrefix"] = "restic/"
        with self.assertRaises(lifecycle.UnsafeLifecycle):
            lifecycle.desired_rules(bucket, changed)

    def test_refuses_expiration_of_current_objects_and_wrong_bucket(self):
        bucket, policy = fixture()
        for field, value in [("daysFromUploadingToHiding", 1), ("daysFromHidingToDeleting", 0),
                             ("fileNamePrefix", "")]:
            changed = copy.deepcopy(policy); changed["lifecycleRules"][0][field] = value
            with self.assertRaises(lifecycle.UnsafeLifecycle):
                lifecycle.desired_rules(bucket, changed)
        changed = copy.deepcopy(bucket); changed["bucketName"] = "photos"
        with self.assertRaises(lifecycle.UnsafeLifecycle):
            lifecycle.desired_rules(changed, policy)

    def test_preserves_unrelated_rules_and_is_idempotent(self):
        bucket, policy = fixture()
        unrelated = {"fileNamePrefix": "logs/", "daysFromUploadingToHiding": 30}
        bucket["lifecycleRules"] = [unrelated]
        self.assertEqual(lifecycle.desired_rules(bucket, policy), [unrelated] + policy["lifecycleRules"])
        bucket["lifecycleRules"] += copy.deepcopy(policy["lifecycleRules"])
        bucket["lifecycleRules"][1]["ruleId"] = "server-generated"
        self.assertEqual(lifecycle.desired_rules(bucket, policy), bucket["lifecycleRules"])

    def test_refuses_broader_or_nested_conflicting_rules(self):
        bucket, policy = fixture()
        for prefix in ["", "longhorn/", "longhorn/backupstore/"]:
            changed = copy.deepcopy(bucket)
            changed["lifecycleRules"] = [{"fileNamePrefix": prefix, "daysFromUploadingToHiding": 7}]
            with self.assertRaises(lifecycle.UnsafeLifecycle):
                lifecycle.desired_rules(changed, policy)
        bucket["lifecycleRules"] = policy["lifecycleRules"] * 2
        with self.assertRaises(lifecycle.UnsafeLifecycle):
            lifecycle.desired_rules(bucket, policy)

    def test_update_requires_exact_reviewed_revision_and_only_changes_rules(self):
        bucket, policy = fixture()
        with self.assertRaises(lifecycle.UnsafeLifecycle):
            lifecycle.update_payload(bucket, policy, 2)
        self.assertEqual(lifecycle.update_payload(bucket, policy, 3), {
            "bucketId": "bucket-id", "ifRevisionIs": 3, "lifecycleRules": policy["lifecycleRules"]})


if __name__ == "__main__":
    unittest.main()
