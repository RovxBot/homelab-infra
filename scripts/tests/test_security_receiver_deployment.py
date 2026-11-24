"""Fail-closed checks for the staged receiver's GitOps and RBAC boundaries."""
import json
from pathlib import Path
import sys
import unittest

import yaml

ROOT=Path(__file__).resolve().parents[2]
RECEIVER=ROOT/"infra/kubescape/receiver"


def documents(path):
    return [x for x in yaml.safe_load_all(path.read_text()) if isinstance(x,dict)]


class ReceiverStageTests(unittest.TestCase):
    def test_staged_not_active_and_original_collector_retained(self):
        infra=yaml.safe_load((ROOT/"infra/kubescape/kustomization.yaml").read_text())
        self.assertIn("collector",infra["resources"])
        self.assertNotIn("receiver",infra["resources"])
        self.assertIn("../../ops/security-collector/acceptance/trigger", infra["resources"])
        self.assertIn("../../secrets/security-receiver-ca.enc.yaml",infra["resources"])
        self.assertIn("../../secrets/security-receiver-tls.enc.yaml",infra["resources"])
        # Kubescape's own source owns Namespace creation and SOPS decryption;
        # don't depend on the infra parent applying a Secret first.
        flux=yaml.safe_load((ROOT/"clusters/home/infrastructure/kubescape-kustomization.yaml").read_text())
        self.assertEqual(flux["spec"]["decryption"],
                         {"provider":"sops","secretRef":{"name":"sops-age"}})
        parent=yaml.safe_load((ROOT/"clusters/home/infrastructure/kustomization.yaml").read_text())
        self.assertFalse(any("security-receiver" in path for path in parent["resources"]))
        manifest=yaml.safe_load((RECEIVER/"kustomization.yaml").read_text())
        self.assertEqual(set(manifest["resources"]),{"rbac.yaml","workload.yaml"})

    def test_tls_nonroot_dedicated_longhorn_and_no_public_ingress(self):
        items=documents(RECEIVER/"workload.yaml")
        obj={x["kind"]:x for x in items}
        deploy=obj["Deployment"]
        spec=deploy["spec"]["template"]["spec"]
        self.assertEqual(spec["serviceAccountName"],"security-receiver")
        self.assertIs(spec["automountServiceAccountToken"],False)
        self.assertTrue(spec["securityContext"]["runAsNonRoot"])
        self.assertEqual(spec["securityContext"]["runAsUser"],10001)
        container=spec["containers"][0]
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"],["ALL"])
        self.assertEqual(container["image"],
            "ghcr.io/rovxbot/security-receiver@sha256:9c92582e2428fa9ae85ae08039dd6dd4b8a1fa82ef347404ffed1e19b7d58f84")
        self.assertEqual(obj["Service"]["spec"]["type"],"ClusterIP")
        self.assertTrue(all(p["httpGet"]["scheme"]=="HTTPS"
                            for p in (container["startupProbe"],container["readinessProbe"],
                                      container["livenessProbe"])))
        volumes={v["name"]:v for v in spec["volumes"]}
        self.assertEqual(volumes["state"]["persistentVolumeClaim"]["claimName"],
                         "security-receiver-state")
        self.assertEqual(volumes["tls"]["secret"]["secretName"],"security-receiver-tls")
        projected=volumes["api"]["projected"]["sources"]
        self.assertTrue(any("serviceAccountToken" in s for s in projected))
        self.assertIsNone(next(x for x in items if x["kind"]=="PersistentVolumeClaim")
                          ["metadata"]["annotations"].get("helm.sh/resource-policy"))
        self.assertEqual(obj["PersistentVolumeClaim"]["metadata"]["annotations"]
                         ["kustomize.toolkit.fluxcd.io/prune"],"disabled")

    def test_tokenreview_and_read_only_inventory_without_secrets_or_logs(self):
        items=documents(RECEIVER/"rbac.yaml")
        roles=[x for x in items if x["kind"] in ("Role","ClusterRole")]
        self.assertEqual(len([x for x in roles if x["kind"]=="ClusterRole"]),1)
        cluster=next(x for x in roles if x["kind"]=="ClusterRole")
        self.assertEqual(cluster["rules"],[{"apiGroups":["authentication.k8s.io"],
                                            "resources":["tokenreviews"],"verbs":["create"]}])
        for role in roles:
            for rule in role["rules"]:
                resources=set(rule["resources"])
                self.assertNotIn("*",resources)
                self.assertTrue(resources.isdisjoint({"secrets","pods/log","nodes/proxy",
                                                       "pods/exec","pods/attach"}))
                self.assertTrue(set(rule["verbs"])<={"get","create"})
                if rule["verbs"]==["create"]:
                    self.assertEqual(role["kind"],"ClusterRole")
        bindings=[x for x in items if x["kind"].endswith("Binding")]
        self.assertEqual(len(bindings),5)
        for binding in bindings:
            self.assertEqual(binding["subjects"],[{"kind":"ServiceAccount",
                                                    "name":"security-receiver",
                                                    "namespace":"kubescape"}])

    def test_exact_two_agent_nodes_and_no_unsafe_surface(self):
        config=json.loads((RECEIVER/"config.json").read_text())
        self.assertEqual(config["nodes"],["metal3","metal6"])
        self.assertEqual(config["namespaces"],["homepage","memos","kubescape-runtime-test"])
        self.assertEqual(config["state_dir"],"/state")
        self.assertEqual(config["tls_cert"],"/tls/tls.crt")
        self.assertEqual(config["tls_key"],"/tls/tls.key")
        build=(ROOT/"ops/security-receiver/build-image.py").read_text()
        self.assertIn("security_receiver.service",build)
        self.assertNotIn("scripts/security_collector",build)
        # TLS bootstrap may be completed in a later PR revision. If present,
        # committed Secrets must be SOPS-encrypted, never plaintext.
        for file in ("security-receiver-tls.enc.yaml","security-receiver-ca.enc.yaml"):
            path=ROOT/"secrets"/file
            if path.exists():
                secret=yaml.safe_load(path.read_text())
                self.assertIn("sops",secret)
                self.assertNotIn("stringData",secret)
                self.assertTrue(all("ENC[" in v for v in secret.get("data",{}).values()))


if __name__=="__main__":
    unittest.main()
