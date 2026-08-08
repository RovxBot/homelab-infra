# ToCloud9 clustering

The `test-staging` cluster implementation uses
[ToCloud9](https://github.com/walkline/ToCloud9) as its client gateway and
coordination plane. It is unsafe to scale a legacy worldserver directly: the
gateway is the only public game endpoint once clustering is enabled.

This deployment runs ToCloud9 `v0.1.0` with the existing MariaDB database,
Redis, and NATS. `wotlk-tocloud9-authserver` retains `192.168.1.197:3724` and
`wotlk-tocloud9-gateway` retains `192.168.1.47:8085`; the legacy authserver is
scaled to zero. The upstream Helm chart's `replicaCount` values for its own
authserver, gateway, and gameserver must remain quoted as `"0"`: its templates
otherwise turn a numeric zero into one. The two worldservers use anti-affinity
and have no HostPort, NodePort, LoadBalancer, or public Service.

The chart's pinned Redis `7.2.3-debian-11-r1` image is pulled from
`bitnamilegacy/redis`, because Bitnami no longer publishes that historical tag
from its primary repository.

The `tocloud9-schema` init container applies ToCloud9's four idempotent
character-database migrations before each worldserver starts. The normal
per-pod core `db-import` container is intentionally absent from the clustered
Deployment, since it is not safe to discover and apply arbitrary core/module
updates concurrently. Run a one-off database-import Job for future image
upgrades before scaling or restarting both clustered worldservers.

## Validation after a cutover

1. Confirm `HelmRelease/tocloud9` is Ready and the ToCloud9 services, Redis,
   and NATS have healthy Pods.
2. Confirm both worldserver Pods are Ready and registered in
   `tocloud9-servers-registry`; their `/healthcheck` endpoints provide the
   readiness/liveness signal.
3. Test login, reconnect, map transitions, groups, guilds, mail, auctions, and
   battlegrounds before reopening the realm.

```yaml
- name: TC9_GUID_PROVIDER_ADDRESS
  value: tocloud9-guidserver:8996
- name: TC9_SERVERS_REGISTRY_ADDRESS
  value: tocloud9-servers-registry:8999
- name: TC9_MATCHMAKING_ADDRESS
  value: tocloud9-matchmakingserver:8994
- name: TC9_NATS_URL
  value: nats://tocloud9-nats:4222
- name: TC9_GRPC_PORT
  value: "9509"
- name: TC9_HEALTH_CHECK_PORT
  value: "9604"
```

The existing SOAP Service remains cluster-internal, but its requests can land
on either worldserver; do not use it for a one-node-only administrative action.

## Rollback

Set `Cluster.Enabled` back to `0`, scale the ToCloud9 gateway/authserver to
zero, restore `wotlk-authserver` to one replica, and restore the legacy
worldserver HostPort (`192.168.1.47:8085`) and `metal4` node selector. Restore
the MariaDB backup if testing changed data that cannot be safely retained.
