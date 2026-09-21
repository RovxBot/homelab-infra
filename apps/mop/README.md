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

   It creates `dbc`, `maps`, `vmaps`, and `mmaps` on `skyfire-data-rwx`. The
   worldserver remains unavailable until all four directories are present.
4. Connect on the trusted LAN to auth at `192.168.1.47:3724`. The realm list
   advertises world traffic on `192.168.1.197:8085`. Neither is forwarded by
   the OCI public edge.

The authserver creates `skyfire_auth` automatically. The first worldserver
boot imports the checksum-verified SkyFire DB release, the core's auth and
characters base SQL, then all current updates. It can take substantially
longer than a normal server restart.

## Modules

`mod-playerbots` is the SkyFire core's pinned submodule and is enabled. Bot
creation and random logins are intentionally disabled until an operator picks
a dedicated bot-account password and desired population in
[`config/playerbots.conf`](config/playerbots.conf). Do not use a real-player
or database password for bot accounts.

`mod-ahbot` is compiled from its pinned companion repository. It stays idle
until `AuctionHouseBot.GUIDs` contains one or more unused, non-playerbot
character GUIDs and `AuctionHouseBot.EnableSeller = true`. Those characters
are dummy listing owners and must never be logged in.
