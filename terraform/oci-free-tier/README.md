# OCI Free Tier Terraform

This Terraform stack provisions a replacement Oracle Cloud instance for your current Melbourne WireGuard/public-edge VPS.

## What it creates

- 1 VCN
- 1 public subnet
- 1 internet gateway
- 1 public route table
- 1 network security group
- 1 `VM.Standard.E2.1.Micro` instance

## Ports opened

WireGuard instance:

- `22/TCP` from `ssh_ingress_cidrs`
- `51820/UDP` for WireGuard
- `80/TCP`, `443/TCP`, `3724/TCP`, `8443/TCP` for your current edge use case

## Bootstrap behavior

WireGuard instance:

- Installs `wireguard` and `iptables-persistent`
- Enables IP forwarding
- Optionally installs Caddy from the official repository
- Writes the module-bundled `files/Caddyfile` and `files/vps-public-edge.sh`
- Optionally writes `/etc/wireguard/wg0.conf` and starts `wg-quick@wg0` if `wireguard_peer_config` is provided

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

Create these repository or environment secrets:

- `TF_TOKEN_app_terraform_io`
  - Terraform Cloud user or team token
  - GitHub Actions uses this to authenticate to Terraform Cloud
- `TF_OCI_FREE_TIER_TFVARS_JSON`
  - JSON object containing the stack variables except `private_key_path`, `private_key_pem`, `ssh_public_key_path`, and `ssh_public_key`
  - include the live `wireguard_peer_config` here if you want the replacement VPS to come up with the same tunnel config
- `TF_OCI_API_PRIVATE_KEY_PEM`
  - the OCI API private key content used by Terraform Cloud remote runs
- `TF_OCI_SSH_PUBLIC_KEY`
  - the SSH public key injected into the instance during Terraform Cloud remote runs

Example `TF_OCI_FREE_TIER_TFVARS_JSON` shape:

```json
{
  "tenancy_ocid": "ocid1.tenancy.oc1..example",
  "user_ocid": "ocid1.user.oc1..example",
  "fingerprint": "aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99",
  "region": "ap-melbourne-1",
  "compartment_ocid": "ocid1.compartment.oc1..example",
  "availability_domain_name": "FnnO:AP-MELBOURNE-1-AD-1",
  "wireguard_shape": "VM.Standard.E2.1.Micro",
  "wireguard_image_ocid": "ocid1.image.oc1.ap-melbourne-1.aaaaaaaayettssu2b7iwidreqlrwshrvrz5byufo64cbvusn4mdwo2nnvuya",
  "wireguard_peer_config": "[Interface]\nAddress = 10.77.0.1/24\n..."
}
```

The workflow will target:

- organization: `Cooked`
- project: `K8s`
- workspace: `homelab-oci-free-tier`

If the workspace does not already exist, Terraform Cloud can create it during initialization.

For safer applies, protect the GitHub environment `oci-free-tier` with reviewers.

## Notes

- The current live WireGuard VPS metadata reports:
  - region: `ap-melbourne-1`
  - AD: `FnnO:AP-MELBOURNE-1-AD-1`
  - shape: `VM.Standard.E2.1.Micro`
  - image: `ocid1.image.oc1.ap-melbourne-1.aaaaaaaayettssu2b7iwidreqlrwshrvrz5byufo64cbvusn4mdwo2nnvuya`
- `terraform init` and `terraform validate` succeeded locally for this stack.
- To fully replace the current Oracle VPS, you still need to provide the live `wg0.conf` content via `wireguard_peer_config`.

## Source references

- OCI instance resource docs: https://registry.terraform.io/providers/oracle/oci/latest/docs/resources/core_instance
