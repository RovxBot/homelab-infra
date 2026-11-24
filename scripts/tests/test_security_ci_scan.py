from datetime import datetime, timezone
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"ci"))
import scan_image as S
NOW=datetime(2026,11,24,12,tzinfo=timezone.utc)
DIGEST="sha256:"+"a"*64
ASSET="sha256:"+"b"*64
SHA="c"*40


class ScanContractTests(unittest.TestCase):
    def report(self):
        return {"matches":[{"vulnerability":{"id":"PRIVATE-FINDING"},"artifact":{"name":"PRIVATE-PACKAGE"}}],
                "descriptor":{"name":"grype","version":S.VERSION,"db":{"valid":True,"built":"2026-11-24T00:00:00Z","schemaVersion":"v6.1.10"}},
                "source":{"target":{"userInput":S.IMAGE+DIGEST,"manifestDigest":DIGEST,"architecture":"amd64","os":"linux"}}}

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
            report["descriptor"]["db"][field]=value
            with self.assertRaises(ValueError):
                S.provenance(report,DIGEST,SHA,ASSET,NOW)

    def test_immutable_release_and_exact_asset_required(self):
        asset={"name":S.ARCHIVE,"browser_download_url":S.DOWNLOAD,"state":"uploaded","digest":ASSET,"size":1024}
        release={"tag_name":"v"+S.VERSION,"immutable":True,"draft":False,"prerelease":False,"assets":[asset]}
        self.assertEqual(S.release_asset(release),asset)
        for field,value in (("immutable",False),("prerelease",True),("tag_name","latest"),("assets",[asset,asset])):
            bad=copy.deepcopy(release)
            bad[field]=value
            with self.assertRaises(ValueError):
                S.release_asset(bad)


if __name__=="__main__":
    unittest.main()
