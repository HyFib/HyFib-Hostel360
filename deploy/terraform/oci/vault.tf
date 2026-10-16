# Optional OCI Vault with a customer-managed AES-256 key (HSM-protected) for the boot and
# data volumes. Off by default: Oracle-managed keys already encrypt every volume at rest.

resource "oci_kms_vault" "main" {
  count = var.create_vault ? 1 : 0

  compartment_id = var.compartment_ocid
  display_name   = "${local.name}-vault"
  vault_type     = "DEFAULT"
  freeform_tags  = local.tags
}

resource "oci_kms_key" "volumes" {
  count = var.create_vault ? 1 : 0

  compartment_id      = var.compartment_ocid
  display_name        = "${local.name}-volumes-key"
  management_endpoint = oci_kms_vault.main[0].management_endpoint
  protection_mode     = "HSM"
  freeform_tags       = local.tags

  key_shape {
    algorithm = "AES"
    length    = 32
  }

  lifecycle {
    prevent_destroy = true
  }
}
