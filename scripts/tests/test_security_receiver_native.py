"""Offline tests for the documented node-agent v0.3.310 native HTTP envelope."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security_receiver.native import Rejected, TrustedPod, VerifiedSource, normalize

NOW = datetime(2026, 11, 23, 10, 32, tzinfo=timezone.utc)
INDEX = 'sha256:' + 'a'*64
CHILD = 'sha256:' + 'b'*64
SOURCE = VerifiedSource('system:serviceaccount:kubescape:node-agent', 'agent-pod-uid', 'metal3')
POD = TrustedPod('home','kubescape-runtime-test','runtime-acceptance',
                 'deployment-uid','runtime-acceptance-abc','test-pod-uid',
                 'test','containerd://test-container','metal3',
                 '2026-11-23T10:28:26Z',INDEX)


def payload():
    # Native HTTP RuntimeAlert uses lower-camel-case, FLAT fields.
    # The stdout event's nested "BaseRuntimeMetadata" is not supported here.
    alert = dict(ruleID='R1000',agentVersion='v0.3.310',severity=8,
        timestamp='2026-11-23T10:31:32.350577935Z',uniqueID='event-1',
        namespace=POD.namespace,containerName=POD.container,
        podName=POD.pod_name,podUID=POD.pod_uid,containerID='test-container',
        workloadKind='Deployment',workloadName=POD.deployment,
        workloadUID=POD.deployment_uid,nodeName=POD.node,imageDigest=CHILD,
        message='PRIVATE_MESSAGE_DO_NOT_PUBLISH',arguments={'exec':'SECRET'})
    return {'kind':'RuntimeAlerts','apiVersion':'kubescape.io/v1',
            'metadata':{'name':POD.pod_name,'namespace':POD.namespace},
            'spec':{'alerts':[alert],
                    'processTree':{'cmdline':'PRIVATE_CMDLINE_DO_NOT_PUBLISH'}}}


def encoded(event):
    return json.dumps(event).encode()


class NativeContractTests(unittest.TestCase):
    def test_proven_envelope_and_private_projection(self):
        event=normalize(encoded(payload()),SOURCE,POD,NOW)
        self.assertEqual(event.rule,'R1000')
        self.assertEqual(event.severity,'High')
        self.assertEqual(event.running_digest,INDEX)
        self.assertEqual(event.digest_relation,'different')
        for private in ('SECRET','PRIVATE_CMDLINE','PRIVATE_MESSAGE','processTree'):
            self.assertNotIn(private,repr(event))

    def test_replay_is_stable_and_new_event_is_distinct(self):
        first=normalize(encoded(payload()),SOURCE,POD,NOW)
        self.assertEqual(first.event_id,normalize(encoded(payload()),SOURCE,POD,NOW).event_id)
        p=payload();p['spec']['alerts'][0]['uniqueID']='event-2'
        self.assertNotEqual(first.event_id,normalize(encoded(p),SOURCE,POD,NOW).event_id)

    def test_image_digest_not_silently_equated(self):
        p=payload();a=p['spec']['alerts'][0]
        self.assertEqual(normalize(encoded(p),SOURCE,POD,NOW).digest_relation,'different')
        a['imageDigest']=INDEX
        self.assertEqual(normalize(encoded(p),SOURCE,POD,NOW).digest_relation,'equal')
        del a['imageDigest']
        self.assertEqual(normalize(encoded(p),SOURCE,POD,NOW).digest_relation,'absent')
        a['imageDigest']='sha256:invalid'
        with self.assertRaisesRegex(Rejected,'reported_image_invalid'):
            normalize(encoded(p),SOURCE,POD,NOW)

    def test_source_and_independent_owner_must_match(self):
        for name,value in (
            ('namespace','other'),('containerName','other'),('podName','other'),
            ('podUID','other'),('containerID','other'),('workloadUID','other'),
            ('workloadKind','Pod'),('workloadName','other'),('nodeName','metal6')):
            with self.subTest(field=name):
                p=payload();p['spec']['alerts'][0][name]=value
                with self.assertRaises(Rejected):
                    normalize(encoded(p),SOURCE,POD,NOW)
        with self.assertRaisesRegex(Rejected,'source_identity_invalid'):
            normalize(encoded(payload()),replace(SOURCE,service_account='forged'),POD,NOW)
        with self.assertRaisesRegex(Rejected,'source_node_mismatch'):
            normalize(encoded(payload()),replace(SOURCE,node='metal6'),POD,NOW)

    def test_requires_actual_http_format_not_stdout(self):
        p=payload()['spec']['alerts'][0]
        with self.assertRaisesRegex(Rejected,'exporter_schema_invalid'):
            normalize(encoded(p),SOURCE,POD,NOW)
        p=payload();p['spec']['alerts'][0]={'RuleID':'R1000','BaseRuntimeMetadata':{}}
        with self.assertRaisesRegex(Rejected,'rule_invalid'):
            normalize(encoded(p),SOURCE,POD,NOW)

    def test_batches_are_not_partially_accepted(self):
        p=payload();p['spec']['alerts'].append(dict(p['spec']['alerts'][0]))
        with self.assertRaisesRegex(Rejected,'exporter_batch_unsupported'):
            normalize(encoded(p),SOURCE,POD,NOW)

    def test_validation_and_size_fail_closed(self):
        for raw in (b'[]',b'{',b'{}',b'{"kind":1,"kind":2}',b'{"value":NaN}',
                    b' '* (512*1024+1)):
            with self.subTest(raw_length=len(raw)):
                with self.assertRaises(Rejected) as raised:
                    normalize(raw,SOURCE,POD,NOW)
                self.assertNotIn('NaN',str(raised.exception))
        for field,value in (('severity',11),('severity',True),
                            ('agentVersion','other'),('uniqueID',''),
                            ('timestamp','2026-11-23T10:20:00Z')):
            with self.subTest(field=field):
                p=payload();p['spec']['alerts'][0][field]=value
                with self.assertRaises(Rejected):
                    normalize(encoded(p),SOURCE,POD,NOW)


if __name__=='__main__':
    unittest.main()
