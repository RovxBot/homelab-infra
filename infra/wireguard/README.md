# WireGuard (Oracle VPS ↔ Home K8s)

This sets up a **site-to-site WireGuard tunnel** where:

- The **Oracle VPS** is the WireGuard **server** (`wg0`, `10.77.0.1/24`, UDP `51820`).
- Your home cluster runs a **single** WireGuard **client gateway** pod on `metal7` (`wg0`, `10.77.0.2/24`) that **NATs** traffic into your LAN/cluster.

## Kubernetes pieces (this repo)

- Namespace: `wireguard` (`infra/namespaces/wireguard.yaml`)
- Gateway pod: `Deployment/wireguard-client` (`infra/wireguard/wireguard-client-deployment.yaml`)
- Required secret (created manually): `Secret/wireguard-client-conf` with key `wg0.conf`.

## What you get

From the VPS you can reach home IPs (example):
- `192.168.1.190` (Talos control plane)
- `192.168.1.197` (metal7)

Then you can use `kubectl` against the cluster API over the tunnel, or port-forward to in-cluster services.

## Notes

- The gateway pod is pinned to `metal7`. Change `nodeSelector` if you want another node.
- If Talos doesn’t expose `/lib/modules` or the WireGuard kernel module, the `linuxserver/wireguard` container may not start; in that case we’ll switch to a `wireguard-go` userspace setup.
- Oracle Cloud also has a VCN/NSG firewall: you must allow inbound UDP `51820` to the VPS, otherwise the tunnel will show `sent` bytes but `0 received`.
