"""Fail closed on incomplete Kyverno admission metric observations."""
from pathlib import Path
import sqlite3
from unittest.mock import Mock
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
        self.assertEqual(role["rules"],[{"apiGroups":["discovery.k8s.io"],"resources":["endpointslices"],"verbs":["list"]},
                                        {"apiGroups":[""],"resources":["pods"],"verbs":["list"]}])
        job=next(d for d in docs if d["kind"]=="CronJob")
        pod=job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        self.assertEqual(pod["serviceAccountName"],"security-admission-observer")
        self.assertEqual(pod["containers"][0]["env"],[{"name":"GITEA_TOKEN","valueFrom":{"secretKeyRef":{"name":"homelab-security-gitea","key":"token"}}}])
        self.assertNotIn("envFrom",pod["containers"][0])
        self.assertFalse(any("secret" in v for v in pod["volumes"]))
        self.assertEqual([v["persistentVolumeClaim"]["claimName"] for v in pod["volumes"] if "persistentVolumeClaim" in v],["security-admission-observer-state"])


class DeltaTests(unittest.TestCase):
    def counters(self,n=3):
        return {"a":{"x":[True,n],"y":[False,100]},"b":{"z":[True,7]}}

    def test_baseline_gap_restart_and_counter_reset(self):
        with sqlite3.connect(":memory:") as db:
            self.assertIsNone(A.observe(db,self.counters(),1000))
            self.assertEqual(A.observe(db,self.counters(5),1600),2)
            self.assertIsNone(A.observe(db,self.counters(1),2200))
            self.assertIsNone(A.observe(db,self.counters(10),4000))
            self.assertIsNone(A.observe(db,{"new":self.counters()["a"],"b":self.counters()["b"]},4600))
            self.assertEqual(db.execute("SELECT count FROM daily").fetchone()[0],2)

    def test_missing_series_and_new_series_are_not_new_rejections(self):
        before=self.counters()
        current=self.counters(5)
        current["a"]["new"]=[True,99]
        self.assertIsNone(A.delta(before,current))
        del current["a"]["new"]
        del current["a"]["x"]
        self.assertIsNone(A.delta(before,current))

    def test_hash_only_series_observations(self):
        row=A.sample(series(),include_series=True)
        self.assertEqual(len(row["counters"]),1)
        self.assertNotIn("private",str(row))
        self.assertEqual(next(iter(row["counters"].values())),[True,1])

    def test_daily_single_issue_and_hourly_rate_limit(self):
        class Client:
            def __init__(self):
                self.created=[]
                self.comments_written=[]
            def search(self,key):
                return None
            def create(self,title,body):
                self.created.append({"number":1,"body":body,"state":"open"})
                return self.created[-1]
            def issue(self,number):
                return self.created[0]
            def comment(self,number,body):
                self.comments_written.append(body)
        client=Client()
        with sqlite3.connect(":memory:") as db:
            A.observe(db,self.counters(),1000)
            A.observe(db,self.counters(4),1600)
            self.assertEqual(A.publish(db,client,1600),(["created"],0))
            A.observe(db,self.counters(5),2200)
            self.assertEqual(A.publish(db,client,2200),([],0))
            self.assertEqual(A.publish(db,client,5200),(["updated"],0))
            self.assertEqual(A.publish(db,client,5800),([],0))
            self.assertEqual(len(client.created),1)
            self.assertEqual(len(client.comments_written),1)
            self.assertIn("counter increases (UTC observation day): 2",client.comments_written[0])

if __name__=="__main__":
    unittest.main()
