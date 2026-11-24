"""Fail closed on incomplete Kyverno admission metric observations."""
from pathlib import Path
import copy
import sys
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT/"infra/observability/security-incident-publisher"
sys.path.insert(0,str(DIR))
import admission as A
sys.path.pop(0)


def series(allowed="false",value="1"):
    return 'kyverno_admission_requests_total{request_allowed="'+allowed+'",request_webhook="ValidatingWebhookConfiguration",resource_kind="Pod",resource_namespace="private",resource_request_operation="create"} '+value


def endpoints():
    return [{"metadata":{"namespace":"kyverno","labels":{"kubernetes.io/service-name":"kyverno-svc-metrics"}},
             "addressType":"IPv4","ports":[{"name":"metrics-port","port":8000,"protocol":"TCP"}],
             "endpoints":[{"addresses":["10.245.1."+str(i)],"conditions":{"ready":True,"serving":True,"terminating":False},
                           "targetRef":{"kind":"Pod","namespace":"kyverno","name":"kyverno-admission-controller-abcdef-"+str(i),
                                        "uid":"12345678-1234-1234-1234-123456789ab"+str(i)}} for i in (1,2)]}]


class ObserverTests(unittest.TestCase):
    def test_counter_parser_separates_denies_from_allows(self):
        text=series("true","2e2")+"\n"+series("false","3")+"\nother_metric 99\n"
        self.assertEqual(A.sample(text),{"series":2,"allowed":200,"rejected":3})
        for bad in ("",series(value="NaN"),series(value="-1"),series(value="1.25"),
                    series().replace('request_allowed="false",',''),series()+"\n"+series(),
                    series().replace('request_allowed="false"','request_allowed="unknown"')):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                A.sample(bad)

    def test_two_unique_ready_admission_endpoints_required(self):
        self.assertEqual(len(A.targets(endpoints())),2)
        for mutation in ("missing","not_ready","external","duplicate","wrong_service"):
            value=endpoints()
            eps=value[0]["endpoints"]
            if mutation=="missing": eps.pop()
            elif mutation=="not_ready": eps[0]["conditions"]["ready"]=False
            elif mutation=="external": eps[0]["addresses"]=["8.8.8.8"]
            elif mutation=="duplicate": eps[0]["addresses"]=eps[1]["addresses"]
            else: value[0]["metadata"]["labels"]["kubernetes.io/service-name"]="other"
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                A.targets(value)

    def test_credentials_and_rbac_boundaries(self):
        docs=list(yaml.safe_load_all((DIR/"admission-workload.yaml").read_text()))
        role=next(d for d in docs if d["kind"]=="Role")
        self.assertEqual(role["metadata"]["namespace"],"kyverno")
        self.assertEqual(role["rules"],[{"apiGroups":["discovery.k8s.io"],"resources":["endpointslices"],"verbs":["list"]}])
        job=next(d for d in docs if d["kind"]=="CronJob")
        pod=job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        self.assertEqual(pod["serviceAccountName"],"security-admission-observer")
        self.assertNotIn("env",pod["containers"][0])
        self.assertNotIn("envFrom",pod["containers"][0])
        self.assertFalse(any("secret" in v or "persistentVolumeClaim" in v for v in pod["volumes"]))
        self.assertNotIn("PrivateGitea",(DIR/"admission.py").read_text())


if __name__=="__main__":
    unittest.main()
