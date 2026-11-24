# Kubescape native exporter integration — chart-render qualification

**Status:** evidence-first draft. All files in this PR are non-deploying scripts,
synthetic tests, a CI workflow, and this contract. The live node-agent, chart,
receiver, HelmRelease, Kyverno and Gatus remain unchanged.

## Why this gate is required

Confirmed operator chart **1.42.0** renders `exporters.httpExporterConfig`
only if `components.synchronizer.enabled` is true, although the official
Kubescape **v0.3.310** node-agent exposes HTTP delivery independently.
We deliberately do **not** enable Synchronizer just to expose exporter config,
use the `HTTP_ENDPOINT_URL` unauthenticated fallback, fork the official
agent/chart, inject invalid duplicate JSON keys, or change Cilium enforcement.

Official vendor documentation explicitly instructs configuring the
`node-agent` ConfigMap's `config.json` `exporters.httpExporterConfig`.
Official Flux documents `HelmRelease.spec.postRenderers`. Combining these
*documented mechanisms* requires a carefully audited source contract, since
Helm renders `config.json` as a single string field.

## What this PR proves

`scripts/ci/security_native_exporter_preflight.py` downloads the **exact**
Kubescape chart 1.42.0 release archive and compares the existing pinned
SHA256 `be45f83398f28db8f2ff395e5d8ab411328041e9e2165a42e5fb92a4ba369079`.
It renders the chart with the unchanged source-controlled HelmRelease values.

It fails closed unless:

- Chart's node-agent ConfigMap is unique and its `config.json` has no
  duplicate JSON keys and **no existing HTTP exporter**.
- Runtime detection is enabled, original stdout exporter remains enabled,
  Syslog/Alertmanager outputs are unchanged, and Synchronizer is disabled.
- The pinned DaemonSet declares the `node-agent` ServiceAccount and the
  documented projected rotating SA token with audience `kubescape.io`.
- The token is mounted at `/var/run/secrets/kubescape.io/api-token/token`.
- Candidate JSON differs from the official config only by adding
  `exporters.httpExporterConfig`; no other upstream fields are deleted,
  reordered semantically, or overwritten.

It generates only a local/CI **candidate** JSON6902 post-render patch whose
first operation is an exact `test` of the original chart-provided string
and whose second operation replaces that string with the original settings
plus the vendor-documented native HTTP exporter. This is a tightly gated
candidate, not an applied patch or merge approval. A chart upgrade must
fail the baseline check rather than silently drift.

The exporter candidate is:
```yaml
url: https://security-receiver.kubescape.svc:8443
method: POST
tokenFile: /var/run/secrets/kubescape.io/api-token/token
timeoutSeconds: 8
maxAlertsPerMinute: 30
enableAlertBulking: false
```

`path` is intentionally omitted so v0.3.310 uses its official default
`/v1/runtimealerts`. Token auth uses the chart-projected Pod-bound
ServiceAccount token, never a password or untracked static credential.
The proposed receiver remains ClusterIP-only.

The `Security Native Kubescape Exporter Render Proof` workflow publishes
the candidate and baseline ConfigMap (no secrets or TLS key) as a short-lived
GitHub Actions artifact, after six source-only regression tests.

## Remaining mandatory live gates — NOT claimed by this PR

1. Independently inspect the rendered candidate and chart metadata, apply
   the permitted post-render config as a **separate reviewed commit on this
   same PR**, then prove final exact `helm template`/Kustomize output.
2. Enable only chart-documented `global.extraCaCertificates` pointing at
   the existing SOPS-protected `security-receiver-ca` Secret and validate
   the chart's API `lookup` semantics; the official image's Go trust must
   accept the certificate at runtime. Keep other root CAs working. Never
   set `insecureSkipVerify` or use plaintext HTTP with a bearer.
3. Extend the **exact** node-agent Kyverno/CEL admission exception only for
   the one public CA Secret volume/mount. Preserve host, security context,
   token, and image restrictions. Test with pinned Kyverno 1.20.0 CLI.
4. Verify safe node-agent restart **only metal3 and metal6**, receiver TLS
   handshake and TokenReview; no live HTTP event from this draft yet.
5. Send one **new** harmless R1000, confirm received native HTTP POST returns
   200 only after SQLite commit, one private incident and receipt, duplicate
   idempotency and restart persistence. Monitor export retry/drop and Gatus
   coverage; don't claim native delivery is lossless.
6. Only after that, separately expand to eight nodes and qualify redacted
   GitHub incidents. Retire old collector last; preserve its PVC/SQLite.

## Official contracts

- Kubescape [configure the node-agent ConfigMap HTTP exporter](https://kubescape.io/docs/operator/runtime-threat-routing-to-graylog/).
- Kubescape [node-agent v0.3.310 native HTTP exporter](https://github.com/kubescape/node-agent/blob/v0.3.310/pkg/exporters/http_exporter.go) and [token-file HTTPS client](https://github.com/kubescape/node-agent/blob/v0.3.310/pkg/exporters/http_client.go).
- Kubescape [operator chart 1.42.0 native ConfigMap gate](https://github.com/kubescape/helm-charts/blob/kubescape-operator-1.42.0/charts/kubescape-operator/templates/node-agent/configmap.yaml) and [projected TokenReview audience](https://github.com/kubescape/helm-charts/blob/kubescape-operator-1.42.0/charts/kubescape-operator/templates/_apiauth.tpl).
- Flux [HelmRelease post-renderers](https://fluxcd.io/flux/components/helm/helmreleases/).

This is strictly integration through **documented upstream inputs**, not
modification of Kubescape, Kyverno, Gatus, Renovate or Flux binaries.
