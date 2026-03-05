# wow (private game server scaffold)

This directory provides a basic Kubernetes scaffold for a private, LAN-only TCP game server + MariaDB backend (MaNGOS Zero):

- `wow` namespace
- `wow-mariadb` StatefulSet (Longhorn PVC)
- `wow-realmd` (auth) Deployment pinned to `metal7` using `hostNetwork` (TCP 3724 on `192.168.1.197`)
- `wow-mangosd` (world) Deployment pinned to `metal7` using `hostNetwork` (TCP 8085 on `192.168.1.197`)
- `wow-db-sync` CronJob to bootstrap/migrate DB from your separate content repo

## Build image

This repo builds a container image from upstream `mangoszero/server`:

- Workflow: `.github/workflows/mangoszero-images.yml`
- Image: `ghcr.io/rovxbot/mangoszero:server-<ref>` (see `ops/wow-images/mangoszero.ref`)

## Client files (maps/vmaps/etc)

The world server needs extracted client data files (DBC/maps/vmaps/mmaps) available under the `wow-data` PVC.

This scaffold includes a suspended extraction CronJob (`wow-client-extract`). You will need:

1. Ensure the NFS path in `apps/wow/client-files-pv.yaml` matches where your client lives.
2. Run it once: `kubectl -n wow create job --from=cronjob/wow-client-extract wow-client-extract-1`
3. Wait for it to complete, then start `wow-mangosd`.

## Before enabling

1. Ensure the MaNGOS image has built and is available in GHCR.
2. Ensure your DB content repo has `migrations/` (optional) ready (see `apps/wow/db-sync-config.md`).
3. This scaffold is not currently wired into `clusters/home/kustomization.yaml`; add a Flux Kustomization first if you want it deployed.
4. Run the DB sync once (CronJob is `suspend: true` by default; run manually).

## Flux

No active Flux Kustomization for `apps/wow` exists in this repo today.
