include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

locals {
  module_source_url = include.root.locals.common_vars.remote_modules.talos.source
  talos             = read_terragrunt_config("${include.root.locals.internal_repo_path}/terragrunt/talos-cluster/unit.hcl")
}

terraform {
  source = local.module_source_url
}

inputs = local.talos.locals.module_vars
