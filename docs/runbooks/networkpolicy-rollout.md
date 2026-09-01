# NetworkPolicy rollout

This cluster is in the policy-audit preparation stage. Cilium deliberately
retains `policyEnforcementMode: never`, while `policyAuditMode: true` verifies
the agent configuration on every node. Native Kubernetes `NetworkPolicy`
objects are rendered and reconciled but traffic remains non-enforcing. Do not
change `policyEnforcementMode` as part of adding or reviewing an application
policy.

## Stage 1: cluster-wide audit preparation

After Flux rolls Cilium one node at a time, validate the setting before
considering any audit-backed enforcement work:

```bash
export KUBECONFIG="$HOME/.config/talos/cooked-k8s/kubeconfig-entra"
scripts/cilium-policy-audit-health.sh
```

This stage does **not** make a namespace enforcing and does not enable Hubble,
Prometheus, Grafana, or the host firewall. Its immediate rollback is a single
Git revision setting `policyAuditMode: false`, while leaving
`policyEnforcementMode: never` unchanged.

The later evidence-gathering stage is a separate maintenance change to
`policyEnforcementMode: default` while retaining audit mode. Audit mode then
allows would-be denied L3/L4 traffic while recording verdicts. It must be
time-boxed and reverted or followed by fully reviewed contracts; do not leave
global audit mode on as a security end state.

## Current contracts

The first policies isolate only known backend ingress paths once enforcement is
eventually enabled:

| Backend | Application client | Health client | Port |
| --- | --- | --- | --- |
| Immich PostgreSQL | `immich-server` | Gatus in `monitoring` | TCP 5432 |
| Immich Redis | `immich-server` | Gatus in `monitoring` | TCP 6379 |
| Immich machine learning | `immich-server` | Gatus in `monitoring` | TCP 3003 |
| Invoice Ninja MySQL | `invoiceninja` | Gatus in `monitoring` | TCP 3306 |
| Invoice Ninja Redis | `invoiceninja` | Gatus in `monitoring` | TCP 6379 |

These are ingress-only policies. They intentionally do not restrict egress,
DNS, NFS, Backblaze, SMTP, NodePorts, public tunnels, or other application
traffic. Gatus is included because it actively performs the documented TCP
dependency checks; it does not authenticate to or mutate these services.

## Rules for the inventory stage

- Keep `infra/cilium/helmrelease.yaml` at `policyEnforcementMode: never`.
  `policyAuditMode: true` alone is only the non-enforcing preparation stage.
- Do not add `homelab.cooked.beer/bootstrap: enabled` to any namespace. The
  Kyverno generator attached to that label creates namespace-wide ingress and
  egress deny-all policies and is not safe until the full namespace contract
  is proven.
- Do not apply default-deny policies, Cilium clusterwide policies, or a global
  enforcement-mode change in the same PR as an inventory policy.
- Treat media, WotLK host-network workloads, Longhorn, GPU, WireGuard,
  Cilium, Kubernetes, Kyverno, and Flux as separate projects. Their traffic
  contracts have not yet been captured.

## Validation for an inventory-policy PR

Run locally before merge:

```bash
kubectl kustomize --load-restrictor LoadRestrictionsNone apps/immich >/dev/null
kubectl kustomize --load-restrictor LoadRestrictionsNone apps/invoiceninja >/dev/null

export KUBECONFIG="$HOME/.config/talos/cooked-k8s/kubeconfig-entra"
kubectl -n immich apply --dry-run=server \
  -f apps/immich/networkpolicies.yaml
kubectl -n invoiceninja apply --dry-run=server \
  -f apps/invoiceninja/networkpolicies.yaml
```

The application kustomizations include SOPS-encrypted Secrets. Render them to
validate Kustomize structure, but do not pipe their raw output to `kubectl`;
Flux performs decryption before applying those resources.

After Flux reconciles, require these gates before considering a later
activation PR:

```bash
kubectl -n flux-system get kustomization apps-media apps-invoiceninja
kubectl -n immich get networkpolicy
kubectl -n invoiceninja get networkpolicy
scripts/cilium-primary-health.sh
```

Also verify the Gatus endpoints for all five backends are successful. Cilium
Hubble is currently disabled, so collect live connection evidence before any
new namespace is made enforcing; do not guess NodePort source addresses or
media application's mutable inter-service dependencies.

## Later enforcement, separately

Only after each selected endpoint has documented ingress, DNS and egress
contracts can a separate maintenance-window PR set Cilium to
`policyEnforcementMode: default` while retaining audit mode. This affects all
currently selected endpoints, not only the policy being reviewed. Start only
after Flux, Immich, Invoice Ninja, and the WotLK HostPort/SOAP contracts have
been observed. In particular, do not enable it while the WotLK worldserver
policy lacks Gatus and public HostPort evidence.

The immediate rollback for a selected backend is to remove its new
`NetworkPolicy`; under `default` that restores unrestricted traffic to the
selected Pods. A separate rollback of Cilium back to `never` makes all native
policies inert. Keep those two operations as separate commits so recovery is
unambiguous.
