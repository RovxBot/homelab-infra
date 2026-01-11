# homelab-infra

[![WotLK Images](https://github.com/RovxBot/homelab-infra/actions/workflows/azerothcore-wotlk-images.yml/badge.svg)](https://github.com/RovxBot/homelab-infra/actions/workflows/azerothcore-wotlk-images.yml)
[![Mangos Images](https://github.com/RovxBot/homelab-infra/actions/workflows/mangoszero-images.yml/badge.svg)](https://github.com/RovxBot/homelab-infra/actions/workflows/mangoszero-images.yml)
[![Renovate](https://github.com/RovxBot/homelab-infra/actions/workflows/renovate.yml/badge.svg)](https://github.com/RovxBot/homelab-infra/actions/workflows/renovate.yml)

This is my homelab GitOps repo. It is a learning exercise first and foremost: expect opinionated choices, experimentation, and the occasional breaking change. It drives a Talos Kubernetes cluster with Flux and includes infrastructure, apps, and game servers.

## What lives here

- `clusters/`: Flux entrypoint and Kustomizations for the home cluster
- `apps/`: application manifests (media stack, Immich, Vaultwarden, WoW servers, etc.)
- `infra/`: shared infrastructure (Longhorn, monitoring, Cloudflared, WireGuard, namespaces)
- `secrets/`: SOPS-encrypted Kubernetes secrets (`*.enc.yaml`)
- `ops/`: operational helpers (SQL, build inputs, scripts)

## How changes deploy

1. Edit manifests in this repo.
2. Commit + push to `main`.
3. Flux reconciles and applies changes automatically.

Manual reconcile examples:

```bash
flux get kustomizations -A
flux get sources git -A

flux -n flux-system reconcile kustomization apps-wotlk --with-source
flux -n flux-system reconcile kustomization apps-arr --with-source
flux -n flux-system reconcile kustomization infrastructure --with-source
```

## Public repo notes

- This is not a production template or an official reference architecture.
- No plaintext secrets are stored here; secrets are SOPS-encrypted.
- Use at your own risk and adapt to your environment.

## Docs

- Contributing: `CONTRIBUTING.md`
- License: `LICENSE`
- WotLK server details and images: `WOTLK.md`
