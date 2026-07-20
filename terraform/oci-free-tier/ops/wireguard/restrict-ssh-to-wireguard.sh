#!/usr/bin/env bash
set -euo pipefail

# Close the host's public SSH rule only after a separately tested WireGuard
# workstation peer is available. OCI NSG ingress must be removed separately
# through Terraform once this host-side control has succeeded.

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

if [[ ${SSH_MIGRATION_CONFIRMED:-} != "yes" ]]; then
  echo "Refusing to close public SSH. Set SSH_MIGRATION_CONFIRMED=yes after a successful SSH test over WireGuard." >&2
  exit 1
fi

WG_INTERFACE="${WG_INTERFACE:-wg0}"

if ! ip link show "${WG_INTERFACE}" >/dev/null 2>&1; then
  echo "${WG_INTERFACE} is not available." >&2
  exit 1
fi

if ! iptables -C INPUT -i "${WG_INTERFACE}" -p tcp --dport 22 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT 2>/dev/null; then
  iptables -I INPUT 1 -i "${WG_INTERFACE}" -p tcp --dport 22 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT
fi

# This is the known legacy unrestricted SSH accept rule on the OCI edge.
while iptables -C INPUT -p tcp -m state --state NEW -m tcp --dport 22 -j ACCEPT 2>/dev/null; do
  iptables -D INPUT -p tcp -m state --state NEW -m tcp --dport 22 -j ACCEPT
done

remaining_public_ssh="$(iptables -S INPUT | awk -v interface="${WG_INTERFACE}" '
  /--dport 22/ && /-j ACCEPT/ && index($0, "-i " interface " ") == 0 { print }
')"
if [[ -n ${remaining_public_ssh} ]]; then
  echo "Found an unexpected non-WireGuard SSH accept rule; public SSH may remain open:" >&2
  echo "${remaining_public_ssh}" >&2
  exit 1
fi

if command -v netfilter-persistent >/dev/null 2>&1; then
  netfilter-persistent save
fi

echo "Host firewall now permits new SSH connections only through ${WG_INTERFACE}."
echo "Apply the reviewed OCI NSG change next; do not leave a public TCP/22 rule in Terraform."
