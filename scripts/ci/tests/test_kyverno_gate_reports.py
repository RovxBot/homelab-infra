import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kyverno_gate import load_policy_reports


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
        self.assertEqual(load_policy_reports(""), [])


if __name__ == "__main__":
    unittest.main()
