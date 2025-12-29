# WotLK (AzerothCore) scaffold

This is a **suspended-by-default** GitOps scaffold for a private, LAN-only **3.3.5a (build 12340)** server using:

- https://github.com/azerothcore/azerothcore-wotlk

## Why this is suspended

Auth **must** listen on TCP `3724` (the client cannot specify a custom port), so we use `hostNetwork: true` on `metal7`.
That conflicts with the existing `apps/wow` (1.12) stack if it’s running.

## IPv6 + Cloudflare DNS (WoW Realm)

Cloudflare can be used for DNS (AAAA) but cannot proxy WoW traffic (non-HTTP). For WoW ports, Cloudflare must be **DNS-only**.

Recommended setup:
- **Account creation page**: expose via the existing `cloudflared` tunnel (HTTPS), not via public WAN ports.
- **WoW game traffic**: publish an AAAA record to a routable IPv6 address and allow only the required ports on the UDM Pro.

This repo includes a `cloudflare-ddns-ipv6` CronJob (`infra/cloudflare-ddns-ipv6/cronjob.yaml`) that:
- runs on node `metal7` using `hostNetwork`
- detects the node's preferred outbound IPv6 source address
- creates/updates an AAAA record in Cloudflare for `grimguzzler.cooked.beer` (DNS-only)

To use it:
- Set the Cloudflare API token in `secrets/cloudflare-api-token.enc.yaml` (replace `REPLACE_ME` and re-encrypt with `sops`).
- Confirm your Cloudflare zone name and record name in `infra/cloudflare-ddns-ipv6/cronjob.yaml` (`CF_ZONE_NAME` / `CF_RECORD_NAME`).
- On the UDM Pro: allow inbound IPv6 to the node IPv6 for WoW ports only (typically `TCP 3724` and `TCP 8085`).

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


## Invite code:

To get the code: kubectl -n wotlk get secret wotlk-accountmgr-invite -o jsonpath='{.data.INVITE_CODE}' | base64 -d; echo
xBJLTDCmKl
