# WotLK (AzerothCore) scaffold

This is a **suspended-by-default** GitOps scaffold for a private, LAN-only **3.3.5a (build 12340)** server using:

- https://github.com/azerothcore/azerothcore-wotlk

## Why this is suspended

Auth **must** listen on TCP `3724` (the client cannot specify a custom port), so we use `hostNetwork: true` on `metal7`.
That conflicts with the existing `apps/wow` (1.12) stack if it’s running.

## Build images

This repo includes a manual GitHub Actions workflow:

- `.github/workflows/azerothcore-wotlk-images.yml`
- upstream ref: `ops/wotlk-images/azerothcore.ref`
- optional modules list: `ops/wotlk-images/modules.txt`

It builds and pushes these images to GHCR:

- `ghcr.io/rovxbot/azerothcore-wotlk:authserver-<ref>`
- `ghcr.io/rovxbot/azerothcore-wotlk:worldserver-<ref>`
- `ghcr.io/rovxbot/azerothcore-wotlk:db-import-<ref>`
- `ghcr.io/rovxbot/azerothcore-wotlk:tools-<ref>`

## Database bootstrap

`db-bootstrap-cronjob.yaml` clones AzerothCore and loads:

- `data/sql/base/db_auth` → `acore_auth`
- `data/sql/base/db_characters` → `acore_characters`
- `data/sql/base/db_world` → `acore_world` (large import)

It also upserts the realm row to `192.168.1.197:8085` and marks it online.

Run once when ready:

`kubectl -n wotlk create job --from=cronjob/wotlk-db-bootstrap wotlk-db-bootstrap-1`

## Cutover steps (when ready)

1. Stop the 1.12 stack (`apps/wow`) so ports are free (3724/8085 on metal7).
2. Build images via Actions.
3. Unsuspend Flux `apps-wotlk`.
4. Run the DB bootstrap job once.
5. Add client data extraction (separate step; needs your 3.3.5a client path).

## Progression / gates

See `apps/wotlk/PROGRESSION.md` for the current **Individual Progression** tier list, level caps, and access gates.

## Playerbots pathing (recommended)

If Playerbots feel janky (stuck on hills, odd movement), generate **mmaps** once. This can take hours and will use CPU heavily while it runs:

- `kubectl -n wotlk create job --from=cronjob/wotlk-client-extract-mmaps wotlk-client-extract-mmaps-1`
