include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

locals {
  module_source_url  = include.root.locals.common_vars.remote_modules.proxmox_vm.source
  proxmox_vms_config = yamldecode(file("${get_repo_root()}/../Home-Ops-Internal/terragrunt/proxmox/vms_config.yaml"))
}

terraform {
  source = local.module_source_url
}

inputs = merge(local.proxmox_vms_config.module_vars)
