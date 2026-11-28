"""Native Kubescape receiver trust/storage tests. All operations are offline."""
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from security_receiver.native import Rejected, normalize
from security_receiver.service import Receiver
from security_receiver.store import EventStore
from security_receiver.trust import KubernetesTrust
from test_security_receiver_native import SOURCE, POD, NOW, INDEX, payload, encoded

AGENT_IMAGE = ("quay.io/kubescape/node-agent:v0.3.310@sha256:"
               "99152dbefd07e524418f682882ad300cb74821aaa4e42e6477c42a9fa4686e60")


def mock_resources():
    app = dict(kind="Deployment",apiVersion="apps/v1",metadata=dict(name=POD.deployment, uid=POD.deployment_uid,
        namespace=POD.namespace), spec=dict(template=dict(spec=dict(containers=[
            dict(name=POD.container,image='example@'+INDEX)]))))
    pod = dict(metadata=dict(name=POD.pod_name,uid=POD.pod_uid,namespace=POD.namespace,
        ownerReferences=[dict(apiVersion='apps/v1',kind='ReplicaSet',name='test-rs',uid='rs-uid',controller=True)]),
        spec=dict(nodeName=POD.node,containers=[
            dict(name=POD.container,image='example@'+INDEX)]),
        status=dict(phase='Running',containerStatuses=[
            dict(name=POD.container,ready=True,containerID=POD.container_id,
                imageID='docker.io/example@'+INDEX,
                state=dict(running=dict(startedAt=POD.started_at)))]))
    rs = dict(kind='ReplicaSet',apiVersion='apps/v1',metadata=dict(name='test-rs',namespace=POD.namespace,uid='rs-uid',ownerReferences=[
        dict(apiVersion='apps/v1',kind='Deployment',name=POD.deployment,uid=POD.deployment_uid,controller=True)]))
    agent = dict(metadata=dict(name='node-agent-test',uid=SOURCE.agent_pod_uid,
        namespace='kubescape',ownerReferences=[
            dict(kind='DaemonSet',name='node-agent',uid='ds-uid',controller=True)]),
        spec=dict(nodeName=SOURCE.node,serviceAccountName='node-agent',
            containers=[dict(name='node-agent',image=AGENT_IMAGE)]),
        status=dict(phase='Running',containerStatuses=[
            dict(name='node-agent',ready=True,containerID='containerd://agent',
                 imageID=AGENT_IMAGE,
                 state=dict(running=dict(startedAt='2026-11-23T10:00:00Z')))]))
    ds=dict(metadata=dict(uid='ds-uid'),spec=dict(template=dict(spec=dict(
        serviceAccountName='node-agent',containers=[
            dict(name='node-agent',image=AGENT_IMAGE)]))))
    return {('kubescape','pods','node-agent-test'):agent,
            ('kubescape','daemonsets','node-agent'):ds,
            (POD.namespace,'pods',POD.pod_name):pod,
            (POD.namespace,'replicasets','test-rs'):rs,
            (POD.namespace,'deployments',POD.deployment):app}


class FakeTrust:
    authenticate = KubernetesTrust.authenticate
    inventory = KubernetesTrust.inventory

    def __init__(self):
        self.resources=mock_resources()
        self.nodes=frozenset(('metal0','metal2','metal3','metal4','metal5','metal6','metal7','metal8'))
        self.namespaces=frozenset((POD.namespace,))
        self.review=dict(status=dict(authenticated=True,audiences=['kubescape.io'],
            user=dict(username='system:serviceaccount:kubescape:node-agent',
            extra={'authentication.kubernetes.io/pod-name':['node-agent-test'],
                   'authentication.kubernetes.io/pod-uid':[SOURCE.agent_pod_uid]})))

    def request(self,path,*,body=None):
        if body is None:
            raise AssertionError("Unexpected unauthenticated resource request")
        self.seen_review=json.loads(body)
        return copy.deepcopy(self.review)

    def get(self,namespace,kind,name):
        return copy.deepcopy(self.resources[(namespace,kind,name)])


class TrustedSourceTests(unittest.TestCase):
    def setUp(self):
        self.api=FakeTrust()

    def test_token_review_bound_agent_and_kubernetes_owner_chain(self):
        source=self.api.authenticate('synthetic-pod-bound-token')
        self.assertEqual(self.api.seen_review['spec']['audiences'],['kubescape.io'])
        self.assertEqual(source,SOURCE)
        pod=self.api.inventory(source,payload()['spec']['alerts'][0])
        self.assertEqual(pod,POD)
        event=normalize(encoded(payload()),source,pod,NOW)
        self.assertEqual(event.digest_relation,'different')
        self.assertEqual(event.running_digest,INDEX)

    def test_reject_unauthenticated_wrong_service_account_and_audience(self):
        for modification in ('false','wrong_account','wrong_audience','no_pod_bound_uid'):
            with self.subTest(modification=modification):
                self.api=FakeTrust()
                status=self.api.review['status']
                if modification=='false': status['authenticated']=False
                if modification=='wrong_account':
                    status['user']['username']='system:serviceaccount:kubescape:other'
                if modification=='wrong_audience': status['audiences']=['wrong']
                if modification=='no_pod_bound_uid':
                    del status['user']['extra']['authentication.kubernetes.io/pod-uid']
                with self.assertRaises(Rejected):
                    self.api.authenticate('untrusted')

    def test_source_identity_accepts_exactly_eight_node_names(self):
        for node in sorted(self.api.nodes):
            with self.subTest(node=node):
                self.api.resources[('kubescape','pods','node-agent-test')]['spec']['nodeName']=node
                self.assertEqual(self.api.authenticate('synthetic-pod-bound-token').node,node)
        for outsider in ('metal1','metal9','other-cluster'):
            with self.subTest(outside=outsider):
                self.api.resources[('kubescape','pods','node-agent-test')]['spec']['nodeName']=outsider
                with self.assertRaisesRegex(Rejected,'source_node_invalid'):
                    self.api.authenticate('synthetic-pod-bound-token')

    def test_pinned_agent_spec_accepts_distinct_valid_running_manifest(self):
        # OCI index pins and platform imageIDs can differ legitimately.
        # The independently read DaemonSet and Pod image specs must still
        # match the reviewed pin, and an actual running digest is required.
        agent=self.api.resources[('kubescape','pods','node-agent-test')]
        agent['status']['containerStatuses'][0]['imageID']='quay.io/kubescape/node-agent@sha256:'+('f'*64)
        self.assertEqual(self.api.authenticate('valid'),SOURCE)
        agent['status']['containerStatuses'][0]['imageID']='quay.io/kubescape/node-agent@sha256:malformed'
        with self.assertRaisesRegex(Rejected,'agent_image_invalid'):
            self.api.authenticate('valid')

    def test_reject_owner_chain_or_agent_image_spoof(self):
        for change in ('owner','uid','image','node'):
            with self.subTest(change=change):
                self.api=FakeTrust()
                agent=self.api.resources[('kubescape','pods','node-agent-test')]
                if change=='owner': agent['metadata']['ownerReferences'][0]['uid']='wrong'
                if change=='uid': agent['metadata']['uid']='wrong'
                if change=='image': agent['spec']['containers'][0]['image']='evil'
                if change=='node': agent['spec']['nodeName']='unreviewed'
                with self.assertRaises(Rejected):
                    self.api.authenticate('valid')

    def test_direct_controller_ownership_qualification_and_rejections(self):
        for namespace,kind,name,resource in (
            ("wotlk","StatefulSet","wotlk-mariadb","statefulsets"),
            ("kube-system","DaemonSet","kube-proxy","daemonsets")):
            self.api=FakeTrust()
            self.api.namespaces=frozenset((namespace,))
            pod=self.api.resources[(POD.namespace,"pods",POD.pod_name)]
            app=self.api.resources[(POD.namespace,"deployments",POD.deployment)]
            pod["metadata"]["namespace"]=namespace
            pod["metadata"]["ownerReferences"]=[dict(apiVersion="apps/v1",kind=kind,
                name=name,uid=POD.deployment_uid,controller=True)]
            app.update(kind=kind,apiVersion="apps/v1")
            app["metadata"].update(namespace=namespace,name=name)
            self.api.resources[(namespace,"pods",POD.pod_name)]=pod
            self.api.resources[(namespace,resource,name)]=app
            p=payload()
            alert=p["spec"]["alerts"][0]
            alert.update(namespace=namespace,workloadKind=kind,workloadName=name)
            trusted=self.api.inventory(SOURCE,alert)
            self.assertEqual(trusted.workload_kind,kind)
            self.assertEqual(normalize(encoded(p),SOURCE,trusted,NOW).workload_key,
                             "/".join(("home",namespace,kind,name,POD.container)))
            # No claim may change the independently derived owner.
            alert["workloadKind"]="Deployment"
            with self.assertRaises(Rejected):
                normalize(encoded(p),SOURCE,self.api.inventory(SOURCE,alert),NOW)
            alert["workloadKind"]=kind
            ref=pod["metadata"]["ownerReferences"][0]
            for target,key,bad in ((ref,"uid","wrong"),(ref,"apiVersion","batch/v1"),
                                  (ref,"kind","Job"),(ref,"name","unreviewed"),
                                  (app,"kind","Deployment"),(app["metadata"],"namespace","elsewhere"),
                                  (app["metadata"],"name","wrong"),
                                  (app["metadata"],"deletionTimestamp","now")):
                old=target.get(key)
                target[key]=bad
                with self.assertRaises(Rejected):
                    self.api.inventory(SOURCE,alert)
                if old is None:
                    target.pop(key)
                else:
                    target[key]=old
            pod["metadata"]["ownerReferences"].append(copy.deepcopy(ref))
            with self.assertRaises(Rejected):
                self.api.inventory(SOURCE,alert)

    def test_reject_untrusted_workload_attribution(self):
        for change in ('pod_uid','owner','container_image','container_id','node'):
            with self.subTest(change=change):
                self.api=FakeTrust()
                pod=self.api.resources[(POD.namespace,'pods',POD.pod_name)]
                if change=='pod_uid': pod['metadata']['uid']='new-pod'
                if change=='owner': pod['metadata']['ownerReferences'][0]['uid']='wrong'
                if change=='container_image': pod['spec']['containers'][0]['image']='wrong'
                if change=='container_id':
                    pod['status']['containerStatuses'][0]['containerID']='other'
                if change=='node': pod['spec']['nodeName']='metal6'
                if change in ('container_id','pod_uid'):
                    # Inventory must reflect the actual trusted Pod; native event
                    # still rejects its forged/mismatched claimed identity.
                    trusted=self.api.inventory(SOURCE,payload()['spec']['alerts'][0])
                    with self.assertRaises(Rejected):
                        normalize(encoded(payload()),SOURCE,trusted,NOW)
                else:
                    with self.assertRaises(Rejected):
                        self.api.inventory(SOURCE,payload()['spec']['alerts'][0])


class DurableReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.filename=pathlib.Path(self.temp.name)/'receiver.sqlite'
        self.db=EventStore(self.filename)
        self.addCleanup(lambda: self.db.close() if self.db is not None else None)
        self.api=FakeTrust()
        self.receiver=Receiver(self.api,self.db)

    def post(self,record=None):
        return self.receiver.process('synthetic-pod-bound-token',encoded(record or payload()),NOW)

    def test_commit_then_retry_same_incident(self):
        self.assertEqual(self.post(),{"accepted":True,"duplicate":False})
        self.assertEqual(self.post(),{"accepted":True,"duplicate":True})
        self.assertEqual(self.db.health(),{"database_ok":True,"receipts":1,"incidents":1})
        new=payload()
        new['spec']['alerts'][0]['uniqueID']='different-event'
        self.assertEqual(self.post(new),{"accepted":True,"duplicate":False})
        record=json.loads(self.db.db.execute('SELECT data FROM incidents').fetchone()[0])
        self.assertEqual(record['count'],2)
        self.assertEqual(record['status'],'open')

    def test_restart_retains_receipt_and_original_evidence(self):
        self.post()
        self.db.close()
        self.db=None
        reopened=EventStore(self.filename)
        self.db=reopened
        self.receiver.store=reopened
        self.assertEqual(self.post(),{"accepted":True,"duplicate":True})
        self.assertEqual(reopened.health()['incidents'],1)
        raw=self.filename.read_bytes()
        for forbidden in (b'PRIVATE_MESSAGE',b'PRIVATE_CMDLINE',b'PRIVATE_',b'arguments',b'processTree'):
            self.assertNotIn(forbidden,raw)

    def test_different_body_with_same_receipt_identity_is_not_accepted(self):
        self.post()
        changed=payload()
        changed['spec']['alerts'][0]['severity']=10
        with self.assertRaisesRegex(Rejected,'replay_identity_conflict'):
            self.post(changed)
        self.assertEqual(self.db.health()['receipts'],1)

    def test_capacity_rolls_back_atomically(self):
        with patch('security_receiver.store.MAX_RECEIPTS',1):
            self.post()
            new=payload()
            new['spec']['alerts'][0]['uniqueID']='different'
            with self.assertRaisesRegex(Rejected,'receipt_capacity'):
                self.post(new)
        self.assertEqual(self.db.health(),{"database_ok":True,"receipts":1,"incidents":1})

    def test_untrusted_and_bad_envelope_cannot_create_evidence(self):
        self.api.review['status']['authenticated']=False
        with self.assertRaisesRegex(Rejected,'unauthenticated_source'):
            self.post()
        self.api.review['status']['authenticated']=True
        event=payload()
        event['spec']['alerts'][0]['podUID']='forged'
        with self.assertRaises(Rejected):
            self.post(event)
        self.assertEqual(self.db.health()['incidents'],0)


if __name__=='__main__':
    unittest.main()
