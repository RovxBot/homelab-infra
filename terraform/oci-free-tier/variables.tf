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

variable "teamspeak_enabled" {
  description = "Whether to provision the TeamSpeak 6 instance."
  type        = bool
  default     = true
}

variable "teamspeak_instance_name" {
  description = "Display name for the TeamSpeak 6 instance."
  type        = string
  default     = "teamspeak6"
}

variable "teamspeak_shape" {
  description = "OCI shape for the TeamSpeak 6 instance."
  type        = string
  default     = "VM.Standard.E2.1.Micro"
}

variable "teamspeak_image_ocid" {
  description = "Region-specific image OCID for the TeamSpeak 6 instance. Defaults to the WireGuard image when empty."
  type        = string
  default     = ""
}

variable "teamspeak_ocpus" {
  description = "OCPU count for the TeamSpeak 6 instance when using a Flex shape."
  type        = number
  default     = 1
}

variable "teamspeak_memory_gbs" {
  description = "Memory size in GB for the TeamSpeak 6 instance when using a Flex shape."
  type        = number
  default     = 1
}

variable "teamspeak_voice_port" {
  description = "Public UDP voice port for TeamSpeak 6."
  type        = number
  default     = 9987
}

variable "teamspeak_filetransfer_port" {
  description = "Public TCP file transfer port for TeamSpeak 6."
  type        = number
  default     = 30033
}
