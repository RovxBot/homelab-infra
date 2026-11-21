import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kyverno_gate import load_policy_reports, resource_batches, evaluate_resources


def report(kind="ClusterReport", result="fail"):
    return {"kind": kind, "apiVersion": "openreports.io/v1alpha1", "results": [
        {"result": result, "policy": "test", "message": "details include {braces}",
         "resources": [{"kind": "DaemonSet", "name": "test", "namespace": "longhorn-system"}]}]}


class PolicyReportParsingTests(unittest.TestCase):
    def test_reads_current_and_legacy_report_kinds(self):
        for kind in ["PolicyReport", "ClusterPolicyReport", "Report", "ClusterReport"]:
            expected = report(kind)
            self.assertEqual(load_policy_reports(json.dumps(expected)), [expected])

    def test_mutation_diagnostics_and_resources_do_not_hide_failures(self):
        expected = report()
        output = "policy test applied:\napiVersion: apps/v1\nkind: DaemonSet\nlifecycle: {}\n---\nMutation:\nMutation has been applied successfully."
        output += json.dumps(expected)
        self.assertEqual(load_policy_reports(output), [expected])
        self.assertEqual(load_policy_reports(output)[0]["results"][0]["result"], "fail")

    def test_reads_multiple_reports_and_ignores_unrelated_json(self):
        first, second = report(result="pass"), report("PolicyReport")
        output = json.dumps({"kind": "DaemonSet", "spec": {}}) + "\n" + json.dumps(first) + "\n---\n" + json.dumps(second)
        self.assertEqual(load_policy_reports(output), [first, second])

    def test_rejects_missing_or_malformed_reports(self):
        for output in ["Mutation has been applied successfully.", '{"kind":"ClusterReport",', '{"kind":"DaemonSet"}']:
            with self.assertRaises(RuntimeError):
                load_policy_reports(output)
        with self.assertRaises(RuntimeError):
            load_policy_reports('{"kind":"ClusterReport","results":\n' + json.dumps(report(result="pass")))
        self.assertEqual(load_policy_reports(""), [])


class EvaluationTests(unittest.TestCase):
    def evaluate(self, output, code=0):
        with patch("kyverno_gate.run_command", return_value=CompletedProcess([], code, output, "")):
            return evaluate_resources(kyverno_bin=Path("kyverno"), repo_root=Path("."),
                                      policy_dir=Path("policies"), exception_files=[], resource_dir=Path("resources"))

    def test_conflicting_namespace_variants_are_both_evaluated(self):
        valid = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "shared", "labels": {"owner": "platform"}}}
        invalid = {**valid, "metadata": {"name": "shared"}}
        batches = resource_batches([valid, valid, invalid])
        self.assertEqual(batches, [[valid], [invalid]])

    def test_namespace_aliases_cannot_panic_the_fake_client(self):
        first = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "shared"}}
        alias = {**first, "metadata": {"name": "shared", "namespace": "different"}}
        self.assertEqual(resource_batches([first, alias]), [[first], [alias]])

    def test_cel_reports_retain_legacy_debt_identity(self):
        obj = report()
        obj["results"][0].update(source="KyvernoValidatingPolicy", policy="platform-pod-standards-require-owner-label", rule="validation")
        failure, = self.evaluate(json.dumps(obj), 1)
        self.assertEqual((failure.policy, failure.rule), ("platform-pod-standards", "require-owner-label"))

    def test_collection_and_evaluation_errors_are_not_healthy(self):
        with self.assertRaises(RuntimeError):
            self.evaluate("")
        with self.assertRaises(RuntimeError):
            self.evaluate(json.dumps(report(result="error")))


if __name__ == "__main__":
    unittest.main()
