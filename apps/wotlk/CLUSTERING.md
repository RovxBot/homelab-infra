# ToCloud9 clustering cutover

The upstream `test-staging` cluster feature is implemented by
[ToCloud9](https://github.com/walkline/ToCloud9). It is not safe to scale the
legacy worldserver Deployment directly: clients must instead enter through the
ToCloud9 gateway, which performs routing and becomes the authentication
boundary for clustered worldservers.

This directory stages ToCloud9 `v0.1.0`'s coordination services through Flux.
The two public entrypoints are intentionally `replicas: 0`, and the existing
worldserver remains non-clustered, so applying this change does not alter the
running realm.

## Prerequisites

1. Run the **Build AzerothCore WotLK images** workflow with `build_scope=all`.
   The image Dockerfile now downloads the architecture-matched, checksum-locked
   `libsidecar` 1.0.0 release and builds with `USE_REAL_LIBSIDECAR=ON`.
2. Merge the workflow's image-tag PR and check that the new worldserver logs
   include `libsidecar initialized successfully` in a disposable test rollout.
3. Take a consistent MariaDB backup. ToCloud9 owns shared player/item/instance
   GUID allocation and introduces Redis/NATS state, so do not use an existing
   production database as the first test target without a recovery point.
4. Confirm that `192.168.1.197:3724` and `192.168.1.47:8085` remain the client
   addresses. The staged ToCloud9 authserver and gateway retain those current
   fixed LAN bindings.

## Cutover

Perform the following in one reviewed change, during a maintenance window:

1. Change `Cluster.Enabled` to `1` in `config/worldserver.conf` and leave
   `Cluster.IsCrossrealm=0` for this single-realm deployment.
2. The `TC9_*` environment settings below are already present in the
   `worldserver` manifest. Remove its `hostPort`/`hostIP` from the `world`
   container port, remove the `metal4` node selector, and set `replicas: 2`.
3. Scale `wotlk-tocloud9-authserver` and `wotlk-tocloud9-gateway` to `1`, then
   scale `wotlk-authserver` to `0`. The clustered worldserver port must have no
   client-reachable Service, NodePort, LoadBalancer, or HostPort.
4. Apply database migrations once before the two worldserver pods are allowed
   to start. Do not rely on concurrent per-pod `db-import` init containers for
   a cluster upgrade.
5. Verify both worldserver pods register with `tocloud9-servers-registry` and
   test map transitions, reconnects, groups, mail, auctions, and battlegrounds
   before reopening the realm.

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

Use the sidecar health endpoint (`GET /healthcheck` on port `9604`) for the
clustered worldserver readiness and liveness probes. The existing SOAP Service
can remain cluster-internal, but its requests will land on either worldserver;
avoid using it for a one-node-only administrative operation.

## Rollback

Set `Cluster.Enabled` back to `0`, scale the ToCloud9 gateway/authserver to
zero, restore `wotlk-authserver` to one replica, and restore the legacy
worldserver HostPort and node selector. Restore the MariaDB backup if testing
changed data that cannot be safely retained.
