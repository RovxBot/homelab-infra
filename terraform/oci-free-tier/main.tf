data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

locals {
  availability_domain = coalesce(var.availability_domain_name, data.oci_identity_availability_domains.ads.availability_domains[0].name)
  oci_private_key     = var.private_key_pem != "" ? trimspace(var.private_key_pem) : trimspace(file(var.private_key_path))
  ssh_authorized_keys = var.ssh_public_key != "" ? trimspace(var.ssh_public_key) : trimspace(file(var.ssh_public_key_path))
  matrix_image_ocid   = var.matrix_image_ocid != "" ? var.matrix_image_ocid : data.oci_core_images.matrix[0].images[0].id
  # Keep the manual VPS procedure and Terraform bootstrap on the same reviewed
  # edge configuration.  Move ops/wireguard with this module when extracting
  # homelab-cloud into its own repository.
  wireguard_caddyfile_b64   = base64encode(file("${path.module}/../../ops/wireguard/Caddyfile"))
  wireguard_edge_script_b64 = base64encode(file("${path.module}/../../ops/wireguard/vps-public-edge.sh"))
  common_tags = merge(
    {
      managed-by = "github-actions"
      stack      = "oci-free-tier"
    },
    var.freeform_tags
  )
}

resource "random_password" "matrix_postgres_password" {
  length  = 32
  special = false
}

data "oci_core_images" "matrix" {
  count                    = var.matrix_enabled && var.matrix_image_ocid == "" ? 1 : 0
  compartment_id           = var.compartment_ocid
  operating_system         = var.matrix_operating_system
  operating_system_version = var.matrix_operating_system_version != "" ? var.matrix_operating_system_version : null
  shape                    = var.matrix_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
  state                    = "AVAILABLE"
}

resource "random_password" "matrix_registration_shared_secret" {
  length  = 48
  special = false
}

resource "random_password" "matrix_turn_shared_secret" {
  length  = 48
  special = false
}

resource "oci_core_vcn" "main" {
  compartment_id = var.compartment_ocid
  display_name   = "homelab-free-tier"
  cidr_blocks    = [var.vcn_cidr]
  dns_label      = "homelab"
  freeform_tags  = local.common_tags
}

resource "oci_core_internet_gateway" "main" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "homelab-igw"
  enabled        = true
  freeform_tags  = local.common_tags
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "homelab-public-rt"
  freeform_tags  = local.common_tags

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.main.id
  }
}

resource "oci_core_subnet" "public" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.main.id
  display_name               = "homelab-public-subnet"
  cidr_block                 = var.subnet_cidr
  dns_label                  = "public"
  route_table_id             = oci_core_route_table.public.id
  prohibit_public_ip_on_vnic = false
  freeform_tags              = local.common_tags
}

resource "oci_core_network_security_group" "wireguard" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "wireguard-edge-nsg"
  freeform_tags  = local.common_tags
}

resource "oci_core_network_security_group_security_rule" "wireguard_egress_all" {
  network_security_group_id = oci_core_network_security_group.wireguard.id
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
}

resource "oci_core_network_security_group_security_rule" "wireguard_ssh_ingress" {
  for_each                  = toset(var.ssh_ingress_cidrs)
  network_security_group_id = oci_core_network_security_group.wireguard.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = each.value
  source_type               = "CIDR_BLOCK"

  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}

resource "oci_core_network_security_group_security_rule" "wireguard_udp_ingress" {
  network_security_group_id = oci_core_network_security_group.wireguard.id
  direction                 = "INGRESS"
  protocol                  = "17"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"

  udp_options {
    destination_port_range {
      min = var.wireguard_udp_port
      max = var.wireguard_udp_port
    }
  }
}

resource "oci_core_network_security_group_security_rule" "wireguard_tcp_ingress" {
  for_each                  = toset([for port in var.wireguard_http_ports : tostring(port)])
  network_security_group_id = oci_core_network_security_group.wireguard.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"

  tcp_options {
    destination_port_range {
      min = tonumber(each.value)
      max = tonumber(each.value)
    }
  }
}

resource "oci_core_network_security_group" "matrix" {
  count          = var.matrix_enabled ? 1 : 0
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "matrix-nsg"
  freeform_tags  = local.common_tags
}

resource "oci_core_network_security_group_security_rule" "matrix_egress_all" {
  count                     = var.matrix_enabled ? 1 : 0
  network_security_group_id = oci_core_network_security_group.matrix[0].id
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
}

resource "oci_core_network_security_group_security_rule" "matrix_ssh_ingress" {
  for_each = var.matrix_enabled ? toset(var.ssh_ingress_cidrs) : []

  network_security_group_id = oci_core_network_security_group.matrix[0].id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = each.value
  source_type               = "CIDR_BLOCK"

  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}

resource "oci_core_network_security_group_security_rule" "matrix_http_ingress" {
  for_each = var.matrix_enabled ? toset(["80", "443", "8448", "3478"]) : []

  network_security_group_id = oci_core_network_security_group.matrix[0].id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"

  tcp_options {
    destination_port_range {
      min = tonumber(each.value)
      max = tonumber(each.value)
    }
  }
}

resource "oci_core_network_security_group_security_rule" "matrix_turn_udp_ingress" {
  count                     = var.matrix_enabled ? 1 : 0
  network_security_group_id = oci_core_network_security_group.matrix[0].id
  direction                 = "INGRESS"
  protocol                  = "17"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"

  udp_options {
    destination_port_range {
      min = 3478
      max = 3478
    }
  }
}

resource "oci_core_network_security_group_security_rule" "matrix_turn_relay_udp_ingress" {
  count                     = var.matrix_enabled ? 1 : 0
  network_security_group_id = oci_core_network_security_group.matrix[0].id
  direction                 = "INGRESS"
  protocol                  = "17"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"

  udp_options {
    destination_port_range {
      min = var.matrix_turn_min_port
      max = var.matrix_turn_max_port
    }
  }
}

resource "oci_core_instance" "wireguard" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.availability_domain
  display_name        = var.wireguard_instance_name
  shape               = var.wireguard_shape
  freeform_tags       = local.common_tags

  dynamic "shape_config" {
    for_each = can(regex("Flex$", var.wireguard_shape)) ? [1] : []
    content {
      ocpus         = var.wireguard_ocpus
      memory_in_gbs = var.wireguard_memory_gbs
    }
  }

  source_details {
    source_type = "image"
    source_id   = var.wireguard_image_ocid
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = false
    nsg_ids          = [oci_core_network_security_group.wireguard.id]
    display_name     = "${var.wireguard_instance_name}-vnic"
    hostname_label   = "wireguard"
  }

  metadata = {
    ssh_authorized_keys = local.ssh_authorized_keys
    user_data = base64encode(templatefile("${path.module}/templates/wireguard-cloud-init.yaml.tftpl", {
      wireguard_udp_port        = var.wireguard_udp_port
      wireguard_peer_config_b64 = base64encode(var.wireguard_peer_config)
      wireguard_install_caddy   = var.wireguard_install_caddy
      wireguard_caddyfile_b64   = local.wireguard_caddyfile_b64
      wireguard_edge_script_b64 = local.wireguard_edge_script_b64
    }))
  }
}

data "oci_core_vnic_attachments" "wireguard" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.availability_domain
  instance_id         = oci_core_instance.wireguard.id
}

data "oci_core_vnic" "wireguard_primary" {
  vnic_id = data.oci_core_vnic_attachments.wireguard.vnic_attachments[0].vnic_id
}

data "oci_core_private_ips" "wireguard_primary" {
  vnic_id = data.oci_core_vnic.wireguard_primary.vnic_id
}

resource "oci_core_public_ip" "wireguard" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.wireguard_instance_name}-public-ip"
  lifetime       = "RESERVED"
  private_ip_id = one([
    for private_ip in data.oci_core_private_ips.wireguard_primary.private_ips :
    private_ip.id if private_ip.is_primary
  ])
}

resource "oci_core_instance" "matrix" {
  count               = var.matrix_enabled ? 1 : 0
  compartment_id      = var.compartment_ocid
  availability_domain = local.availability_domain
  display_name        = var.matrix_instance_name
  shape               = var.matrix_shape
  freeform_tags       = local.common_tags

  dynamic "shape_config" {
    for_each = can(regex("Flex$", var.matrix_shape)) ? [1] : []
    content {
      ocpus         = var.matrix_ocpus
      memory_in_gbs = var.matrix_memory_gbs
    }
  }

  source_details {
    source_type = "image"
    source_id   = local.matrix_image_ocid
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = true
    nsg_ids          = [oci_core_network_security_group.matrix[0].id]
    display_name     = "${var.matrix_instance_name}-vnic"
    hostname_label   = "matrix"
  }

  metadata = {
    ssh_authorized_keys = local.ssh_authorized_keys
    user_data = base64encode(templatefile("${path.module}/templates/matrix-cloud-init.yaml.tftpl", {
      matrix_server_name                = var.matrix_server_name
      matrix_acme_email                 = var.matrix_acme_email
      matrix_report_stats               = var.matrix_report_stats ? "yes" : "no"
      matrix_postgres_password          = random_password.matrix_postgres_password.result
      matrix_registration_shared_secret = random_password.matrix_registration_shared_secret.result
      matrix_turn_shared_secret         = random_password.matrix_turn_shared_secret.result
      matrix_turn_min_port              = var.matrix_turn_min_port
      matrix_turn_max_port              = var.matrix_turn_max_port
    }))
  }
}

data "oci_core_vnic_attachments" "matrix" {
  count               = var.matrix_enabled ? 1 : 0
  compartment_id      = var.compartment_ocid
  availability_domain = local.availability_domain
  instance_id         = oci_core_instance.matrix[0].id
}

data "oci_core_vnic" "matrix_primary" {
  count   = var.matrix_enabled ? 1 : 0
  vnic_id = data.oci_core_vnic_attachments.matrix[0].vnic_attachments[0].vnic_id
}
