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
  description = "Path to the OCI API private key on the machine running Terraform."
  type        = string
  default     = ""
}

variable "private_key_pem" {
  description = "OCI API private key content. Prefer this for Terraform Cloud remote runs."
  type        = string
  default     = ""
  sensitive   = true
}

variable "region" {
  description = "OCI region identifier, for example ap-melbourne-1."
  type        = string
  default     = "ap-melbourne-1"
}

variable "compartment_ocid" {
  description = "OCI compartment OCID where resources will be created."
  type        = string
}

variable "availability_domain_name" {
  description = "Optional availability domain name. Leave null to use the first AD in the region."
  type        = string
  default     = null
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key to inject into both instances."
  type        = string
  default     = ""
}

variable "ssh_public_key" {
  description = "SSH public key content to inject into instances. Prefer this for Terraform Cloud remote runs."
  type        = string
  default     = ""
}

variable "wireguard_shape" {
  description = "OCI shape for the WireGuard edge instance."
  type        = string
  default     = "VM.Standard.E2.1.Micro"
}

variable "wireguard_image_ocid" {
  description = "Region-specific image OCID for the WireGuard instance."
  type        = string
}

variable "vcn_cidr" {
  description = "CIDR block for the OCI VCN."
  type        = string
  default     = "10.42.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR block for the public subnet."
  type        = string
  default     = "10.42.10.0/24"
}

variable "ssh_ingress_cidrs" {
  description = "CIDR blocks allowed to SSH to the instances."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "freeform_tags" {
  description = "Optional freeform tags applied to all OCI resources in this stack."
  type        = map(string)
  default = {
    project = "k8s"
  }
}

variable "wireguard_instance_name" {
  description = "Display name for the WireGuard edge instance."
  type        = string
  default     = "wireguard-edge"
}

variable "wireguard_ocpus" {
  description = "OCPU count for the WireGuard instance."
  type        = number
  default     = 1
}

variable "wireguard_memory_gbs" {
  description = "Memory size in GB for the WireGuard instance."
  type        = number
  default     = 6
}

variable "wireguard_udp_port" {
  description = "Public UDP port for the WireGuard server."
  type        = number
  default     = 51820
}

variable "wireguard_http_ports" {
  description = "Public TCP ports exposed on the WireGuard/edge instance for reverse proxy workloads."
  type        = list(number)
  default     = [80, 443, 3724, 8443]
}

variable "wireguard_install_caddy" {
  description = "Whether to install Caddy and deploy the repo Caddyfile on the WireGuard instance."
  type        = bool
  default     = true
}

variable "wireguard_peer_config" {
  description = "Optional wg0.conf content to write to /etc/wireguard/wg0.conf on first boot."
  type        = string
  default     = ""
  sensitive   = true
}

variable "matrix_enabled" {
  description = "Whether to provision the Matrix homeserver instance."
  type        = bool
  default     = true
}

variable "matrix_instance_name" {
  description = "Display name for the Matrix homeserver instance."
  type        = string
  default     = "matrix"
}

variable "matrix_shape" {
  description = "OCI shape for the Matrix homeserver instance."
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "matrix_image_ocid" {
  description = "Optional region-specific ARM image OCID for the Matrix homeserver instance. Leave empty to auto-select the latest compatible Ubuntu image."
  type        = string
  default     = ""
}

variable "matrix_ocpus" {
  description = "OCPU count for the Matrix homeserver instance when using a Flex shape."
  type        = number
  default     = 2
}

variable "matrix_memory_gbs" {
  description = "Memory size in GB for the Matrix homeserver instance when using a Flex shape."
  type        = number
  default     = 8
}

variable "matrix_server_name" {
  description = "Public DNS hostname for the Matrix homeserver and Element Web, for example matrix.example.com."
  type        = string
  default     = ""

  validation {
    condition     = !var.matrix_enabled || var.matrix_server_name != ""
    error_message = "matrix_server_name must be set when matrix_enabled is true."
  }
}

variable "matrix_acme_email" {
  description = "Email address used by Caddy when requesting TLS certificates for the Matrix host."
  type        = string
  default     = "admin@cooked.beer"

  validation {
    condition     = !var.matrix_enabled || var.matrix_acme_email != ""
    error_message = "matrix_acme_email must be set when matrix_enabled is true."
  }
}

variable "matrix_report_stats" {
  description = "Whether Synapse should report anonymized usage statistics upstream."
  type        = bool
  default     = false
}

variable "matrix_turn_min_port" {
  description = "Lowest UDP relay port exposed by coturn."
  type        = number
  default     = 49152
}

variable "matrix_turn_max_port" {
  description = "Highest UDP relay port exposed by coturn."
  type        = number
  default     = 49200
}

variable "matrix_operating_system" {
  description = "Operating system name used when auto-selecting the Matrix image."
  type        = string
  default     = "Canonical Ubuntu"
}

variable "matrix_operating_system_version" {
  description = "Optional operating system version used when auto-selecting the Matrix image, for example 24.04. Leave empty to use the latest compatible release."
  type        = string
  default     = ""
}
