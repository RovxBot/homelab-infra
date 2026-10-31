# Gitea disaster recovery architecture

Inspection date: 31 October 2026 (Australia/Adelaide). Implementation branch:
`feat/gitea-disaster-recovery`. GitHub remains primary.

## Observed platform

- Eight Ready amd64 nodes, Kubernetes 1.34.1, containerd 2.2.4. Talos 1.12.8
  on metal0/2/3/5/6/7 and 1.13.3 on metal4/8. No Windows or Apple Silicon
  runner host is established by the available inventory.
- Flux 2.9.0: source/kustomize/notification controllers 1.9.1, Helm controller
  1.6.1. Served APIs include Kustomization v1, HelmRelease v2 and
  HelmRepository v1. All existing reconciliations were Ready.
- All 31 attached Longhorn volumes were healthy. Two detached volumes report
  robustness unknown: an unbound HA recovery volume and an inactive WotLK Redis
  PVC last used in September. They are outside this installation's scope.
- Longhorn 1.13.0 supplies three replicas. Its default StorageClass has reclaim
  policy Delete. Gitea uses a separate Retain class and pruning guards; existing
  volume settings and backup retention stay as declared.
- metal4 and metal7 primary disks report DiskPressure; other disks, including
  their extra disks, provide schedulable capacity. Approximate available raw
  disk capacity was 2.5 TiB; this is not usable replicated capacity. Initial
  requests require three replicas and must be verified after provisioning.
- Allocatable resources: six nodes have about 4 CPUs, metal4 about 12;
  metal0/2/3/5/6 have about 7 GiB, metal7/8 about 15 GiB, metal4 about 31 GiB.
  Reserve build concurrency at one initially; requests are not a performance SLO.
- Namespace bootstrap quotas are opt-in and are not enabled on current app
  namespaces. Workload ownership, resources, image pinning and runtime security
  are enforced by Kyverno. New namespaces use restricted Pod Security.
- Cilium 1.19.6 uses Geneve, kube-proxy and **policyEnforcementMode: never**.
  NetworkPolicy resources are documentation, not an isolation boundary. No
  cluster-wide networking change is part of this installation. Jobs are trusted
  local workloads, without Kubernetes tokens, host access or privileges.
- Cloudflared has two connectors and file-based ingress, tunnel
  `19dd24d6-5e63-4ba3-a23c-fbebc7312e3f`. `git.cooked.beer` did not resolve
  at inspection. DNS creation must be verified separately from the ingress file.
- SOPS encrypts Secret data/stringData to the existing age recipient. Flux
  decrypts with the cluster-only `flux-system/sops-age` Secret. No decryption
  identity is copied into runners or committed.
- The existing Backblaze B2 Longhorn target is available:
  `s3://cooked-k8s@us-east-005/longhorn`. Immich's separate Restic repository
  retains only one snapshot and is unsuitable for Gitea's versioned retention.
  Gatus supplies health checks; there is no Prometheus stack. Its GitHub issue
  alert channel itself depends on GitHub and is not an offline alert channel.

## Decisions

1. Standalone Flux app Kustomization; supported Gitea Helm chart, external
   single-instance PostgreSQL, file-based cache/session/queues, no HA subcharts.
   PostgreSQL major upgrades and Gitea schema upgrades require a backup first;
   a Helm rollback does not reverse a database migration.
2. Dedicated retained Longhorn PVCs for repositories/LFS/artifacts/configuration,
   database and mirroring state. The namespace, PVCs and database cannot be
   casually pruned. Longhorn replication is not an independent backup.
3. Existing HTTPS tunnel plus trusted-LAN/WireGuard NodePorts. Raw SSH is not
   published through the HTTP tunnel. Entra assignment gates SSO; a separate
   local administrator preserves recovery access.
4. Exactly `RovxBot/wowdecomp` (main) and `RovxBot/wowdecomp15595` (w15595)
   are protected. Action dependencies and the infrastructure recovery archive
   are explicitly separate recovery inputs, not account-wide discovery.
5. RayLabsHQ/gitea-mirror is evaluated from pinned source. Its detector catches
   errors and proceeds, so its setting alone cannot guarantee protection.
   Scheduled Git ref updates require an independent fail-closed guard and a
   verified bundle of the previous refs. Native mirror timers must not bypass
   that guard. Upstream errors never authorize deletion or ref replacement.
6. Gitea runner 5.x supports native Kubernetes job pods. Use that backend,
   separate controller/job namespaces, narrow namespace RBAC and a real Ubuntu
   24.04 toolchain. No Docker-in-Docker. Windows/macOS labels are never
   advertised by the Linux runner.
7. Test CI and failover in writable scratch repositories. Preserve the original
   GitHub workflows and protected pull mirrors. All recovery checkout/action
   URLs must be local; an external registry cache alone is not offline recovery.
8. Existing Flux source remains GitHub. Keep encrypted recovery material and
   exported infrastructure/chart/image inputs off-cluster to avoid circular
   bootstrap dependencies.

## Review findings

The plan is implementable in stages, but a deployment manifest is not evidence
of SSO, restore or failover success. The acceptance ledger in `operations.md`
records actual outcomes. External DNS, human OIDC login, native operating system
hosts and off-host backup credentials can independently block acceptance.

References: [official Helm chart](https://gitea.com/gitea/helm-gitea),
[runner Kubernetes backend](https://docs.gitea.com/runner/installation/kubernetes/),
[mirror source](https://github.com/RayLabsHQ/gitea-mirror),
[mirror detector](https://github.com/RayLabsHQ/gitea-mirror/blob/main/src/lib/utils/force-push-detection.ts).
