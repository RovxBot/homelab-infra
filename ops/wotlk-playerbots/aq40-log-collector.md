# AQ40 Log Collector

This deployment captures only the dedicated `playerbots_aq40` strategy logger.
The worldserver writes `AQ40.log` to the pod-local log volume, and the
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

The AQ40 strategy logger is enabled in
`apps/wotlk/config/playerbots.conf` via:

```ini
AiPlayerbot.Aq40StrategyLog = 1
AiPlayerbot.Aq40StrategyLogThrottleMs = 1000
```

If you want less noise, increase `AiPlayerbot.Aq40StrategyLogThrottleMs`. If
you want to disable AQ40 raid tracing entirely, set `AiPlayerbot.Aq40StrategyLog = 0`.
