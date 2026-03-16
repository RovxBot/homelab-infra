output "wireguard_instance_id" {
  description = "OCI instance OCID for the WireGuard edge node."
  value       = oci_core_instance.wireguard.id
}

output "wireguard_public_ip" {
  description = "Public IP address of the WireGuard edge instance."
  value       = data.oci_core_vnic.wireguard_primary.public_ip_address
}

output "ssh_commands" {
  description = "Convenience SSH commands once the stack is applied."
  value = {
    wireguard = "ssh ubuntu@${data.oci_core_vnic.wireguard_primary.public_ip_address}"
  }
}
