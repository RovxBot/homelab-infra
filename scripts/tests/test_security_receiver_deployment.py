"""Fail-closed checks for the staged receiver's GitOps and RBAC boundaries."""
import json
import subprocess
import tempfile
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
        self.assertIn("receiver",infra["resources"])
        self.assertIn("../../ops/security-collector/acceptance/trigger", infra["resources"])
        self.assertIn("../../secrets",infra["resources"])
        secret_kustom=yaml.safe_load((ROOT/"secrets/kustomization.yaml").read_text())
        self.assertEqual(set(secret_kustom["resources"]),
                         {"security-receiver-ca.enc.yaml","security-receiver-tls.enc.yaml"})
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
        # This must be a single Pod-template rotation, not a PVC/Service
        # mutation or an image/rbac change.
        self.assertEqual(deploy["spec"]["replicas"],1)
        self.assertEqual(deploy["spec"]["strategy"],{"type":"Recreate"})
        self.assertEqual(deploy["spec"]["template"]["metadata"]["annotations"],
                         {"homelab.cooked.beer/persistence-acceptance":
                          "r1000-receipt-20261124-01",
                          "homelab.cooked.beer/tls-leaf-reissue":
                          "strict-x509-aki-20261124-01"})
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
            "ghcr.io/rovxbot/security-receiver@sha256:68d32d0f951f5ee50ace2ea5789b70296ce9ecca965a1b2c7890aed877ea4b32")
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

    def test_strict_x509_rotation_preserves_ca_and_private_key(self):
        bootstrap=(ROOT/"ops/security-receiver/generate-tls-secrets.sh").read_text()
        reissue=(ROOT/"ops/security-receiver/reissue-leaf-strict-x509.sh").read_text()
        for source in (bootstrap,reissue):
            self.assertIn("authorityKeyIdentifier=keyid:always,issuer",source)
            self.assertIn("subjectKeyIdentifier=hash",source)
            self.assertIn("openssl verify -x509_strict",source)
            self.assertIn("security-receiver.kubescape.svc.cluster.local",source)
            self.assertNotIn("insecureSkipVerify",source)
        self.assertIn('sops set "$TMP/candidate.enc.yaml"',reissue)
        self.assertIn("'[\"data\"][\"tls.crt\"]'",reissue)
        self.assertIn('openssl pkey -in "$OUT/tls.key" -pubout',reissue)
        self.assertNotIn('kubectl apply',reissue)
        self.assertNotIn('kubectl delete',reissue)

    def test_generated_leaf_passes_real_strict_openssl_verification(self):
        for script in ("generate-tls-secrets.sh", "reissue-leaf-strict-x509.sh"):
            source = (ROOT / "ops/security-receiver" / script).read_text()
            extensions = source.split("<<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
            with self.subTest(script=script), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                (work / "leaf.ext").write_text(extensions + "\n")
                def run(*args):
                    return subprocess.run(["openssl", *args], cwd=work,
                                          capture_output=True, check=True)
                run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-subj", "/CN=Test Root", "-keyout", "root.key", "-out", "root.crt",
                    "-addext", "basicConstraints=critical,CA:TRUE",
                    "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                    "-addext", "subjectKeyIdentifier=hash")
                run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj",
                    "/CN=security-receiver.kubescape.svc", "-keyout", "leaf.key", "-out", "leaf.csr")
                run("x509", "-req", "-in", "leaf.csr", "-CA", "root.crt", "-CAkey",
                    "root.key", "-set_serial", "1", "-days", "1", "-extfile", "leaf.ext",
                    "-out", "leaf.crt")
                for host in ("security-receiver.kubescape.svc",
                             "security-receiver.kubescape.svc.cluster.local"):
                    run("verify", "-x509_strict", "-purpose", "sslserver",
                        "-verify_hostname", host, "-CAfile", "root.crt", "leaf.crt")
                with self.assertRaises(subprocess.CalledProcessError):
                    run("verify", "-x509_strict", "-purpose", "sslserver",
                        "-verify_hostname", "wrong.example", "-CAfile", "root.crt", "leaf.crt")

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
        koito=next(x for x in roles if x["metadata"].get("namespace")=="koito")
        self.assertEqual(koito["rules"],[
            {"apiGroups":[""],"resources":["pods"],"verbs":["get"]},
            {"apiGroups":["apps"],"resources":["replicasets"],"verbs":["get"]},
            {"apiGroups":["apps"],"resources":["deployments"],"resourceNames":["koito","koito-lastfm-bridge"],"verbs":["get"]}])
        bindings=[x for x in items if x["kind"].endswith("Binding")]
        self.assertEqual(len(bindings),13)
        for namespace, names in {"monitoring":["gatus"],"openbrew":["openbrew","openbrew-db"],"stirling-pdf":["stirling-pdf"],"cloudflared":["cloudflared"],"mqtt":["mosquitto"],"security":["vaultwarden"]}.items():
            role=next(x for x in roles if x['metadata'].get('namespace')==namespace and x['metadata']['name']=='security-receiver-inventory')
            self.assertEqual(role['rules'], [
                {'apiGroups':[''],'resources':['pods'],'verbs':['get']},
                {'apiGroups':['apps'],'resources':['replicasets'],'verbs':['get']},
                {'apiGroups':['apps'],'resources':['deployments'],'resourceNames':names,'verbs':['get']}])
        for binding in bindings:
            self.assertEqual(binding["subjects"],[{"kind":"ServiceAccount",
                                                    "name":"security-receiver",
                                                    "namespace":"kubescape"}])

    def test_exact_eight_agent_nodes_and_no_unsafe_surface(self):
        config=json.loads((RECEIVER/"config.json").read_text())
        self.assertEqual(config["nodes"],["metal0","metal2","metal3","metal4","metal5","metal6","metal7","metal8"])
        self.assertEqual(config["namespaces"],["homepage","memos","kubescape-runtime-test","koito","monitoring","openbrew","stirling-pdf","cloudflared","mqtt","security"])
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
