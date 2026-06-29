#!/usr/bin/env bash
set -euo pipefail

# Public edge on the Oracle VPS.
#
# Purpose:
# - Expose HTTPS locally on the VPS for web apps like Immich (Caddy/Nginx binds 80/443).
# - Forward selected non-HTTP TCP ports over WireGuard into the home network.
#
# Default layout:
# - 80/TCP, 443/TCP terminate on the VPS itself.
# - 3724/TCP -> home authserver (WotLK)
# - 8443/TCP -> home worldserver:8085 (WotLK)
#
# Why 8443?
# - Public 443 must stay available for HTTPS if this VPS is going to front immich.cooked.beer.
# - If WotLK continues to consume public 443, you cannot also terminate HTTPS for Immich on
#   the same public IP.

PUB_IFACE="${PUB_IFACE:-ens3}"
WG_IFACE="${WG_IFACE:-wg0}"
LEGACY_HOME_IP="${HOME_IP:-}"
AUTH_HOME_IP="${WOTLK_AUTH_HOME_IP:-${LEGACY_HOME_IP:-192.168.1.197}}"
WORLD_HOME_IP="${WOTLK_WORLD_HOME_IP:-${LEGACY_HOME_IP:-192.168.1.47}}"

WEB_PORTS=(
  "${HTTP_PUBLIC_PORT:-80}"
  "${HTTPS_PUBLIC_PORT:-443}"
)
WIREGUARD_PUBLIC_PORT="${WIREGUARD_PUBLIC_PORT:-51820}"

# format: "public_port:backend_port"
FORWARD_MAP=(
  "${WOTLK_AUTH_PUBLIC_PORT:-3724}:${AUTH_HOME_IP}:${WOTLK_AUTH_BACKEND_PORT:-3724}"
  "${WOTLK_WORLD_PUBLIC_PORT:-8443}:${WORLD_HOME_IP}:${WOTLK_WORLD_BACKEND_PORT:-8085}"
)

add_rule() {
  local table="$1"
  local chain="$2"
  shift 2
  if iptables -t "$table" -C "$chain" "$@" 2>/dev/null; then
    return 0
  fi
  iptables -t "$table" -I "$chain" 1 "$@"
}

cleanup_legacy_world_443() {
  iptables -D INPUT -i "$PUB_IFACE" -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
  iptables -t nat -D PREROUTING -i "$PUB_IFACE" -p tcp --dport 443 -j DNAT --to-destination "${WORLD_HOME_IP}:8085" 2>/dev/null || true
  iptables -t nat -D POSTROUTING -o "$WG_IFACE" -p tcp -d "$WORLD_HOME_IP" --dport 8085 -j MASQUERADE 2>/dev/null || true
  iptables -D FORWARD -i "$PUB_IFACE" -o "$WG_IFACE" -p tcp -d "$WORLD_HOME_IP" --dport 8085 -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
  iptables -D FORWARD -i "$WG_IFACE" -o "$PUB_IFACE" -p tcp -s "$WORLD_HOME_IP" --sport 8085 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
}

cleanup_legacy_world_443

for web_port in "${WEB_PORTS[@]}"; do
  add_rule filter INPUT -i "$PUB_IFACE" -p tcp --dport "$web_port" -j ACCEPT
done

add_rule filter INPUT -i "$PUB_IFACE" -p udp --dport "$WIREGUARD_PUBLIC_PORT" -j ACCEPT

# Clamp MSS to PMTU for forwarded TCP SYN packets so game traffic behaves over WireGuard.
if ! iptables -t mangle -C FORWARD -o "$WG_IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null; then
  iptables -t mangle -I FORWARD 1 -o "$WG_IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
fi
if ! iptables -t mangle -C FORWARD -i "$WG_IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null; then
  iptables -t mangle -I FORWARD 1 -i "$WG_IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
fi

for mapping in "${FORWARD_MAP[@]}"; do
  public_port="${mapping%%:*}"
  remainder="${mapping#*:}"
  backend_ip="${remainder%%:*}"
  backend_port="${remainder##*:}"

  add_rule filter INPUT -i "$PUB_IFACE" -p tcp --dport "$public_port" -j ACCEPT
  add_rule nat PREROUTING -i "$PUB_IFACE" -p tcp --dport "$public_port" -j DNAT --to-destination "${backend_ip}:${backend_port}"
  add_rule filter FORWARD -i "$PUB_IFACE" -o "$WG_IFACE" -p tcp -d "$backend_ip" --dport "$backend_port" -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT
  add_rule filter FORWARD -i "$WG_IFACE" -o "$PUB_IFACE" -p tcp -s "$backend_ip" --sport "$backend_port" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  add_rule nat POSTROUTING -o "$WG_IFACE" -p tcp -d "$backend_ip" --dport "$backend_port" -j MASQUERADE
done

echo "Rules installed."
echo "--- NAT"
iptables -t nat -S | sed -n '1,160p'
echo "--- INPUT"
iptables -S INPUT | sed -n '1,120p'
echo "--- FORWARD"
iptables -S FORWARD | sed -n '1,120p'
