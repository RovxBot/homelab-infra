"""Test the actual draft publisher with a fake private advisory service."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import yaml

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "infra/observability/security-advisory-publisher"
SPEC = importlib.util.spec_from_file_location("advisory", DIR/"publisher.py")
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
ENTRY = {"schema":1, "findingId":"reviewed-example", "approved":True,
         "source":"homelab-infra", "commit":"a"*40, "summary":"Synthetic test only",
         "description":"Private test evidence", "severity":"high",
         "affectedVersionRange":"test-version", "patchedVersions":None}


class Remote:
    def __init__(self):
        self.items = []
        self.calls = 0
        self.lose_response = False

    def list(self):
        return copy.deepcopy(self.items)

    def create(self, payload):
        self.calls += 1
        self.items.append({**payload, "state":"draft", "ghsa_id":"GHSA-abcd-efgh-jkmn"})
        if self.lose_response:
            raise TimeoutError()
        return copy.deepcopy(self.items[-1])


class AdvisoryTests(unittest.TestCase):
    def test_idle_does_not_create_or_write_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            remote = Remote()
            self.assertEqual(P.sync(remote,state),"idle")
            self.assertEqual(remote.calls,0)
            self.assertEqual(list(state.iterdir()),[])

    def test_only_reviewed_project_vulnerability_can_be_submitted(self):
        for changed in ({"approved":False},{"approved":1},{"source":"kubescape-cve"},
                        {"commit":"bad"},{"findingId":"../bad"},{"severity":"unknown"},
                        {"state":"published"},{"cve_id":"CVE-2026-1234"},
                        {"description":"<!-- homelab-advisory:bad -->"}):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as tmp:
                remote=Remote()
                with self.assertRaises(ValueError):
                    P.sync(remote,Path(tmp),{**ENTRY,**changed})
                self.assertEqual(remote.calls,0)

    def test_draft_payload_and_replay_across_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)
            remote=Remote()
            self.assertEqual(P.sync(remote,state,ENTRY),"created")
            payload=remote.items[0]
            self.assertEqual(payload["state"],"draft")
            self.assertIsNone(payload["cve_id"])
            self.assertIs(payload["start_private_fork"],False)
            self.assertEqual(payload["vulnerabilities"][0]["package"],{"ecosystem":"other","name":"homelab-infra"})
            self.assertEqual(P.sync(remote,state,ENTRY),"existing")
            remote.items[0]["state"]="closed"
            self.assertEqual(P.sync(remote,state,ENTRY),"existing")
            self.assertEqual(remote.calls,1)

    def test_lost_response_reconciles_only_unique_remote_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)
            remote=Remote()
            remote.lose_response=True
            with self.assertRaises(TimeoutError):
                P.sync(remote,state,ENTRY)
            self.assertEqual(P.sync(remote,state,ENTRY),"existing")
            remote.items=[]
            self.assertEqual(P.sync(remote,state,ENTRY),"pending")
            self.assertEqual(remote.calls,1)

    def test_ambiguous_markers_refuse_new_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote=Remote()
            _,marker,_=P.approved_payload(ENTRY)
            remote.items=[{"description":marker,"state":"draft","ghsa_id":"GHSA-abcd-efgh-jkmn"}]*2
            with self.assertRaises(ValueError):
                P.sync(remote,Path(tmp),ENTRY)
            self.assertEqual(remote.calls,0)

    def test_http_boundary_never_supports_update_or_arbitrary_endpoint(self):
        client=P.GitHub("x"*40)
        with mock.patch.object(client,"opener") as opener:
            with self.assertRaises(ValueError):
                client.request("PATCH",{"state":"published"})
            opener.open.assert_not_called()
            response=opener.open.return_value.__enter__.return_value
            response.status=200
            response.headers={"Link":'<https://example.com>; rel="next"'}
            response.read.return_value=b"[]"
            with self.assertRaises(ValueError):
                client.list()
            request=opener.open.call_args.args[0]
            self.assertEqual(request.full_url,P.URL+"?per_page=100")
            self.assertEqual(request.get_method(),"GET")

    def test_isolated_workload_and_optional_queue(self):
        docs=list(yaml.safe_load_all((DIR/"workload.yaml").read_text()))
        self.assertEqual({d["kind"] for d in docs},{"ServiceAccount","PersistentVolumeClaim","CronJob"})
        job=next(d for d in docs if d["kind"]=="CronJob")
        spec=job["spec"]["jobTemplate"]["spec"]
        self.assertEqual(spec["backoffLimit"],0)
        self.assertEqual(job["spec"]["concurrencyPolicy"],"Forbid")
        pod=spec["template"]["spec"]
        self.assertIs(pod["automountServiceAccountToken"],False)
        volumes={v["name"]:v for v in pod["volumes"]}
        self.assertEqual(volumes["credential"]["secret"]["secretName"],"homelab-security-advisories")
        self.assertEqual(volumes["queue"]["secret"]["secretName"],"security-advisory-approved")
        self.assertIs(volumes["queue"]["secret"]["optional"],True)
        self.assertEqual(volumes["state"]["persistentVolumeClaim"]["claimName"],"security-advisory-publisher-state")
        self.assertFalse(any("projected" in v for v in volumes.values()))
        self.assertEqual(pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"],True)


if __name__=="__main__":
    unittest.main()
