data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_images" "matrix" {
  count                    = var.matrix_image_ocid == "" ? 1 : 0
  compartment_id           = var.compartment_ocid
  operating_system         = var.matrix_operating_system
  operating_system_version = var.matrix_operating_system_version != "" ? var.matrix_operating_system_version : null
  shape                    = var.matrix_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
  state                    = "AVAILABLE"
}

locals {
  availability_domain = coalesce(var.availability_domain_name, data.oci_identity_availability_domains.ads.availability_domains[0].name)
  oci_private_key     = var.private_key_pem != "" ? trimspace(var.private_key_pem) : trimspace(file(var.private_key_path))
  ssh_authorized_keys = var.ssh_public_key != "" ? trimspace(var.ssh_public_key) : trimspace(file(var.ssh_public_key_path))
  matrix_image_ocid   = var.matrix_image_ocid != "" ? var.matrix_image_ocid : data.oci_core_images.matrix[0].images[0].id
  common_tags = merge(
    {
      managed-by = "github-actions"
      stack      = "oci-matrix-free-tier"
    },
    var.freeform_tags,
  )
}

resource "oci_core_vcn" "matrix" {
  compartment_id = var.compartment_ocid
  display_name   = "matrix-free-tier"
  cidr_blocks    = [var.vcn_cidr]
  dns_label      = "matrix"
  freeform_tags  = local.common_tags
}

resource "oci_core_internet_gateway" "matrix" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.matrix.id
  display_name   = "matrix-igw"
  enabled        = true
  freeform_tags  = local.common_tags
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.matrix.id
  display_name   = "matrix-public-rt"
  freeform_tags  = local.common_tags

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.matrix.id
  }
}

# OCI's default security list permits public SSH. Matrix deliberately uses a
# separate list with no ingress rules; its NSG is the single ingress policy.
resource "oci_core_security_list" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.matrix.id
  display_name   = "matrix-public-security-list"
  freeform_tags  = local.common_tags

  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
  }
}

resource "oci_core_subnet" "public" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.matrix.id
  display_name               = "matrix-public-subnet"
  cidr_block                 = var.subnet_cidr
  dns_label                  = "public"
  route_table_id             = oci_core_route_table.public.id
  security_list_ids          = [oci_core_security_list.public.id]
  prohibit_public_ip_on_vnic = false
  freeform_tags              = local.common_tags
}

resource "random_password" "matrix_postgres_password" {
  length  = 32
  special = false
}

resource "random_password" "matrix_registration_shared_secret" {
  length  = 48
  special = false
}

resource "random_password" "matrix_turn_shared_secret" {
  length  = 48
  special = false
}

resource "oci_core_network_security_group" "matrix" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.matrix.id
  display_name   = "matrix-nsg"
  freeform_tags  = local.common_tags
}

resource "oci_core_network_security_group_security_rule" "egress_all" {
  network_security_group_id = oci_core_network_security_group.matrix.id
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
}

resource "oci_core_network_security_group_security_rule" "ssh_ingress" {
  for_each                  = toset(var.matrix_ssh_ingress_cidrs)
  network_security_group_id = oci_core_network_security_group.matrix.id
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

resource "oci_core_network_security_group_security_rule" "tcp_ingress" {
  for_each                  = toset(["80", "443", "8448", "3478"])
  network_security_group_id = oci_core_network_security_group.matrix.id
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

resource "oci_core_network_security_group_security_rule" "turn_udp_ingress" {
  network_security_group_id = oci_core_network_security_group.matrix.id
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

resource "oci_core_network_security_group_security_rule" "turn_relay_udp_ingress" {
  network_security_group_id = oci_core_network_security_group.matrix.id
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

resource "oci_core_instance" "matrix" {
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
    nsg_ids          = [oci_core_network_security_group.matrix.id]
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
  compartment_id      = var.compartment_ocid
  availability_domain = local.availability_domain
  instance_id         = oci_core_instance.matrix.id
}

data "oci_core_vnic" "matrix_primary" {
  vnic_id = data.oci_core_vnic_attachments.matrix.vnic_attachments[0].vnic_id
}
