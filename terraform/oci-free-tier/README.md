# OCI Free Tier Terraform

This Terraform stack provisions your Melbourne Oracle Free Tier edge footprint:

- 1 WireGuard/public-edge VPS
- 1 TeamSpeak 6 VPS

## What it creates

- 1 VCN
- 1 public subnet
- 1 internet gateway
- 1 public route table
- 1 network security group
- 1 `VM.Standard.E2.1.Micro` instance
- 1 optional `VM.Standard.E2.1.Micro` TeamSpeak 6 instance

## Ports opened

WireGuard instance:

- `22/TCP` from `ssh_ingress_cidrs`
- `51820/UDP` for WireGuard
- `80/TCP`, `443/TCP`, `3724/TCP`, `8443/TCP` for your current edge use case

TeamSpeak 6 instance:

- `22/TCP` from `ssh_ingress_cidrs`
- `9987/UDP` for voice
- `30033/TCP` for file transfers

## Bootstrap behavior

WireGuard instance:

- Installs `wireguard` and `iptables-persistent`
- Enables IP forwarding
- Optionally installs Caddy from the official repository
- Writes the module-bundled `files/Caddyfile` and `files/vps-public-edge.sh`
- Optionally writes `/etc/wireguard/wg0.conf` and starts `wg-quick@wg0` if `wireguard_peer_config` is provided

TeamSpeak 6 instance:

- Installs Docker
- Starts the official `teamspeaksystems/teamspeak6-server:latest` container via systemd
- Persists server data in `/srv/teamspeak6`
- Uses an ephemeral public IP so the reserved public IP slot stays with the WireGuard edge

## Inputs you must provide

- OCI API auth:
  - `tenancy_ocid`
  - `user_ocid`
  - `fingerprint`
  - `private_key_path` or `private_key_pem`
  - `region`
  - `compartment_ocid`
- Instance boot image:
  - `wireguard_image_ocid`
- SSH access:
  - `ssh_public_key_path` or `ssh_public_key`

Optional TeamSpeak-specific inputs:

- `teamspeak_enabled`
- `teamspeak_instance_name`
- `teamspeak_shape`
- `teamspeak_image_ocid`
- `teamspeak_voice_port`
- `teamspeak_filetransfer_port`

## Usage

1. Copy the example file:

```bash
cp terraform.tfvars.example terraform.tfvars
```

2. Fill in your OCI values and the Melbourne x86 image OCID for the replacement VPS.

3. Apply:

```bash
terraform init
terraform plan
terraform apply
```

## GitHub Actions deployment

If you do not want to run Terraform locally, this repo now includes [terraform-oci-free-tier.yml](/Users/sam/Git/homelab-infra/.github/workflows/terraform-oci-free-tier.yml).

The workflow supports:

- automatic `plan` on pushes to `main` that touch this stack
- manual `plan` or `apply` via `workflow_dispatch`

This workflow is now wired for Terraform Cloud.

Create this GitHub Actions secret:

- `TF_TOKEN_app_terraform_io`
  - Terraform Cloud user or team token
  - GitHub Actions uses this to authenticate to Terraform Cloud

Store Terraform inputs in the Terraform Cloud workspace instead of GitHub.

Terraform Cloud sensitive variables:

- `private_key_pem`
  - OCI API private key contents
- `wireguard_peer_config`
  - full live `/etc/wireguard/wg0.conf` content

Terraform Cloud normal variables:

- `tenancy_ocid`
- `user_ocid`
- `fingerprint`
- `compartment_ocid`
- `availability_domain_name`
- `ssh_public_key`

Optional Terraform Cloud normal variables if you want them managed there instead of relying on repo defaults:

- `region`
- `wireguard_instance_name`
- `wireguard_shape`
- `wireguard_image_ocid`
- `wireguard_ocpus`
- `wireguard_memory_gbs`
- `wireguard_udp_port`
- `wireguard_http_ports`
- `wireguard_install_caddy`
- `teamspeak_enabled`
- `teamspeak_instance_name`
- `teamspeak_shape`
- `teamspeak_image_ocid`
- `teamspeak_ocpus`
- `teamspeak_memory_gbs`
- `teamspeak_voice_port`
- `teamspeak_filetransfer_port`
- `vcn_cidr`
- `subnet_cidr`
- `ssh_ingress_cidrs`
- `freeform_tags`

The workflow will target:

- organization: `Cooked`
- project: `K8s`
- workspace: `homelab-oci-free-tier`

If the workspace does not already exist, Terraform Cloud can create it during initialization.

For safer applies, protect the GitHub environment `oci-free-tier` with reviewers.

Recommended split:

- keep stable, non-secret defaults in the repo
- keep environment-specific normal values in Terraform Cloud normal variables
- keep private keys and WireGuard config only in Terraform Cloud sensitive variables

## Notes

- The current live WireGuard VPS metadata reports:
  - region: `ap-melbourne-1`
  - AD: `FnnO:AP-MELBOURNE-1-AD-1`
  - shape: `VM.Standard.E2.1.Micro`
  - image: `ocid1.image.oc1.ap-melbourne-1.aaaaaaaayettssu2b7iwidreqlrwshrvrz5byufo64cbvusn4mdwo2nnvuya`
- The official TeamSpeak 6 server image is currently x86-only, so the spare second AMD micro is the correct Always Free target.
- `terraform init` and `terraform validate` succeeded locally for this stack.
- To fully replace the current Oracle VPS, you still need to provide the live `wg0.conf` content via `wireguard_peer_config`.

## Source references

- OCI instance resource docs: https://registry.terraform.io/providers/oracle/oci/latest/docs/resources/core_instance
