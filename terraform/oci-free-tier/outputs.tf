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
    teamspeak = var.teamspeak_enabled ? "ssh ubuntu@${data.oci_core_vnic.teamspeak_primary[0].public_ip_address}" : null
  }
}

output "teamspeak_instance_id" {
  description = "OCI instance OCID for the TeamSpeak 6 node."
  value       = var.teamspeak_enabled ? oci_core_instance.teamspeak[0].id : null
}

output "teamspeak_public_ip" {
  description = "Ephemeral public IP address of the TeamSpeak 6 instance."
  value       = var.teamspeak_enabled ? data.oci_core_vnic.teamspeak_primary[0].public_ip_address : null
}
