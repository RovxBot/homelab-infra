#!/usr/bin/env bash
set -euo pipefail

# WotLK port forwarding on Oracle VPS (public -> WireGuard -> home metal7).
#
# Assumptions:
# - VPS public interface: ens3
# - WireGuard interface on VPS: wg0
# - Home server IP (metal7): 192.168.1.197
# - Required ports:
#   - 3724/TCP (auth)
#   - 8085/TCP (world)
#
# This script:
# - DNATs public traffic on the VPS to metal7 via wg0
# - SNATs (MASQUERADE) so replies return through the VPS (no asymmetric routing)
# - Inserts FORWARD allows before Oracle's default REJECT

PUB_IFACE="${PUB_IFACE:-ens3}"
WG_IFACE="${WG_IFACE:-wg0}"
HOME_IP="${HOME_IP:-192.168.1.197}"

PORTS_TCP=(
  3724
  8085
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

for p in "${PORTS_TCP[@]}"; do
  # Allow inbound to VPS (before INPUT REJECT)
  add_rule filter INPUT -i "$PUB_IFACE" -p tcp --dport "$p" -j ACCEPT

  # DNAT public -> home IP over WireGuard
  add_rule nat PREROUTING -i "$PUB_IFACE" -p tcp --dport "$p" -j DNAT --to-destination "${HOME_IP}:${p}"

  # Allow forwarding internet -> wg0 (before FORWARD REJECT)
  add_rule filter FORWARD -i "$PUB_IFACE" -o "$WG_IFACE" -p tcp -d "$HOME_IP" --dport "$p" -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT
  add_rule filter FORWARD -i "$WG_IFACE" -o "$PUB_IFACE" -p tcp -s "$HOME_IP" --sport "$p" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

  # SNAT so home replies return via wg0 to VPS
  add_rule nat POSTROUTING -o "$WG_IFACE" -p tcp -d "$HOME_IP" --dport "$p" -j MASQUERADE
done

echo "Rules installed."
iptables -t nat -S | sed -n '1,120p'
echo "---"
iptables -S INPUT | sed -n '1,80p'
echo "---"
iptables -S FORWARD | sed -n '1,80p'
