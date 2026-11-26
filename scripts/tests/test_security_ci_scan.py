from datetime import datetime, timezone
import base64
import hashlib
from unittest.mock import patch
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"ci"))
import scan_image as S
NOW=datetime(2026,11,24,12,tzinfo=timezone.utc)
MANIFEST=b'{"schemaVersion":2}'
CONFIG=b'{"architecture":"amd64","os":"linux"}'
DIGEST="sha256:"+hashlib.sha256(MANIFEST).hexdigest()
ASSET="sha256:"+"b"*64
SHA="c"*40


class ScanContractTests(unittest.TestCase):
    def report(self):
        return {"matches":[{"vulnerability":{"id":"PRIVATE-FINDING"},"artifact":{"name":"PRIVATE-PACKAGE"}}],
                "descriptor":{"name":"grype","version":S.VERSION,"db":{"status":{"valid":True,"built":"2026-11-24T00:00:00Z","schemaVersion":"v6.1.10"},"providers":{}}},
                "source":{"type":"image","target":{"userInput":S.IMAGE+DIGEST,"manifestDigest":DIGEST,"architecture":"amd64","os":"linux",
                          "manifest":base64.b64encode(MANIFEST).decode(),"config":base64.b64encode(CONFIG).decode(),
                          "imageID":"sha256:"+hashlib.sha256(CONFIG).hexdigest()}}}

    def test_only_safe_provenance_no_findings(self):
        result=S.provenance(self.report(),DIGEST,SHA,ASSET,NOW)
        self.assertEqual(result["image"],S.IMAGE+DIGEST)
        self.assertEqual(result["policyGate"],"not evaluated")
        self.assertNotIn("PRIVATE",json.dumps(result))

    def test_wrong_digest_stale_database_and_invalid_scanner_fail(self):
        for field,value in (("manifestDigest","sha256:"+"f"*64),("architecture","arm64"),("userInput","other:latest")):
            report=self.report()
            report["source"]["target"][field]=value
            with self.assertRaises(ValueError):
                S.provenance(report,DIGEST,SHA,ASSET,NOW)
        for field,value in (("built","2026-11-20T00:00:00Z"),("valid",False),("schemaVersion","v5")):
            report=self.report()
            report["descriptor"]["db"]["status"][field]=value
            with self.assertRaises(ValueError):
                S.provenance(report,DIGEST,SHA,ASSET,NOW)

    def test_tampered_raw_manifest_and_config_rejected(self):
        for field in ("manifest","config"):
            report=self.report()
            report["source"]["target"][field]=base64.b64encode(b"tampered").decode()
            with self.assertRaises(ValueError):
                S.provenance(report,DIGEST,SHA,ASSET,NOW)

    def test_flat_legacy_database_shape_not_accepted(self):
        report=self.report()
        report["descriptor"]["db"]=report["descriptor"]["db"]["status"]
        with self.assertRaises(KeyError):
            S.provenance(report,DIGEST,SHA,ASSET,NOW)

    def test_explicit_repository_allowlist_and_scan_attribution(self):
        for image in sorted(S.ALLOWED_IMAGES):
            report=self.report()
            report["source"]["target"]["userInput"]=image+DIGEST
            result=S.provenance(report,DIGEST,SHA,ASSET,NOW,image=image)
            self.assertEqual(result["image"],image+DIGEST)
            if image!=S.IMAGE:
                with self.assertRaises(ValueError):
                    S.provenance(report,DIGEST,SHA,ASSET,NOW)
        for image in ("ghcr.io/rovxbot/security-collector@", "ghcr.io/other/private@", "https://example.com/image@", "ghcr.io/rovxbot/security-receiver:latest@"):
            report=self.report()
            report["source"]["target"]["userInput"]=image+DIGEST
            with self.assertRaises(ValueError):
                S.provenance(report,DIGEST,SHA,ASSET,NOW,image=image)

    def test_retired_collector_no_longer_builds_or_scans(self):
        root = Path(__file__).resolve().parents[2]
        self.assertFalse((root/".github/workflows/security-collector-image.yml").exists())
        self.assertNotIn("ghcr.io/rovxbot/security-collector@", S.ALLOWED_IMAGES)

    def test_scanner_image_workflow_is_read_only_and_attributed(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root/".github/workflows/kubescape-scanner-image.yml").read_text()
        scan = workflow.split("\n  scan:\n",1)[1]
        self.assertIn("needs: test-build",scan)
        self.assertIn("SCAN_IMAGE_REPOSITORY: ghcr.io/rovxbot/kubevuln@",scan)
        self.assertIn("needs.test-build.outputs.digest",scan)
        self.assertIn("needs.test-build.outputs.source",scan)
        self.assertIn("contents: read",scan)
        self.assertNotIn("packages: write",scan)
        self.assertNotIn("GHCR_TOKEN",scan)
        self.assertIn("path: .work/image-scan/provenance.json",scan)
        self.assertIn('evidence["image"] == image',workflow)
        self.assertIn('evidence["sourceCommit"]',workflow)

    def serious(self, version="1", severity="High", fix="fixed"):
        return {"vulnerability":{"namespace":"PRIVATE-NS","id":"PRIVATE-CVE","severity":severity,
                                 "fix":{"state":fix}},
                "artifact":{"type":"deb","name":"PRIVATE-PACKAGE","version":version}}

    def test_baseline_review_is_exact_report_only_and_private(self):
        baseline, candidate = self.report(), self.report()
        base_proof = S.provenance(baseline,DIGEST,SHA,ASSET,NOW)
        candidate_proof = dict(base_proof,sourceCommit="d"*40)
        baseline["matches"] = [self.serious()]
        candidate["matches"] = [self.serious(),self.serious()]
        result = S.baseline_review(candidate,baseline,candidate_proof,base_proof)
        self.assertEqual(result["state"],"no_candidate_only_matches")
        candidate["matches"].append(self.serious(version="2",severity="Critical"))
        result = S.baseline_review(candidate,baseline,candidate_proof,base_proof)
        self.assertEqual(result["state"],"review_required")
        self.assertEqual(result["policyGate"],"not enforced")
        self.assertNotIn("PRIVATE",json.dumps(result))
        candidate["matches"] = [self.serious(version="2",fix="not-fixed")]
        self.assertEqual(S.baseline_review(candidate,baseline,candidate_proof,base_proof)["state"],"no_candidate_only_matches")

    def test_baseline_rejects_suppression_malformed_and_db_mismatch(self):
        report = self.report()
        proof = S.provenance(report,DIGEST,SHA,ASSET,NOW)
        report["matches"] = [self.serious()]
        with self.assertRaises(ValueError):
            S.baseline_review(report,report,proof,dict(proof,databaseBuilt="different"))
        report["ignoredMatches"] = [{"PRIVATE":"ignored"}]
        with self.assertRaises(ValueError):
            S.baseline_review(report,report,proof,proof)
        report["ignoredMatches"] = []
        report["matches"][0]["artifact"]["version"] = ""
        with self.assertRaises(ValueError):
            S.baseline_review(report,report,proof,proof)

    def test_baseline_fingerprint_detects_database_changes(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as root:
            path = Path(root)/"vulnerability.db"
            path.write_bytes(b"one")
            a = S.database_fingerprint(Path(root))
            path.write_bytes(b"two")
            self.assertNotEqual(a,S.database_fingerprint(Path(root)))
            other = Path(root)/"other"
            other.mkdir()
            (other/"vulnerability.db").write_bytes(b"three")
            with self.assertRaises(ValueError):
                S.database_fingerprint(Path(root))

    def test_exact_binary_checksum_required(self):
        self.assertEqual(S.ASSET_DIGEST,"sha256:e9da18cc1429e786722e32161288103600efffd8e483c90761f90485bd1bec85")
        payload=b"fixture binary"
        with patch.object(S,"ASSET_DIGEST","sha256:"+hashlib.sha256(payload).hexdigest()):
            S.verify_asset(payload)
            for value in (b"",payload+b"modified"):
                with self.assertRaises(ValueError):
                    S.verify_asset(value)



if __name__=="__main__":
    unittest.main()
