# Data volume: 400 GB, Balanced performance (VPU 10), attached paravirtualized with in-transit
# encryption at a consistent device path and mounted at /srv/hostel360 (MariaDB, sites,
# Redis AOF, ClamAV signatures, logs). Encrypted at rest with Oracle-managed keys, or with
# the Vault key when create_vault is true.

locals {
  data_device_path = "/dev/oracleoci/oraclevdb"
}

resource "oci_core_volume" "data" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.ad
  display_name        = "${local.name}-data"
  size_in_gbs         = var.data_volume_size_gbs
  vpus_per_gb         = var.data_volume_vpus_per_gb
  kms_key_id          = var.create_vault ? oci_kms_key.volumes[0].id : null
  freeform_tags       = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_core_volume_attachment" "data" {
  attachment_type                     = "paravirtualized"
  instance_id                         = oci_core_instance.app.id
  volume_id                           = oci_core_volume.data.id
  device                              = local.data_device_path
  display_name                        = "${local.name}-data-attachment"
  is_pv_encryption_in_transit_enabled = true
}

# Scheduled volume backups (in addition to the restic backups in Mumbai): daily
# incrementals for a week, weekly fulls for a month, monthly fulls for six months.
# Times are UTC: 21:00 UTC is 02:30 IST, after the nightly restic run.
resource "oci_core_volume_backup_policy" "data" {
  compartment_id     = var.compartment_ocid
  display_name       = "${local.name}-volume-backups"
  destination_region = var.volume_backup_cross_region_copy ? var.backup_region : null
  freeform_tags      = local.tags

  schedules {
    backup_type       = "INCREMENTAL"
    period            = "ONE_DAY"
    hour_of_day       = 21
    offset_type       = "STRUCTURED"
    retention_seconds = 7 * 24 * 3600
    time_zone         = "UTC"
  }

  schedules {
    backup_type       = "FULL"
    period            = "ONE_WEEK"
    day_of_week       = "SUNDAY"
    hour_of_day       = 22
    offset_type       = "STRUCTURED"
    retention_seconds = 28 * 24 * 3600
    time_zone         = "UTC"
  }

  schedules {
    backup_type       = "FULL"
    period            = "ONE_MONTH"
    day_of_month      = 1
    hour_of_day       = 23
    offset_type       = "STRUCTURED"
    retention_seconds = 180 * 24 * 3600
    time_zone         = "UTC"
  }
}

resource "oci_core_volume_backup_policy_assignment" "data" {
  asset_id  = oci_core_volume.data.id
  policy_id = oci_core_volume_backup_policy.data.id
}

resource "oci_core_volume_backup_policy_assignment" "boot" {
  asset_id  = oci_core_instance.app.boot_volume_id
  policy_id = oci_core_volume_backup_policy.data.id
}
