#!/usr/bin/env bash
set -euo pipefail

# Legacy WotLK port forwarding on Oracle VPS (public -> WireGuard -> home backends).
#
# Assumptions:
# - VPS public interface: ens3
# - WireGuard interface on VPS: wg0
# - Authserver backend IP defaults to metal7: 192.168.1.197
# - Worldserver backend IP defaults to metal4: 192.168.1.47
# - Required public ports:
#   - 3724/TCP (auth)
#   - 8443/TCP (world via VPS forward -> 8085)
#
# This script:
# - DNATs public traffic on the VPS to the configured home backends via wg0
# - SNATs (MASQUERADE) so replies return through the VPS (no asymmetric routing)
# - Inserts FORWARD allows before Oracle's default REJECT
# - Clamps TCP MSS on forwarded SYN packets to avoid MTU issues over WireGuard

PUB_IFACE="${PUB_IFACE:-ens3}"
WG_IFACE="${WG_IFACE:-wg0}"
LEGACY_HOME_IP="${HOME_IP:-}"
AUTH_HOME_IP="${WOTLK_AUTH_HOME_IP:-${LEGACY_HOME_IP:-192.168.1.197}}"
WORLD_HOME_IP="${WOTLK_WORLD_HOME_IP:-${LEGACY_HOME_IP:-192.168.1.47}}"

# Public -> backend mappings
# format: "public_port:backend_ip:backend_port"
PORT_MAP=(
  "3724:${AUTH_HOME_IP}:3724"
  "8443:${WORLD_HOME_IP}:8085"
)

add_rule() {
  # Usage: add_rule <table> <chain> <rule...>
  local table="$1"
  local chain="$2"
  shift 2
  if iptables -t "$table" -C "$chain" "$@" 2>/dev/null; then
    return 0
  fi
  # Insert at the top to ensure we come before any default REJECT rules.
  iptables -t "$table" -I "$chain" 1 "$@"
}

cleanup_8085() {
  # Remove legacy public exposure of 8085 if present.
  iptables -D INPUT -i "$PUB_IFACE" -p tcp --dport 8085 -j ACCEPT 2>/dev/null || true
  iptables -t nat -D PREROUTING -i "$PUB_IFACE" -p tcp --dport 8085 -j DNAT --to-destination "${WORLD_HOME_IP}:8085" 2>/dev/null || true
  iptables -t nat -D POSTROUTING -o "$WG_IFACE" -p tcp -d "$WORLD_HOME_IP" --dport 8085 -j MASQUERADE 2>/dev/null || true
  iptables -D FORWARD -i "$PUB_IFACE" -o "$WG_IFACE" -p tcp -d "$WORLD_HOME_IP" --dport 8085 -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
  iptables -D FORWARD -i "$WG_IFACE" -o "$PUB_IFACE" -p tcp -s "$WORLD_HOME_IP" --sport 8085 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
}

cleanup_8085

# Clamp MSS to PMTU for forwarded TCP SYN packets (helps with some mobile/ISP paths).
if ! iptables -t mangle -C FORWARD -o "$WG_IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null; then
  iptables -t mangle -I FORWARD 1 -o "$WG_IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
fi
if ! iptables -t mangle -C FORWARD -i "$WG_IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null; then
  iptables -t mangle -I FORWARD 1 -i "$WG_IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
fi

for mapping in "${PORT_MAP[@]}"; do
  p="${mapping%%:*}"
  remainder="${mapping#*:}"
  backend_ip="${remainder%%:*}"
  backend_port="${remainder##*:}"
  # Allow inbound to VPS (before INPUT REJECT)
  add_rule filter INPUT -i "$PUB_IFACE" -p tcp --dport "$p" -j ACCEPT

  # DNAT public -> home IP over WireGuard
  add_rule nat PREROUTING -i "$PUB_IFACE" -p tcp --dport "$p" -j DNAT --to-destination "${backend_ip}:${backend_port}"

  # Allow forwarding internet -> wg0 (before FORWARD REJECT)
  add_rule filter FORWARD -i "$PUB_IFACE" -o "$WG_IFACE" -p tcp -d "$backend_ip" --dport "$backend_port" -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT
  add_rule filter FORWARD -i "$WG_IFACE" -o "$PUB_IFACE" -p tcp -s "$backend_ip" --sport "$backend_port" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

  # SNAT so home replies return via wg0 to VPS
  add_rule nat POSTROUTING -o "$WG_IFACE" -p tcp -d "$backend_ip" --dport "$backend_port" -j MASQUERADE
done

echo "Rules installed."
iptables -t nat -S | sed -n '1,120p'
echo "---"
iptables -S INPUT | sed -n '1,80p'
echo "---"
iptables -S FORWARD | sed -n '1,80p'
