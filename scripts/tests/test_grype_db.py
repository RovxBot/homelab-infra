"""Supply-chain, freshness and bounded manual-assessment failure regressions."""

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
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
                                     ("built", "2026-11-19T03:59:59Z", "stale_database"),
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
        evidence["audit"]["sqlite"]["built"] = "2026-11-19T03:59:59Z"
        result = runtime.evaluate(evidence, expected, NOW)
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["reasons"], ["stale_database"])
        self.assertFalse(result["canAssess"])
        self.assertEqual(result["lastSuccessfulAssessmentTime"], "2026-11-22T03:59:00Z")

    def test_exact_age_boundary_and_newer_audit(self):
        evidence, expected = fixture()
        built = db.timestamp(BUILT)
        now = built + timedelta(hours=72)
        evidence["audit"]["checkedAt"] = db.stamp(now)
        result = runtime.evaluate(evidence, expected, now)
        self.assertTrue(result["canAssess"])
        self.assertEqual(runtime.evaluate(evidence, expected, now + timedelta(microseconds=1))["state"], "stale")

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
            evidence, expected = fixture()
            mutation(evidence)
            with self.subTest(reason=reason):
                result = runtime.evaluate(evidence, expected, NOW)
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
        evidence["audit"]["sqlite"]["built"] = "2026-11-19T03:59:59Z"
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, "collect", return_value=evidence), \
             patch.object(db, "utcnow", return_value=NOW), patch.object(runtime, "named") as named:
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


if __name__ == "__main__":
    unittest.main()
