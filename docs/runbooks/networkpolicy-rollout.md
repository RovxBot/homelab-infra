# NetworkPolicy rollout

This cluster is in the policy-inventory stage. Cilium is intentionally set to
`policyEnforcementMode: never`; native Kubernetes `NetworkPolicy` objects are
rendered and reconciled but do not affect traffic. Do not change that setting
as part of adding or reviewing an application policy.

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

Only after each selected namespace has documented ingress, DNS and egress
contracts can a maintenance-window PR set Cilium to
`policyEnforcementMode: default`. Start with a single, selector-specific
backend policy and confirm the application plus Gatus. Do not enable it while
the WotLK host-network SOAP policy has unknown enforcement behavior.

The immediate rollback for a selected backend is to remove its new
`NetworkPolicy`; under `default` that restores unrestricted traffic to the
selected Pods. A separate rollback of Cilium back to `never` makes all native
policies inert. Keep those two operations as separate commits so recovery is
unambiguous.
