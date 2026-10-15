# OCI Logging: load balancer access/error logs and VCN flow logs, kept in India for the
# service maximum of 180 days (CERT-In directions of 28 April 2022 require 180 days).
# Host and application logs are kept on the server for 180 days (Ansible logrotate and
# journald settings) and are part of the nightly backup to Mumbai.

resource "oci_logging_log_group" "main" {
  compartment_id = var.compartment_ocid
  display_name   = "${local.name}-logs"
  description    = "HyFib Hostel360 load balancer and network logs"
  freeform_tags  = local.tags
}

resource "oci_logging_log" "lb" {
  for_each = toset(["access", "error"])

  display_name       = "${local.name}-lb-${each.value}"
  log_group_id       = oci_logging_log_group.main.id
  log_type           = "SERVICE"
  is_enabled         = true
  retention_duration = var.log_retention_days
  freeform_tags      = local.tags

  configuration {
    compartment_id = var.compartment_ocid

    source {
      category    = each.value
      resource    = oci_load_balancer_load_balancer.main.id
      service     = "loadbalancer"
      source_type = "OCISERVICE"
    }
  }
}

resource "oci_logging_log" "flow" {
  for_each = {
    lb  = oci_core_subnet.lb.id
    app = oci_core_subnet.app.id
  }

  display_name       = "${local.name}-flow-${each.key}"
  log_group_id       = oci_logging_log_group.main.id
  log_type           = "SERVICE"
  is_enabled         = true
  retention_duration = var.log_retention_days
  freeform_tags      = local.tags

  configuration {
    compartment_id = var.compartment_ocid

    source {
      category    = "all"
      resource    = each.value
      service     = "flowlogs"
      source_type = "OCISERVICE"
    }
  }
}
