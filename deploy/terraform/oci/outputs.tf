output "lb_public_ip" {
  description = "Public IP of the load balancer: point the portal's DNS A record here."
  value       = var.lb_reserved_public_ip ? oci_core_public_ip.lb[0].ip_address : oci_load_balancer_load_balancer.main.ip_address_details[0].ip_address
}

output "instance_id" {
  description = "OCID of the application server."
  value       = oci_core_instance.app.id
}

output "instance_private_ip" {
  description = "Private IP of the application server (Ansible target through Bastion)."
  value       = oci_core_instance.app.private_ip
}

output "data_volume_id" {
  description = "OCID of the data block volume (H360_BLOCK_VOLUME_ID in backup.env)."
  value       = oci_core_volume.data.id
}

output "bastion_id" {
  description = "OCID of the OCI Bastion."
  value       = oci_bastion_bastion.main.id
}

output "lb_subnet_cidr" {
  description = "Load balancer subnet (LB_SUBNET_CIDR in stack.env; host firewall source)."
  value       = var.lb_subnet_cidr
}

output "backup_bucket" {
  description = "Backup bucket name, namespace and region (backup.env)."
  value = {
    name      = oci_objectstorage_bucket.backups.name
    namespace = data.oci_objectstorage_namespace.ns.namespace
    region    = var.backup_region
  }
}

output "notification_topic_id" {
  description = "Alarm topic (H360_ONS_TOPIC_ID in agent.env)."
  value       = oci_ons_notification_topic.alerts.id
}

output "ocir_repository" {
  description = "Image repository for deploy/docker/build.sh (OCIR_REGION / OCIR_NAMESPACE)."
  value       = "${var.region}.ocir.io/${data.oci_objectstorage_namespace.ns.namespace}/hostel360"
}

output "ssh_via_bastion" {
  description = "Open a managed SSH session through the Bastion."
  value       = "oci bastion session create-managed-ssh --bastion-id ${oci_bastion_bastion.main.id} --target-resource-id ${oci_core_instance.app.id} --target-os-username ubuntu --ssh-public-key-file ~/.ssh/id_ed25519.pub --session-ttl ${var.bastion_max_session_ttl_seconds}"
}

output "exit_inventory" {
  description = "What deploy/terraform/oci/exit-purge.sh needs at the end of the contract (TR 22, deploy/EXIT.md)."
  value = {
    name                    = local.name
    compartment_id          = var.compartment_ocid
    app_region              = var.region
    backup_region           = var.backup_region
    home_region             = var.home_region
    namespace               = data.oci_objectstorage_namespace.ns.namespace
    backup_bucket           = oci_objectstorage_bucket.backups.name
    instance_id             = oci_core_instance.app.id
    data_volume_id          = oci_core_volume.data.id
    boot_volume_id          = oci_core_instance.app.boot_volume_id
    dynamic_group_name      = oci_identity_dynamic_group.app.name
    log_group_id            = oci_logging_log_group.main.id
    log_ids                 = concat([for log in oci_logging_log.lb : log.id], [for log in oci_logging_log.flow : log.id])
    log_retention_days      = var.log_retention_days
    vault_id                = var.create_vault ? oci_kms_vault.main[0].id : ""
    key_id                  = var.create_vault ? oci_kms_key.volumes[0].id : ""
    key_management_endpoint = var.create_vault ? oci_kms_vault.main[0].management_endpoint : ""
  }
}
