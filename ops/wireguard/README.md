# Oracle VPS Edge over WireGuard

This folder contains the Oracle-side pieces for exposing selected home services through the VPS.

## Intended layout

- `80/TCP` and `443/TCP` terminate on the VPS itself for web apps.
- `3724/TCP` forwards over WireGuard to the home authserver.
- `8443/TCP` forwards over WireGuard to the home worldserver (`8085` on `metal7`).
- `immich.cooked.beer` terminates TLS on the VPS and proxies over WireGuard to `192.168.1.197:30283`.
- `jellyfin.cooked.beer` terminates TLS on the VPS and proxies over WireGuard to `192.168.1.197:32096`.

## Why the WotLK world port moves off 443

The current WotLK setup uses public `443/TCP` as a raw TCP forward to the worldserver. That conflicts with hosting `https://immich.cooked.beer` on the same VPS/public IP, because both need `443/TCP`.

If the VPS is going to front Immich, public `443` must belong to the web proxy. The WotLK realm should therefore advertise a different public world port such as `8443`.

Relevant current cluster settings:

- Realm public port: [apps/wotlk/realm-upsert-cronjob.yaml](/Users/sam/Git/homelab-infra/apps/wotlk/realm-upsert-cronjob.yaml)
- Immich NodePort: [apps/immich/server.yaml](/Users/sam/Git/homelab-infra/apps/immich/server.yaml)

## Files

- `vps-public-edge.sh`: iptables rules for web ingress plus WireGuard forwarding.
- `Caddyfile`: TLS reverse proxy config for `immich.cooked.beer` and `jellyfin.cooked.beer`.

## Oracle setup order

1. Install and configure WireGuard on the VPS so `wg0` can reach `192.168.1.197`.
2. Install Caddy on the VPS.
3. Put `Caddyfile` at `/etc/caddy/Caddyfile`.
4. Run `vps-public-edge.sh` as root to install the iptables rules.
5. Open Oracle NSG/VCN ingress for `80/TCP`, `443/TCP`, `3724/TCP`, and `8443/TCP`.
6. Point `immich.cooked.beer` and `jellyfin.cooked.beer` DNS at the VPS public IP.
7. Update the WotLK realm to advertise `8443` instead of `443`.

## Example systemd bootstrap

```ini
[Unit]
Description=Homelab public edge rules
After=network-online.target wg-quick@wg0.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/homelab/ops/wireguard/vps-public-edge.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

## Notes

- `immich.cooked.beer` and `jellyfin.cooked.beer` should be DNS-only in Cloudflare if you keep Cloudflare DNS in front of the VPS.
- The Caddy config assumes `metal7` remains reachable at `192.168.1.197` over WireGuard and that Immich stays on NodePort `30283` while Jellyfin stays on NodePort `32096`.
- Set an explicit `MTU = 1370` in the Oracle-side `wg0.conf`. OCI exposes a jumbo-MTU NIC, and letting WireGuard auto-derive an MTU from that can produce an oversized tunnel MTU for Internet-bound traffic.
- This repo still contains the legacy `vps-wotlk-forwarding.sh` script for the old `443 -> 8085` arrangement.
