# OCI Free Tier Edge Terraform

This Terraform stack owns only the running Melbourne WireGuard/public-edge VPS
and its network. It is the source of truth for the reserved public IP,
WireGuard security group, VCN, public subnet, Caddy configuration, and public
edge firewall script.

Matrix is deliberately **not** part of this stack. Its disabled legacy
definitions remain only to avoid an unreviewed state migration. Do not set
`matrix_enabled = true`; use
[terraform/oci-matrix-free-tier](../oci-matrix-free-tier/README.md) when
Oracle ARM capacity becomes available.

## Current edge exposure

- `51820/UDP` for WireGuard.
- `80/TCP`, `443/TCP`, `3724/TCP`, and `8443/TCP` for the intended public edge
  routes.
- No public `22/TCP`: SSH is permitted only through the tested WireGuard
  administration peer, with matching host firewall and OCI NSG policy.

The direct public `immich.cooked.beer` and `jellyfin.cooked.beer` routes are an
intentional risk decision. Their application login controls remain mandatory;
a DNS-only Cloudflare record is not an access-control boundary.

## Bootstrap behaviour

The edge bootstraps WireGuard and `iptables-persistent`, enables forwarding,
and writes the reviewed files in `ops/wireguard/` to the VPS. That directory is
canonical: keep the Terraform bootstrap and any manual VPS changes aligned.

The optional `wireguard_peer_config` is stored in the encrypted
`wireguard-peer-config.enc.yaml` file. Do not place the configuration or its
private keys in `terraform.tfvars` or normal Terraform Cloud variables.

`wireguard_admin_public_key` is optional non-secret public key material for the
administrator's private `10.77.0.3/32` peer. Set it in the WireGuard HCP
Terraform workspace before any boot-volume replacement so a fresh root
restores the private-only SSH path. The corresponding private key must remain
outside this repository.

## Ubuntu LTS boot-volume replacement

The current edge must move from Ubuntu 20.04 to a pinned Canonical Ubuntu 24.04
x86_64 image. Do not use a dynamic image data source or a sequential
`do-release-upgrade`. Instead, update `wireguard_image_ocid` in the existing
WireGuard workspace only after the private management path and fresh-bootstrap
configuration are verified.

The instance resource preserves the previous boot volume during a successful
Linux image replacement. Review the HCP plan carefully: it must show only an
in-place update of `oci_core_instance.wireguard.source_details`, never VCN,
subnet, NSG, VNIC, reserved public IP, or instance destruction. Confirm the
temporary preserved boot volume remains within the tenancy's Always Free block
storage allowance, then delete it only after the new root passes WireGuard,
SSH, Caddy, firewall, Immich/Jellyfin and WotLK health checks.

Changing cloud-init source in Terraform prepares a **future fresh boot**; it
does not re-run cloud-init on the already-running VPS. Apply the reviewed unit
and SSH hardening manually over the private management path before scheduling
the boot-volume replacement.

For a local plan:

```bash
export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt
python3 terraform/oci-free-tier/materialize_terraform_secret_vars.py \
  --input terraform/oci-free-tier/wireguard-peer-config.enc.yaml \
  --output terraform/oci-free-tier/wireguard.secrets.auto.tfvars.json
terraform -chdir=terraform/oci-free-tier plan \
  -var-file=wireguard.secrets.auto.tfvars.json
```

## HCP Terraform and the GitHub workflow

The deployed workspace is `Cooked / K8s / homelab-oci-free-tier`. The workflow
normally produces a full plan. Its optional target field is for one reviewed,
exceptional repair only: inspect the targeted plan and apply, then reconcile
the wider drift rather than treating targeting as a normal workflow.

Required GitHub secrets:

- `TF_TOKEN_app_terraform_io`
- `SOPS_AGE_KEY`

The workspace needs OCI API variables (`tenancy_ocid`, `user_ocid`,
`fingerprint`, `private_key_pem`, `compartment_ocid`) and the edge SSH public
key/image variables. Keep `wireguard_ssh_ingress_cidrs = []` after the
WireGuard management peer is proven.

## Important migration note

The legacy Matrix resources and sensitive random values may still be present
in this workspace's Terraform state even though Matrix is disabled. Do not run
a broad apply just to remove them. Retire or migrate that state in a separate,
reviewed change after the independent Matrix stack and its administration and
backup design are ready.
