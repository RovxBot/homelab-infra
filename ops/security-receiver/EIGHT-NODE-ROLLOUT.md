# Kubescape native HTTP exporter — eight-node rollout

## Live evidence already accepted

- #2216: actual **R1000** on `metal3` reached the native receiver; authenticated TLS, TokenReview, workload attribution and SQLite receipt verified
- #2217: receiver restarted by GitOps, and read-only SQLite verified `PRAGMA quick_check=ok`, `receipts=1`, `incidents=1`, original R1000 receipt preserved
- Offline native receiver tests already verify **identical event-id replay returns `duplicate=true`**, without a second receipt or incident, including after reopening the database
- **Live identical-event HTTP replay is not claimed.** A bespoke harness requiring access to real Pod-bound bearer tokens or raw event bodies is not required to proceed with a reviewed rollout

## Exactly what changes

Expand the existing *single official* `node-agent` DaemonSet's exact nodeAffinity,
the least-privilege `nodes/proxy` `resourceNames` list, the precise Kyverno
CEL allowlist, and the receiver's authenticated source node allowlist to the
actual eight Ready nodes verified on 24 Nov 2026:

```text
metal0, metal2, metal3, metal4, metal5, metal6, metal7, metal8
```

There is no `metal1` node. CI explicitly denies unknown nodes, extra
capabilities, arbitrary host paths, privilege escalation, credentials
injection and network changes.

Unchanged: official image v0.3.310 with pinned OCI digest, chart 1.42.0,
SOPS/age TLS Secrets and chart-mounted CA, Pod-bound `kubescape.io` token,
receiver ClusterIP and existing Longhorn state, GitOps, controlled runtime
rules and namespace allowlist `homepage,memos,kubescape-runtime-test`,
stdout logging, no Synchronizer, no Cilium enforcement changes, no
auto-remediation or GitHub publishing, no legacy collector cleanup.

**Note:** this expands node coverage from 2 to 8. The existing **three
namespace** runtime filter remains in place; this change does not claim
runtime detection for every namespace. Expand scope separately once
end-to-end safe publication and data handling are qualified.

## Minimal required checks

CI on this PR:
- Render exact pinned Helm chart, compare the post-render DaemonSet
  nodeAffinity and `nodes/proxy` name-scoped RBAC against eight-node contract
- Execute existing Kyverno 1.20.0 CEL policies against all eight positive
  node-specific fixtures and negative fixtures (unknown metal1, wrong
  affinity, privilege/capability/Secret changes)
- Verify exact receiver nodes allowlist and TokenReview-bound node identity;
  run existing duplicate/restart tests against receiver storage
- Existing repo/security validation; no separate deployment/restart test PRs

After human-reviewed merge:

```sh
kubectl -n flux-system get kustomization kubescape
kubectl -n kubescape get helmrelease kubescape
kubectl -n kubescape rollout status daemonset/node-agent --timeout=300s
kubectl -n kubescape get daemonset node-agent
kubectl -n kubescape get pods -l app.kubernetes.io/name=node-agent -o wide
kubectl -n kubescape get deployment security-receiver security-collector
kubectl -n kubescape get pvc security-receiver-state security-collector-state
```

**Gate:** **8/8 node-agent Pods Ready**, one each on the eight actual nodes;
receiver + old collector Ready; both old PVCs Bound; Flux/Helm Ready.
Confirm there are no relevant recent Kyverno admission denials or exporter
errors. The receiver health endpoint still reports
`delivery_coverage: unverified` by design. One prior R1000 proves the
native transport, *not* lossless delivery guarantees on every node.

If any node can't schedule, diagnose taints, readiness and restricted
admission before considering the rollout accepted. Do **not** grant
`tolerations: [{operator: Exists}]` or relax CEL policy blindly to force a
green DaemonSet. If the rollout is unhealthy, revert this one PR in GitOps;
don't delete either Longhorn PVC.

This rollout remains separated from sanitized incident publishing, recovery
checks, and eventual legacy log collector retirement.

## Post-acceptance fixture retirement (26 November 2026)

The accepted native canary and restart evidence above supersede the old draft cleanup design. The live check before this change observed 8/8 agents Ready, receiver and legacy collector Ready, both 256Mi evidence claims Bound, and only the disposable acceptance Pod and no PVC in its test namespace. Remove the trigger overlay from the Flux resource list and its exact workload identity from the legacy collector configuration. The overlay source, policy test fixtures, receiver intake configuration, runtime bindings, historical SQLite state and both prune-protected evidence claims remain. No runtime event or issue is fabricated, closed or deleted. Existing historical evidence is recorded above; the present check does not reread private SQLite contents.

After merge, verify the exact Flux revision, removal of the disposable Deployment, ServiceAccount and its collector-specific RBAC; retain the restricted test namespace for the receiver's existing inventory Role and RoleBinding, eight Ready agents, receiver readiness, both unchanged Bound evidence claims, and native publisher transport stage. Recheck private receipt persistence through the existing read-only acceptance procedure if needed. A failed cleanup must not be addressed by deleting any evidence claim. The old collector and its production RBAC/health checks are retained for a separate reviewed retirement. Do not merge the obsolete draft cleanup independently of this change.

The namespace is now explicitly managed by `infra/kubescape/receiver/acceptance-namespace.yaml`, with exactly the original restricted PSA labels. For any separately approved future fixture activation, replace that namespace reference in the same review when including the trigger overlay (which already defines the namespace), avoiding a duplicate Kustomize resource. No namespace deletion or recreation is required.
