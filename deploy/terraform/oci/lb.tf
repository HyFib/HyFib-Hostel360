# Flexible load balancer (10-100 Mbps): terminates the public certificate, redirects HTTP to
# HTTPS and re-encrypts to the WAF on the server (8443), or forwards to 8080 when
# backend_tls is false. Health check: the application's liveness endpoint.

resource "oci_core_public_ip" "lb" {
  count = var.lb_reserved_public_ip ? 1 : 0

  compartment_id = var.compartment_ocid
  lifetime       = "RESERVED"
  display_name   = "${local.name}-lb-ip"
  freeform_tags  = local.tags

  lifecycle {
    # The DNS record of the portal points here.
    prevent_destroy = true
    ignore_changes  = [private_ip_id]
  }
}

resource "oci_load_balancer_load_balancer" "main" {
  compartment_id             = var.compartment_ocid
  display_name               = "${local.name}-lb"
  shape                      = "flexible"
  subnet_ids                 = [oci_core_subnet.lb.id]
  is_private                 = false
  network_security_group_ids = [oci_core_network_security_group.lb.id]
  freeform_tags              = local.tags

  shape_details {
    minimum_bandwidth_in_mbps = var.lb_min_bandwidth_mbps
    maximum_bandwidth_in_mbps = var.lb_max_bandwidth_mbps
  }

  dynamic "reserved_ips" {
    for_each = var.lb_reserved_public_ip ? [1] : []
    content {
      id = oci_core_public_ip.lb[0].id
    }
  }
}

# --------------------------------------------------------------------------- certificates

resource "oci_load_balancer_certificate" "public" {
  count = local.upload_certificate ? 1 : 0

  load_balancer_id   = oci_load_balancer_load_balancer.main.id
  certificate_name   = "${var.name_prefix}-public-${substr(sha256(var.lb_certificate_pem), 0, 8)}"
  public_certificate = var.lb_certificate_pem
  private_key        = var.lb_private_key_pem
  ca_certificate     = var.lb_ca_certificate_pem != "" ? var.lb_ca_certificate_pem : null

  lifecycle {
    create_before_destroy = true

    precondition {
      condition     = var.lb_certificate_pem != "" && var.lb_private_key_pem != ""
      error_message = "Set lb_certificate_ocid, or provide lb_certificate_pem and lb_private_key_pem."
    }
  }
}

resource "oci_load_balancer_certificate" "backend_ca" {
  count = var.backend_tls && var.backend_ca_certificate_pem != "" ? 1 : 0

  load_balancer_id = oci_load_balancer_load_balancer.main.id
  certificate_name = "${var.name_prefix}-backend-ca-${substr(sha256(var.backend_ca_certificate_pem), 0, 8)}"
  ca_certificate   = var.backend_ca_certificate_pem

  lifecycle {
    create_before_destroy = true
  }
}

# TLS 1.2 and 1.3 with forward-secret AEAD ciphers only.
resource "oci_load_balancer_ssl_cipher_suite" "modern" {
  load_balancer_id = oci_load_balancer_load_balancer.main.id
  name             = "${var.name_prefix}-tls12-13"
  ciphers = [
    "TLS_AES_128_GCM_SHA256",
    "TLS_AES_256_GCM_SHA384",
    "TLS_CHACHA20_POLY1305_SHA256",
    "ECDHE-ECDSA-AES128-GCM-SHA256",
    "ECDHE-RSA-AES128-GCM-SHA256",
    "ECDHE-ECDSA-AES256-GCM-SHA384",
    "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-ECDSA-CHACHA20-POLY1305",
    "ECDHE-RSA-CHACHA20-POLY1305",
  ]
}

# --------------------------------------------------------------------------- backend

resource "oci_load_balancer_backend_set" "app" {
  load_balancer_id = oci_load_balancer_load_balancer.main.id
  name             = "${var.name_prefix}-app"
  policy           = "ROUND_ROBIN"

  health_checker {
    protocol            = "HTTP"
    port                = local.backend_port
    url_path            = "/api/method/hostel360.api.health.liveness"
    return_code         = 200
    response_body_regex = ".*\"status\":\\s*\"ok\".*"
    interval_ms         = 10000
    timeout_in_millis   = 3000
    retries             = 3
  }

  dynamic "ssl_configuration" {
    for_each = var.backend_tls ? [1] : []
    content {
      protocols               = ["TLSv1.2", "TLSv1.3"]
      cipher_suite_name       = oci_load_balancer_ssl_cipher_suite.modern.name
      verify_peer_certificate = var.backend_ca_certificate_pem != ""
      verify_depth            = var.backend_ca_certificate_pem != "" ? 1 : null
      certificate_name        = var.backend_ca_certificate_pem != "" ? oci_load_balancer_certificate.backend_ca[0].certificate_name : null
      server_order_preference = "ENABLED"
    }
  }
}

resource "oci_load_balancer_backend" "app" {
  load_balancer_id = oci_load_balancer_load_balancer.main.id
  backendset_name  = oci_load_balancer_backend_set.app.name
  ip_address       = oci_core_instance.app.private_ip
  port             = local.backend_port
  weight           = 1
}

# --------------------------------------------------------------------------- rule sets

resource "oci_load_balancer_rule_set" "redirect_https" {
  load_balancer_id = oci_load_balancer_load_balancer.main.id
  name             = "redirect_https"

  items {
    action        = "REDIRECT"
    response_code = 301

    conditions {
      attribute_name  = "PATH"
      attribute_value = "/"
      operator        = "FORCE_LONGEST_PREFIX_MATCH"
    }

    redirect_uri {
      protocol = "HTTPS"
      host     = "{host}"
      port     = 443
      path     = "{path}"
      query    = "{query}"
    }
  }
}

resource "oci_load_balancer_rule_set" "https_hardening" {
  load_balancer_id = oci_load_balancer_load_balancer.main.id
  name             = "https_hardening"

  # Reject header names with invalid characters; allow large headers (desk cookies).
  items {
    action                         = "HTTP_HEADER"
    are_invalid_characters_allowed = false
    http_large_header_size_in_kb   = 32
  }

  # Only these methods reach the server.
  items {
    action          = "ALLOW"
    allowed_methods = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    status_code     = 405
  }
}

# --------------------------------------------------------------------------- listeners

resource "oci_load_balancer_listener" "https" {
  load_balancer_id         = oci_load_balancer_load_balancer.main.id
  name                     = "https"
  default_backend_set_name = oci_load_balancer_backend_set.app.name
  port                     = 443
  protocol                 = "HTTP"
  rule_set_names           = [oci_load_balancer_rule_set.https_hardening.name]

  # Long idle timeout for the realtime (socket.io) connections.
  connection_configuration {
    idle_timeout_in_seconds = 300
  }

  ssl_configuration {
    certificate_ids         = local.upload_certificate ? null : [var.lb_certificate_ocid]
    certificate_name        = local.upload_certificate ? oci_load_balancer_certificate.public[0].certificate_name : null
    protocols               = ["TLSv1.2", "TLSv1.3"]
    cipher_suite_name       = oci_load_balancer_ssl_cipher_suite.modern.name
    server_order_preference = "ENABLED"
    verify_peer_certificate = false
  }
}

resource "oci_load_balancer_listener" "http" {
  load_balancer_id         = oci_load_balancer_load_balancer.main.id
  name                     = "http"
  default_backend_set_name = oci_load_balancer_backend_set.app.name
  port                     = 80
  protocol                 = "HTTP"
  rule_set_names           = [oci_load_balancer_rule_set.redirect_https.name]

  connection_configuration {
    idle_timeout_in_seconds = 60
  }
}
