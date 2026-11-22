"""Prove that the Kubescape runtime-status exception cannot widen Flux edit access."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTOR = "system:serviceaccount:kubescape:kubescape-assessment"
NAME = "kubescape-assessment-status"


class AssessmentStatusGuardrailTests(unittest.TestCase):
    def test_cel_exception_requires_exact_operation_identity_and_resource(self):
        data = yaml.safe_load((ROOT / "migration/kyverno/policies/flux.yaml").read_text())
        self.assertEqual(data["metadata"]["name"], "platform-flux-guardrails")
        self.assertEqual(data["spec"]["validationActions"], ["Audit", "Warn"])
        conditions = {c["name"]: c["expression"] for c in data["spec"]["matchConditions"]}
        expr = conditions["kubescape-assessment-status-writer"]
        expected = [
            "request.operation == 'UPDATE'",
            "request.resource.group == ''",
            "request.resource.version == 'v1'",
            "request.resource.resource == 'configmaps'",
            "request.?namespace.orValue('') == 'kubescape'",
            "request.name == 'kubescape-assessment-status'",
            "request.userInfo.username == '" + ACTOR + "'",
        ]
        for clause in expected:
            with self.subTest(clause=clause):
                self.assertIn(clause, expr)
        self.assertEqual(expr.count(" && "), len(expected)-1)
        self.assertTrue(expr.startswith("!(") and expr.endswith(")"))
        self.assertIn("has-flux-label", conditions)
        self.assertIn("longhorn-controller", conditions)
        self.assertIn("system:serviceaccount:flux-system:kustomize-controller",
                      data["spec"]["validations"][0]["expression"])

    def test_legacy_exclusion_has_exact_identity_and_no_delete_or_create(self):
        data = yaml.safe_load((ROOT / "infra/kyverno/policies/platform-flux-guardrails.yaml").read_text())
        rule = data["spec"]["rules"][0]
        self.assertEqual(rule["validate"]["failureAction"], "Audit")
        exceptions = [entry for entry in rule["exclude"]["any"]
                      if entry.get("subjects", [{}])[0].get("name") == "kubescape-assessment"]
        self.assertEqual(len(exceptions), 1)
        self.assertEqual(exceptions[0]["resources"], {
            "kinds": ["ConfigMap"], "names": [NAME],
            "namespaces": ["kubescape"], "operations": ["UPDATE"],
        })
        self.assertEqual(exceptions[0]["subjects"], [{
            "kind": "ServiceAccount", "name": "kubescape-assessment", "namespace": "kubescape",
        }])

    def test_generator_is_source_of_truth(self):
        source = (ROOT / "migration/kyverno/harness/build.py").read_text()
        self.assertIn('name="kubescape-assessment-status-writer"', source)
        self.assertIn("request.name == 'kubescape-assessment-status'", source)
        self.assertIn(ACTOR, source)


if __name__ == "__main__":
    unittest.main()
