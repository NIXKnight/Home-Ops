include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

locals {
  module_source_url = include.root.locals.common_vars.remote_modules.proxmox_vm.source
  vms               = read_terragrunt_config("${include.root.locals.internal_repo_path}/terragrunt/proxmox/vms.hcl")
}

terraform {
  source = local.module_source_url
}

inputs = local.vms.locals.module_vars
