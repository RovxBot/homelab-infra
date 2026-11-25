"""Receiver public projection and independently bound publisher TokenReview tests."""
from datetime import datetime, timezone
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from security_receiver.native import SafeEvent, Rejected
from security_receiver.service import Receiver
from security_receiver.store import EventStore
from security_receiver.trust import KubernetesTrust, PUBLISHER_IMAGE


class PublisherTrust:
    authenticate_publisher=KubernetesTrust.authenticate_publisher

    def __init__(self):
        self.user="system:serviceaccount:monitoring:security-incident-publisher"
        self.pod_uid="a1b2c3-1"
        self.image=PUBLISHER_IMAGE
        self.ready=True

    def request(self,path,*,body=None):
        self.seen=json.loads(body)
        return {"status":{"authenticated":True,"audiences":["kubescape.io"],
                "user":{"username":self.user,"extra":{
                  "authentication.kubernetes.io/pod-name":["security-incident-publisher-123-abc"],
                  "authentication.kubernetes.io/pod-uid":[self.pod_uid]}}}}

    def get(self,namespace,kind,name):
        return {"metadata":{
                "name":name,"namespace":namespace,"uid":self.pod_uid,
                "labels":{"app.kubernetes.io/name":"security-incident-publisher"},
                "ownerReferences":[{"kind":"Job","name":"security-incident-publisher-123",
                                    "controller":True}]},
                "spec":{"serviceAccountName":"security-incident-publisher",
                        "containers":[{"name":"publisher","image":self.image}]},
                "status":{"phase":"Running",
                          "containerStatuses":[{"name":"publisher","ready":self.ready,
                          "containerID":"containerd://test",
                          "state":{"running":{"startedAt":"2026-11-24T04:00:00Z"}}}]}}


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=EventStore(pathlib.Path(self.temp.name)/"receiver.sqlite")
        self.addCleanup(self.store.close)
        self.trust=PublisherTrust()
        self.receiver=Receiver(self.trust,self.store)
        self.now=datetime(2026,11,24,5,0,tzinfo=timezone.utc)

    def sample(self,workload,rule="R1000",severity="High",event_id="a"*64):
        return SafeEvent(
            event_id=event_id,rule=rule,observed_at="2026-11-24T04:43:52Z",
            severity=severity,workload_key=workload,
            running_digest="sha256:"+"a"*64,digest_relation="different",
            node="metal3",agent_pod_uid="PRIVATE_AGENT_UID",
            pod_uid="PRIVATE_POD_UID",container_id="PRIVATE_CONTAINER_ID",
            deployment_uid="PRIVATE_DEPLOYMENT_UID")

    def test_signed_source_and_allowlisted_projection_are_sanitized(self):
        self.store.record(self.sample("home/homepage/Deployment/homepage/homepage"),self.now)
        self.store.record(self.sample("home/memos/Deployment/memos/memos",
                          event_id="b"*64,severity="Low"),self.now)
        self.store.record(self.sample("home/kubescape-runtime-test/Deployment/runtime-acceptance/test",
                          event_id="c"*64),self.now)
        result=self.receiver.publication("bound-test-token")
        self.assertEqual(result["schema"],1)
        self.assertEqual(len(result["incidents"]),1)
        row=result["incidents"][0]
        self.assertEqual(row["workload"],"homepage/Deployment/homepage")
        self.assertEqual(row["rule"],"R1000")
        self.assertEqual(row["severity"],"High")
        self.assertEqual(row["count"],1)
        self.assertEqual(self.trust.seen["spec"]["audiences"],["kubescape.io"])
        data=json.dumps(result)
        for private in ("PRIVATE_", "container_id", "pod_uid", "deployment_uid",
                        "running_digest", "kubescape-runtime-test", "memos"):
            self.assertNotIn(private,data)

    def test_private_projection_expands_only_reviewed_runtime_cohort(self):
        for i, identity in enumerate((
                "home/koito/Deployment/koito/koito",
                "home/koito/Deployment/koito-lastfm-bridge/multi-scrobbler",
                "home/kubescape-runtime-test/Deployment/runtime-acceptance/test",
                "home/gitea/Deployment/private/private")):
            self.store.record(self.sample(identity,event_id=str(i+1)*64),self.now)
        self.assertEqual(self.receiver.publication("bound-test-token")["incidents"],[])
        result=self.receiver.publication("bound-test-token",private=True)
        self.assertEqual({r["workload"] for r in result["incidents"]},
                         {"koito/Deployment/koito","koito/Deployment/koito-lastfm-bridge"})
        self.assertNotIn("PRIVATE_",json.dumps(result))
        self.trust.user="system:serviceaccount:monitoring:gatus-github-issue-sync"
        with self.assertRaises(Rejected):
            self.receiver.publication("untrusted",private=True)

    def test_prepared_application_projection_is_private_only(self):
        identities = ["home/monitoring/Deployment/gatus/gatus","home/openbrew/Deployment/openbrew/openbrew","home/openbrew/Deployment/openbrew/oauth2-proxy","home/openbrew/Deployment/openbrew-db/sqlserver","home/stirling-pdf/Deployment/stirling-pdf/stirling-pdf"]
        for i, identity in enumerate(identities):
            self.store.record(self.sample(identity,event_id=format(i+1,"064x")),self.now)
        self.assertEqual(self.receiver.publication("bound-test-token")["incidents"],[])
        result=self.receiver.publication("bound-test-token",private=True)
        self.assertEqual(len(result["incidents"]),5)
        self.assertEqual({r["workload"] for r in result["incidents"]},set(["monitoring/Deployment/gatus","openbrew/Deployment/openbrew","openbrew/Deployment/openbrew-db","stirling-pdf/Deployment/stirling-pdf"]))
        self.assertNotIn("PRIVATE_",json.dumps(result))

    def test_edge_messaging_and_identity_projection_remains_private(self):
        identities = ["home/cloudflared/Deployment/cloudflared/cloudflared","home/mqtt/Deployment/mosquitto/mosquitto","home/security/Deployment/vaultwarden/vaultwarden"]
        for i, identity in enumerate(identities):
            self.store.record(self.sample(identity,event_id=format(i+1,"064x")),self.now)
        self.assertEqual(self.receiver.publication("bound-test-token")["incidents"],[])
        result=self.receiver.publication("bound-test-token",private=True)
        self.assertEqual(len(result["incidents"]),3)
        self.assertEqual({r["workload"] for r in result["incidents"]},set(["cloudflared/Deployment/cloudflared","mqtt/Deployment/mosquitto","security/Deployment/vaultwarden"]))
        self.assertNotIn("PRIVATE_",json.dumps(result))

    def test_direct_controller_projection_remains_private_and_exact(self):
        identities = ["home/wotlk/StatefulSet/wotlk-mariadb/mysql","home/mop/StatefulSet/skyfire-mariadb/mysql","home/kube-system/DaemonSet/kube-proxy/kube-proxy"]
        for i, identity in enumerate(identities + ["home/kube-system/DaemonSet/cilium/cilium-agent",
                                                   "home/wotlk/Deployment/wotlk-mariadb/mysql",
                                                   "home/mop/StatefulSet/skyfire-mariadb/unreviewed"]):
            self.store.record(self.sample(identity,event_id=format(i+1,"064x")),self.now)
        self.assertEqual(self.receiver.publication("bound-test-token")["incidents"],[])
        result=self.receiver.publication("bound-test-token",private=True)
        self.assertEqual(len(result["incidents"]),3)
        self.assertEqual({r["workload"] for r in result["incidents"]},set(["wotlk/StatefulSet/wotlk-mariadb","mop/StatefulSet/skyfire-mariadb","kube-system/DaemonSet/kube-proxy"]))
        self.assertNotIn("PRIVATE_",json.dumps(result))

    def test_token_principal_image_uid_and_ready_status_fail_closed(self):
        variants=[
            ("user","system:serviceaccount:kubescape:node-agent"),
            ("image","python:latest"),
            ("pod_uid","wrong-uid"),
            ("ready",False),
        ]
        for field,bad in variants:
            with self.subTest(field=field):
                self.trust=PublisherTrust()
                setattr(self.trust,field,bad)
                self.receiver.api=self.trust
                if field=="pod_uid":
                    # Returned TokenReview UID remains the trusted baseline,
                    # while independent Pod inspection changes UID.
                    original_get=self.trust.get
                    self.trust.get=lambda ns,kind,name:dict(original_get(ns,kind,name),
                                                             metadata=dict(original_get(ns,kind,name)["metadata"],uid="forged"))
                with self.assertRaises(Rejected):
                    self.receiver.publication("valid")

    def test_no_publisher_auth_no_public_summary(self):
        self.trust.user="system:serviceaccount:monitoring:gatus-github-issue-sync"
        with self.assertRaises(Rejected):
            self.receiver.publication("not-publisher")


if __name__=="__main__":
    unittest.main()
