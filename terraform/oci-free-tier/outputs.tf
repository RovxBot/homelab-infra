output "wireguard_instance_id" {
  description = "OCI instance OCID for the WireGuard edge node."
  value       = oci_core_instance.wireguard.id
}

output "wireguard_public_ip" {
  description = "Public IP address of the WireGuard edge instance."
  value       = oci_core_public_ip.wireguard.ip_address
}

output "wireguard_public_ip_ocid" {
  description = "OCI OCID for the reserved public IP attached to the WireGuard edge instance."
  value       = oci_core_public_ip.wireguard.id
}

output "ssh_commands" {
  description = "Convenience SSH commands once the stack is applied."
  value = {
    wireguard = "ssh ubuntu@${oci_core_public_ip.wireguard.ip_address}"
  }
}
