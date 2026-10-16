# Private OCI Registry repository for the application image (deploy/docker/build.sh).

resource "oci_artifacts_container_repository" "app" {
  compartment_id = var.compartment_ocid
  display_name   = "hostel360"
  is_public      = false
  is_immutable   = true
  freeform_tags  = local.tags

  readme {
    content = "HyFib Hostel360 application image. Tags are <version>-<git sha>; deploy with h360-deploy <tag>."
    format  = "TEXT_PLAIN"
  }
}
