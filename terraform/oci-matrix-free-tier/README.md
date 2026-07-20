# OCI Matrix Free Tier Terraform

This is an intentionally independent Terraform root for a future Matrix host.
It has its own state and creates its own VCN, public subnet, internet gateway,
route table, security list, network security group and ARM instance. It does
not read, modify, or share resources with the WireGuard public-edge stack.

Do not run `apply` while Oracle reports an out-of-capacity error. This module
has no GitHub deployment workflow on purpose: a future capacity attempt should
be an explicit, reviewed action in a dedicated workspace, never a side effect
of a WireGuard change.

## What it creates

- One dedicated `10.43.0.0/16` VCN by default and a public subnet.
- One `VM.Standard.A1.Flex` Matrix VM, sized at 2 OCPUs and 8 GB by default.
- A minimal custom subnet security list with **no ingress rules**. This avoids
  OCI's default public SSH rule.
- A Matrix NSG allowing public `80/TCP`, `443/TCP`, `8448/TCP`, `3478/TCP`,
  `3478/UDP`, and the configured UDP TURN relay range.
- No public `22/TCP` by default. `matrix_ssh_ingress_cidrs` is empty unless a
  separately designed Matrix management path requires a temporary exception.
- Synapse, PostgreSQL, Element Web, Caddy, and coturn on the Matrix host.

The resource shape is eligible for Oracle Always Free allocation, but ARM
capacity is not guaranteed. Oracle currently documents a temporary “out of
host capacity” condition for Always Free shapes and a total Arm allowance of
2 OCPUs / 12 GB. This module's 2 OCPU / 8 GB default consumes that full CPU
allowance. A free tenancy can have at most two VCNs, so this dedicated VCN also
uses the remaining VCN slot when the current edge VCN is the first. Confirm
current account and regional limits in the OCI console before applying; this
module does not and cannot reserve capacity.

## Preconditions before an apply

1. Choose a Matrix host name and create its public DNS record after Terraform
   returns the instance address. Caddy will retry certificate issuance once DNS
   resolves to the VM.
2. Decide how the host will be administered without public SSH. OCI Bastion
   (which Oracle currently lists as free), OCI serial console, a dedicated
   Matrix-only WireGuard endpoint, or a Cloudflare Access SSH tunnel are
   reasonable choices. Test the path before relying on it.
3. Decide the encrypted off-host backup method for the Synapse and PostgreSQL
   Docker volumes. The existing Kubernetes/Backblaze backup arrangement is not
   automatically connected to this independent VM.
4. Create a new HCP Terraform workspace, for example
   `homelab-oci-matrix-free-tier`, with working directory
   `terraform/oci-matrix-free-tier`. It must never use the existing
   `homelab-oci-free-tier` WireGuard workspace or state.
5. Supply OCI API credentials as sensitive workspace variables, or use a
   local `.tfvars` file that is never committed. Terraform creates the Synapse
   database, registration, and TURN secrets and stores them as sensitive state
   values; protect access to this new workspace accordingly.

## Configuration

Copy the example and fill in the values:

```bash
cd terraform/oci-matrix-free-tier
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
```

The module accepts either `private_key_path` or the sensitive
`private_key_pem`, and either `ssh_public_key_path` or `ssh_public_key`.
For HCP Terraform, use `private_key_pem` and `ssh_public_key` workspace
variables rather than local paths.

Use a non-overlapping `vcn_cidr` and `subnet_cidr` if `10.43.0.0/16` conflicts
with another VCN in the tenancy. Keep `matrix_ssh_ingress_cidrs = []` unless a
separate administration design calls for an exact, deliberate ingress rule.

When capacity is available and all preconditions are complete, inspect the
plan and apply manually:

```bash
terraform apply
```

Then set the Matrix DNS record to the `matrix_public_ip` output, wait for Caddy
to issue TLS, reach the host through the chosen private management path, and
run the `matrix_admin_registration_command` output to create the first admin.

## Operational notes

- The module creates no SSH ingress in the subnet security list or Matrix NSG
  by default. Do not mistake a public VM IP for a management interface.
- Matrix federation and TURN require the public ports listed above; do not put
  TURN UDP relay traffic behind a HTTP reverse proxy.
- The supplied bootstrap pulls container tags named `latest`. Before making it
  a relied-on service, pin images by digest and add a deliberate update process.
- The old, disabled Matrix definitions remain in
  `terraform/oci-free-tier` only to preserve its existing state until a future
  migration/retirement plan is reviewed. Do not re-enable them.

## References

- [OCI Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- [Synapse installation](https://element-hq.github.io/synapse/latest/setup/installation.html)
- [Synapse TURN configuration](https://element-hq.github.io/synapse/latest/turn-howto.html)
