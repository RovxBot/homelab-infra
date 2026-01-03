#!/usr/bin/env bash
set -euo pipefail

# WotLK port forwarding on Oracle VPS (public -> WireGuard -> home metal7).
#
# Assumptions:
# - VPS public interface: ens3
# - WireGuard interface on VPS: wg0
# - Home server IP (metal7): 192.168.1.197
# - Required public ports:
#   - 3724/TCP (auth)
#   - 443/TCP  (world via VPS forward -> 8085)
#
# This script:
# - DNATs public traffic on the VPS to metal7 via wg0 (world is exposed on 443)
# - SNATs (MASQUERADE) so replies return through the VPS (no asymmetric routing)
# - Inserts FORWARD allows before Oracle's default REJECT

PUB_IFACE="${PUB_IFACE:-ens3}"
WG_IFACE="${WG_IFACE:-wg0}"
HOME_IP="${HOME_IP:-192.168.1.197}"

# Public -> backend mappings
# format: "public_port:backend_port"
PORT_MAP=(
  "3724:3724"
  "443:8085"
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
  iptables -t nat -D PREROUTING -i "$PUB_IFACE" -p tcp --dport 8085 -j DNAT --to-destination "${HOME_IP}:8085" 2>/dev/null || true
  iptables -t nat -D POSTROUTING -o "$WG_IFACE" -p tcp -d "$HOME_IP" --dport 8085 -j MASQUERADE 2>/dev/null || true
  iptables -D FORWARD -i "$PUB_IFACE" -o "$WG_IFACE" -p tcp -d "$HOME_IP" --dport 8085 -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
  iptables -D FORWARD -i "$WG_IFACE" -o "$PUB_IFACE" -p tcp -s "$HOME_IP" --sport 8085 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
}

cleanup_8085

for mapping in "${PORT_MAP[@]}"; do
  p="${mapping%%:*}"
  backend="${mapping##*:}"
  # Allow inbound to VPS (before INPUT REJECT)
  add_rule filter INPUT -i "$PUB_IFACE" -p tcp --dport "$p" -j ACCEPT

  # DNAT public -> home IP over WireGuard
  add_rule nat PREROUTING -i "$PUB_IFACE" -p tcp --dport "$p" -j DNAT --to-destination "${HOME_IP}:${backend}"

  # Allow forwarding internet -> wg0 (before FORWARD REJECT)
  add_rule filter FORWARD -i "$PUB_IFACE" -o "$WG_IFACE" -p tcp -d "$HOME_IP" --dport "$backend" -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT
  add_rule filter FORWARD -i "$WG_IFACE" -o "$PUB_IFACE" -p tcp -s "$HOME_IP" --sport "$backend" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

  # SNAT so home replies return via wg0 to VPS
  add_rule nat POSTROUTING -o "$WG_IFACE" -p tcp -d "$HOME_IP" --dport "$backend" -j MASQUERADE
done

echo "Rules installed."
iptables -t nat -S | sed -n '1,120p'
echo "---"
iptables -S INPUT | sed -n '1,80p'
echo "---"
iptables -S FORWARD | sed -n '1,80p'
