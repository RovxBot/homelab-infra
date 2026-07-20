# OCI Free Tier Terraform

This Terraform stack provisions your Melbourne Oracle Free Tier edge footprint:

- 1 WireGuard/public-edge VPS on `VM.Standard.E2.1.Micro`
- 1 Matrix VPS on `VM.Standard.A1.Flex`

## What it creates

- 1 VCN
- 1 public subnet
- 1 internet gateway
- 1 public route table
- 1 network security group for the edge VM
- 1 network security group for the Matrix VM
- 1 reserved public IP on the WireGuard edge
- 1 ephemeral public IP on the Matrix VM

## Ports opened

WireGuard instance:

- no public `22/TCP` by default; administer through a tested WireGuard peer
- `51820/UDP` for WireGuard
- `80/TCP`, `443/TCP`, `3724/TCP`, `8443/TCP` for your current edge use case

Matrix instance:

- `22/TCP` from `matrix_ssh_ingress_cidrs` (the legacy public default remains
  only until Matrix has a separately tested management path)
- `80/TCP`, `443/TCP` for Element Web and Matrix client traffic
- `8448/TCP` for Matrix federation
- `3478/TCP+UDP` for TURN
- `${matrix_turn_min_port}-${matrix_turn_max_port}/UDP` for coturn relay traffic

## Bootstrap behavior

WireGuard instance:

- Installs `wireguard` and `iptables-persistent`
- Enables IP forwarding
- Optionally installs Caddy from the official repository
- Writes the shared, reviewed `ops/wireguard/Caddyfile` and
  `ops/wireguard/vps-public-edge.sh`
- Optionally writes `/etc/wireguard/wg0.conf` and starts `wg-quick@wg0` if `wireguard_peer_config` is provided

Matrix instance:

- Installs Docker, Caddy, coturn, and jq
- Generates a Synapse config in a persistent Docker volume
- Runs `synapse`, `postgres`, and `element-web` with Docker Compose
- Configures Caddy to serve Element Web and reverse proxy `/_matrix` and `/_synapse`
- Configures coturn on the instance public IP for Matrix voice/video calls

## Inputs you must provide

- OCI API auth:
  - `tenancy_ocid`
  - `user_ocid`
  - `fingerprint`
  - `private_key_path` or `private_key_pem`
  - `region`
  - `compartment_ocid`
- WireGuard boot image:
  - `wireguard_image_ocid`
- Matrix boot image:
- SSH access:
  - `ssh_public_key_path` or `ssh_public_key`
  - `wireguard_ssh_ingress_cidrs` (empty after WireGuard administration is tested)
  - `matrix_ssh_ingress_cidrs` (set only after choosing Matrix administration)
- Matrix DNS/TLS:
  - `matrix_server_name`
  - `matrix_acme_email`

Optional Matrix sizing inputs:

- `matrix_enabled`
- `matrix_instance_name`
- `matrix_shape`
- `matrix_ocpus`
- `matrix_memory_gbs`
- `matrix_image_ocid`
- `matrix_operating_system`
- `matrix_operating_system_version`
- `matrix_report_stats`
- `matrix_turn_min_port`
- `matrix_turn_max_port`

## Usage

1. Copy the example file:

```bash
cp terraform.tfvars.example terraform.tfvars
```

2. Fill in your OCI values, the x86 image OCID for WireGuard, and the public DNS name for Matrix.

3. Leave `matrix_image_ocid` empty to auto-select the latest compatible Ubuntu ARM image for the chosen Matrix shape, or set it explicitly if you want to pin a specific image.

4. Point DNS for `matrix_server_name` at the Matrix VM public IP after apply.

5. Apply:

```bash
terraform init
terraform plan
terraform apply
```

6. Create the first Matrix admin user with the `matrix_admin_bootstrap_command` output, then run the printed `register_new_matrix_user` command over SSH.

### Managed WireGuard peer config

The Oracle `wg0.conf` bootstrap source can live in the repo as [terraform/oci-free-tier/wireguard-peer-config.enc.yaml](/Users/sam/Git/homelab-infra/terraform/oci-free-tier/wireguard-peer-config.enc.yaml). This file is SOPS-encrypted and stores `stringData.wireguard_peer_config`.

Materialize it for local Terraform runs with:

```bash
export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt
python3 terraform/oci-free-tier/materialize_terraform_secret_vars.py \
  --input terraform/oci-free-tier/wireguard-peer-config.enc.yaml \
  --output terraform/oci-free-tier/wireguard.secrets.auto.tfvars.json
terraform -chdir=terraform/oci-free-tier plan -var-file=wireguard.secrets.auto.tfvars.json
```

## Terraform Cloud

If you do not want to run Terraform locally, this repo includes [terraform-oci-free-tier.yml](/Users/sam/Git/homelab-infra/.github/workflows/terraform-oci-free-tier.yml).

Suggested Terraform Cloud sensitive variables:

- `private_key_pem`

Suggested Terraform Cloud normal variables:

- `tenancy_ocid`
- `user_ocid`
- `fingerprint`
- `compartment_ocid`
- `availability_domain_name`
- `ssh_public_key`
- `wireguard_image_ocid`
- `matrix_server_name`
- `matrix_acme_email`

Repository workflow prerequisites:

- Add a repository secret named `SOPS_AGE_KEY` containing the private age key that matches [.sops.yaml](/Users/sam/Git/homelab-infra/.sops.yaml).
- Keep the Oracle peer config in [terraform/oci-free-tier/wireguard-peer-config.enc.yaml](/Users/sam/Git/homelab-infra/terraform/oci-free-tier/wireguard-peer-config.enc.yaml) instead of a Terraform Cloud workspace variable.

## Notes

- `immich.cooked.beer` and `jellyfin.cooked.beer` are intentional direct
  public Caddy routes on this edge. They are protected by each application's
  own login, not Cloudflare Access. Keep their application authentication,
  supported versions and TLS renewal healthy; a DNS-only Cloudflare record is
  not an access-control boundary.
- `ops/wireguard/` is the canonical edge configuration used by both the
  Terraform bootstrap and the documented manual procedure. Keep that
  directory with this module when moving OCI infrastructure to
  `homelab-cloud`; do not recreate a second Caddyfile or firewall script.
- This module assumes the Matrix host name is the same host used for Synapse, Element Web, and federation, for example `matrix.example.com`.
- Synapse is configured with `enable_registration = false`; create users explicitly after bootstrap.
- coturn is exposed directly on the Matrix VM because TURN works better with a public IP than through an extra proxy layer.
- If you populate `wireguard_peer_config`, include an explicit `MTU = 1370` in the `wg0.conf` payload for this homelab path. OCI's public NIC advertises a jumbo MTU, which otherwise leads WireGuard to derive an oversized tunnel MTU on the VPS.
- `ssh_ingress_cidrs` is retained only as a migration fallback for existing
  Matrix workspace variables. Do not use it for new changes. The WireGuard
  edge uses `wireguard_ssh_ingress_cidrs`, which defaults to empty; Matrix uses
  `matrix_ssh_ingress_cidrs` and preserves the legacy value until its own
  management route is tested.

## Source references

- OCI instance resource docs: https://registry.terraform.io/providers/oracle/oci/latest/docs/resources/core_instance
- Synapse install docs: https://element-hq.github.io/synapse/latest/setup/installation.html
- Synapse Postgres docs: https://element-hq.github.io/synapse/latest/postgres.html
- Synapse reverse proxy docs: https://element-hq.github.io/synapse/latest/reverse_proxy.html
- Synapse TURN docs: https://element-hq.github.io/synapse/latest/turn-howto.html
- Element Web install docs: https://web-docs.element.dev/install.html
