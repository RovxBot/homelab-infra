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
  description = "OCI instance shape for Matrix."
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "matrix_image_ocid" {
  description = "Optional region-specific Matrix image OCID. Leave empty to choose the latest compatible Ubuntu image."
  type        = string
  default     = ""
}

variable "matrix_ocpus" {
  description = "OCPUs for a Flex Matrix shape."
  type        = number
  default     = 2
}

variable "matrix_memory_gbs" {
  description = "Memory for a Flex Matrix shape."
  type        = number
  default     = 8
}

variable "matrix_server_name" {
  description = "Public Matrix hostname, for example matrix.example.com."
  type        = string
}

variable "matrix_acme_email" {
  description = "Email address used by Caddy for Matrix TLS certificates."
  type        = string
}

variable "matrix_report_stats" {
  description = "Whether Synapse reports anonymized usage statistics."
  type        = bool
  default     = false
}

variable "matrix_turn_min_port" {
  description = "Lowest UDP TURN relay port."
  type        = number
  default     = 49152
}

variable "matrix_turn_max_port" {
  description = "Highest UDP TURN relay port."
  type        = number
  default     = 49200
}

variable "matrix_operating_system" {
  description = "Operating system name for automatic image selection."
  type        = string
  default     = "Canonical Ubuntu"
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

variable "freeform_tags" {
  description = "Optional tags applied to Matrix resources."
  type        = map(string)
  default = {
    project = "k8s"
  }
}
