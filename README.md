# homelab-infra

[![WotLK Images](https://github.com/RovxBot/homelab-infra/actions/workflows/azerothcore-wotlk-images.yml/badge.svg)](https://github.com/RovxBot/homelab-infra/actions/workflows/azerothcore-wotlk-images.yml)
[![Renovate](https://github.com/RovxBot/homelab-infra/actions/workflows/renovate.yml/badge.svg)](https://github.com/RovxBot/homelab-infra/actions/workflows/renovate.yml)

This is my homelab GitOps repo. It drives a Talos Kubernetes cluster with Flux and includes infrastructure, apps, and game servers. The goal is learn-by-building, so expect opinionated choices and ongoing experimentation.

## Architecture at a glance

- OS + cluster: Talos Linux running Kubernetes
- GitOps: Flux (repo is the source of truth)
- Storage: Longhorn (with per-node disk layout)
- Service health: Gatus in-cluster checks, plus public uptime in [homelab-uptime](https://github.com/RovxBot/homelab-uptime)
- Access: Cloudflared tunnel, WireGuard
- Apps: media stack, Immich, Vaultwarden, WotLK server, and more

## Repo layout

- `clusters/`: Flux entrypoint and Kustomizations for the home cluster
- `apps/`: application manifests (media stack, Immich, Vaultwarden, WotLK server, etc.)
- `infra/`: shared infrastructure (Longhorn, monitoring, Cloudflared, WireGuard, namespaces)
- `secrets/`: SOPS-encrypted Kubernetes secrets (`*.enc.yaml`)
- `ops/`: operational helpers (SQL, build inputs, scripts)

## Deployment status

- Active in this cluster: `apps/wotlk`, `apps/homepage`, `clusters/home/apps/media`, `clusters/home/apps/arr`, and `clusters/home/infrastructure`.

## How changes deploy

1. Edit manifests in this repo.
2. Commit + push to `main`.
3. Flux reconciles and applies changes automatically.

## Policy enforcement

- `Repo Checks` renders the Flux-managed manifests, validates them with `kubeconform`, checks unresolved secret references, and runs the Kyverno baseline gate before merge.
- The Kyverno gate compares rendered repo-managed resources against the policies in `infra/kyverno/policies` and the accepted debt baseline in `.github/kyverno-baseline.yaml`.
- New policy failures break CI. Resolved baseline entries also break CI until they are removed from `.github/kyverno-baseline.yaml`.
- Persistent live-cluster policy failures are mirrored into GitHub issues by `infra/kyverno/automation/github-issue-sync-cronjob.yaml` once they remain present past the configured threshold.

To intentionally refresh the accepted baseline after review:

```bash
python3 scripts/ci/kyverno_gate.py generate-baseline \
  --repo-root . \
  --cluster-root clusters/home \
  --baseline .github/kyverno-baseline.yaml \
  --kyverno-bin /path/to/kyverno \
  --work-dir .work/kyverno-gate
```

To verify the gate locally without changing the baseline:

```bash
python3 scripts/ci/kyverno_gate.py check \
  --repo-root . \
  --cluster-root clusters/home \
  --baseline .github/kyverno-baseline.yaml \
  --kyverno-bin /path/to/kyverno \
  --work-dir .work/kyverno-gate
```

Manual reconcile examples:

```bash
flux get kustomizations -A
flux get sources git -A

flux -n flux-system reconcile kustomization apps-wotlk --with-source
flux -n flux-system reconcile kustomization apps-arr --with-source
flux -n flux-system reconcile kustomization infrastructure --with-source
```

## Bootstrap guide (follow-along)

This is a practical walk-through for building a similar setup.

### 1) Prereqs

Install these tools locally:

- `kubectl`
- `flux`
- `sops`
- `age`
- `talosctl` (if you want a Talos-based cluster)

### 2) Create the cluster

Create a Kubernetes cluster (Talos or your preferred distro) and ensure you can:

```bash
kubectl get nodes
```

If you are using Talos, follow the official Talos install guide, generate machine configs, and bootstrap the control plane before continuing.

### 3) Bootstrap Flux

Flux creates the `flux-system` namespace and points it at this repo. Example (adjust to your Git host):

```bash
flux bootstrap github \
  --owner <your-user-or-org> \
  --repository <your-repo> \
  --branch main \
  --path clusters/home \
  --personal
```

This generates `clusters/home/flux-system` and keeps the cluster synced to `main`.

### 4) Configure SOPS + age

This repo expects SOPS to decrypt secrets using an age key named `sops-age` in `flux-system`.

Create a keypair and install the private key in the cluster:

```bash
age-keygen -o age.agekey
kubectl -n flux-system create secret generic sops-age \
  --from-file=age.agekey=./age.agekey
```

Update `.sops.yaml` to use your public age recipient, then re-encrypt any secrets you plan to keep:

```bash
sops -e -i secrets/<your-secret>.enc.yaml
```

### 5) Customize the infrastructure

You will need to edit manifests to match your environment:

- Storage: `infra/longhorn-node-disks/*.yaml` (node names, disk paths)
- Public access: `infra/cloudflared/config.yaml` (hostnames and services)
- VPN: `infra/wireguard/*`
- Backups: `infra/backups/*` and `secrets/*`
- Namespaces: `infra/namespaces/*`

### 6) Add or remove apps

Apps live under `apps/` and are wired in via Kustomizations in `clusters/home/apps/*`.

To add a new app:

1. Create a new `apps/<name>` folder with manifests.
2. Add it to a `clusters/home/apps/*/kustomization.yaml` (or create a new one).
3. Commit and push.

### 7) Service health and public uptime

- In-cluster workload and endpoint checks: `infra/observability/gatus-helmrelease.yaml`
- Public uptime status and its GitHub Actions history: [RovxBot/homelab-uptime](https://github.com/RovxBot/homelab-uptime)
- Add public checks in the separate repository's `.upptimerc.yml`; do not add generated Upptime history to this GitOps repository.

## Public repo notes

- This is not a production template or an official reference architecture.
- No plaintext secrets are stored here; secrets are SOPS-encrypted.
- Use at your own risk and adapt to your environment.

## Docs

- Contributing: `CONTRIBUTING.md`
- License: `LICENSE`
- WotLK server details and images: `WOTLK.md`
