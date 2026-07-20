# ADR 0001: Intentional LAN NodePort access

## Status

Accepted on 2026-07-20.

## Context

Several internal services depend on direct LAN access to Kubernetes services.  In
particular, the WireGuard gateway's Caddy configuration reaches Immich and
Jellyfin through their NodePorts.  Removing NodePorts without replacing these
dependencies would break internal workflows.

The currently intentional NodePorts are:

| Service | NodePort |
| --- | ---: |
| Homepage | 30300 |
| Immich | 30283 |
| Longhorn UI | 30080 |
| Jellyfin | 32096 |
| Jellyseerr | 30055 |
| Prowlarr | 30696 |
| Radarr | 30878 |
| SABnzbd | 30880 |
| Sonarr | 30989 |
| Vaultwarden | 32080 |

Cloudflare Tunnel routes use cluster-internal Services, not these NodePorts.

## Decision

Keep the listed NodePorts available on the trusted LAN.  This is an explicitly
accepted exposure, not an accidental public-ingress mechanism.

The home router/firewall must not forward these ports from the WAN or expose
them through UPnP.  New NodePorts require a documented dependent service and
should be reviewed with this decision.

## Consequences

LAN clients can reach these services without Cloudflare Access.  That is needed
for current internal integrations, but it relies on LAN/VLAN trust boundaries.

The current Flannel CNI does not enforce Kubernetes NetworkPolicies.  A future
CNI migration to an enforcing implementation (for example, Cilium) remains a
separate maintenance-window change; it must be designed and tested before any
default-deny policy is introduced.
