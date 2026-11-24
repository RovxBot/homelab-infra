# Security receiver staging and cutover — operator runbook

**State: staging only, not an active runtime receiver.** No exporter configuration, node-agent, Kyverno/PSA, Gatus, Renovate, Cilium, scanner or existing collector behavior is changed by this stage.

The validated native receiver code was merged in #2212 and #2213. This staging layer adds reproducible image creation, least-privilege RBAC and an off-by-default HTTPS/SQLite deployment manifest.

## Exact deployment boundary

- Source: official Kubescape node-agent **v0.3.310**, operator chart **1.42.0**, no fork or internal API changes.
- Kubescape's documented native HTTP exporter emits `RuntimeAlerts` JSON, not stdout log lines.
- Only two agent nodes at first: **metal3** and **metal6**. Expand directly to all 8 **after** two-node end-to-end acceptance.
- Receiver service: `https://security-receiver.kubescape.svc:8443/v1/runtimealerts`, internal ClusterIP. Never public.
- Receiver API identity: projected Kubernetes API-audience SA token; `create tokenreviews` for sender identity, read-only Pod/ReplicaSet/Deployment lookups. No Secrets API, Pod logs, nodes/proxy or Kubernetes writes.
- Sender identity: chart's projected **kubescape.io** Pod-bound node-agent token read via the supported `tokenFile`; source validated with Kubernetes TokenReview. Do not use unbound static tokens.
- TLS: private receiver leaf certificate and key in `security-receiver-tls`; CA *public certificate only* in `security-receiver-ca`. Node-agent trust uses chart's **documented `global.extraCaCertificates`** support. Confirm trust with a real HTTPS export before any rollout. Do not use insecure TLS or HTTP bearer transmission.
- State: new `security-receiver-state` Longhorn PVC (256Mi), separate from old unpruned `security-collector-state` PVC. Old SQLite is neither mounted nor altered.

## Stage A — this PR (safe to review while offline)

Files:
- `ops/security-receiver/build-image.py`: isolated deterministic stdlib Python image using the same reviewed pinned Python base and checksum-pinned crane as existing collector build.
- `.github/workflows/security-receiver-image.yml`: unit/contract tests; non-root/read-only/no-network smoke, digest proof, GHCR push and anonymous digest retrieval. **The first GHCR package may need an operator to set package visibility public.** This is distinct from publishing any private security evidence.
- `infra/kubescape/receiver/{config.json,workload.yaml,rbac.yaml,kustomization.yaml}`: native receiver staged behind no active Flux reference.
- `ops/security-receiver/generate-tls-secrets.sh`: local one-time keypair bootstrap and SOPS-encrypted Secrets. NO cluster writes.
- `scripts/tests/test_security_receiver_deployment.py`: confirms no accidental activation, narrow RBAC, TLS-only service, dedicated PVC and encrypted Secrets.

The staged receiver image `ghcr.io/rovxbot/security-receiver:staging-not-deployable` is **deliberately invalid as a release**. Do not add `receiver` to `infra/kubescape/kustomization.yaml` until this is replaced with a real `@sha256:…` digest from the successful, anonymously retrievable build.

## Stage B — one-time operator TLS bootstrap

**Run locally in the checked-out branch, not in CI or inside Kubernetes:**

```sh
bash ops/security-receiver/generate-tls-secrets.sh
```

Requires local `openssl`, `kubectl` (only `--dry-run=client`) and `sops`. The script creates a separate non-repository `.work/security-receiver-pki/root.key` CA signing key, 10-year root certificate, 825-day server certificate with DNS SANs, and these encrypted GitOps-only manifests:
- `secrets/security-receiver-tls.enc.yaml` — leaf certificate and its **encrypted** private key.
- `secrets/security-receiver-ca.enc.yaml` — **public** CA certificate, packaged as a Secret because chart `global.extraCaCertificates` requires a named Secret.

`.work/` is ignored by Git. **Back up the CA signing key privately; never attach or commit plaintext key material.** Review `sops` metadata and ensure PEMs are not in diffs before committing. Do not run the script twice for rotation.

For GitOps, add these encrypted paths to the **infrastructure Flux Kustomization**'s referenced manifests (`clusters/home/infrastructure/kustomization.yaml`), where SOPS decryption is enabled and precedes Kubescape through its `dependsOn` relationship. Existing cluster namespace `kubescape` is present. Validate fresh-cluster namespace ordering if modifying bootstrap semantics. Install `security-receiver-ca` before a chart Helm render uses its `lookup` function.

## Stage C — prove the documented native HTTP configuration

The pinned Kubescape chart's `node-agent/configmap.yaml` emits `httpExporterConfig` only under `$components.synchronizer.enabled` even though the node-agent's HTTP exporter is supported independently. Do **not** activate Synchronizer just to render HTTP, fork the agent/chart or put duplicate `exporters` keys into JSON.

Only choose a documented configuration method, with a deterministic **exact chart 1.42.0 render** and values/ConfigMap checks. A separately tracked Flux Helm `postRenderers` change that supplies the chart's *documented node-agent ConfigMap exporter configuration* may be considered **only if** it faithfully preserves all chart-generated settings, is drift-free, and passes upgrade regression testing. Otherwise select a natively exposed exporter such as Syslog/Alertmanager and qualify TLS, attribution, schema and authentication without pretending it has HTTP's bearer-token capability.

The pinned node-agent's Go HTTP client uses system roots and supports a rotating projected token file. It has **no explicit arbitrary CA-file flag**. The pinned chart's `global.extraCaCertificates` mounts the CA cert inside `/etc/ssl/certs`; test actual Go trust inside node-agent using the real canary. A mounted CA isn't itself proof that HTTPS succeeds.

For the HTTP path, require `enableAlertBulking=false` for canary: current receiver rejects multi-alert batches atomically; enable supported batching later only if tested. Use rate ceilings consistent with receiver's private receipt store. Never rely on events being lossless on node-agent restart.

## Stage D — activation PR review/acceptance

**Do not merge/activate until all prerequisites are satisfied:**
1. Build workflow is green and GHCR image digest independently verified; replace the staging image with exactly `ghcr.io/rovxbot/security-receiver@sha256:…`. Verify source file hashes match the current PR head.
2. Review encrypted Secrets and certificate DNS SAN/expiry, apply via Flux, check CA mount and non-leaking output.
3. Render the exact Kubescape Helm chart with native HTTP exporter config and verify node-agent exporter URL, CA, projected token mount and existing eBPF/CEL strict security boundary. Update narrowly scoped Kyverno boundary to permit the approved public-CA Secret mount, preserving all existing controls. A chart or admission failure blocks rollout.
4. Add the receiver Kustomization to the Flux-owned Kubescape manifest only after Secrets exist, safe pinned image is available and preflight passes. Keep existing log collector running until native delivery passes.
5. Validate Kubernetes TokenReview with the bound Pod token, wrong-audience/wrong-SA denial, TLS chain rejection and valid trust.
6. Execute a **new** harmless R1000 and verify a native HTTP request, 200 only after SQLite commit, one private incident+receipt, exact replay deduplication and restart persistence. Historical R1000 seen only in stdout is detector evidence, **not** native HTTP acceptance.
7. Validate agent/receiver freshness and Gatus semantics, DB capacity, retry/drop limits, no payload/credential exposure and no public GitHub writes.

Only then create a **separate reviewed 2→8 rollout**. Security event GitHub publication remains its own source-owned, sanitized feature; old log polling/UID pin enrollment and disposable test artifacts are removed after cutover and historical PVC archive.

## Upstream documentation / immutable code review

- [native exporter v0.3.310](https://github.com/kubescape/node-agent/blob/v0.3.310/pkg/exporters/http_exporter.go)
- [native TLS/token client v0.3.310](https://github.com/kubescape/node-agent/blob/v0.3.310/pkg/exporters/http_client.go)
- [chart 1.42.0 node-agent ConfigMap](https://github.com/kubescape/helm-charts/blob/kubescape-operator-1.42.0/charts/kubescape-operator/templates/node-agent/configmap.yaml)
- [chart 1.42.0 node-agent CA mounts](https://github.com/kubescape/helm-charts/blob/kubescape-operator-1.42.0/charts/kubescape-operator/templates/node-agent/_node-agent.tpl)
- [Flux HelmRelease post-renderers](https://fluxcd.io/flux/components/helm/helmreleases/)
- [SOPS secret protection](https://fluxcd.io/flux/guides/mozilla-sops/)

**Rollback:** remove only the receiver from Flux and stop native exporter delivery, preserving old collector, scanner, rules, Kyverno and both PVCs. Do not delete either Secret without confirming no bound consumer; do not delete CA signing key. No automatic incident closure or destructive remediation.
