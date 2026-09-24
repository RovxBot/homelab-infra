# SkyFire MoP realm

This deployment runs the unmodified upstream
[ProjectSkyfire/SkyFire_548](https://github.com/ProjectSkyfire/SkyFire_548)
core for MoP 5.4.8 build 18414. It does not build or load third-party modules.

## Deployment

1. The image workflow builds the commit pinned in
   `ops/mop-images/skyfire.ref` directly from the upstream repository.
2. Bind `skyfire-client-files` to a legally obtained, unmodified WoW 5.4.8
   build 18414 client and run the extractor jobs to publish `dbc`, `db2`,
   `maps`, `vmaps`, and `mmaps` to `skyfire-data-rwx`.
3. The authserver and worldserver use SkyFire's complete distributed
   configuration templates. Kubernetes renders only database credentials and
   the worldserver's `DataDir = "/data"` requirement at runtime.
4. On Windows, configure SkyFire Launcher with the unmodified 5.4.8 client,
   login address `192.168.1.84`, and Authnet login disabled. The realm
   advertises world traffic on `192.168.1.84:8085`; legacy auth listens on
   TCP `3724`.

The image workflow checksum-verifies the official SkyFire database release.
On a fresh database, SkyFire imports its auth, characters, and world base SQL
and then applies current upstream updates.
