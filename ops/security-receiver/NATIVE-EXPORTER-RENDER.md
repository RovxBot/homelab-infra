# Kubescape native exporter integration — chart-render qualification

**Status:** two-node GitOps activation candidate, **draft and unmerged**.
This PR now modifies our HelmRelease post-render, the documented chart CA
setting, and the narrowly scoped existing node-agent CEL policy. It does
**not** modify upstream Kubescape, Kyverno, Gatus, Renovate or Flux binaries.
Until this PR is approved and merged, the live node-agent and working receiver
remain unchanged. CI evidence must pass before the operator activates it.

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
It renders the chart with the source-controlled HelmRelease values; Helm's offline\n`lookup` cannot see the existing live CA Secret, so the actual CA mount also\nrequires live rollout verification. The exporter setting is byte-exactly\nvalidated on the pinned chart.

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

It generates a local/CI JSON6902 post-render patch whose first operation is an
exact `test` of the original chart-provided string and whose second operation
replaces that string with the original bytes plus one documented HTTP exporter
field. The **same patch is now checked into our HelmRelease**, and CI requires
the generated and checked-in patches to be identical. The PR remains unmerged.
A chart upgrade must fail the baseline check rather than silently drift.

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

1. **Implemented in this PR:** exact native ConfigMap postRenderer patch and
   pinned-chart generation proof. Require CI and reviewer approval before merge.
2. **Configured in this PR:** chart-documented `global.extraCaCertificates`
   pointing at the existing SOPS-protected `security-receiver-ca` Secret.
   **Not yet accepted live:** confirm Helm's API `lookup` mounts exactly
   `receiver-ca.crt` and the official image's Go HTTPS client trusts it.
   Keep other root CAs working; never use insecure TLS or plaintext bearer.
3. **Configured with CI fixtures in this PR:** the node-agent Kyverno/CEL
   exception permits only the one public CA Secret volume/mount and rejects
   altered Secret names, writable mounts and alternate paths. Verify with
   the pinned Kyverno 1.20.0 CLI and then with actual live admission.
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

## Operator gate after review/merge

The existing receiver has already returned `receiver_ready: true` with
TLS hostname validation and a trusted operator-held CA certificate. This does
**not** prove exporter delivery.

Once **all** CI checks pass and this PR is manually merged, verify on both
metal3/metal6 that (1) the node-agent Pod is running/Ready and uses the same
pinned upstream image, (2) Helm populated the *official* `extra-ca-certificates`
Secret mount at `/etc/ssl/certs/receiver-ca.crt` as read-only, (3) the
`kubescape-api-token` volume is a rotating `kubescape.io` audience token,
(4) the ConfigMap contains exactly one `httpExporterConfig` entry and
`stdoutExporter:true`, (5) the receiver and old collector remain Ready,
and (6) no Kyverno admission errors or unsafe permissions appeared. If any
condition fails, revert **this exporter change only** and do not test by
disabling TLS/TokenReview.

Only after those checks should the operator generate a new harmless R1000.
Verify a native authenticated HTTP POST produced one private SQLite receipt
and logical incident; verify retry/duplicate and receiver restart persistence
before considering the eight-node rollout or old collector cleanup.
