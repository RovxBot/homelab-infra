# AQ40 Log Collector

This deployment is configured to capture the dedicated `playerbots_aq40` strategy logger.
The worldserver routes that logger to `AQ40.log` on the pod-local log volume, and the
`aq40-log-collector` sidecar tails that file and persists a copy under:

`/azerothcore/env/dist/data/aq40-logs/`

The collector also mirrors the live AQ40 stream to its own container stdout, so
you can follow the raid trace without touching the worldserver container logs:

```bash
kubectl -n wotlk logs deploy/wotlk-worldserver -c aq40-log-collector -f
```

To inspect archived runs inside the pod:

```bash
kubectl -n wotlk exec deploy/wotlk-worldserver -c aq40-log-collector -- \
  ls -lah /azerothcore/env/dist/data/aq40-logs
```

The current `RovxBot/mod-playerbots` master source no longer provides the
AQ40 strategy logger or its enable/throttle settings. Those obsolete settings
have been removed from `apps/wotlk/config/playerbots.conf`.

The `Appender.Aq40` and `Logger.playerbots_aq40` entries in
`apps/wotlk/config/worldserver.conf` still route any `playerbots_aq40` output to
`AQ40.log`, and the collector remains configured to archive it. New raid traces
require a module build that emits that logger; the current fork does not.
