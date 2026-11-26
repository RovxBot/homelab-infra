"""Exercise production runner failure boundaries without disrupting applications."""
import copy
import gzip
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("recurring", ROOT / "infra/kubescape/recurring/runner.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
CONFIG = json.loads((ROOT / "infra/kubescape/recurring/config.json").read_text())
# The pre-expansion single-Memos scenarios remain independent of the live cohort.
CANARY_CONFIG = {**CONFIG, "targets": CONFIG["targets"][:1]}
EXPECTED = json.loads((ROOT / "infra/kubescape/grype-database.json").read_text())
NOW = datetime(2026, 11, 22, 5, tzinfo=timezone.utc)


def row():
    return {"namespace": "memos", "kind": "Deployment", "workload": "memos", "container": "memos",
            "workloadUID": "workload-uid", "generation": 1, "podUIDs": ["pod-uid"], "ready": True,
            "containerType": "containers", "runningDigest": R.client.MEMOS_IMAGE}


class CompletionWindowTests(unittest.TestCase):
    def test_vulnerability_slots_and_daily_posture(self):
        history = Path("/history")
        markers = [R.completion_markers(history, "vulnerability", NOW.replace(hour=hour), "cohort")[0]
                   for hour in (1, 10, 13, 19)]
        self.assertEqual(len(set(markers)), 4)
        self.assertEqual(R.completion_markers(history, "vulnerability", NOW.replace(hour=5), "cohort")[0], markers[0])
        self.assertNotEqual(R.completion_markers(history, "vulnerability", NOW + timedelta(days=1), "cohort")[0], markers[0])
        posture = [R.completion_markers(history, "posture", NOW.replace(hour=hour), "cohort")[0]
                   for hour in (1, 10, 13, 19)]
        self.assertEqual(len(set(posture)), 1)

    def test_legacy_daily_marker_blocks_all_cutover_day_slots(self):
        history = Path("/history")
        old = history / "vulnerability-2026-11-22-cohort.done"
        for hour in (1, 10, 13, 19):
            marker, blockers = R.completion_markers(history, "vulnerability", NOW.replace(hour=hour), "cohort")
            self.assertIn(marker, blockers)
            self.assertIn(old, blockers)
        self.assertNotIn(old, R.completion_markers(history, "vulnerability", NOW + timedelta(days=1, hours=14), "cohort")[1])


class RetainedProjectionTests(unittest.TestCase):
    def test_recover_only_verified_matching_archive(self):
        fixture = json.loads((ROOT / "scripts/tests/fixtures/kubescape-report-identity.json").read_text())
        current = row()
        report, sbom = fixture["report"], fixture["sbom"]
        report["metadata"]["name"] = R.client.REPORT
        report["spec"]["metadata"]["report"]["createdAt"] = R.db.stamp(NOW)
        checked = R.check_report(report,sbom,current,EXPECTED,R.db.stamp(NOW))
        job = "recurring-" + "a"*32
        record = dict(checked,jobID=job,workloadUID=current["workloadUID"],
                      imageDigest=current["runningDigest"],container=current["container"])
        config = {**CANARY_CONFIG,"databaseChecksum":EXPECTED["metadata"]["checksum"]}
        inventory = {"rows":[dict(current,assessmentState="healthy")],"generatedAt":R.db.stamp(NOW)}
        summary = {"metadata":{"annotations":{"kubescape.io/image-id":current["runningDigest"],
                    "kubescape.io/workload-container-name":current["container"],
                    "kubescape.io/wlid":"wlid://cluster-home/namespace-memos/deployment-memos"}},
                    "spec":{"vulnerabilitiesRef":{"all":{"name":R.client.REPORT}}}}
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp)
            run = history/"vulnerability-fixture"
            run.mkdir()
            def write(name,value):
                with gzip.open(run/(name+"-"+job+".json.gz"),"wt") as stream:
                    json.dump(value,stream)
            write("reports",{"vulnerability":report,"sbom":sbom})
            write("summary",summary)
            R.atomic(run/("terminal-"+job+".json"),{"state":"succeeded","phase":"completed"})
            for mutate in ("stale","wrong_uid","wrong_summary","missing_terminal","valid"):
                records = {R.row_key(current):copy.deepcopy(record)}
                view = copy.deepcopy(inventory)
                if mutate == "stale":
                    view["rows"][0]["assessmentState"]="stale"
                elif mutate == "wrong_uid":
                    records[R.row_key(current)]["workloadUID"]="different"
                elif mutate == "wrong_summary":
                    wrong=copy.deepcopy(summary)
                    wrong["metadata"]["annotations"]["kubescape.io/wlid"]="other"
                    write("summary",wrong)
                elif mutate == "missing_terminal":
                    write("summary",summary)
                    (run/("terminal-"+job+".json")).unlink()
                else:
                    R.atomic(run/("terminal-"+job+".json"),{"state":"succeeded","phase":"completed"})
                self.assertEqual(R.recover_private_projection(history,view,records,config),int(mutate=="valid"))
                if mutate=="valid":
                    self.assertEqual(records[R.row_key(current)]["privateCve"],
                                     R.private_cve_findings(report["spec"]["payload"]["matches"]))
                    self.assertEqual(records[R.row_key(current)]["createdAt"],record["createdAt"])
                    self.assertEqual(records[R.row_key(current)]["privateCveDetails"],
                                     R.private_cve_details(report["spec"]["payload"]["matches"]))
                else:
                    self.assertNotIn("privateCve",records[R.row_key(current)])

    def test_recover_legacy_overflow_only_from_exact_qualified_archive(self):
        fixture = json.loads((ROOT / "scripts/tests/fixtures/kubescape-report-identity.json").read_text())
        current = row()
        report, sbom = fixture["report"], fixture["sbom"]
        report["metadata"]["name"] = R.client.REPORT
        report["spec"]["metadata"]["report"]["createdAt"] = R.db.stamp(NOW)
        matches = [{"vulnerability":{"id":f"CVE-2026-{1000+i}","severity":"High"}}
                   for i in range(R.PREVIOUS_PRIVATE_CVE_LIMIT+1)]
        report["spec"]["payload"]["matches"] = matches
        checked = R.check_report(report,sbom,current,EXPECTED,R.db.stamp(NOW))
        job = "recurring-" + "b"*32
        prior = R.private_cve_findings(matches, limit=R.PREVIOUS_PRIVATE_CVE_LIMIT)
        self.assertEqual(prior, R.private_cve_findings(matches, limit=R.LEGACY_PRIVATE_CVE_LIMIT))
        self.assertEqual(prior["state"],"overflow")
        record = dict(checked, jobID=job, workloadUID=current["workloadUID"],
                      imageDigest=current["runningDigest"],container=current["container"],
                      evidenceKind="direct", privateCve=prior, privateCveDetails=R.private_cve_details(matches),
                      privateRecoveryProof=None)
        config = {**CANARY_CONFIG,"databaseChecksum":EXPECTED["metadata"]["checksum"]}
        inventory = {"rows":[dict(current,assessmentState="healthy")],"generatedAt":R.db.stamp(NOW)}
        summary = {"metadata":{"annotations":{"kubescape.io/image-id":current["runningDigest"],
                   "kubescape.io/workload-container-name":current["container"],
                   "kubescape.io/wlid":R.workload_wlid(current)}},
                   "spec":{"vulnerabilitiesRef":{"all":{"name":R.client.REPORT}}}}
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp)
            run = history/"vulnerability-fixture"
            run.mkdir()
            for name, value in (("reports",{"vulnerability":report,"sbom":sbom}),("summary",summary)):
                with gzip.open(run/(name+"-"+job+".json.gz"),"wt") as stream:
                    json.dump(value,stream)
            R.atomic(run/("terminal-"+job+".json"),{"state":"succeeded","phase":"completed"})
            for corrupt in (True,False):
                records={R.row_key(current):copy.deepcopy(record)}
                saved=records[R.row_key(current)]
                if corrupt:
                    saved["privateCve"]["otherHighCriticalMatches"]=1
                self.assertEqual(R.recover_private_projection(history,inventory,records,config),int(not corrupt))
                self.assertEqual(saved["createdAt"],record["createdAt"])
                self.assertEqual(saved["privateCve"]["state"],"overflow" if corrupt else "complete")
                if not corrupt:
                    self.assertEqual(len(saved["privateCve"]["findings"]), R.PREVIOUS_PRIVATE_CVE_LIMIT + 1)
                    self.assertEqual(saved["privateCve"], R.private_cve_findings(matches))
                    self.assertEqual(R.recover_private_projection(history,inventory,records,config),0)

    def test_retained_reader_rejects_oversize_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"reports.json.gz"
            body=json.dumps({"value":"x"*128}).encode()
            with gzip.open(path,"wb") as stream:
                stream.write(body)
            with mock.patch.object(R,"ARCHIVE_LIMIT",len(body)):
                self.assertEqual(R.read_retained(path),{"value":"x"*128})
            with mock.patch.object(R,"ARCHIVE_LIMIT",len(body)-1):
                with self.assertRaisesRegex(R.db.Rejected,"archive_budget"):
                    R.read_retained(path)
            alias=Path(tmp)/"alias.json.gz"
            alias.symlink_to(path)
            with self.assertRaisesRegex(R.db.Rejected,"archive_unavailable"):
                R.read_retained(alias)
        self.assertEqual(R.ARCHIVE_LIMIT,64*1024**2)

    def test_strip_projection_details_only(self):
        evidence={"privateCve":{"findings":["PRIVATE-ID"]},"publicCve":{"findings":["LEGACY-ID"]},"privateCveDetails":{"matches":["PRIVATE-PACKAGE"]},"privateRecoveryProof":{"allCves":["PRIVATE-RECOVERY"]},"state":"healthy"}
        self.assertEqual(R.status_safe({"assessments":[evidence],"state":"healthy"}),
                         {"assessments":[{"state":"healthy"}],"state":"healthy"})
        self.assertIn("privateCve",evidence)


class CoverageTests(unittest.TestCase):
    def test_coverage_states_and_stale_database(self):
        config = {**CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]}
        current = row()
        record = {"createdAt": R.db.stamp(NOW - timedelta(hours=1)), "database": EXPECTED["metadata"],
                  "severities": {"Critical": 1}, "fixesAvailable": True}
        def state(record, target=current):
            return R.coverage([copy.deepcopy(target)], {R.row_key(target): record}, config, NOW)["rows"][0]["assessmentState"]
        self.assertEqual(state({}), "unknown")
        self.assertEqual(state(record), "healthy")
        self.assertEqual(state({**record, "createdAt": R.db.stamp(NOW - timedelta(hours=73))}), "stale")
        self.assertEqual(state({**record, "lastAttempt": {"state": "failed"}}), "failed")
        self.assertEqual(state({**record, "lastAttempt": {"state": "unknown"}}), "unknown")
        failed = {**record, "lastAttempt": {"state": "failed"}}
        inventory = R.coverage([copy.deepcopy(current)], {R.row_key(current): failed}, config, NOW)
        self.assertEqual(inventory["rows"][0]["scanAgeHours"], 1.0)
        self.assertEqual(state(record, {**current, "workload": "other"}), "deliberately_excluded")
        self.assertEqual(state({}, {**current, "runningDigest": None}), "unsupported")
        changed = {**record, "database": {**EXPECTED["metadata"], "checksum": "sha256:" + "0" * 64}}
        self.assertEqual(state(changed), "stale")
        inventory = R.coverage([copy.deepcopy(current)], {}, config, NOW)
        self.assertIn("validUntil", inventory)

    def test_cohort_health_rejects_missing_rolling_and_stale_targets(self):
        config = {**CANARY_CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]}
        current = row()
        records = {R.row_key(current): {"createdAt": R.db.stamp(NOW-timedelta(hours=71)),
                                       "database": EXPECTED["metadata"]}}
        def evaluate(rows):
            return R.cohort_health(R.coverage(rows, records, config, NOW), config)
        complete = evaluate([copy.deepcopy(current)])
        self.assertEqual(complete["state"], "healthy")
        self.assertEqual(R.db.timestamp(complete["validUntil"]), NOW+timedelta(hours=1))
        self.assertEqual(complete["healthy"], 1)
        for rows in ([], [{**current, "ready": False}], [copy.deepcopy(current), copy.deepcopy(current)],
                     [{**current, "runningDigest": "registry/image@sha256:"+"f"*64}]):
            with self.subTest(rows=rows):
                result=evaluate(rows)
                self.assertEqual(result["state"], "unknown")
                self.assertEqual(result["healthy"], 0)

    def test_new_digest_cannot_inherit_old_workload_coverage(self):
        previous = row()
        changed = {**previous, "runningDigest": "docker.io/image@sha256:" + "f" * 64}
        config = {**CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]}
        inventory = R.coverage([changed], {R.row_key(previous): {"createdAt": R.db.stamp(NOW)}}, config, NOW)
        self.assertEqual(inventory["rows"][0]["assessmentState"], "unknown")


    def test_curated_targets_resolve_independently(self):
        self.assertEqual(len(CONFIG["targets"]), 12)
        home = {**row(), "namespace": "homepage", "workload": "homepage-assets",
                "container": "nginx", "workloadUID": "homepage-uid"}
        config = {**CONFIG, "targets": CONFIG["targets"][:2]}
        selected, outcomes = R.resolve_targets([row(), {**home, "ready": False}], config)
        self.assertEqual(selected, [row()])
        self.assertEqual(outcomes[1]["state"], "skipped")
        selected, outcomes = R.resolve_targets([row()], config)
        self.assertEqual(selected, [row()])
        self.assertEqual(outcomes[1]["reasons"], ["workload_missing"])
        selected, outcomes = R.resolve_targets([row(), {**home, "runningDigest": None}], config)
        self.assertEqual(outcomes[1]["state"], "unsupported")


class RefreshTests(unittest.TestCase):
    def test_stable_unready_bridge_is_image_eligible_not_recovery_ready(self):
        ns, kind, name, container_name = R.UNREADY_IMAGE_TARGET.split("/")
        digest = "registry/image@sha256:"+"a"*64
        container = {"name":container_name,"image":digest}
        workload = {"kind":kind,"metadata":{"name":name,"generation":2},
                    "spec":{"replicas":1,"template":{"spec":{"containers":[container]}}},
                    "status":{"observedGeneration":2,"replicas":1,"updatedReplicas":1,
                              "readyReplicas":0,"availableReplicas":0}}
        pod = {"metadata":{"namespace":ns},"status":{"phase":"Running"}}
        status = {"started":True,"state":{"running":{"startedAt":"2026-11-24T00:00:00Z"}}}
        self.assertTrue(R.stable_unready_image(workload,container,pod,status,digest))
        self.assertFalse(R.deployment_current(workload,container))
        for altered in ({"observedGeneration":1},{"updatedReplicas":0},{"replicas":2}):
            bad = copy.deepcopy(workload)
            bad["status"].update(altered)
            self.assertFalse(R.stable_unready_image(bad,container,pod,status,digest))
        for bad_status in ({"started":False,"state":status["state"]},
                           {"started":True,"state":{"waiting":{}}}):
            self.assertFalse(R.stable_unready_image(workload,container,pod,bad_status,digest))
        self.assertFalse(R.stable_unready_image(workload,container,pod,status,None))
        self.assertFalse(R.stable_unready_image(workload,container,
            dict(pod,metadata={"namespace":ns,"deletionTimestamp":"now"}),status,digest))
        self.assertFalse(R.stable_unready_image(workload,dict(container,image="other"),pod,status,digest))
        current = dict(row(),namespace=ns,kind=kind,workload=name,container=container_name,
                       runningDigest=digest,ready=False,stableUnreadyImage=True,recoveryScopeReady=False)
        config = {**CONFIG,"targets":[{"identity":R.UNREADY_IMAGE_TARGET}],
                  "databaseChecksum":EXPECTED["metadata"]["checksum"]}
        records = {R.row_key(current):{"createdAt":R.db.stamp(NOW),"database":EXPECTED["metadata"],
                    "privateCve":{"state":"complete","findings":[],"otherHighCriticalMatches":0}}}
        self.assertEqual(R.resolve_targets([current],config)[0],[current])
        inventory = R.coverage([copy.deepcopy(current)],records,config,NOW)
        self.assertEqual(R.cohort_health(inventory,config)["state"],"healthy")
        projection = R.rich_private_cve_projection(inventory,records)
        entry = next(e for e in projection["entries"] if e["workload"]==R.UNREADY_IMAGE_TARGET.rsplit("/",1)[0])
        self.assertEqual(entry["state"],"complete")
        self.assertIsNone(entry["recoveryProof"])
        self.assertEqual(R.resolve_targets([dict(current,stableUnreadyImage=False)],config)[0],[])
        self.assertEqual(R.resolve_targets([current,copy.deepcopy(current)],config)[0],[])
        self.assertFalse(R.assessment_eligible(dict(current,workload="another-deployment")))

    def test_deployment_requires_current_template_and_all_updated_replicas(self):
        container = {"name": "memos", "image": "registry/memos@sha256:"+"a"*64}
        workload = {"metadata": {"generation": 2},
                    "spec": {"replicas": 1, "template": {"spec": {"containers": [container]}}},
                    "status": {"observedGeneration": 2, "replicas": 1, "updatedReplicas": 1,
                               "readyReplicas": 1, "availableReplicas": 1}}
        self.assertTrue(R.deployment_current(workload, container))
        for status in ({"observedGeneration": 1}, {"updatedReplicas": 0},
                       {"replicas": 2}, {"availableReplicas": 0}, {"readyReplicas": 0}):
            self.assertFalse(R.deployment_current({**workload,"status":{**workload["status"],**status}},container))
        self.assertFalse(R.deployment_current(workload,{**container,"image":"old-image"}))

    def test_statefulset_and_daemonset_require_complete_current_controller(self):
        container = {"name":"mysql","image":"registry/image@sha256:"+"a"*64}
        pod = {"metadata":{"labels":{"controller-revision-hash":"revision"}}}
        for kind, status in (
                ("StatefulSet", {"observedGeneration":2,"replicas":2,"updatedReplicas":2,
                    "readyReplicas":2,"availableReplicas":2,"currentReplicas":2,
                    "currentRevision":"revision","updateRevision":"revision"}),
                ("DaemonSet", {"observedGeneration":2,"desiredNumberScheduled":2,
                    "currentNumberScheduled":2,"updatedNumberScheduled":2,"numberReady":2,
                    "numberAvailable":2})):
            workload={"kind":kind,"metadata":{"generation":2},"spec":{"replicas":2,
                "template":{"spec":{"containers":[container]}}},"status":status}
            self.assertTrue(R.controller_current(workload,container,pod))
            for changed in ({"observedGeneration":1},
                    {"updatedReplicas":1} if kind=="StatefulSet" else {"updatedNumberScheduled":1},
                    {"currentRevision":"old"} if kind=="StatefulSet" else {"numberMisscheduled":1}):
                self.assertFalse(R.controller_current({**workload,"status":{**status,**changed}},container,pod))
            self.assertFalse(R.controller_current(workload,{**container,"image":"wrong"},pod))
            if kind=="StatefulSet":
                self.assertFalse(R.controller_current(workload,container,
                    {"metadata":{"labels":{"controller-revision-hash":"old"}}}))
            target={**row(),"kind":kind,"workload":"example","container":"mysql"}
            config={**CONFIG,"targets":[{"identity":R.target_key(target)}]}
            self.assertEqual(R.resolve_targets([target],config)[0],[target])
            self.assertEqual(R.workload_wlid(target),"wlid://cluster-home/namespace-memos/"+kind.lower()+"-example")
            self.assertEqual(R.native_summary_name(target),kind.lower()+"-example-mysql")
            self.assertEqual(R.resolve_targets([{**target,"ready":False}],config)[0],[])
            self.assertEqual(R.resolve_targets([target,copy.deepcopy(target)],config)[0],[])
            with self.assertRaises(R.db.Rejected):
                R.native_summary_name({**target,"workload":"x"*253})

    def test_refresh_rechecks_digest_preserves_scan_outcomes_and_does_not_scan(self):
        config = {**CANARY_CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]}
        current = row()
        record = {"createdAt": R.db.stamp(NOW-timedelta(hours=1)),
                  "database": EXPECTED["metadata"],
                  "publicCve":{"state":"complete","findings":[{"id":"CVE-2026-4738","severity":"Critical","fixAvailable":True}],"otherHighCriticalMatches":0}}
        status_path = R.CORE + "/namespaces/kubescape/configmaps/kubescape-assessment-status"
        public_path = R.CORE + "/namespaces/kubescape/configmaps/kubescape-private-cve"
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db,"utcnow",return_value=NOW):
            history=Path(path)
            records={R.row_key(current):record}
            R.atomic(history/"records.json",records)
            original={"vulnerability.state":"failed","posture.state":"healthy",
                      "posture.lastSuccessfulAssessmentAt":R.db.stamp(NOW-timedelta(hours=2))}
            objects={status_path:{"data":dict(original)},public_path:{"data":{}}}
            def request(url,body=None,method=None):
                if method=="PUT":
                    objects[url]=copy.deepcopy(body)
                return copy.deepcopy(objects[url])
            api=mock.Mock()
            api.call.side_effect=request
            with mock.patch.object(R,"workload_rows",return_value=[copy.deepcopy(current)]) as rows, \
                    mock.patch.object(R,"service",side_effect=AssertionError("refresh must not scan")) as service, \
                    mock.patch("builtins.print"):
                self.assertEqual(R.refresh_inventory(api,config,history),0)
                data=objects[status_path]["data"]
                self.assertEqual(data["coverage.state"],"healthy")
                self.assertEqual(data["coverage.observedAt"],R.db.stamp(NOW))
                self.assertEqual(data["coverage.validUntil"],R.db.stamp(NOW+timedelta(minutes=65)))
                self.assertTrue(all(data[k]==v for k,v in original.items()))
                self.assertIn("CVE-2026-4738",objects[public_path]["data"]["projection.json"])
                reasons = json.loads(data["coverage.privateCveReasons"])
                self.assertEqual(reasons["complete"], 1)
                self.assertEqual(sum(reasons.values()), len({x.rsplit("/", 1)[0] for x in R.PRIVATE_CVE_TARGETS}))
                self.assertNotIn("CVE-2026-4738", json.dumps(reasons))
                rows.return_value=[{**current,"runningDigest":"registry/image@sha256:"+"f"*64}]
                self.assertEqual(R.refresh_inventory(api,config,history),0)
                self.assertEqual(objects[status_path]["data"]["coverage.state"],"unknown")
                self.assertNotIn("CVE-2026-4738",objects[public_path]["data"]["projection.json"])
                self.assertEqual(json.loads((history/"records.json").read_text()),records)
                self.assertFalse((history/"latest-vulnerability.json").exists())
                service.assert_not_called()
                R.atomic(history/"inflight.json",{"mode":"vulnerability"})
                with self.assertRaises(R.db.Rejected):
                    R.refresh_inventory(api,config,history)


class BatchTests(unittest.TestCase):
    def rows(self):
        result = []
        for i, target in enumerate(CONFIG["targets"][:6]):
            namespace, kind, workload, container = target["identity"].split("/")
            result.append({**row(), "namespace": namespace, "kind": kind, "workload": workload,
                           "container": container, "workloadUID": "uid-" + str(i),
                           "runningDigest": "registry/image@sha256:" + format(i,"064x")})
        return result

    def config(self):
        return {**CONFIG, "targets": CONFIG["targets"][:6], "databaseChecksum": EXPECTED["metadata"]["checksum"]}

    def test_twelve_target_rotation_preserves_budget_and_fairness(self):
        config = {**CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]}
        rows = []
        for i, target in enumerate(config["targets"]):
            namespace, kind, workload, container = target["identity"].split("/")
            rows.append({**row(), "namespace": namespace, "kind": kind, "workload": workload,
                         "container": container, "workloadUID": str(i),
                         "runningDigest": "registry/image@sha256:" + format(i,"064x")})
        records, seen = {}, set()
        for day in range(3):
            batch = R.select_batch(rows, records, config)
            self.assertLessEqual(len(batch), 4)
            for target in batch:
                key = R.row_key(target)
                seen.add(key)
                records[key] = {"createdAt": R.db.stamp(NOW + timedelta(days=day)),
                                "database": EXPECTED["metadata"]}
        self.assertEqual(seen, {R.row_key(r) for r in rows})
        self.assertEqual(len(rows), 12)
        self.assertEqual(R.PRIVATE_CVE_TARGETS, {t['identity'] for t in CONFIG['targets']})

    def test_equal_digest_requires_direct_workload_evidence_for_exceptions(self):
        source, peer = self.rows()[:2]
        peer["runningDigest"] = source["runningDigest"]
        config = self.config()
        config["targets"] = config["targets"][:2]
        self.assertEqual(R.select_batch([source, peer], {}, config), [source, peer])
        record = {"evidenceKind":"sharedImage", "createdAt":R.db.stamp(NOW),
                  "database":EXPECTED["metadata"], "lastAttempt":{"state":"healthy"},
                  "privateCve":{"state":"complete","findings":[],"otherHighCriticalMatches":0}}
        inventory=R.coverage([peer],{R.row_key(peer):record},config,NOW)
        self.assertEqual(inventory["rows"][0]["assessmentState"],"unknown")
        projection=R.private_cve_projection(inventory,{R.row_key(peer):record})
        workload=R.target_key(peer).rsplit("/",1)[0]
        self.assertEqual(next(e for e in projection["entries"] if e["workload"]==workload)["state"],"unknown")
        # Historical shared evidence does not prevent a direct retry.
        self.assertIn(peer,R.select_batch([source,peer],{R.row_key(peer):record},config))

    def test_batch_rotates_unscanned_changed_stale_and_failures(self):
        rows, config = self.rows(), self.config()
        self.assertEqual(R.select_batch(rows, {}, config), rows[:4])
        records = {R.row_key(r): {"createdAt": R.db.stamp(NOW), "database": EXPECTED["metadata"]} for r in rows[:4]}
        self.assertEqual(R.select_batch(rows, records, config)[:2], rows[4:])
        records[R.row_key(rows[2])]["createdAt"] = R.db.stamp(NOW - timedelta(hours=74))
        self.assertEqual(R.select_batch(rows, records, config)[2], rows[2])
        records[R.row_key(rows[1])]["database"] = {"checksum": "old"}
        self.assertEqual(R.select_batch(rows, records, config)[0], rows[4])
        self.assertIn(rows[1], R.select_batch(rows, records, config))
        changed = {**rows[0], "runningDigest": "registry/image@sha256:" + "f" * 64}
        self.assertEqual(R.select_batch([changed, *rows[1:]], records, config)[0], changed)
        # Failed new images get an attempt timestamp and cannot monopolise a batch.
        failures = {R.row_key(r): {"lastAttempt": {"checkedAt": R.db.stamp(NOW),
                    "databaseChecksum": config["databaseChecksum"]}} for r in rows[:4]}
        self.assertEqual(R.select_batch(rows, failures, config)[:2], rows[4:])

    def test_failure_isolated_and_successes_survive_restart_and_publish(self):
        rows, config = self.rows(), self.config()
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db, "utcnow", return_value=NOW):
            history = Path(path)
            old = {"createdAt": R.db.stamp(NOW - timedelta(hours=73)), "database": EXPECTED["metadata"]}
            R.atomic(history / "records.json", {R.row_key(r): old.copy() for r in rows})
            runner = R.Runner(mock.Mock(), config, EXPECTED, history, "vulnerability")
            def scan(r, _):
                if r == rows[1]:
                    raise R.db.Rejected("scan_failed")
                result = {"createdAt": R.db.stamp(NOW), "database": EXPECTED["metadata"]}
                runner.records[R.row_key(r)] = result
                R.atomic(history / "records.json", runner.records)
                runner.result["assessments"].append(result)
                runner.result["lastSuccessfulAssessmentAt"] = result["createdAt"]
            with mock.patch.object(R, "workload_rows", return_value=rows), mock.patch.object(runner, "publish"), \
                    mock.patch.object(runner, "preflight"), mock.patch.object(runner, "audit", return_value={"checkedAt": R.db.stamp(NOW)}), \
                    mock.patch.object(runner, "gate"), mock.patch.object(runner, "vulnerability", side_effect=scan) as call:
                self.assertEqual(runner.run(), 4)
            self.assertEqual(call.call_count, 4)
            self.assertEqual(runner.records[R.row_key(rows[1])]["createdAt"], old["createdAt"])
            self.assertEqual(runner.records[R.row_key(rows[0])]["createdAt"], R.db.stamp(NOW))
            runner.api.call.return_value = {"data": {"posture.json": "retained"}}
            with mock.patch.object(R, "workload_rows", return_value=rows), mock.patch("builtins.print"):
                runner.publish()
            published = runner.api.call.call_args.args[1]["data"]
            self.assertEqual(published["posture.json"], "retained")
            self.assertEqual(published["vulnerability.lastSuccessfulAssessmentAt"], R.db.stamp(NOW))
            restarted = R.Runner(mock.Mock(), config, EXPECTED, history, "vulnerability")
            self.assertEqual(restarted.records, runner.records)
            self.assertEqual(restarted.result["lastSuccessfulAssessmentAt"], R.db.stamp(NOW))

    def test_public_projection_and_private_status_are_distinct_writes(self):
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db, "utcnow", return_value=NOW):
            api = mock.Mock()
            status_path = R.CORE + "/namespaces/kubescape/configmaps/kubescape-assessment-status"
            public_path = R.CORE + "/namespaces/kubescape/configmaps/kubescape-private-cve"
            objects = {
                status_path: {"metadata": {"name": "kubescape-assessment-status"},
                              "data": {"posture.json": "retained", "private": "private evidence"}},
                public_path: {"metadata": {"name": "kubescape-private-cve"}, "data": {}},
            }
            writes = []
            def request(url, body=None, method=None):
                if method == "PUT":
                    writes.append((url, copy.deepcopy(body)))
                    objects[url] = copy.deepcopy(body)
                    return objects[url]
                return copy.deepcopy(objects[url])
            api.call.side_effect = request
            runner = R.Runner(api, self.config(), EXPECTED, Path(path), "vulnerability")
            runner.result["assessments"]=[{"createdAt":R.db.stamp(NOW),"privateCve":{"findings":["PRIVATE-ID"]}}]
            with mock.patch.object(R, "workload_rows", return_value=[]), mock.patch("builtins.print") as logged:
                runner.publish()
            self.assertNotIn("PRIVATE-ID",json.dumps(objects[status_path]))
            self.assertNotIn("PRIVATE-ID",str(logged.call_args))
            self.assertIn("PRIVATE-ID",(Path(path)/"latest-vulnerability.json").read_text())
            self.assertEqual([url for url, _ in writes], [public_path, status_path])
            self.assertEqual(set(objects[public_path]["data"]), {"projection.json"})
            self.assertNotIn("private evidence", json.dumps(objects[public_path]))
            self.assertEqual(objects[status_path]["data"]["posture.json"], "retained")
            self.assertEqual(objects[status_path]["data"]["private"], "private evidence")
            self.assertNotIn("projection.json", objects[status_path]["data"])

    def test_ambiguous_later_request_preserves_peer_success_and_stops_batch(self):
        rows, config = self.rows()[:3], {**self.config(), "targets": CONFIG["targets"][:3]}
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db, "utcnow", return_value=NOW):
            history = Path(path)
            runner = R.Runner(mock.Mock(), config, EXPECTED, history, "vulnerability")
            def scan(r, _):
                if r == rows[1]:
                    R.atomic(history / "inflight.json", {"jobID": "ambiguous"})
                    raise OSError("lost response")
                result = {"createdAt": R.db.stamp(NOW), "database": EXPECTED["metadata"]}
                runner.records[R.row_key(r)] = result
                R.atomic(history / "records.json", runner.records)
                runner.result["assessments"].append(result)
                runner.result["lastSuccessfulAssessmentAt"] = result["createdAt"]
            with mock.patch.object(R, "workload_rows", return_value=rows), mock.patch.object(runner, "publish"), \
                    mock.patch.object(runner, "preflight"), mock.patch.object(runner, "audit", return_value={"checkedAt": R.db.stamp(NOW)}), \
                    mock.patch.object(runner, "gate"), mock.patch.object(runner, "vulnerability", side_effect=scan) as call:
                self.assertEqual(runner.run(), 3)
            self.assertEqual(call.call_count, 2)
            self.assertTrue((history / "inflight.json").exists())
            stored = json.loads((history / "records.json").read_text())
            self.assertEqual(stored[R.row_key(rows[0])]["createdAt"], R.db.stamp(NOW))
            self.assertEqual(stored[R.row_key(rows[1])]["lastAttempt"]["state"], "unknown")
            self.assertNotIn(R.row_key(rows[2]), stored)
            self.assertEqual(runner.result["targets"][2]["reasons"], ["uncertain_previous_request"])

    def test_existing_other_mode_success_is_exposed_without_another_scan(self):
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db, "utcnow", return_value=NOW):
            api = mock.Mock()
            api.call.return_value = {"data": {"posture.json": json.dumps({"state": "healthy", "assessments": [{"createdAt": R.db.stamp(NOW)}]})}}
            runner = R.Runner(api, self.config(), EXPECTED, Path(path), "vulnerability")
            with mock.patch.object(R, "workload_rows", return_value=[]), mock.patch("builtins.print"):
                runner.publish()
            published = api.call.call_args.args[1]["data"]
            self.assertEqual(published["posture.lastSuccessfulAssessmentAt"], R.db.stamp(NOW))
            self.assertEqual(published["posture.state"], "healthy")

    def test_posture_submission_does_not_resolve_vulnerability_workloads(self):
        def summary(namespace, name):
            return {"metadata": {"namespace": namespace, "name": name,
                    "annotations": {"kubescape.io/report-created-at": R.db.stamp(NOW)}}, "spec": {"controls": {"x": {}}}}
        summaries = [summary(namespace, name) for namespace, name in
                     (target.split("/") for target in CONFIG["postureWorkloads"])]
        summaries.append(summary("kubescape", "shared"))
        result = {"summaryDetails": {"frameworks": [{"controls": [{"status": "passed"}]}]}}
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db, "utcnow", return_value=NOW):
            api = mock.Mock()
            api.items.return_value = summaries
            api.get.side_effect = lambda base, kind, name, namespace: next(s for s in summaries if s["metadata"]["name"] == name)
            runner = R.Runner(api, self.config(), EXPECTED, Path(path), "posture")
            with mock.patch.object(R, "workload_rows", side_effect=AssertionError("target coupling")), \
                    mock.patch.object(R, "component_pod", return_value={"metadata": {"uid": "scanner"}}), \
                    mock.patch.object(R, "service", side_effect=[{"id": "posture"}, {"type": "success", "response": result}]), \
                    mock.patch.object(runner, "gate"), mock.patch.object(runner, "observe"):
                runner.posture({})
            self.assertEqual(runner.result["lastSuccessfulAssessmentAt"], R.db.stamp(NOW))
            self.assertFalse((Path(path) / "inflight.json").exists())

    def test_expanded_posture_requires_fresh_summary_for_each_reviewed_workload(self):
        def summary(namespace, name):
            return {"metadata": {"namespace": namespace, "name": name,
                    "annotations": {"kubescape.io/report-created-at": R.db.stamp(NOW)}},
                    "spec": {"controls": {"x": {}}}}
        required = CONFIG["postureWorkloads"]
        summaries = [summary(*target.split("/")) for target in required] + [summary("kubescape", "shared")]
        result = {"summaryDetails": {"frameworks": [{"controls": [{"status": "passed"}]}]}}
        self.assertEqual(R.check_posture(result, summaries, R.db.stamp(NOW), required)["requiredWorkloadCount"], 49)
        for i, target in enumerate(required):
            # Memos retains its earlier explicit prerequisite; all other missing
            # workload summaries fail the expanded scope check.
            reason = "posture_memos_missing" if target == "memos/deployment-memos" else "posture_scope_incomplete"
            with self.subTest(missing=target), self.assertRaisesRegex(R.db.Rejected, reason):
                R.check_posture(result, summaries[:i]+summaries[i+1:], R.db.stamp(NOW), required)

    def test_missing_vulnerability_targets_do_not_gate_posture(self):
        with tempfile.TemporaryDirectory() as path:
            runner = R.Runner(mock.Mock(), self.config(), EXPECTED, Path(path), "posture")
            with mock.patch.object(runner, "publish"), mock.patch.object(runner, "preflight"), \
                    mock.patch.object(runner, "audit"), mock.patch.object(runner, "gate"), \
                    mock.patch.object(runner, "posture") as posture, mock.patch.object(R, "targets", side_effect=AssertionError("coupled")):
                self.assertEqual(runner.run(), 0)
            posture.assert_called_once()


class SchedulingTests(unittest.TestCase):
    def test_catchup_is_daily_and_only_unqualified_rows(self):
        history = Path("/history")
        self.assertEqual(R.catchup_marker(history,NOW), R.catchup_marker(history,NOW.replace(hour=19)))
        self.assertNotEqual(R.catchup_marker(history,NOW), R.catchup_marker(history,NOW+timedelta(days=1)))
        a = row()
        b = dict(a, workload="another", workloadUID="other")
        config = {**CANARY_CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"],
                  "targets":[{"identity":R.target_key(x)} for x in (a,b)]}
        records = {R.row_key(a): {"createdAt":R.db.stamp(NOW),"database":EXPECTED["metadata"]}}
        self.assertEqual([R.row_key(x) for x in R.catchup_targets([a,b],records,config,NOW)], [R.row_key(b)])
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db,"utcnow",return_value=NOW):
            root = Path(path)
            runner = R.Runner(mock.Mock(),config,EXPECTED,root,"vulnerability",catchup=True)
            with mock.patch.object(runner,"publish") as publish, mock.patch("builtins.print"):
                self.assertEqual(runner.run(),0)
                publish.assert_not_called()
            marker,_ = R.completion_markers(root,"vulnerability",NOW,
                R.hashlib.sha256(json.dumps(config["targets"],sort_keys=True).encode()).hexdigest()[:12])
            R.atomic(marker,{"runID":"normal"})
            R.atomic(root/"records.json",records)
            with mock.patch.object(R,"workload_rows",return_value=[a,b]), \
                    mock.patch.object(runner,"publish"), mock.patch.object(runner,"preflight",side_effect=R.db.Rejected("node_unhealthy")):
                self.assertEqual(runner.run(),4)
            self.assertTrue(R.catchup_marker(root,NOW).exists())
            again = R.Runner(mock.Mock(),config,EXPECTED,root,"vulnerability",catchup=True)
            with mock.patch.object(again,"publish") as publish, mock.patch.object(again,"preflight") as preflight, mock.patch("builtins.print"):
                self.assertEqual(again.run(),0)
                publish.assert_not_called()
                preflight.assert_not_called()
            self.assertEqual(json.loads(marker.read_text())["runID"],"normal")

    def test_duplicate_process_fails_without_queueing(self):
        with tempfile.TemporaryDirectory() as path:
            with R.locked(Path(path)):
                with self.assertRaisesRegex(R.db.Rejected, "duplicate_execution"):
                    with R.locked(Path(path)):
                        self.fail("second process acquired lock")
            with R.locked(Path(path)):
                pass

    def test_uncertain_request_checks_existing_id_and_never_posts(self):
        with tempfile.TemporaryDirectory() as path:
            runner = R.Runner(mock.Mock(), CANARY_CONFIG, EXPECTED, Path(path), "vulnerability")
            R.atomic(Path(path) / "inflight.json", {"jobID": "existing-request"})
            with mock.patch.object(R, "service", return_value={"state": "succeeded"}) as call:
                with self.assertRaisesRegex(R.db.Rejected, "uncertain_previous_request"):
                    runner.fence()
            call.assert_called_once_with("kubevuln", "/v1/scanStatus/existing-request")
            self.assertTrue((Path(path) / "inflight.json").exists())

    def test_fenced_posture_lost_response_cannot_resubmit(self):
        with tempfile.TemporaryDirectory() as path:
            runner = R.Runner(mock.Mock(), CANARY_CONFIG, EXPECTED, Path(path), "posture")
            R.atomic(Path(path) / "inflight.json", {"mode": "posture"})
            with mock.patch.object(R, "service") as call:
                with self.assertRaisesRegex(R.db.Rejected, "uncertain_previous_request"):
                    runner.fence()
            call.assert_not_called()


class FailureTests(unittest.TestCase):
    def run_failure(self, reason, state="failed"):
        with tempfile.TemporaryDirectory() as path:
            runner = R.Runner(mock.Mock(), CANARY_CONFIG, EXPECTED, Path(path), "vulnerability")
            with mock.patch.object(R, "workload_rows", return_value=[row()]), mock.patch.object(runner, "publish"), \
                    mock.patch.object(runner, "preflight", side_effect=R.db.Rejected(reason)), mock.patch.object(runner, "audit") as audit:
                self.assertNotEqual(runner.run(), 0)
                self.assertEqual(runner.result["state"], state)
                self.assertEqual(runner.result["reasons"], [reason])
                audit.assert_not_called()

    def test_stale_database(self):
        self.assertEqual(R.db.database_freshness(EXPECTED["metadata"]["built"], NOW + timedelta(days=8))["state"], "grace")
        self.run_failure("stale_database", "stale")

    def test_refresh_in_progress(self):
        self.run_failure("database_refresh_in_progress")

    def test_scanner_unavailable(self):
        api = mock.Mock()
        api.items.return_value = []
        with self.assertRaisesRegex(R.db.Rejected, "kubevuln_unavailable"):
            R.component_pod(api, "kubevuln")
        self.run_failure("kubevuln_unavailable")

    def test_storage_api_unavailable(self):
        with tempfile.TemporaryDirectory() as path:
            runner = R.Runner(mock.Mock(), CANARY_CONFIG, EXPECTED, Path(path), "vulnerability")
            with mock.patch.object(R, "workload_rows", return_value=[row()]), mock.patch.object(runner, "publish"), \
                    mock.patch.object(runner, "preflight", side_effect=R.APIError(503)):
                self.assertEqual(runner.run(), 3)
                self.assertEqual(runner.result["state"], "unknown")

    def test_failed_preflight_replaces_cached_health_without_submission(self):
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db, "utcnow", return_value=NOW):
            history = Path(path)
            success = R.db.stamp(NOW - timedelta(days=1))
            R.atomic(history / "latest-vulnerability.json", {"state": "healthy", "lastSuccessfulAssessmentAt": success})
            api = mock.Mock()
            api.call.return_value = {"data": {"vulnerability.state": "healthy"}}
            runner = R.Runner(api, {**CANARY_CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]},
                              EXPECTED, history, "vulnerability")
            with mock.patch.object(R, "workload_rows", return_value=[row()]), \
                    mock.patch.object(runner, "preflight", side_effect=R.db.Rejected("scanner_busy")), \
                    mock.patch.object(runner, "audit") as audit, mock.patch.object(R, "service") as service, \
                    mock.patch("builtins.print"):
                self.assertEqual(runner.run(), 4)
            audit.assert_not_called()
            service.assert_not_called()
            data = api.call.call_args.args[1]["data"]
            self.assertEqual(data["vulnerability.state"], "failed")
            self.assertEqual(data["vulnerability.lastSuccessfulAssessmentAt"], success)
            self.assertEqual(json.loads(data["vulnerability.json"])["reasons"], ["scanner_busy"])
            self.assertFalse((history / "inflight.json").exists())
            self.assertFalse(list(history.glob("*.done")))

    def test_low_storage(self):
        self.run_failure("database_pvc_low_space")
        self.run_failure("replica_disk_low_space")
        self.run_failure("reports_pvc_low_space")

    def test_failed_timeout_missing_report_and_changed_image_keep_fence(self):
        for case in ("scan_failed", "scan_timeout", "report_missing", "workload_image_changed", "engine_restarted"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as path:
                runner = R.Runner(mock.Mock(), {**CANARY_CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]}, EXPECTED, Path(path), "vulnerability")
                def fail(*_):
                    R.atomic(Path(path) / "inflight.json", {"jobID": "submitted"})
                    raise R.db.Rejected(case)
                with mock.patch.object(R, "workload_rows", return_value=[row()]), mock.patch.object(runner, "publish"), \
                        mock.patch.object(runner, "preflight", return_value={}), mock.patch.object(runner, "audit", return_value={"checkedAt": R.db.stamp(R.db.utcnow())}), \
                        mock.patch.object(runner, "gate"), mock.patch.object(runner, "vulnerability", side_effect=fail) as scan:
                    self.assertEqual(runner.run(), 3)
                    scan.assert_called_once()
                    self.assertTrue((Path(path) / "inflight.json").exists())
                    self.assertEqual(runner.records[R.row_key(row())]["lastAttempt"]["state"], "unknown")

    def test_incomplete_or_old_reports_rejected_before_success(self):
        report = {"metadata": {"annotations": {"kubescape.io/status": "incomplete"}}}
        with self.assertRaisesRegex(R.db.Rejected, "report_incomplete"):
            R.check_report(report, {}, row(), EXPECTED, R.db.stamp(NOW))
        report = {"metadata": {"annotations": {"kubescape.io/status": "ready"}},
                  "spec": {"metadata": {"report": {"createdAt": R.db.stamp(NOW - timedelta(hours=1))}}}}
        with self.assertRaisesRegex(R.db.Rejected, "report_not_new"):
            R.check_report(report, {}, row(), EXPECTED, R.db.stamp(NOW))

    def test_posture_needs_actual_controls_and_stored_results(self):
        for result in ({}, {"summaryDetails": {"frameworks": []}},
                       {"summaryDetails": {"frameworks": [{"controls": [{"status": "skipped"}]}]}}):
            with self.assertRaises(R.db.Rejected):
                R.check_posture(result, [], R.db.stamp(NOW))

    def test_posture_distinguishes_action_required_and_unsupported(self):
        result = {"summaryDetails": {"frameworks": [{"controls": [
            {"status": "passed"}, {"status": "failed"}, {"status": "actionRequired"}, {"status": "skipped"}]}]}}
        summary = {"metadata": {"name": "deployment-memos", "namespace": "memos",
                               "annotations": {"kubescape.io/report-created-at": R.db.stamp(NOW)}}, "spec": {"controls": {"control": {}}}}
        shared = copy.deepcopy(summary)
        shared["metadata"].update(name="shared-rbac", namespace="kubescape")
        counts = R.check_posture(result, [summary, shared], R.db.stamp(NOW))["controls"]
        self.assertEqual(counts, {"Passed": 1, "Failed": 1, "Action Required": 2})
        summary["spec"]["controls"] = {}
        with self.assertRaisesRegex(R.db.Rejected, "posture_controls_empty"):
            R.check_posture(result, [summary, shared], R.db.stamp(NOW))

    def test_pinned_scanner_controls_map_and_review_substatus(self):
        result = {"summaryDetails": {"frameworks": [{"name": "NSA", "controls": {
            "passed": {"status": "passed", "statusInfo": {"status": "passed"}},
            "manual": {"status": "skipped", "statusInfo": {"status": "skipped", "subStatus": "manual review"}},
            "host": {"status": "skipped", "statusInfo": {"status": "skipped", "subStatus": "notEvaluated"}},
            "cloud": {"status": "passed", "statusInfo": {"status": "passed", "subStatus": "irrelevant"}}
        }}]}}
        def summary(namespace, name):
            return {"metadata": {"namespace": namespace, "name": name,
                    "annotations": {"kubescape.io/report-created-at": R.db.stamp(NOW)}}, "spec": {"controls": {"x": {}}}}
        assessed = R.check_posture(result, [summary("memos", "deployment-memos"), summary("kubescape", "shared")], R.db.stamp(NOW))
        self.assertEqual(assessed["controls"], {"Passed": 2, "Action Required": 2})
        self.assertEqual(assessed["coverage"]["notEvaluatedControls"], 1)
        self.assertEqual(assessed["coverage"]["notEvaluatedControlIDs"], ["host"])
        self.assertEqual(assessed["coverage"]["incompleteCoverageControls"], 0)


    def test_native_counts_and_not_evaluated_overlay(self):
        # Synthetic 26-control shape reproducing the pinned printer's *status*
        # semantics; this is NOT a fixture exported from the production PVC.
        controls = {}
        for i in range(15):
            controls[f"C-P-{i}"] = {"status": "passed", "statusInfo": {"status": "passed"}}
        for i in range(3):
            controls[f"C-I-{i}"] = {"status": "passed", "statusInfo": {"status": "passed", "subStatus": "irrelevant"}}
        for i in range(3):
            controls[f"C-F-{i}"] = {"status": "failed", "statusInfo": {"status": "failed"}}
        for i in range(3):
            controls[f"C-R-{i}"] = {"status": "skipped", "statusInfo": {"status": "skipped", "subStatus": "manual review"}}
        for i in range(2):
            controls[f"C-N-{i}"] = {"status": "skipped", "statusInfo": {"status": "skipped", "subStatus": "notEvaluated"}}
        result = {"summaryDetails": {"frameworks": [{"name": "NSA", "controls": controls}]}}
        summary = lambda namespace, name: {"metadata": {"namespace": namespace, "name": name,
            "annotations": {"kubescape.io/report-created-at": R.db.stamp(NOW)}},
            "spec": {"controls": {"C-X": {"status": "passed"}}}}
        assessed = R.check_posture(result, [summary("memos", "deployment-memos"),
                                            summary("kubescape", "shared-rbac")], R.db.stamp(NOW))
        self.assertEqual(assessed["controls"], {"Passed": 18, "Failed": 3, "Action Required": 5})
        self.assertEqual(sum(assessed["controls"].values()), 26)
        self.assertEqual(assessed["coverage"]["notEvaluatedControls"], 2)
        self.assertEqual(assessed["coverage"]["notEvaluatedControlIDs"], ["C-N-0", "C-N-1"])

    def test_statusinfo_overrides_legacy_and_unknown_is_not_silently_safe(self):
        summary = lambda namespace, name: {"metadata": {"namespace": namespace, "name": name,
            "annotations": {"kubescape.io/report-created-at": R.db.stamp(NOW)}},
            "spec": {"controls": {"C-X": {}}}}
        summaries = [summary("memos", "deployment-memos"), summary("kubescape", "shared-rbac")]
        result = {"summaryDetails": {"frameworks": [{"controls": {
            "C-1": {"status": "passed", "statusInfo": {"status": "failed"}},
            "C-2": {"status": "passed", "statusInfo": {"status": "passed", "subStatus": "incompleteCoverage"}},
        }}]}}
        assessed = R.check_posture(result, summaries, R.db.stamp(NOW))
        self.assertEqual(assessed["controls"], {"Failed": 1, "Passed": 1})
        self.assertEqual(assessed["coverage"]["incompleteCoverageControlIDs"], ["C-2"])
        result["summaryDetails"]["frameworks"][0]["controls"]["C-1"]["statusInfo"]["status"] = "unknownNewStatus"
        with self.assertRaisesRegex(R.db.Rejected, "posture_status_unrecognised"):
            R.check_posture(result, summaries, R.db.stamp(NOW))

class PrivateCVEProjectionTests(unittest.TestCase):
    def test_projection_diagnostics_are_bounded_and_match_eligibility(self):
        current = dict(row(), assessmentState="healthy")
        target = R.target_key(current)
        good = {"createdAt": R.db.stamp(NOW), "privateCve": {
            "state": "complete", "findings": [], "otherHighCriticalMatches": 0}}
        cases = [
            ([], good, "inventory_missing"),
            ([current, copy.deepcopy(current)], good, "inventory_ambiguous"),
            ([dict(current, assessmentState="stale")], good, "assessment_stale"),
            ([dict(current, assessmentState="PRIVATE-TEXT")], good, "assessment_unknown"),
            ([current], {}, "projection_missing"),
            ([current], dict(good, evidenceKind="sharedImage"), "shared_evidence_unqualified"),
            ([current], dict(good, privateCve={"state": "overflow"}), "projection_overflow"),
            ([current], dict(good, privateCve={"state": "PRIVATE-TEXT"}), "projection_unqualified"),
            ([current], dict(good, privateCve=dict(good["privateCve"],
                findings=[{"id": f"CVE-2026-{1000+i}", "severity": "High", "fixAvailable": True}
                          for i in range(R.MAX_PRIVATE_CVES + 1)])), "workload_projection_bound"),
            ([current], good, "complete"),
        ]
        with mock.patch.object(R, "PRIVATE_CVE_TARGETS", {target}):
            for rows, record, reason in cases:
                with self.subTest(reason=reason):
                    counts = R.Counter()
                    inventory = {"rows": rows, "generatedAt": R.db.stamp(NOW)}
                    result = R.rich_private_cve_projection(
                        inventory, {R.row_key(current): record}, counts)
                    self.assertEqual(dict(counts), {reason: 1})
                    self.assertEqual(result["entries"][0]["state"],
                                     "complete" if reason == "complete" else "unknown")
                    self.assertNotIn("PRIVATE-TEXT", json.dumps(counts))

    def test_twelve_target_private_projection_and_legacy_boundary(self):
        rows, records = [], {}
        for target in sorted(R.PRIVATE_CVE_TARGETS):
            namespace, kind, workload, container = target.split("/")
            current = dict(row(), namespace=namespace, kind=kind, workload=workload,
                           container=container, assessmentState="healthy")
            rows.append(current)
            records[R.row_key(current)] = {"createdAt":R.db.stamp(NOW),
                "privateCve":{"state":"complete","findings":[], "otherHighCriticalMatches":0}}
        inventory = {"generatedAt":R.db.stamp(NOW),"rows":rows}
        projection = R.private_cve_projection(inventory, records)
        self.assertEqual(len(projection["entries"]),12)
        self.assertTrue(all(e["state"]=="complete" for e in projection["entries"]))
        target = next(r for r in rows if R.target_key(r) not in R.LEGACY_PUBLIC_CVE_TARGETS)
        record = records[R.row_key(target)]
        record["publicCve"] = record.pop("privateCve")
        projected = R.private_cve_projection(inventory,records)
        self.assertEqual(sum(e["state"]=="unknown" for e in projected["entries"]),1)
        self.assertNotIn("runningDigest",json.dumps(projected))

    def test_multicontainer_projection_requires_every_reviewed_container(self):
        first = dict(row(), assessmentState="healthy")
        second = dict(first, container="sidecar")
        scope = {R.target_key(first), R.target_key(second)}
        def evidence(when, ids, severity, fix, other):
            return {"createdAt":R.db.stamp(when),"privateCve":{"state":"complete",
                "findings":[{"id":i,"severity":severity,"fixAvailable":fix} for i in ids],
                "otherHighCriticalMatches":other}}
        records={R.row_key(first):evidence(NOW,["CVE-2026-1000"],"High",False,1),
                 R.row_key(second):evidence(NOW-timedelta(hours=2),["CVE-2026-1000","CVE-2026-1001"],"Critical",True,2)}
        def project(rows, data=records):
            return R.private_cve_projection({"rows":rows,"generatedAt":R.db.stamp(NOW)},data)["entries"]
        with mock.patch.object(R,"PRIVATE_CVE_TARGETS",scope):
            entries=project([first,second])
            self.assertEqual(len(entries),1)
            result=entries[0]
            self.assertEqual(result["state"],"complete")
            self.assertEqual(result["assessedAt"],R.db.stamp(NOW-timedelta(hours=2)))
            self.assertEqual(result["otherHighCriticalMatches"],3)
            self.assertEqual(result["findings"],[
                {"id":"CVE-2026-1000","severity":"Critical","fixAvailable":True},
                {"id":"CVE-2026-1001","severity":"Critical","fixAvailable":True}])
            for rows in ([first],[first,dict(second,assessmentState="stale")],
                         [first,second,copy.deepcopy(second)]):
                self.assertEqual(project(rows)[0]["state"],"unknown")
            for invalid in ({"evidenceKind":"sharedImage"},{"privateCve":{"state":"overflow"}}):
                altered=copy.deepcopy(records)
                altered[R.row_key(second)].update(invalid)
                self.assertEqual(project([first,second],altered)[0]["state"],"unknown")
            overflow=copy.deepcopy(records)
            overflow[R.row_key(second)]=evidence(NOW,[f"CVE-2026-{i}" for i in range(2000,2000+R.MAX_PRIVATE_CVES)],"High",False,0)
            self.assertEqual(project([first,second],overflow)[0]["state"],"unknown")
            self.assertNotIn("sidecar",json.dumps(result))

    def test_only_bounded_high_critical_cves_and_fix_flags(self):
        result = R.private_cve_findings([
            {"vulnerability":{"id":"CVE-2026-4738","severity":"High","fix":{"state":"not-fixed"}}, "artifact":{"name":"private"}},
            {"vulnerability":{"id":"CVE-2026-4738","severity":"Critical","fix":{"state":"fixed"}}},
            {"vulnerability":{"id":"CVE-2026-1234","severity":"Medium"}},
            {"vulnerability":{"id":"PRIVATE-ADVISORY","severity":"High"}},
        ])
        self.assertEqual(result, {"state":"complete","findings":[{"id":"CVE-2026-4738","severity":"Critical","fixAvailable":True}],"otherHighCriticalMatches":1})
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(R.private_cve_findings([{"vulnerability":{"id":f"CVE-2026-{1000+i}","severity":"High"}} for i in range(R.MAX_PRIVATE_CVES+1)])["state"], "overflow")


    def test_qualified_package_details_are_bounded_and_display_safe(self):
        def match(i, severity="High"):
            return {"vulnerability":{"id":f"CVE-2026-{1000+i}","severity":severity,
                                    "fix":{"state":"fixed","versions":["1.4-r0"]}},
                    "artifact":{"name":f"pkg-{i}","type":"apk","version":"1.3.2-r1",
                                "locations":[{"path":"/PRIVATE-PATH"}],"metadata":{"PRIVATE-KEY":"value"}}}
        matches=[match(i) for i in range(15)]+[match(99,"Critical")]
        malformed=match(100)
        malformed["artifact"]["name"]="bad|markdown"
        matches.append(malformed)
        details=R.private_cve_details(matches)
        self.assertEqual(details,R.private_cve_details(list(reversed(matches))))
        self.assertEqual(details["detailState"],"partial")
        self.assertEqual(len(details["matches"]),12)
        self.assertEqual(details["omittedMatches"],5)
        self.assertEqual(details["matches"][0]["severity"],"Critical")
        self.assertEqual(details["matches"][0]["installedVersion"],"1.3.2-r1")
        self.assertEqual(details["matches"][0]["fixedVersions"],["1.4-r0"])
        self.assertNotIn("PRIVATE",json.dumps(details))
        self.assertEqual(len(R.private_cve_findings(matches)["findings"]),17)
        self.assertEqual(R.private_cve_details([]),{"detailState":"complete","matches":[],"omittedMatches":0})

    def test_full_cohort_maximum_advisory_summary_fits_existing_wire_bound(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location("bounded_publisher",ROOT/"infra/observability/security-incident-publisher/publisher.py")
        publisher=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(publisher)
        self.assertEqual(R.MAX_PRIVATE_CVES,publisher.MAX_PRIVATE_CVES)
        rows, records = [], {}
        findings = [{"id":f"CVE-2026-{1000000000+i}","severity":"Critical","fixAvailable":False}
                    for i in range(R.MAX_PRIVATE_CVES)]
        for target in sorted(R.PRIVATE_CVE_TARGETS):
            ns, kind, workload, container = target.split("/")
            current = dict(row(),namespace=ns,kind=kind,workload=workload,container=container,assessmentState="healthy")
            rows.append(current)
            records[R.row_key(current)]={"createdAt":R.db.stamp(NOW),
                "privateCve":{"state":"complete","findings":findings,"otherHighCriticalMatches":1000000}}
        inventory={"rows":rows,"generatedAt":R.db.stamp(NOW)}
        projection=R.rich_private_cve_projection(inventory,records)
        wire=json.dumps({"metadata":{"name":"kubescape-private-cve","namespace":"kubescape"},
                         "data":{"projection.json":json.dumps(projection,separators=(",",":"))}}).encode()
        # Leave at least 8 KiB for actual Kubernetes object metadata and
        # serialization overhead; optional detail/proof still falls back.
        self.assertLess(len(wire)+8*1024,R.MAX_PRIVATE_PROJECTION_BYTES)
        self.assertLess(len(wire),publisher.MAX_CVE_BODY)
        entries=publisher.validate_cve(projection,NOW)
        self.assertEqual(len(entries),len(R.PRIVATE_CVE_TARGETS))
        self.assertTrue(all(len(e["findings"])==R.MAX_PRIVATE_CVES for e in entries))
        projection["entries"][0]["findings"].append({"id":"CVE-2026-9999","severity":"High","fixAvailable":False})
        with self.assertRaises(ValueError):
            publisher.validate_cve(projection,NOW)

    def test_rich_projection_consumer_accepts_qualified_and_legacy_records(self):
        import importlib.util
        source=ROOT/"infra/observability/security-incident-publisher/publisher.py"
        spec=importlib.util.spec_from_file_location("rich_cve_publisher",source)
        publisher=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(publisher)
        current=dict(row(),assessmentState="healthy")
        matches=[{"vulnerability":{"id":"CVE-2026-4738","severity":"Critical",
                                   "fix":{"state":"fixed","versions":["1.4-r0"]}},
                  "artifact":{"name":"zlib","type":"apk","version":"1.3.2-r1"}}]
        evidence={"createdAt":R.db.stamp(NOW),"privateCve":R.private_cve_findings(matches),
                  "privateCveDetails":R.private_cve_details(matches)}
        inventory={"rows":[current],"generatedAt":R.db.stamp(NOW)}
        records={R.row_key(current):evidence}
        projection=R.rich_private_cve_projection(inventory,records)
        self.assertEqual(projection["schema"],3)
        entries=publisher.validate_cve(projection,NOW)
        self.assertEqual(len(entries),1)
        container=entries[0]["containers"][0]
        self.assertEqual(container["image"],current["runningDigest"])
        self.assertEqual(container["matches"][0]["package"],"zlib")
        self.assertIn("1.4-r0",publisher.render_cve(entries[0])[1])
        del evidence["privateCveDetails"]
        entries=publisher.validate_cve(R.rich_private_cve_projection(inventory,records),NOW)
        self.assertEqual(entries[0]["containers"][0]["detailState"],"unavailable")
        inventory["rows"][0]["assessmentState"]="stale"
        self.assertEqual(publisher.validate_cve(R.rich_private_cve_projection(inventory,records),NOW),[])


    def test_recovery_proof_requires_direct_full_report_and_all_severities(self):
        fixture=json.loads((ROOT/"scripts/tests/fixtures/kubescape-report-identity.json").read_text())
        report=fixture["report"]
        current=dict(row(),assessmentState="healthy",recoveryScopeReady=True)
        record=R.check_report(report,fixture["sbom"],current,EXPECTED,
                              report["spec"]["metadata"]["report"]["createdAt"])
        record.update(evidenceKind="direct",jobID="recurring-"+"a"*32,
                      workloadUID=current["workloadUID"],imageDigest=current["runningDigest"],
                      container=current["container"])
        payload=report["spec"]["payload"]
        payload["matches"]=[
            {"vulnerability":{"id":"CVE-2026-1000","severity":"Medium"}},
            {"vulnerability":{"id":"VENDOR-1","severity":"Low"},
             "relatedVulnerabilities":[{"id":"CVE-2026-1001"}]}]
        proof=R.private_recovery_proof(report,current,record)
        self.assertEqual(proof["allCves"],["CVE-2026-1000","CVE-2026-1001"])
        for change in ("ignored","relevancy","invalid-db","non-cve","overflow","shared"):
            copy_report,copy_record=copy.deepcopy(report),copy.deepcopy(record)
            p=copy_report["spec"]["payload"]
            if change=="ignored":
                p["ignoredMatches"]=[{"vulnerability":{"id":"CVE-2026-1000"}}]
            elif change=="relevancy":
                copy_report["spec"]["metadata"]["withRelevancy"]=True
            elif change=="invalid-db":
                p["descriptor"]["db"]["valid"]=False
            elif change=="non-cve":
                p["matches"].append({"vulnerability":{"id":"UNKNOWN","severity":"Low"}})
            elif change=="overflow":
                p["matches"]=[{"vulnerability":{"id":f"CVE-2026-{1000+i}"}} for i in range(257)]
            else:
                copy_record["evidenceKind"]="sharedImage"
            with self.subTest(change=change):
                self.assertIsNone(R.private_recovery_proof(copy_report,current,copy_record))
        record["privateCve"]=R.private_cve_findings(payload["matches"])
        record["privateCveDetails"]=R.private_cve_details(payload["matches"])
        record["privateRecoveryProof"]=proof
        inventory={"rows":[current],"generatedAt":R.db.stamp(NOW)}
        records={R.row_key(current):record}
        projection=R.rich_private_cve_projection(inventory,records)
        import importlib.util
        spec=importlib.util.spec_from_file_location("recovery_consumer",ROOT/"infra/observability/security-incident-publisher/publisher.py")
        publisher=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(publisher)
        entries=publisher.validate_cve(projection,NOW)
        self.assertEqual(entries[0]["recoveryProof"]["containers"],[proof])
        self.assertNotIn(current["workloadUID"],json.dumps(entries[0]["recoveryProof"]))
        for extra in ("sidecar","init"):
            inventory["rows"].append(dict(current,container=extra,
                containerType="initContainers" if extra=="init" else "containers"))
            entry=next(e for e in R.rich_private_cve_projection(inventory,records)["entries"]
                       if e["workload"]=="memos/Deployment/memos")
            self.assertIsNone(entry["recoveryProof"])
            inventory["rows"].pop()
        current["recoveryScopeReady"]=False
        self.assertIsNone(publisher.validate_cve(R.rich_private_cve_projection(inventory,records),NOW)[0]["recoveryProof"])

    def test_projection_rejects_stale_records_and_unreviewed_workloads(self):
        current = row()
        private = {**current,"namespace":"gitea","workload":"private-repo"}
        record = {"createdAt":R.db.stamp(NOW),"publicCve":{"state":"complete","findings":[{"id":"CVE-2026-4738","severity":"Critical","fixAvailable":True}],"otherHighCriticalMatches":0}}
        inventory = {"generatedAt":R.db.stamp(NOW),"rows":[{**current,"assessmentState":"healthy"},{**private,"assessmentState":"healthy"}]}
        records = {R.row_key(current):record,R.row_key(private):record}
        result = R.private_cve_projection(inventory, records)
        memos = next(x for x in result["entries"] if x["workload"]=="memos/Deployment/memos")
        self.assertEqual(memos["state"],"complete")
        self.assertNotIn("private-repo",json.dumps(result))
        inventory["rows"][0]["assessmentState"]="stale"
        result=R.private_cve_projection(inventory,records)
        self.assertTrue(all(x["state"]=="unknown" for x in result["entries"]))


class SubmissionTests(unittest.TestCase):
    """Drive the actual POST/poll/report path, including ambiguous acceptance."""
    def test_full_production_path_requires_actual_result_then_deduplicates(self):
        for kind in ("Deployment","StatefulSet","DaemonSet"):
            with self.subTest(kind=kind):
                self.production_path(kind)

    def production_path(self, kind):
        fixture = json.loads((ROOT / "scripts/tests/fixtures/kubescape-report-identity.json").read_text())
        current = {**row(), "kind":kind}
        report, sbom = fixture["report"], fixture["sbom"]
        for obj in (report, sbom):
            obj["metadata"].update(name=R.client.REPORT)
            obj["metadata"]["annotations"]["kubescape.io/image-id"] = current["runningDigest"]
        api = mock.Mock()
        api.items.return_value = [report]
        summary = {"metadata": {"annotations": {
            "kubescape.io/image-id": current["runningDigest"],
            "kubescape.io/workload-container-name": "memos",
            "kubescape.io/wlid": "wlid://cluster-home/namespace-memos/"+kind.lower()+"-memos"}},
            "spec": {"vulnerabilitiesRef": {"all": {"name": R.client.REPORT}}}}
        def get(base, resource, *args, **kwargs):
            if resource == "vulnerabilitymanifestsummaries":
                self.assertEqual(args, (kind.lower()+"-memos-memos","memos"))
            return copy.deepcopy(summary if resource == "vulnerabilitymanifestsummaries" else sbom if resource == "sbomsyfts" else report)
        api.get.side_effect = get
        api.call.return_value = {"metadata": {"name": "kubescape-assessment-status"}, "data": {}}
        posted = []
        def service(component, path, value=None):
            if value is not None:
                posted.append(value)
                self.assertIs(value["args"]["forceRescan"], True)
                self.assertEqual(value["wlid"],"wlid://cluster-home/namespace-memos/"+kind.lower()+"-memos")
                report["spec"]["metadata"]["report"]["createdAt"] = R.db.stamp(clock.return_value)
                return {"accepted": True}
            return {"state": "succeeded", "phase": "completed", "startedAt": R.db.stamp(clock.return_value),
                    "finishedAt": R.db.stamp(clock.return_value + timedelta(seconds=1))}
        with tempfile.TemporaryDirectory() as path, mock.patch.object(R.db, "utcnow", return_value=NOW) as clock, \
                mock.patch.object(R, "workload_rows", return_value=[current]), mock.patch.object(R, "service", side_effect=service), \
                mock.patch.object(R, "component_pod", return_value={"metadata": {"uid": "engine-uid"}}):
            history = Path(path)
            config = {**CANARY_CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"],
                      "targets":[{"identity":R.target_key(current)}]}
            runner = R.Runner(api, config, EXPECTED, history, "vulnerability")
            with mock.patch.object(runner, "preflight", return_value={}), \
                    mock.patch.object(runner, "audit", return_value={"podUID": "engine-uid", "checkedAt": R.db.stamp(NOW)}), \
                    mock.patch.object(runner, "gate"), mock.patch.object(runner, "observe"), mock.patch("builtins.print"):
                self.assertEqual(runner.run(), 0)
            self.assertEqual(runner.result["state"], "healthy")
            self.assertEqual(len(posted), 1)
            self.assertFalse((history / "inflight.json").exists())
            stored = json.loads((history / "records.json").read_text())
            self.assertEqual(stored[R.row_key(current)]["createdAt"], R.db.stamp(NOW))
            self.assertEqual(json.loads((history / "coverage.json").read_text())["rows"][0]["assessmentState"], "healthy")
            self.assertTrue(list(runner.work.glob("previous-*.json.gz")))
            duplicate = R.Runner(api, config, EXPECTED, history, "vulnerability")
            with mock.patch("builtins.print"):
                self.assertEqual(duplicate.run(), 0)
            self.assertEqual(duplicate.result["reasons"], ["duplicate_execution"])
            self.assertEqual(json.loads((history / "latest-vulnerability.json").read_text())["runID"], runner.run_id)
            self.assertEqual(len(posted), 1)
            # A completed earlier slot cannot suppress the next scheduled batch.
            clock.return_value = NOW + timedelta(hours=6)
            tomorrow = R.Runner(api, config, EXPECTED, history, "vulnerability")
            with mock.patch.object(tomorrow, "preflight", return_value={}), \
                    mock.patch.object(tomorrow, "audit", return_value={"podUID": "engine-uid", "checkedAt": R.db.stamp(clock.return_value)}), \
                    mock.patch.object(tomorrow, "gate"), mock.patch.object(tomorrow, "observe"), mock.patch("builtins.print"):
                self.assertEqual(tomorrow.run(), 0)
            self.assertEqual(len(posted), 2)
            self.assertNotEqual(posted[0]["jobID"], posted[1]["jobID"])
            self.assertEqual(tomorrow.records[R.row_key(current)]["createdAt"], R.db.stamp(clock.return_value))
            self.assertEqual(len(list(history.glob("*.done"))), 2)

    def test_terminal_failure_does_not_resubmit_and_clears_intent(self):
        self.submission_case("failed", "scan_failed")

    def test_size_rejection_remains_failed_without_resubmission(self):
        self.submission_case("failed", "image_too_large", terminal_reason="image_too_large")
        self.submission_case("failed", "sbom_too_large", terminal_reason="sbom_too_large")
        self.submission_case("failed", "scan_failed", terminal_reason="untrusted error with private data")

    def test_failure_reason_allowlist_is_bounded(self):
        for reason in (None, {}, [], "unknown_reason", "image_too_large\nprivate"):
            self.assertEqual(R.scan_failure_reason({"reason": reason}), "scan_failed")
        self.assertEqual(R.scan_failure_reason({"reason": "image_auth_failed"}), "image_auth_failed")

    def test_missing_report_cannot_pass_a_successful_api_job(self):
        self.submission_case("succeeded", "report_missing")

    def test_timeout_leaves_a_queryable_intent_without_retry(self):
        self.submission_case("running", "scan_timeout", timeout=True)

    def test_abandoned_request_keeps_fence(self):
        self.submission_case("abandoned", "scan_abandoned")

    def test_restart_after_acceptance_leaves_fence(self):
        self.submission_case("succeeded", "engine_restarted", restarted=True)

    def test_lost_post_response_queries_id_without_post_retry(self):
        self.submission_case("succeeded", "report_missing", lost=True)

    def submission_case(self, status_state, reason, timeout=False, restarted=False, lost=False, terminal_reason=None):
        with tempfile.TemporaryDirectory() as path:
            api = mock.Mock()
            api.items.return_value = []  # No full report/SBOM persisted.
            runner = R.Runner(api, CANARY_CONFIG, EXPECTED, Path(path), "vulnerability")
            requests = []
            def service(component, endpoint, value=None):
                requests.append((component, endpoint, value))
                if endpoint == "/v1/scanImage":
                    if lost:
                        raise OSError("lost response")
                    return {"accepted": True}
                return {"state": status_state, "phase": "completed" if status_state == "succeeded" else "scanning",
                        "reason": terminal_reason}
            audit = {"podUID": "engine-uid"}
            with mock.patch.object(runner, "preflight"), mock.patch.object(runner, "gate"), \
                    mock.patch.object(runner, "observe"), mock.patch.object(R, "service", side_effect=service), \
                    mock.patch.object(R, "component_pod", return_value={"metadata": {"uid": "other" if restarted else "engine-uid"}}), \
                    mock.patch.object(R.time, "monotonic", side_effect=[0, 0, 361] if timeout else lambda: 0):
                with self.assertRaisesRegex(R.db.Rejected, reason):
                    runner.vulnerability(row(), audit)
            intent_path = Path(path) / "inflight.json"
            self.assertEqual(intent_path.exists(), timeout or restarted or status_state == "abandoned")
            intent = json.loads(intent_path.read_text()) if intent_path.exists() else {"jobID": requests[0][2]["jobID"]}
            posts = [r for r in requests if r[2] is not None]
            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0][2]["jobID"], intent["jobID"])
            self.assertEqual(posts[0][2]["imageHash"], row()["runningDigest"])
            self.assertEqual(runner.result["assessments"], [])
            if lost:
                self.assertEqual(requests[1][1], "/v1/scanStatus/" + intent["jobID"])


class PrerequisiteTests(unittest.TestCase):
    def test_storage_timestamp_precision_preserves_new_and_rejects_cached_results(self):
        fixture = json.loads((ROOT / "scripts/tests/fixtures/kubescape-report-identity.json").read_text())
        report, sbom = fixture["report"], fixture["sbom"]
        old = copy.deepcopy(report)
        report["spec"]["metadata"]["report"]["createdAt"] = "2026-11-22T04:37:22Z"
        result = R.check_report(report, sbom, row(), EXPECTED, "2026-11-22T04:37:22.261402Z", old)
        self.assertEqual(result["createdAt"], "2026-11-22T04:37:22Z")
        with self.assertRaisesRegex(R.db.Rejected, "report_not_new"):
            R.check_report(old, sbom, row(), EXPECTED, "2026-11-22T04:37:22.261402Z", old)
        with self.assertRaisesRegex(R.db.Rejected, "report_not_new"):
            R.check_report(report, sbom, row(), EXPECTED, "2026-11-22T04:37:22.261402Z", report)
        sbom["spec"]["syft"]["source"]["metadata"]["config"] = "YWx0ZXJlZA=="
        with self.assertRaisesRegex(R.db.Rejected, "sbom_identity_invalid"):
            R.check_report(report, sbom, row(), EXPECTED, "2026-11-22T04:37:22.261402Z", old)

    def test_list_items_inherit_kind_from_kubernetes_list(self):
        api = R.API(local=True)
        with mock.patch.object(api, "get", return_value={"kind": "DeploymentList", "items": [{"metadata": {"name": "example"}}]}):
            self.assertEqual(api.items(R.APPS, "deployments")[0]["kind"], "Deployment")

    def test_actual_preflight_rejects_refresh_low_space_and_memory(self):
        for failure in ("database_refresh_in_progress", "database_pvc_low_space", "memory_budget_exhausted"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as path:
                api = mock.Mock()
                release = {"metadata": {"generation": 3}, "spec": {"values": {"grypeOfflineDB": {"image": {"sha": EXPECTED["indexDigest"]}}}},
                           "status": {"observedGeneration": 2 if failure == "database_refresh_in_progress" else 3,
                                      "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 3}]}}
                api.get.return_value = release
                api.items.return_value = [{"metadata": {"name": "engine"}, "timestamp": R.db.stamp(NOW),
                                           "containers": [{"name": "kubevuln", "usage": {"cpu": "1m", "memory": "3Gi"}}]}]
                config = {**CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]}
                runner = R.Runner(api, config, EXPECTED, Path(path), "vulnerability")
                free = 1024**3 if failure == "database_pvc_low_space" else 6 * 1024**3
                with mock.patch.object(R.db, "utcnow", return_value=NOW), \
                        mock.patch.object(R, "component_pod", return_value={"metadata": {"name": "engine", "uid": "engine-uid"}}), \
                        mock.patch.object(R.os, "statvfs", return_value=SimpleNamespace(f_bavail=free, f_frsize=1)):
                    with self.assertRaisesRegex(R.db.Rejected, failure):
                        runner.preflight()

    def test_actual_physical_disk_gate_includes_retained_allocations(self):
        with tempfile.TemporaryDirectory() as path:
            api = mock.Mock()
            api.get.return_value = {"status": {"phase": "Bound"}, "spec": {"volumeName": "volume"}}
            volume = {"metadata": {"name": "volume"}, "status": {"robustness": "healthy"}}
            replicas = [{"metadata": {"name": str(i)}, "spec": {"volumeName": "volume", "nodeID": "node", "diskID": "disk"},
                         "status": {"currentState": "running"}} for i in range(3)]
            node = {"metadata": {"name": "node"}, "status": {"diskStatus": {"disk": {
                "diskUUID": "disk", "storageAvailable": 9 * 1024**3, "storageMaximum": 100 * 1024**3}}}}
            api.items.side_effect = [ [volume], replicas, [node] ]
            runner = R.Runner(api, CANARY_CONFIG, EXPECTED, Path(path), "vulnerability")
            with self.assertRaisesRegex(R.db.Rejected, "replica_disk_low_space"):
                runner.storage_gate()


class IdleResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.api = mock.Mock()
        self.runner = R.Runner(self.api, {**CONFIG, "databaseChecksum": EXPECTED["metadata"]["checksum"]},
                               EXPECTED, Path(self.temp.name), "vulnerability")
        self.elapsed = 0
        self.start = datetime(2026, 11, 22, 19, 15, 56, tzinfo=timezone.utc)
        self.runner.deadline = 1500
        self.engine = {"metadata": {"name": "engine", "uid": "engine-uid"}, "spec": {"nodeName": "node"}}
        self.clock = self.enterContext(mock.patch.object(R.db, "utcnow", side_effect=lambda: self.start + timedelta(seconds=self.elapsed)))
        self.enterContext(mock.patch.object(R.time, "monotonic", side_effect=lambda: self.elapsed))
        self.sleep = self.enterContext(mock.patch.object(R.time, "sleep", side_effect=self.advance))
        self.component = self.enterContext(mock.patch.object(R, "component_pod", return_value=self.engine))
        self.service = self.enterContext(mock.patch.object(R, "service", return_value={"queueDepth": 0}))

    def advance(self, seconds):
        self.elapsed += seconds

    def metric(self, cpu="496760303n", memory="239152Ki", seconds=-29):
        # Exact CPU/memory and timestamp offset from the last retained Koito
        # sample, 19:15:27Z, before the 19:15:56Z scanner_busy failure. The
        # rejected preflight itself did not retain its sample or metrics window.
        return {"metadata": {"name": "engine"}, "timestamp": R.db.stamp(self.start + timedelta(seconds=seconds)),
                "containers": [{"name": "kubevuln", "usage": {"cpu": cpu, "memory": memory}}]}

    def test_previous_scan_cpu_waits_for_new_idle_sample(self):
        busy, idle = self.metric(), self.metric(cpu="1m", seconds=5)
        self.api.items.side_effect = [[busy], [busy], [idle]]
        self.assertEqual(self.runner.idle_resources(self.engine), idle["containers"][0]["usage"])
        self.assertEqual(self.elapsed, 10)
        self.assertEqual(len(self.runner.result["resourcePreflight"]), 3)
        self.assertEqual(self.runner.result["resourcePreflight"][0]["usage"]["cpu"], "496760303n")
        self.service.assert_not_called()
        self.assertFalse((self.runner.history / "inflight.json").exists())

    def test_busy_scanner_stays_failed_and_bounded(self):
        self.api.items.return_value = [self.metric()]
        with self.assertRaisesRegex(R.db.Rejected, "scanner_busy"):
            self.runner.idle_resources(self.engine)
        self.assertEqual(self.elapsed, R.IDLE_WAIT_SECONDS)
        self.assertEqual(self.api.items.call_count, 1 + R.IDLE_WAIT_SECONDS // R.IDLE_POLL_SECONDS)
        self.service.assert_not_called()

    def test_idle_sample_must_advance_after_busy(self):
        # A regressed or cached metric cannot turn a busy observation into idle.
        busy, idle = self.metric(), self.metric(cpu="1m", seconds=-30)
        self.api.items.side_effect = [[busy]] + [[idle]] * 24
        with self.assertRaisesRegex(R.db.Rejected, "scanner_busy"):
            self.runner.idle_resources(self.engine)

    def test_wait_preserves_memory_freshness_identity_and_transport_gates(self):
        for failure in ("memory_budget_exhausted", "resource_metrics_stale", "engine_restarted", "api_unavailable"):
            with self.subTest(failure=failure):
                self.elapsed = 0
                self.component.return_value = self.engine
                second = self.metric(memory="3Gi") if failure == "memory_budget_exhausted" else self.metric(seconds=-181)
                self.api.items.side_effect = [[self.metric()], [second]]
                if failure == "engine_restarted":
                    self.component.return_value = {"metadata": {"uid": "replacement"}}
                if failure == "api_unavailable":
                    self.api.items.side_effect = [[self.metric()], R.APIError(503)]
                    with self.assertRaises(R.APIError):
                        self.runner.idle_resources(self.engine)
                else:
                    with self.assertRaisesRegex(R.db.Rejected, failure):
                        self.runner.idle_resources(self.engine)
                self.assertEqual(self.elapsed, 5)
        self.service.assert_not_called()

    def test_wait_leaves_time_for_scan_and_publication(self):
        self.runner.deadline = self.runner.config["scanTimeoutSeconds"] + 60 + 7
        self.api.items.return_value = [self.metric()]
        with self.assertRaisesRegex(R.db.Rejected, "execution_time_budget"):
            self.runner.idle_resources(self.engine)
        self.assertEqual(self.elapsed, 7)
        self.service.assert_not_called()

    def test_full_preflight_still_rejects_nonempty_queue_after_idle(self):
        busy, idle = self.metric(), self.metric(cpu="1m", seconds=5)
        pod_samples = iter([[busy], [idle]])
        node = {"metadata": {"name": "node"}, "status": {
            "conditions": [{"type": "Ready", "status": "True"}], "allocatable": {"cpu": "4", "memory": "8Gi"}}}
        def items(base, resource, namespace=None):
            if resource == "pods":
                return next(pod_samples) if namespace == R.NS else []
            if base == R.CORE:
                return [node]
            return [{"metadata": {"name": "node"}, "timestamp": R.db.stamp(self.clock()),
                     "usage": {"cpu": "1m", "memory": "1Gi"}}]
        self.api.items.side_effect = items
        self.api.get.return_value = {"metadata": {"generation": 1}, "spec": {"values": {
            "grypeOfflineDB": {"image": {"sha": EXPECTED["indexDigest"]}}}}, "status": {"observedGeneration": 1,
            "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 1}]}}
        self.service.return_value = {"queueDepth": 1}
        with mock.patch.object(self.runner, "storage_gate") as storage, \
                mock.patch.object(R.os, "statvfs", return_value=SimpleNamespace(f_bavail=6 * 1024**3, f_frsize=1)):
            with self.assertRaisesRegex(R.db.Rejected, "engine_not_idle"):
                self.runner.preflight()
        storage.assert_called_once()
        self.assertEqual(self.elapsed, 5)
        self.assertTrue(all(call.args[1] != "/v1/scanImage" for call in self.service.call_args_list))


if __name__ == "__main__":
    unittest.main()
