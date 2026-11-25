"""Private policy evidence and routing boundaries."""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
import textwrap
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "infra/observability/security-incident-publisher"
sys.path.insert(0, str(DIR))
import policy as P
sys.path.pop(0)
NOW = datetime(2026, 11, 24, 10, tzinfo=timezone.utc)


class PolicyPublisherTests(unittest.TestCase):
    def exceptions(self):
        return list(yaml.safe_load_all((ROOT / "migration/kyverno/exceptions/exceptions.yaml").read_text()))

    def test_expiry_review_and_expired_share_identity(self):
        objects = self.exceptions()
        for item in objects:
            item["metadata"]["annotations"]["kyverno.cooked.beer/expires-at"] = "2027-01-01"
        item = objects[0]
        item["metadata"]["annotations"]["kyverno.cooked.beer/expires-at"] = "2026-12-08"
        read = lambda path: {"items": objects}
        due, = P.exception_items(read, NOW.date())
        self.assertEqual(due["details"]["status"], "Review due")
        item["metadata"]["annotations"]["kyverno.cooked.beer/expires-at"] = "2026-11-23"
        expired, = P.exception_items(read, NOW.date())
        self.assertEqual(due["key"], expired["key"])
        self.assertEqual(expired["details"]["status"], "Expired")
        item["metadata"]["annotations"]["kyverno.cooked.beer/expires-at"] = "2026-02-30"
        invalid, = P.exception_items(read, NOW.date())
        self.assertEqual(invalid["details"]["status"], "Invalid accountability metadata")
        objects.pop(0)
        missing, = P.exception_items(read, NOW.date())
        self.assertEqual(missing["key"], due["key"])
        self.assertEqual(missing["details"]["status"], "Expected exception missing")

    def test_private_persistent_report_has_no_raw_message(self):
        report = {"scope": {"kind":"Deployment","name":"private-app","namespace":"private"},
                  "results":[{"source":"KyvernoValidatingPolicy",
                   "policy":"platform-pod-standards-require-owner-label","rule":"validation",
                   "result":"fail","timestamp":"2026-11-22T10:00:00Z",
                   "message":"TOKEN_SECRET_RAW_PAYLOAD"}]}
        read = lambda path: {"items": [report] if path.endswith("/policyreports") else []}
        item, = P.policy_items(read, NOW)
        title, body = P.render_policy(item)
        self.assertIn("private-app", title)
        self.assertNotIn("TOKEN_SECRET_RAW_PAYLOAD",json.dumps(item)+body)
        for timestamp in ("2026-11-24T09:59:59Z","2026-11-25T10:00:00Z","invalid"):
            report["results"][0]["timestamp"]=timestamp
            self.assertEqual(P.policy_items(read,NOW),[])
        report["results"][0].update(timestamp="2026-11-22T10:00:00Z",result="skip")
        self.assertEqual(P.policy_items(read,NOW),[])

    def test_actionable_exception_guidance_and_stable_identity(self):
        self.assertTrue((ROOT/P.EXCEPTION_SOURCE).is_file())
        self.assertEqual(set(P.EXCEPTION_GUIDANCE),P.EXCEPTIONS)
        item=P.stable_item("exception",("kyverno","wotlk-latest-tag"),
                           {"status":"Review due","expires":"2026-12-05"})
        title,body=P.render_policy(item)
        for value in ("## Required action","## Resolution checklist",P.EXCEPTION_SOURCE,
                      "weekly WotLK restart job","immutable image digest","2026-12-05",
                      "not proof of an active policy violation","Automatic closure is not enabled yet"):
            self.assertIn(value,body)
        self.assertEqual(body.count(P.marker(item["key"])),1)
        self.assertNotEqual(P.policy_revision(item),P.revision(item))
        self.assertEqual(P.policy_revision(item),P.policy_revision(dict(item)))
        unknown=P.stable_item("exception",("other","wotlk-latest-tag"),
                             {"status":"Review due","expires":"2026-12-05","inventory":"Unexpected exception"})
        self.assertNotIn("weekly WotLK restart job",P.render_policy(unknown)[1])

    def test_actionable_policy_guidance_and_unknown_fallback(self):
        for (policy,rule),(path,guidance) in P.RULE_GUIDANCE.items():
            self.assertTrue((ROOT/path).is_file())
            item=P.stable_item("violation",(policy,rule,"Deployment","private","app"),
                               {"status":"Persistent failure"})
            body=P.render_policy(item)[1]
            self.assertIn(path,body)
            self.assertIn(guidance,body)
            self.assertIn("fresh positive evaluation",body)
        item=P.stable_item("violation",("unknown","rule","Deployment","private","app"),
                           {"status":"Persistent failure"})
        self.assertIn("not established by this report",P.render_policy(item)[1])

    def test_budget_deferral_does_not_hide_other_policy_updates(self):
        from unittest.mock import Mock
        writer=Mock()
        writer.sync.side_effect=[P.WriteBudgetDeferred("write_budget_deferred"),"updated"]
        items=[P.stable_item("exception",("kyverno",name),{"status":"Review due"})
               for name in ("wotlk-latest-tag","home-assistant")]
        self.assertEqual(P.sync_policy_items(writer,items),["deferred","updated"])
        writer.sync.reset_mock()
        writer.sync.side_effect=OSError("ambiguous")
        with self.assertRaises(OSError):
            P.sync_policy_items(writer,items)
        writer.sync.assert_called_once()

    def test_canonical_aliases_match_existing_reviewed_registry(self):
        old = yaml.safe_load((ROOT/"infra/kyverno/automation/github-issue-sync-configmap.yaml").read_text())["data"]["sync.py"]
        def block(text):
            return text.split("# BEGIN GENERATED POLICY IDENTITIES",1)[1].split("# END GENERATED POLICY IDENTITIES",1)[0]
        self.assertEqual(block(old),block((DIR/"policy.py").read_text()))

    def test_read_only_rbac_and_no_public_credential(self):
        docs = list(yaml.safe_load_all((DIR/"policy-workload.yaml").read_text()))
        role=next(d for d in docs if d["kind"]=="ClusterRole")
        self.assertEqual(role["rules"],[
            {"apiGroups":["wgpolicyk8s.io"],"resources":["clusterpolicyreports","policyreports"],"verbs":["list"]},
            {"apiGroups":["policies.kyverno.io"],"resources":["policyexceptions"],"verbs":["list"]}])
        job=next(d for d in docs if d["kind"]=="CronJob")
        pod=job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        self.assertEqual(pod["serviceAccountName"],"security-policy-publisher")
        self.assertEqual(pod["containers"][0]["env"],[{"name":"GITEA_TOKEN",
            "valueFrom":{"secretKeyRef":{"name":"homelab-security-gitea","key":"token"}}}])
        self.assertTrue(pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(job["spec"]["concurrencyPolicy"],"Forbid")
        self.assertEqual(job["spec"]["jobTemplate"]["spec"]["backoffLimit"],0)
        old=yaml.safe_load((ROOT/"infra/kyverno/automation/github-issue-sync-cronjob.yaml").read_text())
        self.assertIs(old["spec"]["suspend"],True)
        self.assertNotIn("api.github.com",(DIR/"policy.py").read_text())


if __name__=="__main__":
    unittest.main()
