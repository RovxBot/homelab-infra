"""Safety regressions for the intentionally suspended Kubescape staging task."""

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

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


def certgen_workload():
    workload = safe_workload()
    pod = CHECK.pod_template(workload)["spec"]
    pod["serviceAccountName"] = "storage"
    pod["initContainers"] = []
    for name in ("certgen-create", "certgen-patch"):
        container = copy.deepcopy(pod["containers"][0])
        container.pop("resources")
        container.update(name=name, command=["/usr/bin/kubescape-ops-tool", name],
                         args=["--secret-name", "storage-tls-init"])
        pod["initContainers"].append(container)
    return workload


def scheduler_workload():
    workload = safe_workload()
    workload["apiVersion"] = "batch/v1"
    workload["kind"] = "CronJob"
    workload["metadata"]["name"] = "kubescape-scheduler"
    workload["spec"] = {"schedule": "0 8 * * *", "jobTemplate": {"spec": workload["spec"]}}
    return workload


def corrected_resources(raw):
    resources = copy.deepcopy(raw)
    for resource in resources:
        if resource["kind"] == "Deployment" and resource["metadata"]["name"] == "storage":
            for container in CHECK.pod_template(resource)["spec"]["initContainers"]:
                container["resources"] = copy.deepcopy(CHECK.CERTGEN_BUDGET)
        if resource["kind"] == "CronJob":
            resource["spec"]["suspend"] = True
    return resources


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
        patch, = [p for p in renderer["kustomize"]["patches"] if p["target"]["kind"] == "CronJob"]
        self.assertEqual(patch["target"]["kind"], "CronJob")
        self.assertEqual(patch["target"]["name"], "kubescape-scheduler")
        self.assertEqual(yaml.safe_load(patch["patch"]), [{"op": "add", "path": "/spec/suspend", "value": True}])

    def test_scope_guard_rejects_kyverno_gatus_or_cilium_changes(self):
        protected = ["infra/kyverno/helmrelease.yaml", "infra/kyverno-policies/example.yaml",
                     "infra/observability/gatus-configmap.yaml", "infra/cilium/helmrelease.yaml"]
        self.assertEqual(CHECK.check_scope(protected + ["infra/kubescape/helmrelease.yaml"]), protected)

    def test_recorded_inventory_has_complete_certgen_budgets_and_no_blockers(self):
        inventory = json.loads((ROOT / "infra/kubescape/rendered-inventory.json").read_text())
        self.assertEqual(inventory["admissionBlockers"], [])
        self.assertEqual(inventory["certificateInitBudgets"], {
            name: CHECK.CERTGEN_BUDGET for name in CHECK.CERTGEN_NAMES})
        self.assertEqual(sum(inventory["counts"].values()), 47)
        self.assertEqual(inventory["counts"]["Deployment"], 3)
        self.assertEqual(inventory["counts"]["APIService"], 1)
        self.assertNotIn("DaemonSet", inventory["counts"])


class PostRenderTests(unittest.TestCase):
    def setUp(self):
        self.raw = [certgen_workload(), scheduler_workload(),
                    {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
                     "metadata": {"name": "storage-certgen-kubescape"},
                     "rules": [{"apiGroups": ["apiregistration.k8s.io"], "resources": ["apiservices"],
                                "verbs": ["get", "patch"]}]}]
        self.effective = corrected_resources(self.raw)

    def test_only_named_certgen_budgets_and_scheduler_suspension_are_allowed(self):
        CHECK.check_post_render(self.raw, self.effective)
        for container in CHECK.pod_template(self.effective[0])["spec"]["initContainers"]:
            self.assertEqual(container["resources"], CHECK.CERTGEN_BUDGET)

    def test_missing_partial_or_different_certgen_budgets_fail(self):
        for name in CHECK.CERTGEN_NAMES:
            for side in ("requests", "limits"):
                for unit in ("cpu", "memory"):
                    for value in (None, "0"):
                        with self.subTest(name=name, side=side, unit=unit, value=value):
                            effective = copy.deepcopy(self.effective)
                            init = next(c for c in CHECK.pod_template(effective[0])["spec"]["initContainers"]
                                        if c["name"] == name)
                            if value is None:
                                del init["resources"][side][unit]
                            else:
                                init["resources"][side][unit] = value
                            with self.assertRaisesRegex(AssertionError, "post-render changed"):
                                CHECK.check_post_render(self.raw, effective)
                            if value is None:
                                self.assertTrue(CHECK.findings(effective))

    def test_chart_init_container_name_or_budget_drift_fails(self):
        for mutation in (lambda cs: cs.pop(),
                         lambda cs: cs[0].update(name="renamed-certgen"),
                         lambda cs: cs.append(copy.deepcopy(cs[0])),
                         lambda cs: cs[0].update(resources=CHECK.CERTGEN_BUDGET)):
            raw = copy.deepcopy(self.raw)
            mutation(CHECK.pod_template(raw[0])["spec"]["initContainers"])
            with self.assertRaises(AssertionError):
                CHECK.check_post_render(raw, self.effective)

    def test_changes_to_unrelated_fields_fail(self):
        mutations = [
            lambda rs: CHECK.pod_template(rs[0])["spec"].update(serviceAccountName="other"),
            lambda rs: CHECK.pod_template(rs[0])["spec"].update(volumes=[]),
            lambda rs: CHECK.pod_template(rs[0])["spec"]["securityContext"].update(runAsUser=0),
            lambda rs: rs[2]["rules"][0]["verbs"].append("create"),
            lambda rs: rs[1]["spec"].update(suspend=False),
            lambda rs: rs.pop(),
        ]
        for where in ("containers", "initContainers"):
            for field, value in (("image", "changed"), ("command", ["changed"]), ("args", ["changed"]),
                                 ("securityContext", {}), ("volumeMounts", [])):
                mutations.append(lambda rs, w=where, f=field, v=value:
                                 CHECK.pod_template(rs[0])["spec"][w][0].update({f: v}))
        for mutate in mutations:
            effective = copy.deepcopy(self.effective)
            mutate(effective)
            with self.assertRaises(AssertionError):
                CHECK.check_post_render(self.raw, effective)

    @unittest.skipUnless(shutil.which("kustomize"), "Kustomize integration runs in Kubescape Staging Checks")
    def test_real_flux_patches_merge_by_name_and_preserve_other_fields(self):
        release = yaml.safe_load((ROOT / "infra/kubescape/helmrelease.yaml").read_text())
        renderer, = release["spec"]["postRenderers"]
        # Reordered inputs and same-name/namespace decoys catch positional or broad patches.
        for reverse in (False, True):
            with self.subTest(reverse=reverse), tempfile.TemporaryDirectory() as directory:
                raw = copy.deepcopy(self.raw)
                if reverse:
                    CHECK.pod_template(raw[0])["spec"]["initContainers"].reverse()
                for namespace, name in (("other", "storage"), ("kubescape", "other")):
                    decoy = certgen_workload()
                    decoy["metadata"].update(namespace=namespace, name=name)
                    raw.append(decoy)
                work = Path(directory)
                (work / "raw.yaml").write_text(yaml.safe_dump_all(raw))
                (work / "kustomization.yaml").write_text(yaml.safe_dump({
                    "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
                    "resources": ["raw.yaml"], **renderer["kustomize"]}))
                effective = CHECK.documents(subprocess.check_output(["kustomize", "build", directory], text=True))
                storage = next(r for r in effective if r["kind"] == "Deployment"
                               and r["metadata"] == raw[0]["metadata"])
                before = {c["name"]: c for c in CHECK.pod_template(raw[0])["spec"]["initContainers"]}
                after = CHECK.pod_template(storage)["spec"]["initContainers"]
                self.assertEqual({c["name"] for c in after}, CHECK.CERTGEN_NAMES)
                for container in after:
                    self.assertEqual(container.pop("resources"), CHECK.CERTGEN_BUDGET)
                    self.assertEqual(container, before[container["name"]])
                # Undo only the allowed edits, then compare every resource and field.
                scheduler = next(r for r in effective if r["kind"] == "CronJob")
                self.assertIs(scheduler["spec"].pop("suspend"), True)
                CHECK.pod_template(storage)["spec"]["initContainers"].sort(key=lambda c: c["name"])
                CHECK.pod_template(raw[0])["spec"]["initContainers"].sort(key=lambda c: c["name"])
                identity = lambda r: (r["kind"], r["metadata"].get("namespace", ""), r["metadata"]["name"])
                self.assertEqual(sorted(effective, key=identity), sorted(raw, key=identity))


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
        self.assertEqual(CHECK.findings([certgen_workload()]), [
            f"Deployment/storage initContainers/{name}: missing CPU/memory requests/limits"
            for name in sorted(CHECK.CERTGEN_NAMES)])

    def test_neither_preflight_mode_permits_an_unsuspended_release(self):
        release = yaml.safe_load((ROOT / "infra/kubescape/helmrelease.yaml").read_text())
        for mode in (False, True):
            for value in (False, None):
                with self.subTest(staged_review=mode, suspend=value):
                    release["spec"]["suspend"] = value
                    with mock.patch.object(CHECK.yaml, "safe_load", return_value=release):
                        with self.assertRaisesRegex(AssertionError, "remain suspended"):
                            CHECK.run(SimpleNamespace(staged_review=mode))

    def test_admission_rejects_failures_errors_skips_and_empty_evaluations(self):
        passing = {"apiVersion": "openreports.io/v1alpha1", "summary": {
            "pass": 27, "fail": 0, "error": 0, "skip": 0, "warn": 0},
            "results": [{"policy": "platform-pod-standards", "rule": "require-requests-and-limits",
                         "result": "pass", "resources": [{"kind": "Deployment", "name": "storage"}]}] * 27}
        invalid = []
        for result in ("fail", "error", "skip", "warn"):
            report = copy.deepcopy(passing)
            report["summary"].update({"pass": 26, result: 1})
            report["results"][0]["result"] = result
            invalid.append((report, int(result in {"fail", "error"})))
        empty = copy.deepcopy(passing)
        empty["summary"]["pass"] = 0
        empty["results"] = []
        invalid.extend(((empty, 0), (passing, 1)))
        for report, returncode in invalid:
            with self.subTest(summary=report["summary"], returncode=returncode):
                evaluated = SimpleNamespace(stdout=yaml.safe_dump(report), returncode=returncode)
                with tempfile.TemporaryDirectory() as directory:
                    with mock.patch.object(CHECK.subprocess, "check_output", return_value="Version: 1.19.2\n"), \
                         mock.patch.object(CHECK.subprocess, "run", return_value=evaluated):
                        with self.assertRaises(AssertionError):
                            CHECK.check_admission(SimpleNamespace(kyverno="kyverno"), Path(directory), [])

    def test_unsuspended_scheduler_fails(self):
        workload = safe_workload()
        workload["kind"] = "CronJob"
        workload["metadata"]["name"] = "kubescape-scheduler"
        workload["spec"] = {"schedule": "0 8 * * *", "jobTemplate": {"spec": workload["spec"]}}
        self.assertTrue(any("scheduled scanning is active" in f for f in CHECK.findings([workload])))


if __name__ == "__main__":
    unittest.main()
