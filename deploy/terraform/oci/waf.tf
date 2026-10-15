# Optional OCI Web Application Firewall on the load balancer (enable_oci_waf). ModSecurity
# with the OWASP Core Rule Set on the server is the primary WAF; this adds a per-client
# request rate limit at the edge, before traffic reaches the VCN.

resource "oci_waf_web_app_firewall_policy" "main" {
  count = var.enable_oci_waf ? 1 : 0

  compartment_id = var.compartment_ocid
  display_name   = "${local.name}-waf-policy"
  freeform_tags  = local.tags

  actions {
    name = "allow"
    type = "ALLOW"
  }

  actions {
    name = "too-many-requests"
    type = "RETURN_HTTP_RESPONSE"
    code = 429

    headers {
      name  = "Retry-After"
      value = "60"
    }

    body {
      type = "STATIC_TEXT"
      text = "Too many requests. Please wait a minute and try again."
    }
  }

  request_rate_limiting {
    rules {
      name        = "per-client-rate"
      type        = "REQUEST_RATE_LIMITING"
      action_name = "too-many-requests"

      configurations {
        period_in_seconds          = 60
        requests_limit             = var.oci_waf_rate_limit_per_minute
        action_duration_in_seconds = 60
      }
    }
  }
}

resource "oci_waf_web_app_firewall" "main" {
  count = var.enable_oci_waf ? 1 : 0

  compartment_id             = var.compartment_ocid
  display_name               = "${local.name}-waf"
  backend_type               = "LOAD_BALANCER"
  load_balancer_id           = oci_load_balancer_load_balancer.main.id
  web_app_firewall_policy_id = oci_waf_web_app_firewall_policy.main[0].id
  freeform_tags              = local.tags
}
