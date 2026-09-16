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
- One `VM.Standard.A1.Flex` Matrix VM, sized at 2 OCPUs and 8 GB by default,
  with a fixed 50 GB boot volume.
- One reserved public IPv4 address, so Matrix DNS and coturn's external address
  survive an instance replacement.
- A minimal custom subnet security list with **no ingress rules**. This avoids
  OCI's default public SSH rule.
- A Matrix NSG allowing public `80/TCP`, `443/TCP`, `8448/TCP`, `3478/TCP`,
  `3478/UDP`, and the configured TCP and UDP TURN relay range.
- No public `22/TCP` by default. `matrix_ssh_ingress_cidrs` is empty unless a
  separately designed Matrix management path requires a temporary exception.
- Synapse, PostgreSQL, Element Web, Caddy, and coturn on the Matrix host. The
  container images are immutable, reviewed ARM64 digests; Renovate proposes
  future digest updates for review.

## Always Free allocation and WireGuard priority

The allocation below is the complete intended OCI footprint. It deliberately
keeps WireGuard out of the shared Arm allowance.

| Workload | Shape and allocation | Boot volume | Network allocation |
| --- | --- | --- | --- |
| WireGuard/public edge | `VM.Standard.E2.1.Micro` (fixed 1/8 OCPU, 1 GB) | 50 GB | Existing VCN and reserved IPv4 |
| Matrix | `VM.Standard.A1.Flex` (2 OCPUs, 8 GB) | 50 GB | Dedicated second VCN and reserved IPv4 |
| Total | A1: **2 OCPUs / 8 GB** | **100 GB** | **2 VCNs** |

Oracle's current Always Free tenancy-wide limits are 2 A1 OCPUs, 12 GB A1
memory, 200 GB combined boot/block storage, two E2 micro instances, and two
VCNs. Thus Matrix consumes all remaining A1 CPU capacity but does not compete
with the WireGuard E2 micro instance; it leaves 4 GB of A1 memory and 100 GB
of block storage unallocated. Both Terraform roots enforce their eligible
compute shapes, and Matrix enforces its 50 GB boot disk and A1 upper bounds.

ARM capacity is still not guaranteed. Oracle documents a temporary “out of
host capacity” condition for Always Free shapes. Do not modify or replace the
WireGuard edge while seeking Matrix capacity: try another availability domain,
or wait and retry the Matrix-only plan. A free tenancy can have at most two
VCNs, so this dedicated VCN uses the last VCN slot when the current edge VCN
is the first. The module cannot see resources created outside its state, so
confirm no other A1, VCN, boot/block-volume, or paid resources exist before an
apply.

## Preconditions before an apply

1. Choose a Matrix host name and create its public DNS record after Terraform
   returns the reserved instance address. The Matrix server name cannot be
   changed after users have been created. Caddy will retry certificate issuance
   once DNS resolves to the VM.
2. Decide how the host will be administered without public SSH. OCI Bastion
   (which Oracle currently lists as free), OCI serial console, a dedicated
   Matrix-only WireGuard endpoint, or a Cloudflare Access SSH tunnel are
   reasonable choices. Test the path before relying on it.
3. Decide the encrypted off-host backup method for the Synapse and PostgreSQL
   Docker volumes. The existing Kubernetes/Backblaze backup arrangement is not
   automatically connected to this independent VM. The 50 GB boot volume is an
   Always Free capacity guardrail, not a backup strategy.
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
The automatic image lookup is restricted to Canonical Ubuntu, which OCI lists
as Always Free eligible for A1. If you pin `matrix_image_ocid`, select only a
Canonical Ubuntu image labelled **Always Free Eligible** in the OCI Console.

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
  TURN relay traffic behind a HTTP reverse proxy. The configured relay range is
  intentionally open for both TCP and UDP because Synapse advertises both TURN
  transports.
- The supplied image references include immutable ARM64 digests. The tag is
  retained only for Renovate's update lookup; Docker pulls the digest, not a
  moving `latest` image. Review and test every proposed digest update before
  using it in a new Matrix instance.
- OCI can reclaim an idle Always Free instance. Monitor this independent host
  and keep tested, encrypted backups before treating it as a primary service.
- Oracle includes 10 TB/month of outbound transfer in Always Free. Large media
  rooms can exhaust the 50 GB boot disk or that transfer allowance; do not add
  paid block volumes or egress-heavy use without an explicit cost decision.
- The old, disabled Matrix definitions remain in
  `terraform/oci-free-tier` only to preserve its existing state until a future
  migration/retirement plan is reviewed. Do not re-enable them.

## References

- [OCI Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- [OCI public IP addresses](https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/managingpublicIPs.htm)
- [Synapse installation](https://element-hq.github.io/synapse/latest/setup/installation.html)
- [Synapse TURN configuration](https://element-hq.github.io/synapse/latest/turn-howto.html)
