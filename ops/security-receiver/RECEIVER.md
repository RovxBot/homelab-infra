# Native Kubescape event receiver — draft PR 2

This PR implements a **source-only** receiver. It changes no live Deployment,
Secret, RBAC, HelmRelease, service, Flux Kustomization, or CronJob. The existing
Kubescape detector, scanner, and old collector are not touched.

## What is implemented

- TLS-only server on a configurable port (8443) with the **exact upstream**
  `POST /v1/runtimealerts` path.
- The pinned v0.3.310 HTTP envelope, normalized by merged
  `scripts/security_receiver/native.py`, not stdout JSON.
- Every event must carry the agent's **projected Pod-bound service-account
  token**. A real `authentication.k8s.io/v1 TokenReview` requires audience
  `kubescape.io` and username
  `system:serviceaccount:kubescape:node-agent`.
- Pod name and UID are taken from Kubernetes TokenReview user.extra (never
  from the message or arbitrary HTTP headers). The receiver independently
  gets the actual node-agent Pod and DaemonSet, verifies ServiceAccount,
  owner, node, Ready/running state and pinned image.
- The claimed application Pod is independently resolved through a Pod ->
  ReplicaSet -> Deployment controller UID chain, including node and
  container image ID, before calling the normalized-event adapter.
- No Pod-log polling, timestamp cursor, agent Pod-name/UID config enrollment,
  GitHub token, issue writer, automatic closure, Pod delete, secret retrieval
  or runtime enforcement.
- One serial HTTPS server process and one SQLite writer; the transaction
  inserts one allowlisted receipt and creates/updates one source-owned runtime
  incident **before** returning HTTP 200. Replays leave counts unchanged;
  same ID with mutated evidence fails closed; database/rate limit failures do
  not acknowledge.
- Database stores trusted Pod/container identity, a digest comparison result
  and rule/severity, but never raw event JSON, command arguments or process
  tree. The private receipt DB is a **new** file, not the old collector PVC.
- Private health endpoints describe receiver/storage only; no pretend
  node-agent delivery health from a silent event feed.

## Supported upstream interfaces

- [Kubescape node-agent v0.3.310 HTTP exporter](https://github.com/kubescape/node-agent/blob/v0.3.310/pkg/exporters/http_exporter.go):
  `POST` default endpoint `/v1/runtimealerts`, `tokenFile` with
  rotating bearer auth, 2xx handling, configurable retry.
- [node-agent HTTP client v0.3.310](https://github.com/kubescape/node-agent/blob/v0.3.310/pkg/exporters/http_client.go):
  ordinary Go TLS trust; HTTPS downgrade redirects are refused when tokenFile
  is used. **It does not expose a bespoke CA-file switch in this client.**
- [node-agent's exact armoapi-go v0.0.742 model](https://github.com/armosec/armoapi-go/blob/v0.0.742/armotypes/runtimeincidents.go):
  native `RuntimeAlerts` flat lower-camel-case fields.
- [kubescape-operator chart 1.42.0](https://github.com/kubescape/helm-charts/blob/kubescape-operator-1.42.0/charts/kubescape-operator/templates/node-agent/configmap.yaml):
  renders `httpExporterConfig` only while Synchronizer is enabled, but
  the node-agent config format itself is upstream documented.
- The same chart defaults to `global.apiAuth.enabled=true`,
  audience `kubescape.io`, and provides projected tokens for supported
  node-agent configurations. **Explicitly prove the actual rendered pod has
  the required token mount before deploying this server.**

## Required deployment PR / blockers

1. **Exporter:** render the **exact packaged chart 1.42.0** and prove the
   chosen official exporter configuration (documented ConfigMap configuration
   through tracked GitOps with drift/upgrade tests, or switch to another
   documented exporter) without forcing Synchronizer and without modifying
   upstream binary code.
2. **Certificate:** receiver needs a TLS cert/key with a hostname matching
   the exporter URL and an issuer already trusted by the pinned Go runtime.
   Do not use `insecureSkipVerify`, plaintext bearer traffic or publish a
   ClusterIP as a security boundary. Prefer a DNS name with a valid public
   CA certificate and private-only DNS/service routing, or explicitly
   qualify a small CA distribution with supported chart configuration.
   This repository has not yet proven an existing cert-manager/issuer to
   reuse. No credentials or certs are included here.
3. **Receiver Kubernetes RBAC:** narrowly allow `create tokenreviews` (the
   Kubernetes authentication API), `get daemonsets/node-agent`,
   `get pods` for agent and enrolled workloads, and `get replicasets`
   and `get deployments` in enrolled namespaces. The receiver needs a
   rotating API-server-audience token for its own Kubernetes API access;
   do not accidentally reuse the agent's `kubescape.io` bearer as the
   receiver's API identity. Do not grant pods/log, Secrets, Kubernetes write
   privileges or cluster-admin.
4. **Source admission:** permit only approved Talos nodes and namespaces in
   the receiver's reviewed private configuration; one canary step restricts
   to metal3 and metal6. Do not treat unbound TokenReview credentials as a
   verified source.
5. **Deployment:** use a new non-root, read-only filesystem, tightly scoped
   SecurityContext, dedicated Longhorn PVC, projected API token and CA. Bind
   private receiver to ClusterIP only. Neither an image digest pin nor
   production YAML is provided until the image is built and verified.
6. **Real acceptance:** valid TLS, expired/bad token, wrong audience/SA,
   process restart, storage exhaustion, malformed body, lost HTTP response,
   duplicate delivery, independent source/Pod attribution, current image
   digest mismatch, receipt/incident persistence, no GitHub writes.
   Require a **new** harmless HTTP-exported R1000 from the two-node canary.
   The old R1000 in node-agent logs is not native HTTP acceptance.
7. **Health:** separately verify agent coverage/exporter effectiveness,
   storage capacity and gaps; receiver readiness is not proof of event
   delivery. Existing Gatus/Kyverno issue ownership stays unchanged.
8. **After successful canary:** expand directly **2 -> 8** through GitOps,
   introduce the separate sanitized issue publisher and remove the old
   log reader, its Pod-log RBAC, pin enrollment and temporary test resources.
   Do not delete `security-collector-state` or historical SQLite/Longhorn
   backup evidence. Cleanup PR #2210 is blocked/superseded.

## Tests and controls

`python3 -m unittest discover -s scripts/tests -p 'test_security_receiver_*.py' -v`

The merged native-envelope test suite runs alongside new TokenReview/owner
chain and SQLite transactional replay tests under the existing
`Security Native Exporter Contract` workflow. The tests use fake
Kubernetes API responses and **synthetic** event bodies; they do not
qualify production TLS, RBAC or a live HTTP POST.

### Deliberate limitations

- The first canary accepts only **one alert per POST**. Keep the official
  `enableAlertBulking=false` native configuration in the reviewed
  exporter (default). A batch must be supported atomically with tests
  before enabling agent bulking.
- Duplicate receipts are durable; native exporter retries may still be
  bounded/volatile, so this architecture does **not** claim lossless
  upstream transport. No event silence implies safety.
- The receiver is **not** a vulnerability scanner. Existing Kubescape
  assessments and separate safe publisher will be handled in later PRs.
- There is no side-effect-free way to claim TokenReview authentication was
  accepted in production from these synthetic tests. No live acceptance
  claims until the separate deployment PR is reviewed and tested.
