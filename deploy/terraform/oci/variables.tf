# --------------------------------------------------------------------------- tenancy and regions

variable "tenancy_ocid" {
  description = "Tenancy OCID (identity resources and the Object Storage namespace)."
  type        = string
}

variable "compartment_ocid" {
  description = "Compartment for every Hostel360 resource."
  type        = string
}

variable "oci_profile" {
  description = "Profile in ~/.oci/config used by the provider."
  type        = string
  default     = "DEFAULT"
}

variable "region" {
  description = "Application region (OCI Hyderabad)."
  type        = string
  default     = "ap-hyderabad-1"
}

variable "backup_region" {
  description = "Region of the backup bucket (OCI Mumbai)."
  type        = string
  default     = "ap-mumbai-1"
}

variable "home_region" {
  description = "Home region of the tenancy, where dynamic groups, groups and policies live."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for resource display names."
  type        = string
  default     = "hostel360"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.name_prefix))
    error_message = "Use 2-21 lowercase letters, digits or dashes, starting with a letter."
  }
}

variable "environment" {
  description = "Environment label used in tags and names (prod, staging)."
  type        = string
  default     = "prod"
}

variable "freeform_tags" {
  description = "Extra freeform tags for every resource."
  type        = map(string)
  default     = {}
}

# --------------------------------------------------------------------------- network

variable "vcn_cidr" {
  description = "VCN address range."
  type        = string
  default     = "10.20.0.0/16"
}

variable "lb_subnet_cidr" {
  description = "Public subnet of the load balancer. The WAF trusts X-Forwarded-For only from this range."
  type        = string
  default     = "10.20.1.0/24"
}

variable "app_subnet_cidr" {
  description = "Private subnet of the application server (and the Bastion endpoint)."
  type        = string
  default     = "10.20.2.0/24"
}

variable "lb_ingress_cidrs" {
  description = "Sources allowed to reach the load balancer on 80/443."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "app_egress_tcp_ports" {
  description = "TCP ports the server may open to the internet (HTTPS/HTTP for APIs, mirrors and updates; SMTP submission)."
  type        = list(number)
  default     = [80, 443, 465, 587]
}

variable "bastion_client_cidrs" {
  description = "Public addresses (HyFib office / VPN egress) allowed to open Bastion sessions."
  type        = list(string)

  validation {
    condition     = length(var.bastion_client_cidrs) > 0 && !contains(var.bastion_client_cidrs, "0.0.0.0/0")
    error_message = "List the administrators' egress addresses; 0.0.0.0/0 is not allowed."
  }
}

variable "bastion_max_session_ttl_seconds" {
  description = "Longest Bastion session (seconds)."
  type        = number
  default     = 10800
}

# --------------------------------------------------------------------------- compute and storage

variable "availability_domain_index" {
  description = "Availability domain (0-based index); Hyderabad has one."
  type        = number
  default     = 0
}

variable "instance_shape" {
  description = "Compute shape (OCI estimate option 1)."
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "instance_ocpus" {
  description = "OCPUs of the application server."
  type        = number
  default     = 8
}

variable "instance_memory_gbs" {
  description = "Memory of the application server (GB)."
  type        = number
  default     = 16
}

variable "instance_hostname" {
  description = "Host name label inside the VCN."
  type        = string
  default     = "hostel360-app"
}

variable "boot_volume_size_gbs" {
  description = "Boot volume size (GB)."
  type        = number
  default     = 100
}

variable "ssh_public_keys" {
  description = "SSH public keys of the administrators (login only through OCI Bastion)."
  type        = list(string)

  validation {
    condition     = length(var.ssh_public_keys) > 0
    error_message = "At least one SSH public key is required."
  }
}

variable "data_volume_size_gbs" {
  description = "Data block volume size (GB), mounted at /srv/hostel360."
  type        = number
  default     = 400
}

variable "data_volume_vpus_per_gb" {
  description = "Block volume performance (10 = Balanced)."
  type        = number
  default     = 10
}

variable "volume_backup_cross_region_copy" {
  description = "Also copy the scheduled block volume backups to the backup region."
  type        = bool
  default     = false
}

variable "ansible_pull_repo_url" {
  description = "Optional Git URL for ansible-pull at first boot; empty leaves the server ready for ansible-playbook from an admin workstation."
  type        = string
  default     = ""
}

variable "ansible_pull_branch" {
  description = "Branch or tag for ansible-pull."
  type        = string
  default     = "main"
}

# --------------------------------------------------------------------------- load balancer

variable "site_hostname" {
  description = "Public host name of the portal (certificate name), e.g. hostel.accet.ac.in."
  type        = string
}

variable "lb_min_bandwidth_mbps" {
  description = "Flexible load balancer minimum bandwidth (Mbps)."
  type        = number
  default     = 10
}

variable "lb_max_bandwidth_mbps" {
  description = "Flexible load balancer maximum bandwidth (Mbps)."
  type        = number
  default     = 100
}

variable "lb_reserved_public_ip" {
  description = "Use a reserved public IP so the DNS record survives a load balancer replacement."
  type        = bool
  default     = true
}

variable "lb_certificate_ocid" {
  description = "Certificate OCID from the OCI Certificates service. Leave empty to upload PEM files instead."
  type        = string
  default     = ""
}

variable "lb_certificate_pem" {
  description = "Public certificate (PEM, with intermediates) when not using the Certificates service."
  type        = string
  default     = ""
  sensitive   = true
}

variable "lb_private_key_pem" {
  description = "Private key (PEM) of lb_certificate_pem."
  type        = string
  default     = ""
  sensitive   = true
}

variable "lb_ca_certificate_pem" {
  description = "CA chain (PEM) of lb_certificate_pem, if not bundled."
  type        = string
  default     = ""
  sensitive   = true
}

variable "backend_tls" {
  description = "Re-encrypt from the load balancer to the WAF on 8443 (true) or forward plain HTTP to 8080 (false)."
  type        = bool
  default     = true
}

variable "backend_ca_certificate_pem" {
  description = "CA (or self-signed certificate) of the WAF's internal certificate; when set, the load balancer verifies the backend."
  type        = string
  default     = ""
}

# --------------------------------------------------------------------------- backups

variable "backup_bucket_name" {
  description = "Object Storage bucket for restic backups (Mumbai)."
  type        = string
  default     = "hostel360-backups-mumbai"
}

variable "backup_noncurrent_version_days" {
  description = "Days previous object versions are kept. Versions protect the repository if objects are deleted or overwritten."
  type        = number
  default     = 30
}

variable "backup_kms_key_ocid" {
  description = "Optional customer-managed key in the backup region for the bucket (Oracle-managed keys otherwise)."
  type        = string
  default     = ""
}

variable "backup_reader_group_name" {
  description = "IAM group for ACGCET officials with read-only access to the backup bucket."
  type        = string
  default     = "acgcet-backup-readers"
}

# --------------------------------------------------------------------------- vault

variable "create_vault" {
  description = "Create an OCI Vault with a customer-managed key for the block volumes."
  type        = bool
  default     = false
}

# --------------------------------------------------------------------------- monitoring and logging

variable "alarm_emails" {
  description = "E-mail addresses subscribed to the alarm topic (HyFib helpdesk, technical lead)."
  type        = list(string)
  default     = []
}

variable "alarm_https_endpoint" {
  description = "Hostel360 OCI notification endpoint, e.g. https://<site>/api/method/hostel360.api.ops.oci_notification?token=<token>."
  type        = string
  default     = ""
  sensitive   = true
}

variable "alarm_pagerduty_endpoint" {
  description = "PagerDuty Events API v1 URL (https://events.pagerduty.com/integration/<key>/enqueue)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "alarm_slack_webhook" {
  description = "Slack incoming webhook URL."
  type        = string
  default     = ""
  sensitive   = true
}

variable "alarm_cpu_percent" {
  description = "CPU alarm threshold (percent, 10 minutes)."
  type        = number
  default     = 80
}

variable "alarm_memory_percent" {
  description = "Memory alarm threshold (percent)."
  type        = number
  default     = 85
}

variable "alarm_lb_5xx_per_5min" {
  description = "Load balancer 5xx responses in 5 minutes that raise an alarm."
  type        = number
  default     = 50
}

variable "log_retention_days" {
  description = "OCI Logging retention (days). 180 is the service maximum and meets the CERT-In 180-day rule."
  type        = number
  default     = 180

  validation {
    condition     = contains([30, 60, 90, 120, 150, 180], var.log_retention_days)
    error_message = "OCI Logging accepts 30, 60, 90, 120, 150 or 180 days."
  }
}

# --------------------------------------------------------------------------- OCI WAF

variable "enable_oci_waf" {
  description = "Attach an OCI Web Application Firewall policy to the load balancer (in addition to ModSecurity on the server)."
  type        = bool
  default     = false
}

variable "oci_waf_rate_limit_per_minute" {
  description = "Requests per minute per client address before the OCI WAF answers 429."
  type        = number
  default     = 6000
}
