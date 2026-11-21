import json
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_kyverno_migration_inventory as check


class MigrationInventoryTests(unittest.TestCase):
    def setUp(self):
        self.inventory = json.loads((check.ROOT / check.INVENTORY).read_text())

    def test_current_repository_has_complete_coverage(self):
        self.assertEqual(check.check(check.ROOT, self.inventory), [])
        self.assertEqual(len(self.inventory["policies"]), 8)
        self.assertEqual(sum(len(p["rules"]) for p in self.inventory["policies"]), 29)
        self.assertEqual(len(self.inventory["exceptions"]), 6)

    def test_new_legacy_policy_outside_kyverno_directory_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "apps/new-app/policy.yaml"
            path.parent.mkdir(parents=True)
            path.write_text(yaml.safe_dump({"apiVersion": "kyverno.io/v2", "kind": "CleanupPolicy",
                                           "metadata": {"name": "new", "namespace": "new-app"}, "spec": {}}))
            errors = check.check(root, self.inventory)
            self.assertTrue(any("Unmapped legacy resource" in e for e in errors))

    def test_removed_rule_mapping_fails(self):
        self.inventory["policies"][0]["rules"].pop()
        self.assertTrue(any("Rule coverage differs" in e for e in check.check(check.ROOT, self.inventory)))

    def test_source_contract_drift_fails(self):
        self.inventory["policies"][0]["contract_sha256"] = "0" * 64
        self.assertTrue(any("Source contract changed" in e for e in check.check(check.ROOT, self.inventory)))

    def test_policy_annotations_are_part_of_the_contract(self):
        original = next(iter(check.discover(check.ROOT).values()))[1]
        changed = dict(original, metadata=dict(original["metadata"], annotations={
            "pod-policies.kyverno.io/autogen-controllers": "Deployment",
        }))
        self.assertNotEqual(check.fingerprint(check.resource_contract(original)),
                            check.fingerprint(check.resource_contract(changed)))

    def test_enforcement_cannot_be_mislabelled_audit(self):
        rule = next(r for p in self.inventory["policies"] for r in p["rules"] if r["enforcement"] == "Enforce")
        rule["enforcement"] = "Audit"
        self.assertTrue(any("Mapped enforcement/background differs" in e for e in check.check(check.ROOT, self.inventory)))

    def test_unplanned_exception_target_fails(self):
        self.inventory["exceptions"][0]["target_policy_refs"].append({"kind": "ValidatingPolicy", "name": "*"})
        self.assertTrue(any("Exception target scope differs" in e for e in check.check(check.ROOT, self.inventory)))

    def test_grouping_all_pod_rules_would_widen_exceptions(self):
        # Even coordinated changes to the proposed policy and exception names
        # must not exempt unrelated rules through a shared policy target.
        policy = next(p for p in self.inventory["policies"] if p["name"] == "platform-pod-standards")
        for rule in policy["rules"]:
            rule["target"]["name"] = "combined-pod-policy"
        for exception in self.inventory["exceptions"]:
            exception["target_policy_refs"] = [{"kind": "ValidatingPolicy", "name": "combined-pod-policy"}]
        self.assertTrue(any("exempt additional rules" in e for e in check.check(check.ROOT, self.inventory)))

    def test_exception_scope_snapshot_cannot_drift(self):
        self.inventory["exceptions"][0]["match"] = {"any": [{"resources": {"namespaces": ["*"]}}]}
        self.assertTrue(any("Exception snapshot differs" in e for e in check.check(check.ROOT, self.inventory)))

    def test_omitted_planned_tests_fail(self):
        self.inventory["policies"][0]["rules"][0]["test_cases"] = []
        self.assertTrue(any("Missing planned tests" in e for e in check.check(check.ROOT, self.inventory)))

    def test_app_custom_yaml_tags_do_not_hide_resource_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "apps/home-assistant/config.yaml"
            path.parent.mkdir(parents=True)
            path.write_text("packages: !include packages.yaml\n")
            self.assertEqual(check.discover(Path(temp)), {})


if __name__ == "__main__":
    unittest.main()
