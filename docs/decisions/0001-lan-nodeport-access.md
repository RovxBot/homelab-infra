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
| Home Assistant | 31234 |
| Gitea HTTP recovery path | 30301 |
| Gitea SSH | 30222 |

Cloudflare Tunnel routes use cluster-internal Services, not these NodePorts.

Gitea's HTTP and SSH NodePorts support LAN/WireGuard development and recovery
when Cloudflare is unavailable. Git SSH uses public-key authentication. HTTP
remains authenticated but is plaintext on the trusted network; use SSH for Git
and a localhost `kubectl port-forward` for recovery administration. There is no
WAN forwarding rule for either Gitea port. See `docs/gitea/operations.md`.

## Decision

Keep the listed NodePorts available on the trusted LAN.  This is an explicitly
accepted exposure, not an accidental public-ingress mechanism.

Home Assistant uses its NodePort as a stable LAN endpoint for browsers and
companion apps. Its workload also uses host networking so it can receive the
LAN multicast traffic required for device discovery; the NodePort does not
replace that discovery path.

The home router/firewall must not forward these ports from the WAN or expose
them through UPnP.  New NodePorts require a documented dependent service and
should be reviewed with this decision.

## Consequences

LAN clients can reach these services without Cloudflare Access.  That is needed
for current internal integrations, but it relies on LAN/VLAN trust boundaries.

Cilium is now the primary CNI, but its policy enforcement remains intentionally
`never` until namespace-specific dependency policies have been designed and
tested. The existing NetworkPolicies therefore do not yet provide application
isolation. Do not introduce a generic default-deny policy or change enforcement
without that separate, reviewed project.
