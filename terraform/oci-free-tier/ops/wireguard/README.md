# Oracle VPS Edge over WireGuard

This folder contains the Oracle-side pieces for exposing selected home services through the VPS.

## Intended layout

- `80/TCP` and `443/TCP` terminate on the VPS itself for web apps.
- `3724/TCP` forwards over WireGuard to the home authserver on `metal7`.
- `8443/TCP` forwards over WireGuard to the home worldserver (`8085` on `metal4`).
- `immich.cooked.beer` terminates TLS on the VPS and proxies over WireGuard to `192.168.1.197:30283`.
- `jellyfin.cooked.beer` terminates TLS on the VPS and proxies over WireGuard to `192.168.1.197:32096`.

Immich and Jellyfin are intentional public application routes. Each service's
own login is the access control; do not add Cloudflare Access in front of
these paths unless every intended client is tested with it. The Caddyfile
enforces HTTPS and response security headers, but it is not an authentication
layer. Keep both applications patched, disable unneeded self-registration,
and use unique strong user passwords with MFA where the application supports
it.

## Why the WotLK world port moves off 443

The current WotLK setup uses public `443/TCP` as a raw TCP forward to the worldserver. That conflicts with hosting `https://immich.cooked.beer` on the same VPS/public IP, because both need `443/TCP`.

If the VPS is going to front Immich, public `443` must belong to the web proxy. The WotLK realm should therefore advertise a different public world port such as `8443`.

Relevant current cluster settings:

- Realm public port: [apps/wotlk/realm-upsert-cronjob.yaml](../../../../apps/wotlk/realm-upsert-cronjob.yaml)
- Immich NodePort: [apps/immich/server.yaml](../../../../apps/immich/server.yaml)

## Files

- `vps-public-edge.sh`: iptables rules for web ingress plus WireGuard forwarding.
- `Caddyfile`: TLS reverse proxy config for `immich.cooked.beer` and `jellyfin.cooked.beer`.

`vps-public-edge.sh` accepts separate backend overrides via `WOTLK_AUTH_HOME_IP` and `WOTLK_WORLD_HOME_IP` if auth and world do not live on the same node.

## Administrative SSH access

The preferred management path is a dedicated WireGuard peer for the
administrator's workstation. A dynamic home public IP is not a problem: the
workstation initiates its UDP connection to the VPS, so it needs neither a
static address nor inbound port forwarding. Restrict that peer's client
`AllowedIPs` to the VPS's `wg0` address only; it is an SSH management path,
not a route into the home LAN.

Generate the workstation private key locally, retain it locally (or in the
administrator's password manager), and provide only its public key to the
person performing the server change. Never commit the workstation private key
or the resulting client configuration. On the current edge, use the reserved
client address `10.77.0.3/32` and restrict its client route to `10.77.0.1/32`.

Run `install-admin-wireguard-peer.sh` on the VPS as root with
`ADMIN_PUBLIC_KEY` set to that public key. It persists only the public key in
a `wg-quick@wg0` systemd drop-in; it does not read or overwrite `wg0.conf`.
After the workstation connects, test `ssh ubuntu@10.77.0.1`. Only then run
`restrict-ssh-to-wireguard.sh` as root with `SSH_MIGRATION_CONFIRMED=yes`.
The latter retains the public WireGuard listener but removes the known
unrestricted host SSH rule.

Use [sshd-hardening.conf](./sshd-hardening.conf) as
`/etc/ssh/sshd_config.d/99-homelab-hardening.conf`. Validate it with
`sshd -t` before reloading SSH. It intentionally permits the existing `ubuntu`
and `opc` accounts rather than guessing which one should be removed.

Use [fail2ban-sshd.local](./fail2ban-sshd.local) as
`/etc/fail2ban/jail.d/homelab-sshd.local` while public SSH is still open. It
contains no credentials and rate-limits repeated failed SSH authentication;
it is not a substitute for closing public SSH after the management peer works.

Do not close public `22/TCP` in the host firewall or OCI NSG until the
workstation peer connects and `ssh` over the WireGuard address succeeds. Once
it does, permit TCP/22 only on `wg0`, remove the OCI SSH ingress rule, and
retain public UDP/51820 for WireGuard. This leaves the intended public
Immich/Jellyfin routes and WotLK forwarding unchanged.

## Oracle setup order

1. Install and configure WireGuard on the VPS so `wg0` can reach the required home backend IPs, currently `192.168.1.197` and `192.168.1.47`.
2. Install Caddy on the VPS.
3. Put `Caddyfile` at `/etc/caddy/Caddyfile`.
4. Run `vps-public-edge.sh` as root to install the iptables rules.
5. Open Oracle NSG/VCN ingress for `80/TCP`, `443/TCP`, `3724/TCP`, and `8443/TCP`.
6. Point `immich.cooked.beer` and `jellyfin.cooked.beer` DNS at the VPS public IP.
7. Update the WotLK realm to advertise `8443` instead of `443`.

## Systemd bootstrap

[`homelab-public-edge.service`](./homelab-public-edge.service) replays the
reviewed firewall and forwarding rules after WireGuard starts. The Terraform
cloud-init template installs and enables it on a fresh edge boot volume when a
`wg0` peer configuration is supplied. For a running host, copy the unit, the
reviewed script, and `sshd-hardening.conf` over private SSH; validate the SSH
configuration with `sshd -t`, run `systemctl daemon-reload`, then enable the
public-edge unit. The unit requires `wg-quick@wg0.service`, so it cannot claim
success while the tunnel it forwards through is unavailable.

## Notes

- `immich.cooked.beer` and `jellyfin.cooked.beer` should be DNS-only in Cloudflare if you keep Cloudflare DNS in front of the VPS. DNS-only records do not hide or protect the VPS IP; the direct public application model above is intentional.
- The Caddy config still targets `metal7` at `192.168.1.197` for the Immich and Jellyfin NodePorts; that remains valid because those Services are exposed with `externalTrafficPolicy: Cluster`.
- The WoTLK forwarding scripts now default auth traffic to `192.168.1.197` and world traffic to `192.168.1.47`.
- Set an explicit `MTU = 1370` in the Oracle-side `wg0.conf`. OCI exposes a jumbo-MTU NIC, and letting WireGuard auto-derive an MTU from that can produce an oversized tunnel MTU for Internet-bound traffic.
- This repo still contains the legacy `vps-wotlk-forwarding.sh` script for the old `443 -> 8085` arrangement.
