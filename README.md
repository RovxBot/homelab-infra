# homelab-infra

[![Repo Checks](https://github.com/RovxBot/homelab-infra/actions/workflows/ci.yml/badge.svg)](https://github.com/RovxBot/homelab-infra/actions/workflows/ci.yml)
[![Security Review](https://github.com/RovxBot/homelab-infra/actions/workflows/security-review.yml/badge.svg)](https://github.com/RovxBot/homelab-infra/actions/workflows/security-review.yml)
[![WotLK Images](https://github.com/RovxBot/homelab-infra/actions/workflows/azerothcore-wotlk-images.yml/badge.svg)](https://github.com/RovxBot/homelab-infra/actions/workflows/azerothcore-wotlk-images.yml)

GitOps source for the `cooked-k8s` Talos Kubernetes homelab. Flux reconciles reviewed `main` into the cluster; this repository holds desired state, not live credentials, rendered Talos machine configurations, backups or Terraform state.

This is a personal, opinionated homelab—not a turnkey production template. It is designed around eight disk-bearing nodes, constrained lab capacity, and the operational decisions recorded here.

## At a glance

| Area | Current design |
| --- | --- |
| Cluster | Eight-node Talos Linux Kubernetes cluster, reconciled by Flux |
| Identity | Entra OIDC for normal `kubectl` administration; offline certificate break-glass access |
| Storage | Longhorn on every node disk; general volumes use three replicas |
| Backups | Longhorn and Immich/Restic backups go to Backblaze B2 |
| Health | Gatus for in-cluster and edge checks; [homelab-uptime](https://github.com/RovxBot/homelab-uptime) publishes external status |
| Networking | Cilium is the primary Geneve CNI; kube-proxy remains enabled and policy enforcement is intentionally `never` |
| Edge | Cloudflare Tunnel for managed public routes; OCI WireGuard edge for intended Immich and Jellyfin public paths |
| Policy | PSA, Kyverno and CI gates provide progressive workload hardening |
| Dependency updates | Hosted Renovate App, manual merges only, with dashboard gates for maintenance-sensitive changes |
| Observability trade-off | No Grafana, Prometheus, Loki or Prometheus Operator; use Gatus, `kubectl top`, logs, events, Talos and Longhorn |

## Repository boundaries

Keep configuration with the system that consumes it. Do not create a shared plaintext secret store.

| Location | Purpose | Must not contain |
| --- | --- | --- |
| This repository | Flux manifests, SOPS ciphertext, application and infrastructure configuration | Plaintext secrets, private keys, Terraform state, Talos machine configs |
| Private `TalosConfigs` repository | Reusable Talos patches, schematic IDs, inventory and runbooks | Rendered machine configs, `talosconfig`, kubeconfigs, machine secrets |
| Private encrypted recovery repository | Encrypted Talos/Kubernetes recovery archives and checksums | Decrypted archive contents or passwords |
| [homelab-uptime](https://github.com/RovxBot/homelab-uptime) | Upptime configuration, generated history and public status page | Flux manifests or generated status history here |
| `terraform/oci-free-tier` | Existing OCI WireGuard/public-edge infrastructure | State files, plans or credentials |
| `terraform/oci-matrix-free-tier` | Independent, unapplied Matrix environment | Shared edge state or an automatic apply workflow |

SOPS-encrypted Kubernetes Secrets live in `secrets/*.enc.yaml`. The Flux age identity is cluster-only; keep recovery and cloud identities separate and out of Git.

## GitOps topology

`clusters/home` is the Flux entrypoint. It composes infrastructure, policy, storage-node definitions, and application Kustomizations.

```text
main
 └── clusters/home
     ├── infrastructure        → access, Kyverno, Longhorn, backups, edge, Gatus
     ├── cilium                → primary CNI and its signed OCI chart
     ├── kyverno-policies      → policy definitions and exceptions
     ├── longhorn-node-disks   → per-node disk definitions
     └── applications          → media, *arr, Homepage, Invoice Ninja,
                                  Grimguzzler registration and WotLK
```

| Path | Contents |
| --- | --- |
| `apps/` | Immich, media, Vaultwarden, Homepage, Invoice Ninja, WotLK and other application manifests |
| `infra/` | Entra RBAC, Cilium, Longhorn, backups, Cloudflared, WireGuard, Gatus, Kyverno and Metrics Server |
| `clusters/` | Flux bootstrap and reconciliation boundaries |
| `secrets/` | SOPS ciphertext only |
| `terraform/` | OCI edge and independent Matrix Terraform roots |
| `ops/` | WotLK build inputs and operational helpers |
| `docs/decisions/` | Accepted architecture decisions |

## Applications and exposure

The primary workloads are Immich, Jellyfin and the media stack, Vaultwarden, Homepage, Invoice Ninja, Gatus, Grimguzzler registration and the WotLK realm.

Ten HTTP NodePorts are intentionally available on the trusted LAN for internal integrations. This includes the OCI edge's WireGuard path to Immich and Jellyfin. They are an accepted risk, not an accidental public-ingress model: do not forward them from the WAN or expose them through UPnP. New NodePorts need a documented dependency and review. See [ADR 0001](docs/decisions/0001-lan-nodeport-access.md) for the list and constraints.

Immich and Jellyfin remain public through the OCI edge by design and rely on their application login. Administrative routes should use Cloudflare Access with Entra where compatible; do not add a proxy login in front of public media clients without testing those clients first.

## Storage and backups

All eight nodes contribute Longhorn disks. Do not remove a disk or taint/drain a node casually: first confirm every affected volume has three healthy replicas and perform one-node-at-a-time maintenance.

- Longhorn uses conservative one-per-node replica rebuild concurrency.
- Replica auto-balance is `least-effort`; do not force broad rebuilds merely to make placement look symmetrical.
- New volumes are refused when all requested replicas cannot be scheduled.
- Existing Longhorn and Immich backup schedules, credentials, retention and Backblaze B2 targets are deliberate and must not be changed as unrelated cleanup.

Before a Talos, Kubernetes, Longhorn or node-maintenance rollout, check Flux, Longhorn volume robustness and disk schedulability. A non-healthy volume is a stop condition.

## Access and administration

Entra OIDC is the normal Kubernetes administration path. It maps Entra `homelab.admin`, `homelab.operator` and `homelab.viewer` roles to Kubernetes RBAC groups. Administrator is cluster-admin; operator and viewer deliberately exclude Secrets and workload mutation.

```bash
export KUBECONFIG="$HOME/.config/talos/cooked-k8s/kubeconfig-entra"
kubectl auth whoami
kubectl get nodes
```

Talos credentials are recovery material, not routine access. Keep the local recovery file at mode `0600`, retain its encrypted recovery copy, and do not commit it. Never print or upload a Talos machine-configuration diff: it can include sensitive key material.

Azure Arc and Azure consumption services are intentionally absent. Entra ID P1 from Microsoft 365 E3 is used only for identity; this design does not require an Azure subscription resource.

## Health, status and troubleshooting

Gatus is the in-cluster health system. It checks cluster health, internal Services and public routes, and opens/resolves GitHub issues for sustained failures. Its configuration is [`infra/observability/gatus-helmrelease.yaml`](infra/observability/gatus-helmrelease.yaml).

Upptime belongs in [homelab-uptime](https://github.com/RovxBot/homelab-uptime). It monitors public HTTPS paths and publishes the status page; never commit its generated history into this GitOps repository.

Without a metrics/logging stack, use focused operational tools:

```bash
kubectl top nodes
kubectl top pods -A
kubectl get events -A --sort-by=.lastTimestamp
kubectl -n longhorn-system get volumes.longhorn.io
kubectl -n flux-system get kustomizations
kubectl -n kube-system get helmreleases
scripts/cilium-primary-health.sh
```

Use `kubectl logs`, `talosctl logs`, Longhorn and workload events for deeper diagnosis. Do not reinstall Prometheus merely for a one-off incident.

## Change workflow

1. Create a focused branch from current `main`.
2. Change desired state only; never manually edit Flux-owned live resources.
3. Render and validate the affected Kustomization locally.
4. Open a PR with the reason, impact, rollback path and validation evidence.
5. Merge only after checks and review pass; Flux applies `main`.
6. Watch reconciliation and workload/storage health before the next step.

The default reconciliation checks are:

```bash
export KUBECONFIG="$HOME/.config/talos/cooked-k8s/kubeconfig-entra"

kubectl -n flux-system get kustomizations,helmreleases
kubectl get nodes
kubectl -n longhorn-system get volumes.longhorn.io
```

Manual reconciliation is useful after a reviewed merge, not as a substitute for Git:

```bash
flux -n flux-system reconcile kustomization infrastructure --with-source
flux -n flux-system reconcile kustomization apps-arr --with-source
flux -n flux-system reconcile kustomization apps-wotlk --with-source
```

### CI and policy gates

Repository CI is intentionally strict:

- Renders the Flux entrypoint and every `apps/`, `infra/` and `clusters/` Kustomize root.
- Validates rendered resources, public-edge configuration and WireGuard shell syntax.
- Detects unresolved Secret references and forbidden credential-like files.
- Requires exact SHA-256 digests for generated Flux bootstrap controller images.
- Runs the Kyverno baseline gate, Gitleaks, GitGuardian, Zizmor, YAML lint and workflow lint.

The Kyverno gate compares the repository against policies in `infra/kyverno/policies` and the reviewed debt baseline in `.github/kyverno-baseline.yaml`. New failures fail CI; resolved baseline entries must be removed rather than retained indefinitely.

Run the policy gate locally without changing the accepted baseline:

```bash
python3 scripts/ci/kyverno_gate.py check \
  --repo-root . \
  --cluster-root clusters/home \
  --baseline .github/kyverno-baseline.yaml \
  --kyverno-bin /path/to/kyverno \
  --work-dir .work/kyverno-gate
```

Only regenerate the baseline after reviewing each accepted exception:

```bash
python3 scripts/ci/kyverno_gate.py generate-baseline \
  --repo-root . \
  --cluster-root clusters/home \
  --baseline .github/kyverno-baseline.yaml \
  --kyverno-bin /path/to/kyverno \
  --work-dir .work/kyverno-gate
```

## Maintenance boundaries

The following require a maintenance plan, an explicit rollback path and post-change health checks:

- Talos, Kubernetes, Longhorn and CNI upgrades.
- Longhorn disks, replica settings, node scheduling or taint changes.
- Terraform applies to OCI edge infrastructure.
- Cloudflare Access, public routing or DNS changes.
- SOPS recipients, secret rotation or Entra OIDC configuration.

The Matrix Terraform root is intentionally independent and unapplied. Do not run a whole OCI workspace apply that could mix Matrix decisions with the running edge, and do not create the VM until capacity, independent management and an encrypted off-host backup plan are ready.

## Adapting this repository

Forking this repository requires replacing every environment-specific input:

1. Create your own Talos cluster and private recovery process.
2. Replace SOPS recipients and re-encrypt every Secret.
3. Replace node names, Longhorn disk paths, domains, tunnel configuration and external addresses.
4. Decide on LAN exposure, backup targets, identity provider and public routes before applying manifests.
5. Keep the Flux bootstrap path and repository ownership consistent with your fork.

Never copy this repository's encrypted Secrets, OCI configuration or Talos recovery material into another environment.

## Further reading

- [Contributing](CONTRIBUTING.md)
- [WotLK server and image notes](WOTLK.md)
- [LAN NodePort decision](docs/decisions/0001-lan-nodeport-access.md)
- [Cilium primary-CNI decision](docs/decisions/0002-cilium-dual-overlay-migration.md)
- [Cilium primary-CNI operations](docs/runbooks/cilium-primary-operations.md)
- [Renovate operations](docs/runbooks/renovate-operations.md)
- [Flux bootstrap maintenance](docs/runbooks/flux-bootstrap-maintenance.md)
- [OCI public-edge Terraform](terraform/oci-free-tier/README.md)
- [Independent Matrix Terraform](terraform/oci-matrix-free-tier/README.md)
- [License](LICENSE)
