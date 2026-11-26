"""Safety regressions for suspended and explicitly active Kubescape modes."""

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
        if resource["kind"] == "ClusterRole" and resource["metadata"]["name"] == "storage-certgen-kubescape":
            resource["rules"][0]["resourceNames"] = [CHECK.APISERVICE]
    return resources


class LeafCiphertextTests(unittest.TestCase):
    def test_only_leaf_and_sops_bookkeeping_can_change(self):
        before = {"data": {"tls.crt": "ENC[AES256_GCM,old]",
                           "tls.key": "ENC[AES256_GCM,key]"},
                  "metadata": {"name": "security-receiver-tls"},
                  "sops": {"age": ["recipient"], "mac": "old"}}
        after = copy.deepcopy(before)
        after["data"]["tls.crt"] = "ENC[AES256_GCM,new]"
        after["sops"]["mac"] = "new"
        CHECK.check_leaf_ciphertext(before, after)
        for path, value in ((("data", "tls.crt"), before["data"]["tls.crt"]),
                            (("data", "tls.key"), "ENC[AES256_GCM,other]"),
                            (("data", "tls.crt"), "plaintext"),
                            (("metadata", "name"), "other"),
                            (("sops", "age"), ["other"])):
            with self.subTest(path=path, value=value):
                changed = copy.deepcopy(after)
                changed[path[0]][path[1]] = value
                with self.assertRaises(AssertionError):
                    CHECK.check_leaf_ciphertext(before, changed)


class StagedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.release = yaml.safe_load((ROOT / "infra/kubescape/helmrelease.yaml").read_text())
        self.release["spec"]["suspend"] = True

    def test_suspended_stage_is_valid(self):
        CHECK.check_release(self.release)
        self.assertTrue(self.release["spec"]["suspend"])

    def test_unsuspending_fails(self):
        self.release["spec"]["suspend"] = False
        with self.assertRaisesRegex(AssertionError, "explicit review mode"):
            CHECK.check_release(self.release)

    def test_active_mode_requires_explicit_unsuspension_and_health_waiting(self):
        child = yaml.safe_load((ROOT / "clusters/home/infrastructure/kubescape-kustomization.yaml").read_text())
        self.release["spec"]["suspend"] = False
        CHECK.check_release(self.release, active=True)
        CHECK.check_child(child, active=True)
        for value in (True, None):
            self.release["spec"]["suspend"] = value
            with self.assertRaises(AssertionError):
                CHECK.check_release(self.release, active=True)
        for value in (False, None):
            child["spec"]["wait"] = value
            with self.assertRaisesRegex(AssertionError, "health waiting"):
                CHECK.check_child(child, active=True)

    def test_canary_scope_framework_and_request_are_fixed(self):
        for key, value in (("includeNamespaces", ""), ("includeNamespaces", "default"),
                           ("defaultFrameworks", ["all"]),
                           ("kubescapeScheduler", {"requestBody": {"commands": []}})):
            release = copy.deepcopy(self.release)
            release["spec"]["values"][key] = value
            with self.assertRaises(AssertionError):
                CHECK.check_release(release)

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
        self.assertTrue(child["wait"])
        self.assertEqual(child["dependsOn"], [{"name": "infrastructure"}, {"name": "kyverno-policies"}])
        policy_child_path = "infrastructure/kyverno-policies-kustomization.yaml"
        self.assertEqual(cluster["resources"].count(policy_child_path), 1)
        policy_child = yaml.safe_load((ROOT / "clusters/home" / policy_child_path).read_text())["spec"]
        self.assertIn({"apiVersion": "policies.kyverno.io/v1", "kind": "ValidatingPolicy",
                       "name": "kubescape-runtime-boundary"}, policy_child["healthChecks"])
        infrastructure = yaml.safe_load((ROOT / "clusters/home/infrastructure/kustomization.yaml").read_text())
        self.assertNotIn("../../../infra/kubescape", infrastructure["resources"])
        source = yaml.safe_load((ROOT / "infra/kubescape/kustomization.yaml").read_text())
        baseline = {"namespace.yaml", "helmrepository.yaml", "helmrelease.yaml",
                    "recurring/rbac.yaml", "recurring/cronjobs.yaml"}
        # The two encrypted receiver TLS/CA Secrets are intentionally owned
        # by this namespace's SOPS-enabled Flux Kustomization. Keep all other
        # resource additions fail-closed until independently reviewed.
        secret_kustomization = yaml.safe_load((ROOT / "secrets/kustomization.yaml").read_text())
        self.assertEqual(set(secret_kustomization["resources"]),
                         {"security-receiver-ca.enc.yaml","security-receiver-tls.enc.yaml"})
        for filename in secret_kustomization["resources"]:
            secret = yaml.safe_load((ROOT / "secrets" / filename).read_text())
            self.assertEqual(secret["kind"], "Secret")
            self.assertEqual(secret["metadata"]["namespace"], "kubescape")
            self.assertEqual(secret["sops"]["encrypted_regex"], "^(data|stringData)$")
            self.assertTrue(all(str(x).startswith("ENC[AES256_GCM,")
                                for x in secret["data"].values()))
        self.assertEqual(child["decryption"], {"provider":"sops","secretRef":{"name":"sops-age"}})
        # Explicitly reviewed, opt-in disposable R1000 fixture only.
        trigger = "../../ops/security-collector/acceptance/trigger"
        resources = set(source["resources"])
        self.assertEqual(len(resources), len(source["resources"]), "duplicate Kubescape resource include")
        receiver = "receiver"
        self.assertEqual(resources, baseline | {"../../secrets",receiver})
        self.assertNotIn(trigger, resources)
        self.assertNotIn("collector", resources)
        self.assertNotIn("collector-retained-state.yaml", resources)
        namespace = yaml.safe_load((ROOT / "infra/kubescape/namespace.yaml").read_text())
        labels = namespace["metadata"]["labels"]
        self.assertEqual(labels["pod-security.kubernetes.io/enforce"], "privileged")
        self.assertEqual(labels["pod-security.kubernetes.io/audit"], "restricted")
        policy_source = (ROOT / "infra/kyverno-policies/kustomization.yaml").read_text()
        self.assertIn("../kubescape/runtime/admission.yaml", policy_source)
        self.assertTrue(labels["homelab.cooked.beer/owner"])

    def test_scheduler_has_explicit_flux_postrender_suspension(self):
        renderer, = self.release["spec"]["postRenderers"]
        patches = [p for p in renderer["kustomize"]["patches"] if p["target"]["kind"] == "CronJob"]
        self.assertEqual({p["target"]["name"] for p in patches}, {"kubescape-scheduler", "kubevuln-scheduler"})
        for patch in patches:
            self.assertEqual(yaml.safe_load(patch["patch"]), [{"op": "add", "path": "/spec/suspend", "value": True}])

    def test_manual_image_routing_disables_operator_watchers(self):
        renderer, = self.release["spec"]["postRenderers"]
        patch, = [p for p in renderer["kustomize"]["patches"]
                  if p["target"]["kind"] == "ConfigMap"
                  and p["target"]["name"] == "ks-capabilities"]
        self.assertEqual(patch["target"]["name"], "ks-capabilities")
        data = json.loads(yaml.safe_load(patch["patch"])[0]["value"])
        for key in ("kubevuln", "kubevulnScheduler", "sbomScanner", "synchronizer"):
            self.assertIs(data["components"][key]["enabled"], False)
        self.assertIs(data["components"]["kubescape"]["enabled"], True)
        self.assertIs(data["components"]["nodeAgent"]["enabled"], True)

    def test_database_and_scan_bounds_are_required(self):
        for path, value in [(("grypeOfflineDB", "image", "tag"), "v6-latest"),
                            (("grypeOfflineDB", "image", "sha"), ""),
                            (("kubevuln", "sbomScanner", "enabled"), True),
                            (("kubevuln", "config", "grypeDbPersistence"), False),
                            (("kubevuln", "config", "maxImageSize"), 5368709120),
                            (("persistence", "size", "kubevuln"), "5Gi")]:
            with self.subTest(path=path):
                changed = copy.deepcopy(self.release)
                target = changed["spec"]["values"]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(AssertionError):
                    CHECK.check_release(changed)

    def test_scanner_only_image_limit_is_exact_and_cannot_be_unbounded(self):
        for invalid in ("1610612736", "0", "3221225472"):
            changed = copy.deepcopy(self.release)
            env = changed["spec"]["values"]["kubevuln"]["env"]
            next(e for e in env if e["name"] == "MAXIMAGESIZE")["value"] = invalid
            with self.subTest(value=invalid), self.assertRaises(AssertionError):
                CHECK.check_release(changed)

    def test_scanner_resource_budget_is_exact(self):
        for side, unit, invalid in (("requests", "ephemeral-storage", "1Gi"),
                                    ("limits", "ephemeral-storage", "5Gi"),
                                    ("limits", "memory", "4Gi"),
                                    ("limits", "cpu", "2")):
            changed = copy.deepcopy(self.release)
            changed["spec"]["values"]["kubevuln"]["resources"][side][unit] = invalid
            with self.subTest(side=side, unit=unit, value=invalid), self.assertRaises(AssertionError):
                CHECK.check_release(changed)

    def test_scope_guard_still_applies_to_kubescape_infrastructure(self):
        self.assertTrue(CHECK.scope_guard_required(["infra/kubescape/helmrelease.yaml", "infra/kyverno/helmrelease.yaml"]))
        self.assertFalse(CHECK.scope_guard_required(["scripts/ci/check_kubescape.py", "infra/kyverno/helmrelease.yaml"]))

    def test_mismatched_cli_is_rejected_before_admission_checks(self):
        with mock.patch.object(CHECK.subprocess, "check_output", return_value="Version: 1.19.2\n"):
            with self.assertRaisesRegex(AssertionError, "verified proposed release"):
                CHECK.check_admission(SimpleNamespace(kyverno="kyverno", expected_kyverno_version="1.20.0"), Path("unused"), [])

    def test_scoped_gatus_endpoint_preserves_all_other_settings(self):
        after = yaml.safe_load((ROOT / "infra/observability/gatus-helmrelease.yaml").read_text())
        before = copy.deepcopy(after)
        endpoints = before["spec"]["values"]["config"]["endpoints"]
        endpoints[:] = [e for e in endpoints if e["name"] != "kubescape-assessments"
                        and not e['name'].startswith('security-collector-')]
        CHECK.check_assessment_alert(before, after)
        # The legacy endpoints may be removed but may never become public alerts.
        self.assertFalse(any(e["name"].startswith("security-collector-")
                             for e in after["spec"]["values"]["config"]["endpoints"]))
        legacy = {"name":"security-collector-process","group":"cluster",
                  "url":"http://security-collector.kubescape.svc.cluster.local:8080/readyz",
                  "interval":"1m","conditions":["[STATUS] == 200","[BODY].ready == true"],"alerts":[]}
        endpoints.append(legacy)
        CHECK.check_assessment_alert(before, after)
        legacy["alerts"] = [{"type":"github"}]
        with self.assertRaisesRegex(AssertionError, "assessment/collector alert differs"):
            CHECK.check_assessment_alert(before, after)
        endpoints.remove(legacy)
        next(e for e in after["spec"]["values"]["config"]["endpoints"] if e['name'] == 'gitea')['interval'] = '20m'
        with self.assertRaisesRegex(AssertionError, "outside the assessment/collector endpoints"):
            CHECK.check_assessment_alert(before, after)

    def test_scope_guard_rejects_kyverno_gatus_or_cilium_changes(self):
        protected = ["infra/kyverno/helmrelease.yaml", "infra/kyverno-policies/example.yaml",
                     "infra/observability/gatus-configmap.yaml", "infra/cilium/helmrelease.yaml"]
        self.assertEqual(CHECK.check_scope(protected + ["infra/kubescape/helmrelease.yaml"]), protected)

    def test_recorded_inventory_has_complete_certgen_budgets_and_no_blockers(self):
        inventory = json.loads((ROOT / "infra/kubescape/rendered-inventory.json").read_text())
        self.assertEqual(inventory["admissionBlockers"], [])
        self.assertEqual(inventory["certificateInitBudgets"], {
            name: CHECK.CERTGEN_BUDGET for name in CHECK.CERTGEN_NAMES})
        self.assertEqual(sum(inventory["counts"].values()), 72)
        self.assertEqual(inventory["counts"]["Deployment"], 5)
        self.assertEqual(inventory["counts"]["APIService"], 1)
        self.assertEqual(inventory["counts"]["DaemonSet"], 1)


class PostRenderTests(unittest.TestCase):
    def test_image_routing_schedulers_and_data_retention_are_exact(self):
        raw = [certgen_workload(), scheduler_workload(),
               {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
                "metadata": {"name": "storage-certgen-kubescape"},
                "rules": [{"apiGroups": ["apiregistration.k8s.io"], "resources": ["apiservices"],
                           "verbs": ["get", "patch"]}]}]
        vulnerability_job = scheduler_workload()
        vulnerability_job["metadata"]["name"] = "kubevuln-scheduler"
        raw.append(vulnerability_job)
        raw.append({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
            "name": "ks-capabilities", "namespace": "kubescape"}, "data": {
                "capabilities": json.dumps({"components": {
                    "kubevuln": {"enabled": True}, "kubevulnScheduler": {"enabled": True}}})}})
        for name in ("kubescape-storage", "kubescape-kubevuln"):
            raw.append({"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {
                "name": name, "namespace": "kubescape"}, "spec": {"storageClassName": "longhorn"}})
        raw.append({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {
            "name": "operator", "namespace": "kubescape"}, "rules": [
                {"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get"]},
                {"apiGroups": [""], "resources": ["secrets"],
                 "verbs": ["create", "get", "update", "watch", "list", "patch", "delete"]}]})
        for name in ("kubevuln", "grype-offline-db"):
            workload = safe_workload()
            workload["metadata"]["name"] = name
            workload["spec"]["strategy"] = {"type": "Recreate"}
            raw.append(workload)
        rendered = corrected_resources(raw)
        next(r for r in rendered if r["kind"] == "Role")["rules"].pop(1)
        engine = next(r for r in rendered if r["kind"] == "Deployment" and r["metadata"]["name"] == "kubevuln")
        CHECK.pod_template(engine)["spec"]["initContainers"] = [CHECK.mirror_init("test@sha256:" + "a" * 64)]
        caps = next(r["data"] for r in rendered if r["kind"] == "ConfigMap")
        caps["capabilities"] = json.dumps({"components": {
            "kubevuln": {"enabled": False}, "kubevulnScheduler": {"enabled": False}}}, separators=(",", ":"))
        for pvc in (r for r in rendered if r["kind"] == "PersistentVolumeClaim"):
            pvc["metadata"]["annotations"] = {"helm.sh/resource-policy": "keep"}
        CHECK.check_post_render(raw, rendered)
        for index in range(len(rendered)):
            if (rendered[index]["kind"] not in {"ConfigMap", "CronJob", "PersistentVolumeClaim", "Role"}
                    and rendered[index]["metadata"]["name"] != "kubevuln"):
                continue
            changed = copy.deepcopy(rendered)
            changed[index] = copy.deepcopy(raw[index])
            with self.subTest(resource=raw[index]["metadata"]["name"]):
                with self.assertRaises(AssertionError):
                    CHECK.check_post_render(raw, changed)

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
                certgen = next(r for r in effective if r["kind"] == "ClusterRole")
                self.assertEqual(certgen["rules"][0].pop("resourceNames"), [CHECK.APISERVICE])
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

    def test_suspended_preflight_modes_reject_an_unsuspended_release(self):
        release = yaml.safe_load((ROOT / "infra/kubescape/helmrelease.yaml").read_text())
        for mode in (False, True):
            for value in (False, None):
                with self.subTest(staged_review=mode, suspend=value):
                    release["spec"]["suspend"] = value
                    with mock.patch.object(CHECK.yaml, "safe_load", return_value=release):
                        with self.assertRaisesRegex(AssertionError, "explicit review mode"):
                            CHECK.run(SimpleNamespace(staged_review=mode, active_review=False))

    def test_admission_rejects_failures_errors_skips_and_empty_evaluations(self):
        passing = {"apiVersion": "openreports.io/v1alpha1", "summary": {
            "pass": 46, "fail": 0, "error": 0, "skip": 0, "warn": 0},
            "results": [{"policy": "platform-pod-standards", "rule": "require-requests-and-limits",
                         "result": "pass", "resources": [{"kind": "Deployment", "name": "storage"}]}] * 46}
        invalid = []
        for result in ("fail", "error", "skip", "warn"):
            report = copy.deepcopy(passing)
            report["summary"].update({"pass": 45, result: 1})
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
                            CHECK.check_admission(SimpleNamespace(kyverno="kyverno", expected_kyverno_version="1.19.2"), Path(directory), [])

    def test_cel_admission_requires_its_complete_report(self):
        for count in [28, 53, 54]:
            report = {"apiVersion": "openreports.io/v1alpha1", "summary": {
                "pass": count, "fail": 0, "error": 0, "skip": 1, "warn": 0},
                "results": [{"policy": "platform-pod-standards-require-owner-label", "rule": "validation",
                             "result": "pass", "resources": [{"kind": "Deployment", "name": "storage"}]}] * count}
            report['results'].append({'policy': 'platform-pod-standards-require-runtime-hardening',
                'result': 'skip', 'resources': [{'apiVersion': 'apps/v1', 'kind': 'DaemonSet',
                'name': 'node-agent', 'namespace': 'kubescape'}]})
            evaluated = SimpleNamespace(stdout=yaml.safe_dump(report), returncode=0)
            with tempfile.TemporaryDirectory() as directory, \
                 mock.patch.object(CHECK.subprocess, "check_output", return_value="Version: v1.20.0\n"), \
                 mock.patch.object(CHECK.subprocess, "run", return_value=evaluated):
                args = SimpleNamespace(kyverno="kyverno", expected_kyverno_version="1.20.0")
                if count == 54:
                    CHECK.check_admission(args, Path(directory), [])
                else:
                    with self.assertRaises(AssertionError):
                        CHECK.check_admission(args, Path(directory), [])

    def test_unsuspended_scheduler_fails(self):
        workload = safe_workload()
        workload["kind"] = "CronJob"
        workload["metadata"]["name"] = "kubescape-scheduler"
        workload["spec"] = {"schedule": "0 8 * * *", "jobTemplate": {"spec": workload["spec"]}}
        self.assertTrue(any("scheduled scanning is active" in f for f in CHECK.findings([workload])))


if __name__ == "__main__":
    unittest.main()
