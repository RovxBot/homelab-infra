# Koito with Last.fm as the source

Players continue scrobbling to **Last.fm user `rovbot`**. Multi-Scrobbler polls
that account and submits listens to Koito over its internal ListenBrainz API:

```text
Music players → Last.fm (rovbot) → Multi-Scrobbler → Koito
```

Koito is available at `https://koito.cooked.beer`, using its local `rovbot`
account. Native Entra OAuth/OIDC and trusted authentication headers are not
supported by Koito v0.3.2. Cloudflare Access with Entra is a possible future
website gate; it does not sign in to Koito's administration account. The
chosen initial setup uses Koito's own login and `KOITO_LOGIN_GATE=true`.

## Deployment and persistence

- Flux: `apps-koito`, source `apps/koito`, depends on `infrastructure`.
- Koito: pinned v0.3.2 image, one replica, Recreate updates, UID/GID 1000.
- `koito-data`: 20 GiB Longhorn volume mounted at `/etc/koito`; includes the
  SQLite database, import files, uploaded artwork and image cache.
- `koito-lastfm-bridge-data`: 1 GiB Longhorn volume mounted at `/config`;
  preserves Multi-Scrobbler's database, duplicate tracking and Last.fm session.
- Multi-Scrobbler: pinned 0.19.2 image; its web UI is available only through a
  localhost port-forward. The upstream Node entry point runs directly as a
  non-root user, bypassing the image's root-only s6 initialization.
- The bridge starts at zero replicas until its Koito token is configured.
  The API key and shared secret are in `secrets/koito.enc.yaml`.

Neither the bridge nor Koito forwards listens back to Last.fm. The sole
configured source is Last.fm; the sole client is Koito. Keep existing player
scrobbling settings unchanged, and avoid adding a second direct Koito source
for the same plays.

The new PVCs use the existing Longhorn replication and backup configuration.
Verify their backup coverage after provisioning, using the existing
[volume backup runbook](longhorn-volume-backups.md). Before a version upgrade
or large import, take and verify a backup of `koito-data`; single-instance
SQLite does not support scaling this deployment to multiple writers.

## Initial rollout

Merge the reviewed GitOps change, then check:

```bash
kubectl -n flux-system get kustomization apps-koito
kubectl -n koito rollout status deployment/koito --timeout=10m
kubectl -n koito get pvc
```

The tunnel route is declared in `infra/cloudflared/config.yaml`. If DNS does
not already cover this hostname, create a **proxied CNAME** for
`koito.cooked.beer` pointing to
`19dd24d6-5e63-4ba3-a23c-fbebc7312e3f.cfargotunnel.com`. Do not add a NodePort
or a WAN port-forward. Confirm the HTTPS URL loads and that statistics require
login. Gatus and Homepage check `/apis/web/v1/health` internally.

The initial password is random and SOPS-encrypted. Retrieve it privately from
the Secret, store it in the password manager, and sign in as `rovbot`:

```bash
kubectl -n koito get secret koito \
  -o jsonpath='{.data.KOITO_DEFAULT_PASSWORD}' | base64 --decode
```

Koito's Settings > Account lets you change the password. The environment
variable only initializes an empty database; changing it later does not reset
the account password. `KOITO_LOG_LEVEL=warn` intentionally suppresses an
upstream INFO log that prints the bootstrap password on first startup. Do
not enable INFO/DEBUG during the initial creation of the account.

## Connect Last.fm to the bridge

Create a [Last.fm API account](https://www.last.fm/api/account/create) with:

- Application homepage: `https://koito.cooked.beer`
- Callback URL: `http://localhost:9078/lastfm/callback`

Using the existing SOPS workflow, keep the API key and shared secret in
`LASTFM_API_KEY` and `LASTFM_SHARED_SECRET` in `secrets/koito.enc.yaml`. Do not
commit the plaintext account details. Koito also uses `LASTFM_API_KEY` for
artwork; restart Koito after a later change to that value.

In Koito Settings > API Keys, create a dedicated key labelled
`Last.fm bridge`. Put its value in `KOITO_API_TOKEN` in the same encrypted
Secret. The token authenticates the internal bridge even with the login gate
enabled. Multi-Scrobbler's `KOITO_URL` is the application's **base URL**, with
no `/apis/listenbrainz` suffix; its native Koito client adds that path.

Change `replicas` to `1` in `apps/koito/lastfm-bridge.yaml`, commit the Secret
and replica change, and wait for Flux to start the bridge. Establish:

```bash
kubectl -n koito port-forward deployment/koito-lastfm-bridge 9078:9078
```

Open `http://localhost:9078`, use the Last.fm source's authentication action,
sign in to **rovbot**, and authorize the application. The localhost callback
reaches the bridge through this port-forward. Its session persists on the
bridge PVC, so subsequent restarts do not require authorization again.

Until authorization succeeds, the bridge's readiness probe and Flux health
check can report not ready. Its TCP liveness probe deliberately leaves it
running so the authentication flow can be completed. Once authenticated,
check `http://localhost:9078/api/health` returns 200 and both the Last.fm source
and Koito client are ready; then close the port-forward.

Play a track through an existing player. Confirm it appears first in Last.fm,
then in Koito with the same timestamp, and confirm restarting the bridge does
not add another copy. The bridge uses its persistent history plus Koito's
existing listens for duplicate checks. Koito also ignores identical
track/timestamp inserts; renamed tracks or different metadata can still need
manual reconciliation.

## Import all history

Start the live bridge before the full export to cover plays during a long
import. Use the included exporter, which reads every page of
`user.getRecentTracks`, fixes the end timestamp, excludes now-playing entries,
retries temporary failures, and verifies the completed count against Last.fm.
Only a complete export is published. It never writes scrobbles to Last.fm.

```bash
export LASTFM_API_KEY="$(kubectl -n koito get secret koito \
  -o jsonpath='{.data.LASTFM_API_KEY}' | base64 --decode)"
python3 scripts/koito-export-lastfm.py \
  --user rovbot --output /tmp/rovbot-recenttracks.json
unset LASTFM_API_KEY
```

Keep the export outside Git; it is personal listening history. Select the
current Koito Pod, copy using a temporary name that Koito does not recognize,
then rename and restart:

```bash
KOITO_POD="$(kubectl -n koito get pod -l app=koito \
  -o jsonpath='{.items[0].metadata.name}')"
kubectl -n koito cp /tmp/rovbot-recenttracks.json \
  "$KOITO_POD:/etc/koito/import/lastfm-upload.part"
kubectl -n koito exec "$KOITO_POD" -- mv \
  /etc/koito/import/lastfm-upload.part /etc/koito/import/rovbot-recenttracks.json
kubectl -n koito rollout restart deployment/koito
kubectl -n koito rollout status deployment/koito --timeout=10m
```

Health readiness indicates the server is available, not that the background
import is complete. Check that the file moves into
`/etc/koito/import_complete/rovbot-recenttracks.json` and compare Koito's
all-time listen count and earliest listen against Last.fm. Counts may differ
where Last.fm supplied malformed data or duplicate track/timestamp entries.
Check recent warning/error logs if the file stays in `import/`.

MusicBrainz remains enabled at its public one-request-per-second limit, so
large imports and artwork backfills can take hours. For an unusually large
export, raise the memory limit before import or split the export into smaller
JSON arrays of page objects. Last.fm does not supply track duration, so hours
listened may initially be incomplete; Koito's MusicBrainz backfill and future
listens can fill durations where available.

The bridge replays up to 200 recent listens on startup; this is not a complete
history recovery mechanism after a long outage. Re-run the exporter with
`--after-unix <timestamp-before-the-gap>` and a new `recenttracks` filename
to import a missed interval. Include a small overlap and verify the resulting
counts and timestamps.

## Artwork

Deezer, Cover Art Archive and MusicBrainz are enabled. The Last.fm API key
adds Last.fm artwork as another source. `KOITO_FETCH_IMAGES_DURING_IMPORT=true`
downloads artwork during imports, and Koito also attempts to fill missing
artist and album images at startup. The image cache, including source-size
WebP artwork and generated display sizes, persists under
`/etc/koito/image_cache` on the data PVC.

Availability depends on provider coverage. Last.fm frequently returns a
placeholder for artist photos; Deezer supplies many of these instead. Use
Koito's image upload feature for missing or incorrect artist/album images.
Check several artists and albums after import, and confirm their artwork
still loads after a restart. Monitor volume usage and expand the Longhorn PVC
if the cache approaches its capacity.

## Rollback

To stop polling, return the bridge to zero replicas in Git; Last.fm continues
receiving plays from existing players. To withdraw web access, remove the
Koito tunnel route and its DNS record, and remove the Homepage/Gatus entries.
Keep the application manifests and PVCs while investigating so Flux pruning
does not delete the data claims. Restore the verified data backup before
downgrading across a database or image-cache migration.

## References

- [Koito configuration](https://koito.io/reference/configuration/)
- [Koito history import format](https://koito.io/guides/importing/)
- [Multi-Scrobbler Last.fm source](https://foxxmd.github.io/multi-scrobbler/configuration/sources/lastfm-source/)
- [Multi-Scrobbler Koito client](https://foxxmd.github.io/multi-scrobbler/configuration/clients/koito/)
- [Last.fm user.getRecentTracks](https://www.last.fm/api/show/user.getRecentTracks)
