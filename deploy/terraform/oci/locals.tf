data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

data "oci_objectstorage_namespace" "ns" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_services" "all" {
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}

locals {
  name = "${var.name_prefix}-${var.environment}"
  ad   = data.oci_identity_availability_domains.ads.availability_domains[var.availability_domain_index].name

  tags = merge(
    {
      application = "hostel360"
      environment = var.environment
      managed-by  = "terraform"
      owner       = "hyfib-solutions"
    },
    var.freeform_tags,
  )

  # Backend port on the server: 8443 when the load balancer re-encrypts, 8080 otherwise.
  backend_port = var.backend_tls ? 8443 : 8080

  upload_certificate = var.lb_certificate_ocid == ""
}
