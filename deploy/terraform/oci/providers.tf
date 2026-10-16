# Credentials come from the OCI CLI configuration (~/.oci/config, profile var.oci_profile)
# or the usual OCI_* environment variables; nothing is stored in this directory.

# Application region: Hyderabad
provider "oci" {
  region              = var.region
  config_file_profile = var.oci_profile
}

# Backup region: Mumbai (Object Storage bucket for restic)
provider "oci" {
  alias               = "mumbai"
  region              = var.backup_region
  config_file_profile = var.oci_profile
}

# Identity resources must be created in the tenancy's home region
provider "oci" {
  alias               = "home"
  region              = var.home_region
  config_file_profile = var.oci_profile
}
