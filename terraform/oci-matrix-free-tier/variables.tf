variable "tenancy_ocid" {
  description = "OCI tenancy OCID."
  type        = string
}

variable "user_ocid" {
  description = "OCI user OCID used by Terraform."
  type        = string
}

variable "fingerprint" {
  description = "Fingerprint for the OCI API key."
  type        = string
}

variable "private_key_path" {
  description = "Path to the OCI API private key. Use private_key_pem for HCP Terraform remote execution."
  type        = string
  default     = ""
}

variable "private_key_pem" {
  description = "OCI API private key content."
  type        = string
  default     = ""
  sensitive   = true
}

variable "region" {
  description = "OCI region identifier."
  type        = string
  default     = "ap-melbourne-1"
}

variable "compartment_ocid" {
  description = "OCI compartment in which the dedicated Matrix network and instance will be created."
  type        = string
}

variable "vcn_cidr" {
  description = "CIDR block for Matrix's dedicated OCI VCN. It must not overlap an existing VCN."
  type        = string
  default     = "10.43.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR block for Matrix's dedicated public subnet. It must be inside vcn_cidr."
  type        = string
  default     = "10.43.10.0/24"
}

variable "availability_domain_name" {
  description = "Optional availability domain. Leave null to select the first domain."
  type        = string
  default     = null
}

variable "ssh_public_key_path" {
  description = "Path to the Matrix administrator SSH public key."
  type        = string
  default     = ""
}

variable "ssh_public_key" {
  description = "Matrix administrator SSH public key content."
  type        = string
  default     = ""
}

variable "matrix_instance_name" {
  description = "Display name for the Matrix homeserver instance."
  type        = string
  default     = "matrix"
}

variable "matrix_shape" {
  description = "OCI Always Free instance shape for Matrix."
  type        = string
  default     = "VM.Standard.A1.Flex"

  validation {
    condition     = var.matrix_shape == "VM.Standard.A1.Flex"
    error_message = "Matrix must use the Always Free VM.Standard.A1.Flex shape."
  }
}

variable "matrix_image_ocid" {
  description = "Optional region-specific Matrix image OCID. Leave empty to choose the latest compatible Ubuntu image."
  type        = string
  default     = ""
}

variable "matrix_ocpus" {
  description = "OCPUs for Matrix. The whole tenancy has only 2 Always Free A1 OCPUs."
  type        = number
  default     = 2

  validation {
    condition     = var.matrix_ocpus >= 1 && var.matrix_ocpus <= 2
    error_message = "Matrix must use between 1 and 2 A1 OCPUs to remain within the tenancy-wide Always Free allowance."
  }
}

variable "matrix_memory_gbs" {
  description = "Memory for Matrix. The whole tenancy has only 12 GB of Always Free A1 memory."
  type        = number
  default     = 8

  validation {
    condition     = var.matrix_memory_gbs >= 1 && var.matrix_memory_gbs <= 12
    error_message = "Matrix must use between 1 and 12 GB of A1 memory to remain within the tenancy-wide Always Free allowance."
  }
}

variable "matrix_boot_volume_size_in_gbs" {
  description = "Always Free Matrix boot-volume size. It is fixed at OCI's 50 GB default so the existing WireGuard boot volume and Matrix stay within the 200 GB tenancy allowance."
  type        = number
  default     = 50

  validation {
    condition     = var.matrix_boot_volume_size_in_gbs == 50
    error_message = "Matrix uses the 50 GB Always Free boot-volume allocation. Use a reviewed storage and backup design before changing this module."
  }
}

variable "matrix_server_name" {
  description = "Public Matrix hostname, for example matrix.example.com."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", var.matrix_server_name)) && length(var.matrix_server_name) <= 253
    error_message = "matrix_server_name must be a lowercase fully qualified DNS name. It is used in Caddy, Synapse, and coturn configuration."
  }
}

variable "matrix_acme_email" {
  description = "Email address used by Caddy for Matrix TLS certificates."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.matrix_acme_email))
    error_message = "matrix_acme_email must be a valid email address for ACME expiry notices."
  }
}

variable "matrix_report_stats" {
  description = "Whether Synapse reports anonymized usage statistics."
  type        = bool
  default     = false
}

variable "matrix_turn_min_port" {
  description = "Lowest TCP and UDP TURN relay port."
  type        = number
  default     = 49152

  validation {
    condition     = var.matrix_turn_min_port >= 1024 && var.matrix_turn_min_port <= 65535
    error_message = "matrix_turn_min_port must be an unprivileged TCP and UDP port between 1024 and 65535."
  }
}

variable "matrix_turn_max_port" {
  description = "Highest TCP and UDP TURN relay port."
  type        = number
  default     = 49200

  validation {
    condition     = var.matrix_turn_max_port >= 1024 && var.matrix_turn_max_port <= 65535
    error_message = "matrix_turn_max_port must be an unprivileged TCP and UDP port between 1024 and 65535."
  }
}

variable "matrix_operating_system" {
  description = "Always Free operating system name for automatic image selection."
  type        = string
  default     = "Canonical Ubuntu"

  validation {
    condition     = var.matrix_operating_system == "Canonical Ubuntu"
    error_message = "Matrix must use Canonical Ubuntu, which OCI lists as Always Free eligible for A1."
  }
}

variable "matrix_operating_system_version" {
  description = "Optional operating-system version for image selection."
  type        = string
  default     = ""
}

variable "matrix_ssh_ingress_cidrs" {
  description = "CIDRs allowed to SSH to Matrix. Empty by default; establish and test a separate management path before adding any CIDR."
  type        = list(string)
  default     = []
}

# These ARM64 image digests were reviewed on 2026-09-16. Keep the image
# reference immutable and let the dedicated Renovate regex manager propose
# reviewed digest updates before a new Matrix instance is launched.
variable "matrix_synapse_image" {
  description = "Digest-pinned ARM64 Synapse image."
  type        = string

  # renovate: datasource=docker depName=ghcr.io/element-hq/synapse
  default = "ghcr.io/element-hq/synapse:latest@sha256:700c047866357b5b9cd615986ff7f9aab4f2890e639def91298d4603a5313446"

  validation {
    condition     = can(regex("^ghcr\\.io/element-hq/synapse:[^@[:space:]]+@sha256:[a-f0-9]{64}$", var.matrix_synapse_image))
    error_message = "matrix_synapse_image must be a digest-pinned ghcr.io/element-hq/synapse image."
  }
}

variable "matrix_postgres_image" {
  description = "Digest-pinned ARM64 PostgreSQL image."
  type        = string

  # renovate: datasource=docker depName=postgres
  default = "postgres:16-alpine@sha256:738d1359df5aa0b6d50a9071e989c49fdd39152a2a805c6ff131bf5e2243e0b3"

  validation {
    condition     = can(regex("^postgres:[^@[:space:]]+@sha256:[a-f0-9]{64}$", var.matrix_postgres_image))
    error_message = "matrix_postgres_image must be a digest-pinned postgres image."
  }
}

variable "matrix_element_image" {
  description = "Digest-pinned ARM64 Element Web image."
  type        = string

  # renovate: datasource=docker depName=vectorim/element-web
  default = "vectorim/element-web:latest@sha256:332789537e6e608bee162a66cf9362bd45517c18cdad61f61ca13f351e32dade"

  validation {
    condition     = can(regex("^vectorim/element-web:[^@[:space:]]+@sha256:[a-f0-9]{64}$", var.matrix_element_image))
    error_message = "matrix_element_image must be a digest-pinned vectorim/element-web image."
  }
}

variable "freeform_tags" {
  description = "Optional tags applied to Matrix resources."
  type        = map(string)
  default = {
    project = "k8s"
  }
}
