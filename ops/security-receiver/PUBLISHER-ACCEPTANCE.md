# Native incident publisher — production rollout

## Ownership and data boundaries

- Kubescape node-agent posts authenticated native events to
  `kubescape/security-receiver`. The receiver keeps the private Longhorn
  SQLite database and has **no GitHub credentials**.
- `GET /v1/public-incidents` requires a different Pod-bound projected token
  for `monitoring/security-incident-publisher`, verified through Kubernetes
  TokenReview, a live Job Pod UID, image pin, service account and readiness.
- The source returns **only High/Critical** sanitized incidents for three
  statically named non-sensitive homepage/memos workloads. Unknown identities,
  Gitea, raw process data, image IDs, Pod IDs, and the disposable R1000 fixture
  are **not publishable**. No new GitHub issue is expected from the previously
  recorded R1000 in `kubescape-runtime-test`.
- The independent monitoring publisher reuses the existing repository-scoped
  `gatus-github-token`, not the receiver's token. This deliberately shares the
  credential's blast radius but **not** issue namespaces or process ownership.
  Replace it with a dedicated issues-only GitHub credential if desired later.
- Only `get` on the explicitly named **public CA-only**
  `kubescape/security-receiver-ca` Secret is granted to the publishing SA.
  The publisher validates receiver TLS and uses its own rotating
  `kubescape.io` audience token. The TLS private-key Secret cannot be read.
- The publisher stores write fences/revisions on a separate prune-protected
  `monitoring/security-incident-publisher-state` 128Mi Longhorn PVC.
  External create/comment outcome uncertainty leaves a persistent manual
  reconciliation hold; never delete/reset that PVC to fix delivery.
- Gatus owns transport health. The new publisher owns only
  `Security (Kubescape):` issues. Kyverno owns policy issues.
  **There is no automatic incident resolution, reopening or remediation.**

## One merge and necessary acceptance only

Before merge: CI must prove exact rebuilt receiver image digest matches
`infra/kubescape/receiver/workload.yaml` and all repo manifest, admission,
receiver and synthetic publisher tests pass.

After merge, confirm GitOps Ready and existing security services/agent pods
remain available. The receiver will restart once because its image changed:

```sh
kubectl -n flux-system get kustomization kubescape infrastructure
kubectl -n kubescape get deploy security-receiver security-collector
kubectl -n kubescape get daemonset node-agent
kubectl -n kubescape get pvc security-receiver-state security-collector-state
kubectl -n monitoring get cronjob security-incident-publisher
kubectl -n monitoring get pvc security-incident-publisher-state
kubectl -n monitoring get jobs -l app.kubernetes.io/name=security-incident-publisher
```

The publisher runs every 10 minutes, no separate manual R1000, token
extraction or receiver restart test required. Inspect only static result
counts in the publishing Pod log:
`{"total":N,"created":N,"updated":N,"pending":N}`.
`security_native_publisher_failed` is a deliberate content-free failure
signal; troubleshoot RBAC/Pod-bound TokenReview and named CA, then repository
credential permissions, without printing tokens, responses or raw events.

Acceptance requires a **successful completed publishing Job**, receiver 1/1
Ready with original incident preserved and all eight agents Ready. If no
approved High/Critical findings exist, the correct result is `total=0`;
do not fabricate an incident in production just to test issue creation.
CI covers synthetic creation, duplicates, hostile evidence redaction,
ambiguous writes, human notes and human-closed issues.

If an approved finding exists, validate one private-source-owned GitHub
issue with only allowed workload, rule, severity, timestamps and bounded
occurrence count. Re-running the Job must not create a duplicate.

## Rollback

Revert the source-control publisher resource reference and receiver image/API
change through GitOps. Preserve `security-incident-publisher-state`,
`security-receiver-state` and the legacy collector PVC, private receipts,
existing GitHub issues and Gatus issue ownership. No direct `kubectl delete
pvc`, forced Pod restarts, Cilium enforcement changes or destructive
remediation. Separate phase removes obsolete log-reading collector RBAC
after a successful native issue lifecycle check. 
