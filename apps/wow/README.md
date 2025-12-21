# wow (private game server scaffold)

This directory provides a basic Kubernetes scaffold for a private, LAN-only TCP game server + MariaDB backend (MaNGOS Zero):

- `wow` namespace
- `wow-mariadb` StatefulSet (Longhorn PVC)
- `wow-realmd` (auth) Deployment + NodePort Service (TCP 3724)
- `wow-mangosd` (world) Deployment + NodePort Service (TCP 8085)
- `wow-db-sync` CronJob to bootstrap/migrate DB from your separate content repo

## Build image

This repo builds a container image from upstream `mangoszero/server`:

- Workflow: `.github/workflows/mangoszero-images.yml`
- Image: `ghcr.io/rovxbot/mangoszero:latest`

## Client files (maps/vmaps/etc)

The world server needs extracted client data files (DBC/maps/vmaps/mmaps) available under the `wow-data` PVC.

This scaffold includes a suspended extraction CronJob (`wow-client-extract`). You will need:

1. Ensure the NFS path in `apps/wow/client-files-pv.yaml` matches where your client lives.
2. Run it once: `kubectl -n wow create job --from=cronjob/wow-client-extract wow-client-extract-1`
3. Wait for it to complete, then start `wow-mangosd`.

## Before enabling

1. Ensure the MaNGOS image has built and is available in GHCR.
2. Ensure your DB content repo has `bootstrap/` SQL ready (see `apps/wow/db-sync-config.md`).
3. Unsuspend Flux Kustomization `apps-wow` (it is shipped as `suspend: true` so nothing deploys until you’re ready).
4. Run the DB sync once (CronJob is `suspend: true` by default; run manually).

## Flux

The Flux Kustomization is defined in `clusters/home/apps/wow-kustomization.yaml`.
