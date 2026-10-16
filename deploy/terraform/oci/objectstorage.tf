# restic backup repository in OCI Object Storage, Mumbai (second Indian region).
#
# Versioning keeps overwritten and deleted objects for backup_noncurrent_version_days. The
# server may create, read, overwrite and delete objects (restic prunes) but has no
# permission to delete object versions, so a compromised server cannot destroy the history.

resource "oci_objectstorage_bucket" "backups" {
  provider = oci.mumbai

  compartment_id        = var.compartment_ocid
  namespace             = data.oci_objectstorage_namespace.ns.namespace
  name                  = var.backup_bucket_name
  access_type           = "NoPublicAccess"
  storage_tier          = "Standard"
  versioning            = "Enabled"
  object_events_enabled = false
  auto_tiering          = "Disabled"
  kms_key_id            = var.backup_kms_key_ocid != "" ? var.backup_kms_key_ocid : null
  freeform_tags         = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_objectstorage_object_lifecycle_policy" "backups" {
  provider = oci.mumbai

  namespace = data.oci_objectstorage_namespace.ns.namespace
  bucket    = oci_objectstorage_bucket.backups.name

  rules {
    name        = "expire-previous-versions"
    action      = "DELETE"
    target      = "previous-object-versions"
    time_amount = var.backup_noncurrent_version_days
    time_unit   = "DAYS"
    is_enabled  = true
  }

  rules {
    name        = "abort-incomplete-uploads"
    action      = "ABORT"
    target      = "multipart-uploads"
    time_amount = 7
    time_unit   = "DAYS"
    is_enabled  = true
  }

  # The Object Storage service needs its own policy to apply lifecycle rules.
  depends_on = [oci_identity_policy.object_storage_lifecycle]
}
