# Instance principal for the application server, least-privilege policies, and the
# read-only group for ACGCET officials. Identity resources live in the home region.

resource "oci_identity_dynamic_group" "app" {
  provider = oci.home

  compartment_id = var.tenancy_ocid
  name           = "${local.name}-app-servers"
  description    = "HyFib Hostel360 application server (instance principal)"
  matching_rule  = "ALL {instance.id = '${oci_core_instance.app.id}'}"
  freeform_tags  = local.tags
}

locals {
  dg = "dynamic-group ${oci_identity_dynamic_group.app.name}"
  cp = "compartment id ${var.compartment_ocid}"

  app_statements = [
    # restic through rclone: objects of the backup bucket only, never object versions
    "Allow ${local.dg} to read buckets in ${local.cp} where target.bucket.name = '${var.backup_bucket_name}'",
    "Allow ${local.dg} to manage objects in ${local.cp} where all {target.bucket.name = '${var.backup_bucket_name}', any {request.permission = 'OBJECT_CREATE', request.permission = 'OBJECT_INSPECT', request.permission = 'OBJECT_READ', request.permission = 'OBJECT_OVERWRITE', request.permission = 'OBJECT_DELETE'}}",
    # h360-snapshot before deployments and restores
    "Allow ${local.dg} to use volumes in ${local.cp} where target.volume.id = '${oci_core_volume.data.id}'",
    "Allow ${local.dg} to manage volume-backups in ${local.cp}",
    # h360-deploy pulls the application image from OCI Registry
    "Allow ${local.dg} to read repos in ${local.cp}",
    # Secrets kept in OCI Vault (registry tokens, integration keys)
    "Allow ${local.dg} to read secret-bundles in ${local.cp}",
    # Read metrics for the host agent and diagnostics
    "Allow ${local.dg} to read metrics in ${local.cp}",
    # Critical alerts straight to the alarm topic when the application cannot take them
    "Allow ${local.dg} to use ons-topics in ${local.cp} where target.topic.id = '${oci_ons_notification_topic.alerts.id}'",
  ]
}

resource "oci_identity_policy" "app" {
  provider = oci.home

  compartment_id = var.compartment_ocid
  name           = "${local.name}-app-server"
  description    = "HyFib Hostel360 server: backups, volume backups, registry, vault secrets, metrics"
  statements     = local.app_statements
  freeform_tags  = local.tags
}

resource "oci_identity_policy" "object_storage_lifecycle" {
  provider = oci.home

  compartment_id = var.compartment_ocid
  name           = "${local.name}-objectstorage-lifecycle"
  description    = "Lets Object Storage in the backup region apply the backup bucket lifecycle rules"
  statements = [
    "Allow service objectstorage-${var.backup_region} to manage object-family in ${local.cp} where target.bucket.name = '${var.backup_bucket_name}'",
  ]
  freeform_tags = local.tags
}

resource "oci_identity_policy" "block_storage_keys" {
  count    = var.create_vault ? 1 : 0
  provider = oci.home

  compartment_id = var.compartment_ocid
  name           = "${local.name}-blockstorage-keys"
  description    = "Block Volume service use of the Hostel360 volume key"
  statements = [
    "Allow service blockstorage to use keys in ${local.cp} where target.key.id = '${oci_kms_key.volumes[0].id}'",
  ]
  freeform_tags = local.tags
}

# --------------------------------------------------------------------------- ACGCET read-only access

resource "oci_identity_group" "backup_readers" {
  provider = oci.home

  compartment_id = var.tenancy_ocid
  name           = var.backup_reader_group_name
  description    = "ACGCET officials: read-only access to the Hostel360 backup bucket"
  freeform_tags  = local.tags
}

resource "oci_identity_policy" "backup_readers" {
  provider = oci.home

  compartment_id = var.compartment_ocid
  name           = "${local.name}-backup-readers"
  description    = "Read-only access for ACGCET to the Hostel360 backups (tender: direct access for officials)"
  statements = [
    "Allow group ${oci_identity_group.backup_readers.name} to read buckets in ${local.cp} where target.bucket.name = '${var.backup_bucket_name}'",
    "Allow group ${oci_identity_group.backup_readers.name} to read objects in ${local.cp} where target.bucket.name = '${var.backup_bucket_name}'",
  ]
  freeform_tags = local.tags
}
