# WotLK Server Stack

This repo contains my WotLK (AzerothCore + Playerbots) deployment and related tooling. It is designed for my homelab and may require adjustments for other environments.

## What is included

- Auth server, world server, and MariaDB statefulset
- World/auth configuration templates
- CronJobs for realm updates, bot maintenance, and custom content
- Optional account management portal assets

Key manifests live under:

- `apps/wotlk/worldserver-deployment.yaml`
- `apps/wotlk/authserver-deployment.yaml`
- `apps/wotlk/mariadb-statefulset.yaml`
- `apps/wotlk/worldserver-config.yaml`
- `apps/wotlk/authserver-config.yaml`

## Prebuilt images

Images are published to GHCR under:

- `ghcr.io/rovxbot/homelab-infra/azerothcore-wotlk`

Tags vary by build and component. The deployments in `apps/wotlk/` reference the exact image tags used by this cluster. To update:

1. Build/publish new images.
2. Update the image tags in:
   - `apps/wotlk/worldserver-deployment.yaml`
   - `apps/wotlk/authserver-deployment.yaml`
   - `apps/wotlk/db-import-job.yaml` (if used)
3. Commit + push to `main` so Flux applies the changes.

## Licensing and upstreams

This repo is MIT licensed (see `LICENSE`) and is free to use. Upstream components have their own licenses and must be respected:

- AzerothCore: https://github.com/azerothcore/azerothcore-wotlk
- Playerbots: https://github.com/mod-playerbots/mod-playerbots

When using prebuilt images or module content, ensure you comply with all upstream licenses and terms.
