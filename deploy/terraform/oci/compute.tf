# Application server: VM.Standard.A1.Flex (Ampere, arm64), Ubuntu 24.04, no public IP.

data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = var.instance_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"

  # The full (not Minimal) aarch64 image
  filter {
    name   = "display_name"
    values = ["^Canonical-Ubuntu-24\\.04-aarch64-.*"]
    regex  = true
  }
}

resource "oci_core_instance" "app" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.ad
  display_name        = "${local.name}-app"
  shape               = var.instance_shape
  freeform_tags       = local.tags

  shape_config {
    ocpus         = var.instance_ocpus
    memory_in_gbs = var.instance_memory_gbs
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs = var.boot_volume_size_gbs
    boot_volume_vpus_per_gb = 10
    kms_key_id              = var.create_vault ? oci_kms_key.volumes[0].id : null
  }

  create_vnic_details {
    subnet_id                 = oci_core_subnet.app.id
    assign_public_ip          = false
    assign_private_dns_record = true
    hostname_label            = var.instance_hostname
    nsg_ids                   = [oci_core_network_security_group.app.id]
    display_name              = "${local.name}-app-vnic"
  }

  is_pv_encryption_in_transit_enabled = true

  # Instance metadata service v2 only: tokens cannot be fetched by a plain request forgery.
  instance_options {
    are_legacy_imds_endpoints_disabled = true
  }

  availability_config {
    recovery_action = "RESTORE_INSTANCE"
  }

  agent_config {
    is_monitoring_disabled = false
    is_management_disabled = false

    plugins_config {
      name          = "Compute Instance Monitoring"
      desired_state = "ENABLED"
    }

    plugins_config {
      name          = "Bastion"
      desired_state = "ENABLED"
    }

    plugins_config {
      name          = "Vulnerability Scanning"
      desired_state = "ENABLED"
    }
  }

  metadata = {
    ssh_authorized_keys = join("\n", var.ssh_public_keys)
    user_data = base64encode(templatefile("${path.module}/templates/cloud-init.yaml", {
      data_device         = local.data_device_path
      data_mount          = "/srv/hostel360"
      ansible_pull_repo   = var.ansible_pull_repo_url
      ansible_pull_branch = var.ansible_pull_branch
      lb_subnet_cidr      = var.lb_subnet_cidr
      site_hostname       = var.site_hostname
    }))
  }

  lifecycle {
    # New platform images and edited cloud-init must not replace a running server; patching
    # is done in place (unattended-upgrades, Ansible).
    ignore_changes = [
      source_details[0].source_id,
      metadata["user_data"],
    ]
  }
}
