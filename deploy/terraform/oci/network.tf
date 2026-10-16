# VCN with a public subnet for the load balancer and a private subnet for the server.
# The server has no public address: inbound traffic arrives only from the load balancer,
# administrators come in through OCI Bastion, and outbound traffic leaves through the NAT
# gateway (internet) or the service gateway (Oracle services in Hyderabad).

resource "oci_core_vcn" "main" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${local.name}-vcn"
  dns_label      = "h360"
  freeform_tags  = local.tags
}

resource "oci_core_internet_gateway" "igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-igw"
  enabled        = true
  freeform_tags  = local.tags
}

resource "oci_core_nat_gateway" "nat" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-nat"
  freeform_tags  = local.tags
}

resource "oci_core_service_gateway" "sgw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-sgw"
  freeform_tags  = local.tags

  services {
    service_id = data.oci_core_services.all.services[0].id
  }
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-rt-public"
  freeform_tags  = local.tags

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.igw.id
  }
}

resource "oci_core_route_table" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-rt-private"
  freeform_tags  = local.tags

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.nat.id
  }

  route_rules {
    destination       = data.oci_core_services.all.services[0].cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.sgw.id
  }
}

# Subnet security lists carry only what NSGs cannot express (path MTU discovery); every
# other rule lives in the network security groups below. The VCN default list (which
# opens SSH to the world) is not used by either subnet.
resource "oci_core_security_list" "baseline" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-sl-baseline"
  freeform_tags  = local.tags

  ingress_security_rules {
    protocol    = "1"
    source      = "0.0.0.0/0"
    stateless   = false
    description = "ICMP fragmentation needed (path MTU discovery)"

    icmp_options {
      type = 3
      code = 4
    }
  }

  ingress_security_rules {
    protocol    = "1"
    source      = var.vcn_cidr
    stateless   = false
    description = "ICMP destination unreachable within the VCN"

    icmp_options {
      type = 3
    }
  }
}

resource "oci_core_subnet" "lb" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.main.id
  cidr_block                 = var.lb_subnet_cidr
  display_name               = "${local.name}-subnet-lb"
  dns_label                  = "lb"
  prohibit_public_ip_on_vnic = false
  route_table_id             = oci_core_route_table.public.id
  security_list_ids          = [oci_core_security_list.baseline.id]
  freeform_tags              = local.tags
}

resource "oci_core_subnet" "app" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.main.id
  cidr_block                 = var.app_subnet_cidr
  display_name               = "${local.name}-subnet-app"
  dns_label                  = "app"
  prohibit_public_ip_on_vnic = true
  prohibit_internet_ingress  = true
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.baseline.id]
  freeform_tags              = local.tags
}

# --------------------------------------------------------------------------- NSG: load balancer

resource "oci_core_network_security_group" "lb" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-nsg-lb"
  freeform_tags  = local.tags
}

resource "oci_core_network_security_group_security_rule" "lb_https_in" {
  for_each = toset(var.lb_ingress_cidrs)

  network_security_group_id = oci_core_network_security_group.lb.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = each.value
  source_type               = "CIDR_BLOCK"
  description               = "HTTPS from clients"

  tcp_options {
    destination_port_range {
      min = 443
      max = 443
    }
  }
}

resource "oci_core_network_security_group_security_rule" "lb_http_in" {
  for_each = toset(var.lb_ingress_cidrs)

  network_security_group_id = oci_core_network_security_group.lb.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = each.value
  source_type               = "CIDR_BLOCK"
  description               = "HTTP from clients (redirected to HTTPS)"

  tcp_options {
    destination_port_range {
      min = 80
      max = 80
    }
  }
}

resource "oci_core_network_security_group_security_rule" "lb_to_app" {
  for_each = toset(["8443", "8080"])

  network_security_group_id = oci_core_network_security_group.lb.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = oci_core_network_security_group.app.id
  destination_type          = "NETWORK_SECURITY_GROUP"
  description               = "Load balancer to the WAF on the application server"

  tcp_options {
    destination_port_range {
      min = tonumber(each.value)
      max = tonumber(each.value)
    }
  }
}

# --------------------------------------------------------------------------- NSG: application server

resource "oci_core_network_security_group" "app" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-nsg-app"
  freeform_tags  = local.tags
}

resource "oci_core_network_security_group_security_rule" "app_from_lb" {
  for_each = toset(["8443", "8080"])

  network_security_group_id = oci_core_network_security_group.app.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = oci_core_network_security_group.lb.id
  source_type               = "NETWORK_SECURITY_GROUP"
  description               = "WAF port ${each.value} from the load balancer only"

  tcp_options {
    destination_port_range {
      min = tonumber(each.value)
      max = tonumber(each.value)
    }
  }
}

resource "oci_core_network_security_group_security_rule" "app_ssh_from_bastion" {
  network_security_group_id = oci_core_network_security_group.app.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = var.app_subnet_cidr
  source_type               = "CIDR_BLOCK"
  description               = "SSH from the OCI Bastion endpoint in this subnet (no public SSH)"

  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}

resource "oci_core_network_security_group_security_rule" "app_egress_tcp" {
  for_each = toset([for port in var.app_egress_tcp_ports : tostring(port)])

  network_security_group_id = oci_core_network_security_group.app.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
  description               = "Outbound TCP ${each.value}"

  tcp_options {
    destination_port_range {
      min = tonumber(each.value)
      max = tonumber(each.value)
    }
  }
}

resource "oci_core_network_security_group_security_rule" "app_egress_ntp" {
  network_security_group_id = oci_core_network_security_group.app.id
  direction                 = "EGRESS"
  protocol                  = "17"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
  description               = "NTP to NIC and NPL time servers (CERT-In)"

  udp_options {
    destination_port_range {
      min = 123
      max = 123
    }
  }
}

resource "oci_core_network_security_group_security_rule" "app_egress_osn" {
  network_security_group_id = oci_core_network_security_group.app.id
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = data.oci_core_services.all.services[0].cidr_block
  destination_type          = "SERVICE_CIDR_BLOCK"
  description               = "Oracle services in the region (Object Storage, OCIR, monitoring)"
}

resource "oci_core_network_security_group_security_rule" "app_egress_vcn" {
  network_security_group_id = oci_core_network_security_group.app.id
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = var.vcn_cidr
  destination_type          = "CIDR_BLOCK"
  description               = "Replies and traffic within the VCN"
}
