locals {
  common_vars                 = read_terragrunt_config(find_in_parent_folders("common.hcl")).locals
  state_conn_string           = get_env("TERRAGRUNT_STATE_CONN_STRING")
  state_encryption_passphrase = get_env("TERRAGRUNT_STATE_ENCRYPTION_PASSPHRASE")
  internal_repo_path          = get_env("TERRAGRUNT_INTERNAL_REPO_PATH")
}

# Configure Terragrunt to store state in PostgreSQL
remote_state {
  backend = "pg"

  config = {
    conn_str    = "${local.state_conn_string}"
    schema_name = replace("${path_relative_to_include()}", "/", "_")
  }

  encryption = {
    key_provider = "pbkdf2"
    passphrase   = local.state_encryption_passphrase
  }

  generate = {
    path      = "opentofu_state_backend.tf"
    if_exists = "overwrite_terragrunt"
  }
}
