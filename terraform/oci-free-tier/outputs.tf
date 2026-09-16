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
  description = "WireGuard administration status. Direct SSH to the public IP is deliberately disabled."
  value = {
    wireguard = "Public SSH disabled. Connect through the tested WireGuard administration peer, then SSH to the edge's WireGuard address."
    matrix    = null
  }
}

output "matrix_instance_id" {
  description = "OCI instance OCID for the Matrix node."
  value       = var.matrix_enabled ? oci_core_instance.matrix[0].id : null
}

output "matrix_public_ip" {
  description = "Ephemeral public IP address of the Matrix instance."
  value       = var.matrix_enabled ? data.oci_core_vnic.matrix_primary[0].public_ip_address : null
}

output "matrix_admin_bootstrap_command" {
  description = "SSH command that opens an interactive shell and prints the register_new_matrix_user invocation for creating the first admin user."
  value       = var.matrix_enabled ? "ssh -t ubuntu@${data.oci_core_vnic.matrix_primary[0].public_ip_address} 'docker compose -f /srv/matrix/docker-compose.yml exec synapse register_new_matrix_user -c /data/homeserver.yaml http://localhost:8008'" : null
}
