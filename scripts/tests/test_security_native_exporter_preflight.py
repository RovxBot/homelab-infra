"""Offline boundaries for documented Kubescape HTTP exporter candidate generation."""
import json
from pathlib import Path
import sys
import unittest

import yaml

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts/ci"))
from security_native_exporter_preflight import (EXPORTER, candidate, configmap_patch,
                                                load_config, unique_pairs)


def baseline(**exporter_changes):
    exports={"stdoutExporter":True, "alertManagerExporterUrls":[],
             "syslogExporterURL":""}
    exports.update(exporter_changes)
    return json.dumps({"runtimeDetectionEnabled": True, "other": {"x": "y"},
                       "exporters":exports}, indent=4)+"\n"


class DocumentedExporterCandidateTests(unittest.TestCase):
    def test_adds_only_documented_http_config_leaves_stdout_and_all_keys(self):
        original=baseline()
        proposed=candidate(original)
        self.assertEqual(load_config(proposed)["exporters"]["httpExporterConfig"],EXPORTER)
        after=load_config(proposed)
        before=load_config(original)
        after["exporters"].pop("httpExporterConfig")
        self.assertEqual(after,before)
        self.assertTrue(after["exporters"]["stdoutExporter"])
        self.assertEqual(EXPORTER["tokenFile"],
                         "/var/run/secrets/kubescape.io/api-token/token")
        self.assertEqual(EXPORTER["url"],
                         "https://security-receiver.kubescape.svc:8443")
        self.assertFalse(EXPORTER["enableAlertBulking"])
        self.assertNotIn("insecureSkipVerify",EXPORTER)
        self.assertNotIn("HTTP_ENDPOINT_URL",str(EXPORTER))

    def test_patch_tests_exact_chart_original_before_replacement(self):
        old=baseline()
        new=candidate(old)
        patch=configmap_patch(old,new)
        self.assertEqual(patch["target"],
                         {"version":"v1","kind":"ConfigMap","name":"node-agent",
                          "namespace":"kubescape"})
        changes=yaml.safe_load(patch["patch"])
        self.assertEqual(changes,[{"op":"test","path":"/data/config.json","value":old},
                                  {"op":"replace","path":"/data/config.json","value":new}])
        self.assertEqual(yaml.safe_load(yaml.safe_dump(patch)),patch)

    def test_fail_closed_if_chart_now_exposes_http(self):
        with self.assertRaisesRegex(ValueError,"chart_now_emits_http_exporter"):
            candidate(baseline(httpExporterConfig={"url":"https://example.test"}))

    def test_fail_closed_on_other_sinks_and_disabled_stdout(self):
        for changes in ({"stdoutExporter":False},
                        {"syslogExporterURL":"udp://host"},
                        {"alertManagerExporterUrls":["http://invalid"]}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    candidate(baseline(**changes))

    def test_rejects_duplicate_json_keys(self):
        with self.assertRaisesRegex(ValueError,"duplicate_chart_json_key"):
            load_config('{"exporters":{"stdoutExporter":true,"stdoutExporter":false}}')
        with self.assertRaisesRegex(ValueError,"duplicate_chart_json_key"):
            unique_pairs([("exporters",1),("exporters",2)])

    def test_no_active_manifest_change_in_preflight(self):
        text=(ROOT/"scripts/ci/security_native_exporter_preflight.py").read_text()
        self.assertNotIn("kubectl apply",text)
        self.assertNotIn("HTTP_ENDPOINT_URL",text)
        helm=yaml.safe_load((ROOT/"infra/kubescape/helmrelease.yaml").read_text())
        self.assertEqual(helm["spec"]["chart"]["spec"]["version"],"1.42.0")
        self.assertTrue(helm["spec"]["values"]["global"]["apiAuth"]["enabled"])
        self.assertNotIn("httpExporterConfig",helm["spec"]["values"]["nodeAgent"]["config"])
        self.assertTrue(helm["spec"]["values"]["nodeAgent"]["config"]["stdoutExporter"])


if __name__=="__main__":
    unittest.main()
