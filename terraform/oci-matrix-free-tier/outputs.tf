output "matrix_instance_id" {
  description = "OCI instance OCID for the Matrix node."
  value       = oci_core_instance.matrix.id
}

output "matrix_vcn_id" {
  description = "OCI VCN OCID dedicated to Matrix."
  value       = oci_core_vcn.matrix.id
}

output "matrix_public_ip" {
  description = "Reserved public IP address of the Matrix instance."
  value       = oci_core_public_ip.matrix.ip_address
}

output "matrix_admin_registration_command" {
  description = "Run this command on the Matrix host after reaching it through the separately designed management path."
  value       = "docker compose -f /srv/matrix/docker-compose.yml exec synapse register_new_matrix_user -c /data/homeserver.yaml http://localhost:8008"
}
