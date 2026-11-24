"""Policy assurance verifies authored active inventory and fails closed on drift."""
from datetime import date
import copy
import json
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = list(yaml.safe_load_all((ROOT / "infra/kyverno/automation/policy-assurance.yaml").read_text()))
ASSURANCE = {}
exec(compile(DOCUMENTS[0]["data"]["assurance.py"], "assurance.py", "exec"), ASSURANCE)
TODAY = date(2026, 11, 24)
BASE = ASSURANCE["BASE"]
POLICY_FILES = [
    "migration/kyverno/policies/workloads.yaml",
    "migration/kyverno/policies/namespace.yaml",
    "migration/kyverno/policies/flux.yaml",
    "migration/kyverno/policies/runners.yaml",
    "infra/kubescape/runtime/admission.yaml",
]


class PolicyAssuranceTests(unittest.TestCase):
    def setUp(self):
        self.objects = {BASE+"validatingpolicies": [], BASE+"namespacedvalidatingpolicies": [],
                        BASE+"policyexceptions": []}
        for path in POLICY_FILES:
            for item in yaml.safe_load_all((ROOT/path).read_text()):
                kind = item["kind"]
                if kind not in ("ValidatingPolicy", "NamespacedValidatingPolicy"):
                    continue
                item["status"] = {"conditionStatus":{"ready":True,"conditions":[{"status":"True"}]}}
                self.objects[BASE+kind.lower().replace("policy","policies")].append(item)
        self.objects[BASE+"policyexceptions"] = list(yaml.safe_load_all(
            (ROOT/"migration/kyverno/exceptions/exceptions.yaml").read_text()))
        for item in self.objects[BASE+"policyexceptions"]:
            item["metadata"]["annotations"]["kyverno.cooked.beer/expires-at"]="2027-01-01"

    def evaluate(self):
        return ASSURANCE["inventory"](lambda path:self.objects[path], TODAY)

    def test_reviewed_inventory_is_exact(self):
        self.assertEqual(sum(self.evaluate().values()), 0)
        expected = {(kind, ns, name, action) for kind, ns, name, action in ASSURANCE["EXPECTED"]}
        authored = {(p["kind"],p["metadata"].get("namespace",""),p["metadata"]["name"],",".join(sorted(p["spec"]["validationActions"])))
                    for key in ("validatingpolicies","namespacedvalidatingpolicies") for p in self.objects[BASE+key]}
        self.assertEqual(expected, authored)
        self.assertEqual(len(expected),25)
        self.assertEqual(ASSURANCE["EXCEPTIONS"], {p["metadata"]["name"] for p in self.objects[BASE+"policyexceptions"]})

    def test_due_expired_invalid_and_unexpected_exceptions(self):
        items = self.objects[BASE+"policyexceptions"]
        for item, expiry in zip(items, ["2026-12-08","2026-11-23","2026-02-30","2026-12-09","2026-11-24","2027-01-01"]):
            item["metadata"]["annotations"]["kyverno.cooked.beer/expires-at"]=expiry
        result=self.evaluate()
        self.assertEqual(result["exceptionDue"],2)
        self.assertEqual(result["exceptionExpired"],1)
        self.assertEqual(result["exceptionMetadata"],1)
        items[0]["metadata"]["namespace"]="private-namespace"
        result=self.evaluate()
        self.assertEqual(result["exceptionInventory"],2)
        self.assertNotIn("private-namespace",json.dumps(result))

    def test_missing_or_weak_policy_and_unready_status(self):
        policies=self.objects[BASE+"validatingpolicies"]
        policies[0]["spec"]["validationActions"]=["Audit"]
        policies[1]["spec"]["failurePolicy"]="Ignore"
        policies[2]["spec"]["evaluation"]["admission"]["enabled"]=False
        policies[3]["status"]["conditionStatus"]["ready"]=False
        policies.pop()
        result=self.evaluate()
        self.assertEqual(result["policyInventory"],1)
        self.assertEqual(result["policyConfiguration"],3)
        self.assertEqual(result["policyReadiness"],1)

    def test_flux_guardrails_requires_both_audit_and_warn(self):
        policy = next(p for p in self.objects[BASE+"validatingpolicies"]
                      if p["metadata"]["name"] == "platform-flux-guardrails")
        for actions, failures in ((["Audit","Warn"],0), (["Warn","Audit"],0),
                                  (["Audit"],1), (["Warn"],1), (["Deny"],1)):
            with self.subTest(actions=actions):
                policy["spec"]["validationActions"]=actions
                self.assertEqual(self.evaluate()["policyConfiguration"],failures)

    def test_permissions_and_job_are_bounded(self):
        role=next(d for d in DOCUMENTS if d["kind"]=="ClusterRole")
        self.assertEqual(role["rules"],[{"apiGroups":["policies.kyverno.io"],
            "resources":["validatingpolicies","namespacedvalidatingpolicies","policyexceptions"],"verbs":["list"]}])
        job=next(d for d in DOCUMENTS if d["kind"]=="CronJob")
        spec=job["spec"]["jobTemplate"]["spec"]
        self.assertEqual(spec["backoffLimit"],0)
        self.assertEqual(spec["activeDeadlineSeconds"],180)
        self.assertEqual(job["spec"]["concurrencyPolicy"],"Forbid")
        pod=spec["template"]["spec"]
        self.assertEqual(pod["serviceAccountName"],"kyverno-policy-assurance")
        self.assertEqual(len(pod["containers"]),1)
        self.assertFalse(pod["containers"][0].get("env"))
        self.assertEqual(pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"],True)


if __name__ == "__main__":
    unittest.main()
