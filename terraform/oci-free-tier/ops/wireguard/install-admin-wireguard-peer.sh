#!/usr/bin/env bash
set -euo pipefail

# Add a least-privilege workstation peer without reading or rewriting the
# existing wg0.conf, which contains the VPS private key. The systemd drop-in
# reapplies the public-key-only peer whenever wg-quick@wg0 starts.

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

WG_INTERFACE="${WG_INTERFACE:-wg0}"
ADMIN_PUBLIC_KEY="${ADMIN_PUBLIC_KEY:?Set ADMIN_PUBLIC_KEY to the workstation WireGuard public key.}"
ADMIN_ADDRESS="${ADMIN_ADDRESS:-10.77.0.3/32}"

if [[ ! ${ADMIN_PUBLIC_KEY} =~ ^[A-Za-z0-9+/]{43}=$ ]]; then
  echo "ADMIN_PUBLIC_KEY is not a WireGuard public key." >&2
  exit 1
fi

if [[ ! ${ADMIN_ADDRESS} =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/32$ ]]; then
  echo "ADMIN_ADDRESS must be an IPv4 /32." >&2
  exit 1
fi

if ! ip link show "${WG_INTERFACE}" >/dev/null 2>&1; then
  echo "${WG_INTERFACE} is not available." >&2
  exit 1
fi

while read -r peer allowed_ips; do
  IFS=',' read -r -a prefixes <<<"${allowed_ips}"
  for prefix in "${prefixes[@]}"; do
    if [[ ${prefix} == "${ADMIN_ADDRESS}" && ${peer} != "${ADMIN_PUBLIC_KEY}" ]]; then
      echo "${ADMIN_ADDRESS} is already assigned to another peer." >&2
      exit 1
    fi
  done
done < <(wg show "${WG_INTERFACE}" allowed-ips)

dropin_dir="/etc/systemd/system/wg-quick@${WG_INTERFACE}.service.d"
dropin="${dropin_dir}/20-admin-peer.conf"
install -d -o root -g root -m 0755 "${dropin_dir}"
install -o root -g root -m 0644 /dev/stdin "${dropin}" <<EOF
[Service]
ExecStartPost=/usr/bin/wg set ${WG_INTERFACE} peer ${ADMIN_PUBLIC_KEY} allowed-ips ${ADMIN_ADDRESS}
EOF

systemctl daemon-reload
wg set "${WG_INTERFACE}" peer "${ADMIN_PUBLIC_KEY}" allowed-ips "${ADMIN_ADDRESS}"

echo "Admin peer active: ${ADMIN_ADDRESS} on ${WG_INTERFACE}."
echo "Test SSH to the VPS WireGuard address before restricting public SSH."
