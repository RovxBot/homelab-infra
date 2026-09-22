# SkyFire MoP realm

This is a parallel SkyFire 5.4.8 (build 18414) realm. It does not modify the
running AzerothCore WotLK realm or its databases. It reuses only the existing
`wotlk-mariadb-auth` credentials because both stacks currently live in the
`wotlk` namespace; its MariaDB data and Longhorn volumes are independent.

## First deployment

1. Run the **Build SkyFire MoP images** workflow and wait for the GHCR package
   tag `core-ef7d36a6c2c83db59fb81765104f38fc5e71163f` to exist. This is the
   Authnet-capable ProjectSkyfire core with the standalone DigiD702 playerbots
   module copied into `modules/mod-playerbots` at build time.
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
4. On Windows, configure **SkyFire Launcher** with client location set to the
   unmodified 5.4.8 build 18414 directory, login address `192.168.1.47`, and
   **Authnet login disabled**. Start every game launch through the launcher so
   it applies its temporary legacy-routing changes; the client files remain
   untouched. Legacy auth is on TCP `3724`; the realm list advertises world
   traffic on `192.168.1.197:8085`. Neither is forwarded by the OCI public
   edge.

The authserver creates `skyfire_auth` automatically. The first worldserver
boot imports the checksum-verified SkyFire DB release, the core's auth and
characters base SQL, then all current updates. It can take substantially
longer than a normal server restart.

## Modules

`mod-playerbots` is a separately pinned DigiD702 module copied into the
ProjectSkyfire core's `modules/mod-playerbots` directory during the image
build. The initial profile creates 250 dedicated `RNDBOT` accounts (one random
level 1–90 character per account) and keeps at most 250 bots online, with LFG
fill enabled. Its generated bot-only password is held in the SOPS-encrypted
`skyfire-playerbots` Secret; never use a real-player or database password for
bot accounts.

`mod-ahbot` is compiled from its pinned companion repository. It remains idle
until the character schema exists and the owner bootstrap is run:

```bash
kubectl -n wotlk create job --from=cronjob/skyfire-ahbot-owner-bootstrap skyfire-ahbot-owner-bootstrap-initial
kubectl -n wotlk logs job/skyfire-ahbot-owner-bootstrap-initial
```

The initial bootstrap generated owner GUIDs `1000`–`1004`, now configured in
[`config/mod_ahbot.conf`](config/mod_ahbot.conf). Those characters are dummy
listing owners and must never be logged in or reused for playerbots. The initial
market target is 5,000 listings per auction house; buyers stay disabled until
real-player supply warrants them.
