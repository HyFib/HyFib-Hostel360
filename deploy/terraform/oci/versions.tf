terraform {
  required_version = ">= 1.9.0"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 9.10"
    }
  }

  # State holds OCIDs and, when PEM certificates are uploaded, the load balancer private
  # key: keep it in a private, versioned Object Storage bucket (see backend.tf.example).
}
