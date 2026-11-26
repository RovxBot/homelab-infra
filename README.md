# homelab-infra

[![Repo Checks](https://github.com/RovxBot/homelab-infra/actions/workflows/ci.yml/badge.svg)](https://github.com/RovxBot/homelab-infra/actions/workflows/ci.yml)

GitOps source for `cooked-k8s`, a personal eight-node Talos Kubernetes homelab. Flux reconciles reviewed changes from `main`. This repository contains desired state, automation and qualification evidence; the **[GitHub Wiki](https://github.com/RovxBot/homelab-infra/wiki)** is the maintained guide to architecture, applications and operations.

This is an opinionated homelab, with environment-specific hosts, domains and recovery dependencies. It requires adaptation before use elsewhere.

## Architecture overview

| Area | Design |
| --- | --- |
| Platform | Talos Linux; three control-plane nodes and five workers |
| Delivery | GitHub pull requests, CI, manual merge and Flux reconciliation |
| Networking | Cilium Geneve with kube-proxy; `policyEnforcementMode: never` |
| Storage | Longhorn, generally three replicas; independent off-site backups in Backblaze B2 |
| Identity and secrets | Entra OIDC, Kubernetes RBAC and SOPS-encrypted Secrets |
| Workload controls | PSA and Kyverno, with explicit reviewed exceptions |
| Exposure | Cloudflare Tunnel and the OCI WireGuard edge; intentional trusted-LAN access |
| Health | Gatus, external Upptime, Metrics Server, logs and events |

See [Cluster Architecture](https://github.com/RovxBot/homelab-infra/wiki/Cluster-Architecture), [Network Architecture](https://github.com/RovxBot/homelab-infra/wiki/Network-Architecture) and [Architecture Decisions](https://github.com/RovxBot/homelab-infra/wiki/Architecture-Decisions) for design and safety boundaries.

## GitOps topology

[`clusters/home/`](clusters/home/) is the Flux entrypoint. It composes infrastructure, Cilium, policies, opt-in namespace defaults, Longhorn node disks and application Kustomizations. Dependencies and prune boundaries belong to those manifests.

```text
Reviewed PR → main → Flux source → clusters/home → infrastructure and applications
```

Flux owns Kubernetes desired state. Talos configuration, application data and Terraform state have separate recovery and management paths. See [Flux GitOps](https://github.com/RovxBot/homelab-infra/wiki/Flux-GitOps) and [Backup and Recovery](https://github.com/RovxBot/homelab-infra/wiki/Backup-and-Recovery).

## Repository map

| Path | Purpose |
| --- | --- |
| [clusters/home/](clusters/home/) | Flux bootstrap and reconciliation graph |
| [apps/](apps/) | Application manifests and necessary implementation notes |
| [infra/](infra/) | Platform services, policy and component contracts |
| [secrets/](secrets/) | SOPS ciphertext; decryption identities stay outside Git |
| [ops/](ops/) | Build inputs, operational helpers and recovery tooling |
| [scripts/](scripts/) | Validators, tests and guarded maintenance helpers |
| [.github/](.github/) | GitHub Actions, CI policy and test fixtures |
| [terraform/](terraform/) | Independent OCI edge and Matrix roots |
| [docs/decisions/](docs/decisions/) | Accepted architecture decision records |
| [docs/](docs/) | Retained specialised procedures and dated evidence pending verified Wiki coverage |
| [scripts/ci/kyverno/](scripts/ci/kyverno/) | Active isolated qualification, trust tooling and fixtures |
| [infra/kyverno-policies/](infra/kyverno-policies/) | Production CEL policies, exceptions and generated policy map |
| [docs/kyverno/](docs/kyverno/) | Historical migration evidence, activation and exception decisions |

## Documentation and changes

- Start with the [Wiki](https://github.com/RovxBot/homelab-infra/wiki), [Application Catalog](https://github.com/RovxBot/homelab-infra/wiki/Application-Catalog), [Cluster Maintenance](https://github.com/RovxBot/homelab-infra/wiki/Cluster-Maintenance) or [Disaster Recovery](https://github.com/RovxBot/homelab-infra/wiki/Disaster-Recovery).
- Follow [CONTRIBUTING.md](CONTRIBUTING.md) for focused PRs and [AGENTS.md](AGENTS.md) for agent scope and operating boundaries. Change Flux-owned resources through reviewed Git changes.
- Use [CI and GitHub Actions](https://github.com/RovxBot/homelab-infra/wiki/CI-and-GitHub-Actions) and [Renovate](https://github.com/RovxBot/homelab-infra/wiki/Renovate) for validation and dependency maintenance. Check reconciliation and application health after an approved merge.
- Preserve Longhorn data, healthy replica placements and verified backups. Never commit plaintext secrets, kubeconfigs, Talos machine configurations, decrypted SOPS material or Terraform state.

[MIT license](LICENSE). Application and build inputs retain their respective upstream licences.
