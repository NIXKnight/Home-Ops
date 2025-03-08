locals {
  common_vars                 = yamldecode(file("common.yaml"))
  state_conn_string           = get_env("TERRAGRUNT_STATE_CONN_STRING")
  state_encryption_passphrase = get_env("TERRAGRUNT_STATE_ENCRYPTION_PASSPHRASE")
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

inputs = {
  state_encryption_passphrase = local.state_encryption_passphrase
}
