# OCI Monitoring alarms -> Notifications topic -> HyFib (e-mail, and optionally PagerDuty,
# Slack and the Hostel360 application itself, which turns them into Ops Alerts and runs its
# escalation matrix). These alarms see the server from outside, so they still fire when
# the application or the whole VM is down. Runbook: deploy/monitoring/oci-alarm-runbook.md.

resource "oci_ons_notification_topic" "alerts" {
  compartment_id = var.compartment_ocid
  name           = "${local.name}-alerts"
  description    = "HyFib Hostel360 infrastructure alarms"
  freeform_tags  = local.tags
}

resource "oci_ons_subscription" "email" {
  for_each = toset(var.alarm_emails)

  compartment_id = var.compartment_ocid
  topic_id       = oci_ons_notification_topic.alerts.id
  protocol       = "EMAIL"
  endpoint       = each.value
  freeform_tags  = local.tags
}

resource "oci_ons_subscription" "application" {
  count = var.alarm_https_endpoint != "" ? 1 : 0

  compartment_id = var.compartment_ocid
  topic_id       = oci_ons_notification_topic.alerts.id
  protocol       = "CUSTOM_HTTPS"
  endpoint       = var.alarm_https_endpoint
  freeform_tags  = local.tags
}

resource "oci_ons_subscription" "pagerduty" {
  count = var.alarm_pagerduty_endpoint != "" ? 1 : 0

  compartment_id = var.compartment_ocid
  topic_id       = oci_ons_notification_topic.alerts.id
  protocol       = "PAGERDUTY"
  endpoint       = var.alarm_pagerduty_endpoint
  freeform_tags  = local.tags
}

resource "oci_ons_subscription" "slack" {
  count = var.alarm_slack_webhook != "" ? 1 : 0

  compartment_id = var.compartment_ocid
  topic_id       = oci_ons_notification_topic.alerts.id
  protocol       = "SLACK"
  endpoint       = var.alarm_slack_webhook
  freeform_tags  = local.tags
}

locals {
  instance_filter = "{resourceId = \"${oci_core_instance.app.id}\"}"
  lb_filter       = "{resourceId = \"${oci_load_balancer_load_balancer.main.id}\"}"

  alarms = {
    cpu = {
      namespace = "oci_computeagent"
      query     = "CpuUtilization[1m]${local.instance_filter}.mean() > ${var.alarm_cpu_percent}"
      pending   = "PT10M"
      severity  = "WARNING"
      summary   = "Hostel360 server CPU above ${var.alarm_cpu_percent}% for 10 minutes"
      body      = "Runbook: deploy/monitoring/oci-alarm-runbook.md#cpu. Check h360-agent metrics and the slow query log; a sustained load at peak (fee deadlines) may need more gunicorn workers."
    }
    memory = {
      namespace = "oci_computeagent"
      query     = "MemoryUtilization[1m]${local.instance_filter}.mean() > ${var.alarm_memory_percent}"
      pending   = "PT5M"
      severity  = "WARNING"
      summary   = "Hostel360 server memory above ${var.alarm_memory_percent}%"
      body      = "Runbook: deploy/monitoring/oci-alarm-runbook.md#memory. docker stats shows the container near its limit."
    }
    agent_heartbeat = {
      namespace = "oci_computeagent"
      query     = "CpuUtilization[1m]${local.instance_filter}.absent()"
      pending   = "PT5M"
      severity  = "CRITICAL"
      summary   = "Hostel360 server stopped reporting metrics"
      body      = "Runbook: deploy/monitoring/oci-alarm-runbook.md#heartbeat. The VM is down, hung, or the Oracle Cloud Agent stopped."
    }
    accessibility = {
      namespace = "oci_compute_instance_health"
      query     = "instance_accessibility_status[1m]${local.instance_filter}.max() == 1"
      pending   = "PT5M"
      severity  = "CRITICAL"
      summary   = "Hostel360 server not accessible"
      body      = "Runbook: deploy/monitoring/oci-alarm-runbook.md#accessibility. OCI cannot reach the VM's network interface; check the console and serial console history."
    }
    lb_unhealthy = {
      namespace = "oci_lbaas"
      query     = "UnHealthyBackendServers[1m]${local.lb_filter}.max() >= 1"
      pending   = "PT2M"
      severity  = "CRITICAL"
      summary   = "Hostel360 portal backend unhealthy at the load balancer"
      body      = "Runbook: deploy/monitoring/oci-alarm-runbook.md#lb-unhealthy. The liveness check through the WAF fails: users get errors."
    }
    lb_5xx = {
      namespace = "oci_lbaas"
      query     = "HttpResponses5xx[5m]${local.lb_filter}.sum() > ${var.alarm_lb_5xx_per_5min}"
      pending   = "PT5M"
      severity  = "WARNING"
      summary   = "Hostel360 portal returning server errors"
      body      = "Runbook: deploy/monitoring/oci-alarm-runbook.md#lb-5xx. Check Error Log in Hostel360 and the backend container logs."
    }
  }
}

resource "oci_monitoring_alarm" "this" {
  for_each = local.alarms

  compartment_id        = var.compartment_ocid
  metric_compartment_id = var.compartment_ocid
  display_name          = "${local.name}-${replace(each.key, "_", "-")}"
  namespace             = each.value.namespace
  query                 = each.value.query
  pending_duration      = each.value.pending
  severity              = each.value.severity
  alarm_summary         = each.value.summary
  body                  = each.value.body
  destinations          = [oci_ons_notification_topic.alerts.id]
  is_enabled            = true
  message_format        = "ONS_OPTIMIZED"
  resolution            = "1m"
  # Critical alarms repeat every 2 hours until resolved; warnings once a day.
  repeat_notification_duration = each.value.severity == "CRITICAL" ? "PT2H" : "P1D"
  freeform_tags                = local.tags
}
