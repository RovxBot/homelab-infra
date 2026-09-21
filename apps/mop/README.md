# SkyFire MoP realm

This is a parallel SkyFire 5.4.8 (build 18414) realm. It does not modify the
running AzerothCore WotLK realm or its databases. It reuses only the existing
`wotlk-mariadb-auth` credentials because both stacks currently live in the
`wotlk` namespace; its MariaDB data and Longhorn volumes are independent.

## First deployment

1. Run the **Build SkyFire MoP images** workflow and wait for the GHCR package
   tag `core-a5a4bdbfe016e41f76618d37f91119f2fd9931de` to exist.
2. Bind `skyfire-client-files` to a static PV containing a legally obtained
   WoW 5.4.8 build 18414 client. Its `Data/` directory may be directly at the
   PV root or up to four levels below it.
3. Run the one-off extractor:

   ```bash
   kubectl -n wotlk create job --from=cronjob/skyfire-client-extract skyfire-client-extract-initial
   ```

   It creates `dbc`, `db2`, `maps`, `vmaps`, and `mmaps` on
   `skyfire-data-rwx`. The worldserver remains unavailable until all five
   directories are present.

   If the initial extractor was run before the `db2` publish fix, recover just
   those files without regenerating maps or mmaps:

   ```bash
   kubectl -n wotlk create job --from=cronjob/skyfire-client-db2-extract skyfire-client-db2-extract-initial
   kubectl -n wotlk logs -f job/skyfire-client-db2-extract-initial
   ```
4. Connect on the trusted LAN to auth at `192.168.1.47:3724`. The realm list
   advertises world traffic on `192.168.1.197:8085`. Neither is forwarded by
   the OCI public edge.

The authserver creates `skyfire_auth` automatically. The first worldserver
boot imports the checksum-verified SkyFire DB release, the core's auth and
characters base SQL, then all current updates. It can take substantially
longer than a normal server restart.

## Modules

`mod-playerbots` is the SkyFire core's pinned submodule. The initial profile
creates 250 dedicated `RNDBOT` accounts (one random level 1–90 character per
account) and keeps at most 250 bots online, with LFG fill enabled. Its generated
bot-only password is held in the SOPS-encrypted `skyfire-playerbots` Secret;
never use a real-player or database password for bot accounts.

`mod-ahbot` is compiled from its pinned companion repository. It remains idle
until the character schema exists and the owner bootstrap is run:

```bash
kubectl -n wotlk create job --from=cronjob/skyfire-ahbot-owner-bootstrap skyfire-ahbot-owner-bootstrap-initial
kubectl -n wotlk logs job/skyfire-ahbot-owner-bootstrap-initial
```

The Job prints five generated, unused owner GUIDs. Add them to
`AuctionHouseBot.GUIDs`, set `AuctionHouseBot.EnableSeller = true`, then commit
and reconcile. Those characters are dummy listing owners and must never be
logged in or reused for playerbots. The initial market target is 5,000 listings
per auction house; buyers stay disabled until real-player supply warrants them.
