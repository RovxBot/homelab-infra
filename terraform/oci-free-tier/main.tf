data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

locals {
  availability_domain = coalesce(var.availability_domain_name, data.oci_identity_availability_domains.ads.availability_domains[0].name)
  ssh_authorized_keys = trimspace(file(var.ssh_public_key_path))
  wireguard_caddyfile_b64 = base64encode(file("${path.module}/../../ops/wireguard/Caddyfile"))
  wireguard_edge_script_b64 = base64encode(file("${path.module}/../../ops/wireguard/vps-public-edge.sh"))
  common_tags = merge(
    {
      managed-by = "terraform"
      stack      = "oci-free-tier"
    },
    var.freeform_tags
  )
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
    assign_public_ip = true
    nsg_ids          = [oci_core_network_security_group.wireguard.id]
    display_name     = "${var.wireguard_instance_name}-vnic"
    hostname_label   = "wireguard"
  }

  metadata = {
    ssh_authorized_keys = local.ssh_authorized_keys
    user_data = base64encode(templatefile("${path.module}/templates/wireguard-cloud-init.yaml.tftpl", {
      wireguard_udp_port         = var.wireguard_udp_port
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
