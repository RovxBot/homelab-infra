"""Safety regressions for the intentionally suspended Kubescape staging task."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("check_kubescape", ROOT / "scripts/ci/check_kubescape.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def safe_workload():
    return {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": "storage", "namespace": "kubescape"},
        "spec": {"template": {
            "metadata": {"labels": {"homelab.cooked.beer/owner": "sam"}},
            "spec": {"securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
                     "containers": [{"name": "apiserver", "image": "test@sha256:" + "a" * 64,
                                     "securityContext": {"runAsNonRoot": True, "allowPrivilegeEscalation": False,
                                                         "capabilities": {"drop": ["ALL"]}},
                                     "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                                                   "limits": {"cpu": "1", "memory": "256Mi"}}}]}}},
    }


class StagedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.release = yaml.safe_load((ROOT / "infra/kubescape/helmrelease.yaml").read_text())

    def test_suspended_stage_is_valid(self):
        CHECK.check_release(self.release)
        self.assertTrue(self.release["spec"]["suspend"])

    def test_unsuspending_fails(self):
        self.release["spec"]["suspend"] = False
        with self.assertRaisesRegex(AssertionError, "remain suspended"):
            CHECK.check_release(self.release)

    def test_dangerous_capabilities_cannot_be_enabled_or_implicit(self):
        for key in CHECK.DISABLED:
            for value in (None, "enable"):
                with self.subTest(key=key, value=value):
                    changed = copy.deepcopy(self.release)
                    if value is None:
                        del changed["spec"]["values"]["capabilities"][key]
                    else:
                        changed["spec"]["values"]["capabilities"][key] = value
                    with self.assertRaises(AssertionError):
                        CHECK.check_release(changed)

    def test_node_agent_dependencies_are_disabled_independently(self):
        for path in (("nodeAgent", "config", "hostSensor"), ("nodeAgent", "sbomScanner"),
                     ("nodeAgent", "autoscaler"), ("nodeAgent", "multipleDaemonSets"),
                     ("nodeAgent", "gVisorStart")):
            with self.subTest(path=path):
                changed = copy.deepcopy(self.release)
                item = changed["spec"]["values"]
                for key in path:
                    item = item[key]
                item["enabled"] = True
                with self.assertRaises(AssertionError):
                    CHECK.check_release(changed)

    def test_chart_and_active_component_versions_are_pinned(self):
        changed = copy.deepcopy(self.release)
        changed["spec"]["chart"]["spec"]["version"] = "1.x"
        with self.assertRaises(AssertionError):
            CHECK.check_release(changed)
        for key in ("kubescape", "operator", "storage", "kubescapeOpsTool"):
            changed = copy.deepcopy(self.release)
            changed["spec"]["values"][key]["image"]["tag"] = "latest"
            with self.assertRaises(AssertionError):
                CHECK.check_release(changed)

    def test_flux_source_tree_includes_suspended_release(self):
        cluster = yaml.safe_load((ROOT / "clusters/home/kustomization.yaml").read_text())
        child_path = "infrastructure/kubescape-kustomization.yaml"
        self.assertEqual(cluster["resources"].count(child_path), 1)
        child = yaml.safe_load((ROOT / "clusters/home" / child_path).read_text())["spec"]
        self.assertEqual(child["path"], "./infra/kubescape")
        self.assertFalse(child["wait"])
        self.assertEqual(child["dependsOn"], [{"name": "infrastructure"}, {"name": "kyverno-policies"}])
        infrastructure = yaml.safe_load((ROOT / "clusters/home/infrastructure/kustomization.yaml").read_text())
        self.assertNotIn("../../../infra/kubescape", infrastructure["resources"])
        source = yaml.safe_load((ROOT / "infra/kubescape/kustomization.yaml").read_text())
        self.assertEqual(set(source["resources"]), {"namespace.yaml", "helmrepository.yaml", "helmrelease.yaml"})
        namespace = yaml.safe_load((ROOT / "infra/kubescape/namespace.yaml").read_text())
        labels = namespace["metadata"]["labels"]
        self.assertEqual(labels["pod-security.kubernetes.io/enforce"], "restricted")
        self.assertTrue(labels["homelab.cooked.beer/owner"])

    def test_scheduler_has_explicit_flux_postrender_suspension(self):
        renderer, = self.release["spec"]["postRenderers"]
        patch, = renderer["kustomize"]["patches"]
        self.assertEqual(patch["target"]["kind"], "CronJob")
        self.assertEqual(patch["target"]["name"], "kubescape-scheduler")
        self.assertEqual(yaml.safe_load(patch["patch"]), [{"op": "add", "path": "/spec/suspend", "value": True}])

    def test_scope_guard_rejects_kyverno_gatus_or_cilium_changes(self):
        protected = ["infra/kyverno/helmrelease.yaml", "infra/kyverno-policies/example.yaml",
                     "infra/observability/gatus-configmap.yaml", "infra/cilium/helmrelease.yaml"]
        self.assertEqual(CHECK.check_scope(protected + ["infra/kubescape/helmrelease.yaml"]), protected)

    def test_recorded_inventory_is_explicit_about_blockers(self):
        inventory = json.loads((ROOT / "infra/kubescape/rendered-inventory.json").read_text())
        self.assertEqual(inventory["admissionBlockers"], CHECK.KNOWN_BLOCKERS)
        self.assertEqual(sum(inventory["counts"].values()), 47)
        self.assertEqual(inventory["counts"]["Deployment"], 3)
        self.assertEqual(inventory["counts"]["APIService"], 1)
        self.assertNotIn("DaemonSet", inventory["counts"])


class FailClosedPreflightTests(unittest.TestCase):
    def test_policy_report_warnings_do_not_hide_failures(self):
        text = "Warning: legacy policy deprecated\napiVersion: openreports.io/v1alpha1\nsummary: {fail: 1}\n"
        self.assertEqual(CHECK.policy_report(text)["summary"]["fail"], 1)
        with self.assertRaises(AssertionError):
            CHECK.policy_report("no report produced")

    def test_valid_workload_passes(self):
        self.assertEqual(CHECK.findings([safe_workload()]), [])

    def test_privilege_host_mounts_and_host_namespaces_fail(self):
        mutations = [lambda p: p.update(hostNetwork=True), lambda p: p.update(hostPID=True),
                     lambda p: p.update(hostIPC=True), lambda p: p.update(volumes=[{"hostPath": {"path": "/"}}]),
                     lambda p: p["containers"][0]["securityContext"].update(privileged=True),
                     lambda p: p["containers"][0]["securityContext"].update(runAsUser=0),
                     lambda p: p["containers"][0]["securityContext"].update(capabilities={"drop": ["ALL"], "add": ["SYS_ADMIN"]})]
        for mutate in mutations:
            workload = safe_workload()
            mutate(workload["spec"]["template"]["spec"])
            self.assertTrue(CHECK.findings([workload]))

    def test_unexpected_admission_webhooks_and_daemonsets_fail(self):
        for kind in ("MutatingWebhookConfiguration", "ValidatingWebhookConfiguration", "NetworkPolicy", "DaemonSet"):
            resource = safe_workload()
            resource["kind"] = kind
            self.assertTrue(any("forbidden optional" in f for f in CHECK.findings([resource])))

    def test_missing_init_container_resources_fail(self):
        workload = safe_workload()
        pod = workload["spec"]["template"]["spec"]
        init = copy.deepcopy(pod["containers"][0])
        del init["resources"]
        init["name"] = "certgen-create"
        pod["initContainers"] = [init]
        self.assertEqual(CHECK.findings([workload]), [CHECK.KNOWN_BLOCKERS[0]])

    def test_unsuspended_scheduler_fails(self):
        workload = safe_workload()
        workload["kind"] = "CronJob"
        workload["metadata"]["name"] = "kubescape-scheduler"
        workload["spec"] = {"schedule": "0 8 * * *", "jobTemplate": {"spec": workload["spec"]}}
        self.assertTrue(any("scheduled scanning is active" in f for f in CHECK.findings([workload])))


if __name__ == "__main__":
    unittest.main()
