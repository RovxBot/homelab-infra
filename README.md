# homelab-infra

GitOps repo for my homelab Kubernetes cluster (Talos) managed by Flux. This repo defines both cluster infrastructure (storage, networking, observability) and application workloads (media stack, Immich, Vaultwarden, WoW servers, etc).

## How this repo is applied

Flux is bootstrapped in the cluster and continuously syncs `./clusters/home` from the `main` branch:

- Flux source + sync: `clusters/home/flux-system/gotk-sync.yaml`
- Root Kustomization: `clusters/home/kustomization.yaml`

General workflow:

1. Change manifests in this repo.
2. Commit + push to `main`.
3. Flux reconciles and applies changes (or you trigger a reconcile manually).

Useful commands:

```bash
# Flux status
flux get kustomizations -A
flux get sources git -A

# Force a reconcile (examples)
flux -n flux-system reconcile kustomization apps-wotlk --with-source
flux -n flux-system reconcile kustomization apps-arr --with-source
flux -n flux-system reconcile kustomization infrastructure --with-source
```

## Repo layout

- `clusters/`
  - `clusters/home/`: Flux entrypoint for the “home” cluster (what gets applied)
  - `clusters/home/apps/`: Flux Kustomizations for app groups (arr/homepage/wotlk/etc)
  - `clusters/home/infrastructure/`: Flux Kustomizations for infrastructure components
- `apps/`: Kubernetes manifests for workloads (mostly plain YAML + kustomize)
  - `apps/arr/` is represented by the individual apps (`apps/sonarr`, `apps/radarr`, `apps/prowlarr`, `apps/sabnzbd`, etc)
  - `apps/immich/`, `apps/vaultwarden/`, `apps/homepage/`, `apps/jellyfin/`, `apps/jellyseerr/`
  - `apps/wotlk/` and `apps/wow/`: WoW server stacks
- `infra/`: cluster-level services (Longhorn, monitoring, Cloudflared, WireGuard, namespaces, etc)
- `secrets/`: SOPS-encrypted Kubernetes Secrets/credentials (`*.enc.yaml`)
- `ops/`: operational helpers (SQL, image build inputs, WireGuard scripts, etc)

## Secrets (SOPS)

This repo uses Mozilla SOPS with Age to store secrets safely in Git.

- SOPS rules live in `.sops.yaml`
- Flux decrypts SOPS secrets using an Age key stored in-cluster (see `clusters/home/flux-system/gotk-sync.yaml` `spec.decryption`)

Typical flow:

```bash
# Encrypt a secret for committing (example)
sops --encrypt --in-place secrets/my-secret.enc.yaml

# Edit an encrypted secret
sops secrets/my-secret.enc.yaml
```

Notes:

- Do not commit plaintext secrets.
- Keep your Age private key out of Git and back it up securely.

## Renovate (dependency/image updates)

Renovate is configured in `renovate.json` and runs via GitHub Actions:

- Workflow: `.github/workflows/renovate.yml`
- It pins container image digests so updates can be reviewed/merged via PRs.
- WoW stacks are excluded from Renovate updates (see `renovate.json` ignore rules).

## Adding/updating apps

High-level pattern:

1. Add/update YAML in `apps/<app>/` (and `kustomization.yaml` inside that folder).
2. Ensure the app is referenced by the relevant Flux Kustomization under `clusters/home/apps/`.
3. Commit + push; Flux rolls it out.

## Troubleshooting

- See what Flux thinks is applied:
  - `flux get kustomizations -A`
  - `flux logs -A --kind Kustomization --follow`
- See what Kubernetes is doing:
  - `kubectl get pods -A`
  - `kubectl -n <ns> describe deploy/<name>`
  - `kubectl -n <ns> logs deploy/<name> --tail=200`

## Notes

This is a personal homelab repo; manifests are opinionated and tailored to the “home” cluster.

