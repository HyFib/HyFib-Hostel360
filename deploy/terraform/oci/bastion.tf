# OCI Bastion: the only way in for administrators. Sessions are time-limited, opened from
# the listed office addresses, and land in the private subnet (port 22 is reachable only
# from the bastion endpoint inside that subnet).
#
#   oci bastion session create-managed-ssh \
#     --bastion-id <bastion_id> --target-resource-id <instance_id> \
#     --target-os-username ubuntu --ssh-public-key-file ~/.ssh/id_ed25519.pub
# or a port-forwarding session for Ansible (see deploy/ansible/inventory.example.ini).

resource "oci_bastion_bastion" "main" {
  compartment_id               = var.compartment_ocid
  bastion_type                 = "standard"
  name                         = replace("${local.name}-bastion", "-", "")
  target_subnet_id             = oci_core_subnet.app.id
  client_cidr_block_allow_list = var.bastion_client_cidrs
  max_session_ttl_in_seconds   = var.bastion_max_session_ttl_seconds
  freeform_tags                = local.tags
}
