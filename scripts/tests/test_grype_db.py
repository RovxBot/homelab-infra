"""Supply-chain, freshness and bounded manual-assessment failure regressions."""

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import grype_db as db
import grype_propose as proposal
import kubescape_db as runtime

NOW = datetime(2026, 11, 22, 4, tzinfo=timezone.utc)
BUILT = "2026-11-21T06:20:06Z"
CHECKSUM = "sha256:" + "a" * 64
INDEX = "sha256:" + "b" * 64
MANIFEST = "sha256:" + "c" * 64
LATEST = {"status": "active", "built": BUILT, "schemaVersion": "v6.1.10",
          "checksum": CHECKSUM, "path": "vulnerability-db_v6.1.10_2026-11-21T00:21:39Z_1795242006.tar.zst"}
SOURCE = "http://grype-offline-db:80/databases/v6/" + LATEST["path"] + "?checksum=sha256%3A" + "a" * 64


def fixture():
    expected = {"state": "verified", "metadata": copy.deepcopy(LATEST), "indexDigest": INDEX,
                "manifestDigest": MANIFEST, "image": db.IMAGE + "@" + INDEX,
                "database": {"bytes": 1000, "archiveBytes": 100, "sha256": CHECKSUM},
                "publisher": {"status": "verified", "document": copy.deepcopy(LATEST),
                              "url": db.PUBLISHER, "method": "independent-publisher-https-metadata"}}
    report = {"metadata": {"annotations": {"kubescape.io/status": "ready"}}, "spec": {
        "metadata": {"report": {"createdAt": "2026-11-22T03:59:00Z"}, "tool": {"databaseVersion": "a" * 64}},
        "payload": {"descriptor": {"db": {"built": BUILT, "schemaVersion": "v6.1.10"}}, "matches": []}}}
    evidence = {"diagnostics": {"cveDBVersion": "a" * 64, "storageEnabled": True, "queueDepth": 0},
                "enginePodUID": "engine-uid", "declaredDigest": INDEX,
                "mirrorImage": expected["image"], "mirrorImageID": db.IMAGE + "@" + MANIFEST,
                "mirrorMetadata": copy.deepcopy(LATEST), "assessment": report,
                "audit": {"podUID": "engine-uid", "checkedAt": "2026-11-22T03:58:00Z",
                          "sqlite": {"built": BUILT, "schemaVersion": "v6.1.10", "metadataReadable": True,
                                     "sha256": CHECKSUM, "import": {"source": SOURCE}},
                          "checksum": {"valid": True, "built": BUILT, "schemaVersion": "v6.1.10", "from": SOURCE}}}
    return evidence, expected


class VerificationTests(unittest.TestCase):
    def test_independent_publisher_identity_requires_all_fields(self):
        db.publisher_match(LATEST, LATEST, NOW)
        for field, value in (("checksum", "sha256:" + "d" * 64), ("built", "2026-11-21T07:00:00Z"),
                             ("path", "vulnerability-db_v6.1.10_other.tar.zst")):
            publisher = {**LATEST, field: value}
            with self.subTest(field=field), self.assertRaisesRegex(db.Rejected, "publisher_mismatch"):
                db.publisher_match(LATEST, publisher, NOW)

    def test_invalid_future_stale_and_incompatible_metadata_fail(self):
        for field, value, reason in (("built", "bad", "invalid_timestamp"),
                                     ("built", "2026-11-22T05:00:00Z", "future_timestamp"),
                                     ("built", "2026-11-21T06:20:06", "invalid_timestamp"),
                                     ("schemaVersion", "v7.0.0", "incompatible_schema"),
                                     ("schemaVersion", "v6.2.0", "incompatible_schema"),
                                     ("path", "../../db.tar.zst", "invalid_archive_path"),
                                     ("checksum", "md5:abc", "invalid_checksum")):
            with self.subTest(value=value), self.assertRaisesRegex(db.Rejected, reason):
                db.metadata({**LATEST, field: value}, NOW)

    def test_unavailable_publisher_fails_closed(self):
        with patch.object(db, "get_bytes", side_effect=OSError("offline")):
            with self.assertRaisesRegex(db.Rejected, "publisher_unavailable"):
                db.publisher_metadata()

    def test_mirror_verification_cannot_substitute_for_publisher(self):
        with patch.object(db, "verify_image", return_value={"metadata": LATEST}), \
             patch.object(db, "publisher_metadata", side_effect=db.Rejected("publisher_unavailable")):
            with self.assertRaisesRegex(db.Rejected, "publisher_unavailable"):
                db.qualify("v6-latest", Path("unused"))

    def test_corrupt_oci_blob_rejected_even_when_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            digest = db.digest_bytes(b"expected")
            (work / digest.replace(":", "-")).write_bytes(b"corrupt!")
            with self.assertRaisesRegex(db.Rejected, "oci_digest_mismatch"):
                db.descriptor_bytes({"digest": digest, "size": 8}, "blobs", work)

    def test_sqlite_checks_real_metadata_and_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vulnerability.db"
            with sqlite3.connect(path) as c:
                c.execute("CREATE TABLE db_metadata(build_timestamp TEXT, model INTEGER, revision INTEGER, addition INTEGER)")
                c.execute("INSERT INTO db_metadata VALUES (?,6,1,10)", (BUILT,))
            actual = db.inspect_sqlite(path)
            self.assertEqual(actual["built"], BUILT)
            self.assertEqual(actual["quickCheck"], "ok")
            path.write_bytes(b"not a SQLite database")
            with self.assertRaisesRegex(db.Rejected, "sqlite_integrity_failed"):
                db.inspect_sqlite(path)

    def test_storage_preserves_old_db_and_accounts_for_hydration_and_temps(self):
        required = db.storage_budget(2_679_586_816, 205_235_440)["requiredFreeBytes"]
        self.assertGreater(required, 2 * 2_679_586_816)
        db.storage_budget(2_679_586_816, 205_235_440, required)
        with self.assertRaisesRegex(db.Rejected, "insufficient_storage"):
            db.storage_budget(2_679_586_816, 205_235_440, required - 1)

    @unittest.skipUnless(importlib.util.find_spec("zstandard"), "zstandard required for compressed archive integration")
    def test_archive_checksum_and_path_traversal(self):
        import zstandard
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "database.tar.zst"
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as contents:
                member = tarfile.TarInfo("../vulnerability.db")
                member.size = 3
                contents.addfile(member, io.BytesIO(b"bad"))
            path.write_bytes(zstandard.ZstdCompressor().compress(buffer.getvalue()))
            with self.assertRaisesRegex(db.Rejected, "archive_checksum_mismatch"):
                db.inspect_archive(path, LATEST, Path(directory))
            with self.assertRaisesRegex(db.Rejected, "unsafe_archive_member"):
                db.inspect_archive(path, {**LATEST, "checksum": db.digest_file(path)}, Path(directory))

    def test_prepare_changes_only_image_and_rollout_annotation(self):
        import yaml
        _, expected = fixture()
        expected["database"].update(built=BUILT, schemaVersion="v6.1.10", quickCheck="ok")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "infra/kubescape"
            destination.mkdir(parents=True)
            original = (db.ROOT / "infra/kubescape/helmrelease.yaml").read_text()
            original = original.replace(INDEX, "sha256:" + "e" * 64)
            import re
            original = re.sub(r"(        sha: |        homelab.cooked.beer/grype-index: )sha256:[a-f0-9]{64}",
                              lambda m: m[1] + "sha256:" + "e" * 64, original)
            path = destination / "helmrelease.yaml"
            path.write_text(original)
            with patch.object(db, "utcnow", return_value=NOW):
                self.assertTrue(db.prepare(expected, root))
                self.assertFalse(db.prepare(expected, root))
            before, after = yaml.safe_load(original), yaml.safe_load(path.read_text())
            before["spec"]["values"]["grypeOfflineDB"]["image"]["sha"] = INDEX
            before["spec"]["values"]["kubevuln"].setdefault("podAnnotations", {})["homelab.cooked.beer/grype-index"] = INDEX
            self.assertEqual(before, after)

    def test_proposal_records_old_and_new_identities(self):
        _, lock = fixture()
        lock["previous"] = {"indexDigest": "sha256:" + "e" * 64, "metadata": {}}
        lock["storage"] = db.storage_budget(1000, 100)
        text = proposal.body(lock)
        for value in (INDEX, CHECKSUM, BUILT, db.PUBLISHER, "human-reviewed", "#2135"):
            self.assertIn(value, text)

    def test_proposal_deduplicates_existing_same_database_without_writes(self):
        _, lock = fixture()
        replies = [proposal.PATHS[0], "", '[{"number": 123}]', "d" * 40 + "\trefs/heads/" + proposal.BRANCH,
                   "", "\n".join(proposal.PATHS), json.dumps(lock)]
        with patch.object(proposal.Path, "read_text", return_value=json.dumps(lock)), \
             patch.object(proposal, "run", side_effect=replies) as run:
            proposal.main()
        self.assertFalse(any(call.args[:2] == ("git", "push") for call in run.call_args_list))

    def test_proposal_refuses_unrelated_working_tree_changes(self):
        _, lock = fixture()
        with patch.object(proposal.Path, "read_text", return_value=json.dumps(lock)), \
             patch.object(proposal, "run", side_effect=["apps/memos/deployment.yaml", ""]):
            with self.assertRaisesRegex(RuntimeError, "unrelated"):
                proposal.main()


class FreshnessTests(unittest.TestCase):
    def test_healthy_and_explicit_zero_findings(self):
        evidence, expected = fixture()
        result = runtime.evaluate(evidence, expected, NOW)
        self.assertEqual(result["state"], "healthy")
        self.assertTrue(result["assessmentTrustworthy"])
        self.assertEqual(result["lastSuccessfulAssessmentTime"], "2026-11-22T03:59:00Z")

    def test_stale_database_is_never_hidden_by_ready_engine_or_good_scan(self):
        evidence, expected = fixture()
        now = db.timestamp(BUILT) + timedelta(days=15)
        evidence["audit"]["checkedAt"] = db.stamp(now)
        result = runtime.evaluate(evidence, expected, now)
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["reasons"], ["stale_database"])
        self.assertFalse(result["canAssess"])
        self.assertEqual(result["lastSuccessfulAssessmentTime"], "2026-11-22T03:59:00Z")

    def test_exact_age_boundary_and_newer_audit(self):
        evidence, expected = fixture()
        built = db.timestamp(BUILT)
        now = built + timedelta(hours=db.DATABASE_MAXIMUM_HOURS)
        evidence["audit"]["checkedAt"] = db.stamp(now)
        result = runtime.evaluate(evidence, expected, now)
        self.assertTrue(result["canAssess"])
        self.assertEqual(runtime.evaluate(evidence, expected, now + timedelta(microseconds=1))["state"], "stale")

    def test_weekly_grace_preserves_integrity_checks(self):
        evidence, expected = fixture()
        now = db.timestamp(BUILT) + timedelta(days=9)
        evidence["audit"]["checkedAt"] = db.stamp(now)
        result = runtime.evaluate(evidence, expected, now)
        self.assertTrue(result["canAssess"])
        self.assertEqual(result["integrity"], "verified")
        self.assertEqual(result["databaseFreshness"], "grace")
        self.assertEqual(result["databaseAgeHours"], 216)
        for mutate in (lambda e: e["audit"]["checksum"].update(valid=False),
                       lambda e: e["audit"]["sqlite"].update(schemaVersion="v7.0.0"),
                       lambda e: e.update(mirrorImageID="mirror@sha256:" + "d" * 64)):
            changed = copy.deepcopy(evidence)
            mutate(changed)
            self.assertFalse(runtime.evaluate(changed, expected, now)["canAssess"])
        self.assertEqual(db.database_freshness(BUILT, db.timestamp(BUILT) + timedelta(days=7))["state"], "fresh")
        self.assertEqual(db.database_freshness(BUILT, db.timestamp(BUILT) + timedelta(days=7, seconds=1))["state"], "grace")

    def test_unknown_missing_and_unverified_provenance(self):
        evidence, expected = fixture()
        self.assertEqual(runtime.evaluate({}, expected, NOW)["state"], "unknown")
        expected["publisher"]["status"] = "mirror-asserted"
        self.assertEqual(runtime.evaluate(evidence, expected, NOW)["state"], "unknown")

    def test_failed_identity_integrity_timestamp_and_schema_cases(self):
        mutations = [
            (lambda e: e["audit"]["sqlite"].update(built="bad"), "invalid_timestamp"),
            (lambda e: e["audit"]["sqlite"].update(built="2026-11-23T00:00:00Z"), "future_timestamp"),
            (lambda e: e["diagnostics"].update(cveDBVersion="wrong"), "loaded_database_mismatch"),
            (lambda e: e["audit"]["checksum"].update(valid=False), "cache_integrity_failed"),
            (lambda e: e["audit"]["sqlite"].update(metadataReadable=False), "cache_integrity_failed"),
            (lambda e: e["audit"]["sqlite"].update(schemaVersion="v7.0.0"), "incompatible_schema"),
            (lambda e: e.update(mirrorImageID="mirror@sha256:" + "d" * 64), "mirror_imageid_mismatch"),
            (lambda e: e.update(declaredDigest="wrong"), "gitops_digest_mismatch"),
            (lambda e: e["audit"].update(podUID="old-pod"), "cache_audit_pod_mismatch"),
        ]
        for mutation, reason in mutations:
            for database_only in (False, True):
                evidence, expected = fixture()
                mutation(evidence)
                with self.subTest(reason=reason, database_only=database_only):
                    result = runtime.evaluate(evidence, expected, NOW, database_only=database_only)
                    self.assertEqual(result["state"], "failed")
                    self.assertEqual(result["reasons"], [reason])
                    self.assertFalse(result["canAssess"])

    def test_old_report_does_not_prove_new_database_assessment(self):
        evidence, expected = fixture()
        evidence["assessment"]["spec"]["metadata"]["tool"]["databaseVersion"] = "old"
        result = runtime.evaluate(evidence, expected, NOW)
        self.assertEqual(result["state"], "unknown")
        self.assertTrue(result["canAssess"])
        self.assertFalse(result["assessmentTrustworthy"])

    def test_supported_scan_does_not_submit_when_freshness_fails(self):
        evidence, expected = fixture()
        now = db.timestamp(BUILT) + timedelta(days=15)
        evidence["audit"]["checkedAt"] = db.stamp(now)
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, "collect", return_value=evidence), \
             patch.object(db, "utcnow", return_value=now), patch.object(runtime, "named") as named:
            request = unittest.mock.Mock()
            with self.assertRaisesRegex(db.Rejected, "freshness_gate_failed"):
                runtime.scan_memos(request, expected, evidence["audit"], Path(directory))
            request.assert_not_called()
            named.assert_not_called()

    def test_audit_uses_read_only_pvc_on_engine_node_and_no_token(self):
        job = runtime.audit_manifest({"spec": {"nodeName": "metal4"}}, "audit-test")
        pod = job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["nodeSelector"]["kubernetes.io/hostname"], "metal4")
        self.assertTrue(pod["volumes"][0]["persistentVolumeClaim"]["readOnly"])
        for container in pod["containers"]:
            self.assertIn("@sha256:", container["image"])
            self.assertTrue(container["volumeMounts"][0]["readOnly"])
            self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])


class QualificationModeTests(unittest.TestCase):
    def cli_evaluate(self, evidence, expected, database_only=False, now=NOW):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            db.save_json(work / "input.json", evidence)
            db.save_json(work / "expected.json", expected)
            argv = ["kubescape_db.py", "evaluate", "--evidence", str(work / "input.json"),
                    "--expected", str(work / "expected.json"), "--work-dir", str(work)]
            if database_only:
                argv.append("--database-only")
            with patch.object(sys, "argv", argv), patch.object(db, "utcnow", return_value=now), \
                 patch("sys.stdout", new_callable=io.StringIO) as output:
                code = runtime.main()
            result = json.loads(output.getvalue())
            self.assertEqual(result, json.loads((work / "freshness.json").read_text()))
            return code, result

    def test_missing_historical_report_keeps_database_and_assessment_distinct(self):
        evidence, expected = fixture()
        for missing in (None, "absent"):
            if missing is None:
                evidence["assessment"] = None
            else:
                evidence.pop("assessment")
            with self.subTest(missing=missing):
                code, result = self.cli_evaluate(evidence, expected)
                self.assertEqual(code, 3)
                self.assertEqual(result["reasons"], ["assessment_unavailable"])
                self.assertTrue(result["canAssess"])
                self.assertFalse(result["assessmentTrustworthy"])
                code, result = self.cli_evaluate(evidence, expected, database_only=True)
                self.assertEqual(code, 0)
                self.assertEqual(result["qualificationMode"], "database-only")
                self.assertEqual(result["databaseState"], "fresh")
                self.assertEqual(result["integrity"], "verified")
                self.assertFalse(result["assessmentTrustworthy"])
                self.assertIsNone(result["lastSuccessfulAssessmentTime"])

    def test_database_only_does_not_evaluate_present_or_malformed_reports(self):
        evidence, expected = fixture()
        for report in (evidence["assessment"], {"malformed": True}):
            with self.subTest(report=report):
                code, result = self.cli_evaluate({**evidence, "assessment": report}, expected, database_only=True)
                self.assertEqual(code, 0)
                self.assertFalse(result["assessmentTrustworthy"])
                self.assertIsNone(result["lastSuccessfulAssessmentTime"])

    def test_database_only_exit_codes_preserve_invalid_and_expired_gates(self):
        cases = [
            (lambda e, x: e.pop("audit"), NOW, 3, "missing_or_invalid_evidence"),
            (lambda e, x: x["publisher"].update(status="unverified"), NOW, 3, "unverified_provenance"),
            (lambda e, x: e["audit"].update(checkedAt=db.stamp(NOW - timedelta(minutes=16))),
             NOW, 3, "cache_audit_expired"),
            (lambda e, x: e["audit"]["checksum"].update(valid=False), NOW, 4, "cache_integrity_failed"),
            (lambda e, x: e["diagnostics"].update(queueDepth=1), NOW, 4, "engine_not_idle"),
            (lambda e, x: e["diagnostics"].update(storageEnabled=False), NOW, 4, "engine_not_idle"),
            (lambda e, x: e["audit"].update(checkedAt=db.stamp(db.timestamp(BUILT) + timedelta(days=15))),
             db.timestamp(BUILT) + timedelta(days=15), 2, "stale_database"),
        ]
        for mutate, now, code, reason in cases:
            evidence, expected = fixture()
            evidence["assessment"] = None
            mutate(evidence, expected)
            with self.subTest(reason=reason):
                actual, result = self.cli_evaluate(evidence, expected, database_only=True, now=now)
                self.assertEqual(actual, code)
                self.assertEqual(result["reasons"], [reason])
                self.assertFalse(result["canAssess"])

    def test_database_only_keeps_existing_grace_boundary(self):
        evidence, expected = fixture()
        now = db.timestamp(BUILT) + timedelta(hours=db.DATABASE_MAXIMUM_HOURS)
        evidence["audit"]["checkedAt"] = db.stamp(now)
        code, result = self.cli_evaluate(evidence, expected, database_only=True, now=now)
        self.assertEqual(code, 0)
        self.assertEqual(result["databaseState"], "grace")
        code, result = self.cli_evaluate(evidence, expected, database_only=True, now=now + timedelta(microseconds=1))
        self.assertEqual(code, 2)
        self.assertFalse(result["canAssess"])

    def test_optional_report_lookup_handles_only_not_found(self):
        with patch.object(runtime, "kubectl", return_value="") as get:
            self.assertIsNone(runtime.named("vulnerabilitymanifests", runtime.REPORT, optional=True))
            self.assertIn("--ignore-not-found", get.call_args.args)
        for response in ("invalid JSON", subprocess.CalledProcessError(1, ["kubectl"])):
            options = {"side_effect": response} if isinstance(response, Exception) else {"return_value": response}
            with self.subTest(response=response), patch.object(runtime, "kubectl", **options):
                with self.assertRaises((ValueError, subprocess.SubprocessError)):
                    runtime.named("vulnerabilitymanifests", runtime.REPORT, optional=True)

    def test_check_collects_without_historical_report_and_database_only_skips_lookup(self):
        evidence, expected = fixture()
        deployment = {"spec": {"template": {"spec": {"containers": [{"image": expected["image"]}]}}}}
        release = {"spec": {"values": {"grypeOfflineDB": {"image": {"sha": INDEX}}}}}
        mirror_pod = {"metadata": {}, "status": {"containerStatuses": [{"imageID": evidence["mirrorImageID"]}]}}
        for database_only in (False, True):
            with self.subTest(database_only=database_only), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                db.save_json(work / "expected.json", expected)
                db.save_json(work / "cache-audit.json", evidence["audit"])
                argv = ["kubescape_db.py", "check", "--work-dir", str(work), "--expected", str(work / "expected.json")]
                if database_only:
                    argv.append("--database-only")
                def resource(kind, name, **kwargs):
                    return {"deployment": deployment, "helmrelease": release, "vulnerabilitymanifests": None}[kind]
                with patch.object(sys, "argv", argv), patch.object(db, "utcnow", return_value=NOW), \
                     patch.object(runtime, "engine_pod", return_value={"metadata": {"uid": "engine-uid"}}), \
                     patch.object(runtime, "named", side_effect=resource) as named, \
                     patch.object(runtime, "kubectl", side_effect=[{"items": [mirror_pod]}, LATEST]), \
                     patch.object(runtime, "api") as api, patch("sys.stdout", new_callable=io.StringIO) as output:
                    api.return_value.__enter__.return_value.return_value = evidence["diagnostics"]
                    self.assertEqual(runtime.main(), 0 if database_only else 3)
                result = json.loads(output.getvalue())
                self.assertTrue(result["canAssess"])
                self.assertFalse(result["assessmentTrustworthy"])
                self.assertIsNone(json.loads((work / "evidence.json").read_text())["assessment"])
                lookups = [call for call in named.call_args_list if call.args[0] == "vulnerabilitymanifests"]
                self.assertEqual(len(lookups), 0 if database_only else 1)
                if lookups:
                    self.assertTrue(lookups[0].kwargs["optional"])

    def test_assessment_mode_exit_codes_still_require_current_complete_reports(self):
        cases = [(lambda r: None, 0),
                 (lambda r: r["spec"]["metadata"]["tool"].update(databaseVersion="old"), 3),
                 (lambda r: r["spec"]["metadata"]["report"].update(createdAt="2026-11-18T00:00:00Z"), 2),
                 (lambda r: r["metadata"]["annotations"].update({"kubescape.io/status": "pending"}), 4),
                 (lambda r: r["spec"]["payload"].pop("matches"), 4)]
        for mutate, code in cases:
            evidence, expected = fixture()
            mutate(evidence["assessment"])
            with self.subTest(code=code):
                actual, result = self.cli_evaluate(evidence, expected)
                self.assertEqual(actual, code)
                self.assertEqual(result["assessmentTrustworthy"], code == 0)

    def test_scan_memos_rejects_database_only_before_any_work(self):
        with patch.object(sys, "argv", ["kubescape_db.py", "scan-memos", "--database-only"]), \
             patch("sys.stderr", new_callable=io.StringIO), patch.object(runtime, "api") as api:
            with self.assertRaises(SystemExit) as error:
                runtime.main()
            self.assertEqual(error.exception.code, 2)
            api.assert_not_called()

    def test_scan_memos_cannot_submit_without_archiving_historical_reports(self):
        evidence, expected = fixture()
        evidence["assessment"] = None
        with tempfile.TemporaryDirectory() as directory, patch.object(db, "utcnow", return_value=NOW), \
             patch.object(runtime, "collect", return_value=evidence), patch.object(runtime, "kubectl", return_value={"items": [
                 {"status": {"containerStatuses": [{"ready": True, "name": "memos", "imageID": runtime.MEMOS_IMAGE}]}}
             ]}), patch.object(runtime, "named", side_effect=[{}, subprocess.CalledProcessError(1, ["kubectl"])]) as named:
            request = unittest.mock.Mock()
            with self.assertRaises(subprocess.SubprocessError):
                runtime.scan_memos(request, expected, evidence["audit"], Path(directory))
            self.assertEqual(named.call_args.args, ("vulnerabilitymanifests", runtime.REPORT))
            self.assertFalse(named.call_args.kwargs.get("optional", False))
            request.assert_not_called()
            self.assertFalse((Path(directory) / "scan-request.json").exists())

    def test_scan_memos_rejects_missing_or_previous_database_result(self):
        for missing in (True, False):
            before, expected = fixture()
            after = copy.deepcopy(before)
            if missing:
                after["assessment"] = None
            else:
                after["assessment"]["spec"]["metadata"]["tool"]["databaseVersion"] = "old"
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory, \
                 patch.object(db, "utcnow", return_value=NOW), patch.object(runtime, "collect", side_effect=[before, after]), \
                 patch.object(runtime, "named", return_value={}), patch.object(runtime, "kubectl", return_value={"items": [
                     {"status": {"containerStatuses": [{"ready": True, "name": "memos", "imageID": runtime.MEMOS_IMAGE}]}}
                 ]}):
                request = unittest.mock.Mock(side_effect=[{}, {"state": "succeeded", "phase": "completed"}])
                with self.assertRaisesRegex(db.Rejected, "assessment_freshness_failed"):
                    runtime.scan_memos(request, expected, before["audit"], Path(directory))


if __name__ == "__main__":
    unittest.main()
