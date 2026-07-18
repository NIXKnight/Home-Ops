# authentik identity resources (OAuth2/OIDC + proxy applications, users, groups,
# service accounts, a proxy outpost, and policy bindings) for the h.nixknight.pk
# estate. Remote-source unit: the OpenTofu-Module-Authentik module is pinned in
# common.hcl, the non-secret desired state lives in the Internal repo's unit.hcl, and
# credentials come from a SOPS-encrypted file -- all merged into inputs below.
#
# SECRETS CONTRACT (SOPS + age):
#   inputs pull authentik_token and every oauth2 client_id/client_secret from
#   ${internal}/terragrunt/authentik/secrets.sops.yaml via sops_decrypt_file(). That
#   file MUST be sops-encrypted (age recipient from the Internal repo .sops.yaml)
#   BEFORE any run: sops_decrypt_file fails hard on a plaintext OR missing file, which
#   aborts config parsing (no partial apply). Decryption needs the matching age PRIVATE
#   key at ~/.config/sops/age/keys.txt or the path in $SOPS_AGE_KEY_FILE. Secret VALUES
#   never appear in this file or in the Internal unit.hcl.
#
# MODULE PIN: plan/apply require remote_modules.authentik.source in common.hcl to carry
#   a real pushed commit SHA -- it ships as REPLACE_WITH_PUSHED_SHA until the module is
#   pushed, and the git:: source will not resolve until then.
#
# STATE-KEY WARNING: applications/users/groups are for_each-keyed by their map keys, so
#   the keys in unit.hcl MUST match the live authentik object identities exactly.
#   Read the REPLACE_ME guidance in the Internal unit.hcl before the first apply.

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

locals {
  module_source_url = include.root.locals.common_vars.remote_modules.authentik.source
  authentik         = read_terragrunt_config("${include.root.locals.internal_repo_path}/terragrunt/authentik/unit.hcl")
  secrets           = yamldecode(sops_decrypt_file("${include.root.locals.internal_repo_path}/terragrunt/authentik/secrets.sops.yaml"))
}

terraform {
  source = local.module_source_url
}

# Merge order: the non-secret desired state (Internal unit.hcl module_vars) is the base;
# the SOPS layer overrides it with the provider token and injects each oauth2 app's
# client_id/client_secret by application key. try(..., {}) lets proxy apps (which have
# no secrets entry) pass through untouched. authentik_url stays as authored in unit.hcl.
inputs = merge(
  local.authentik.locals.module_vars,
  {
    authentik_token = local.secrets.authentik_token

    applications = {
      for k, v in local.authentik.locals.module_vars.applications :
      k => merge(v, try(local.secrets.oauth2[k], {}))
    }
  }
)
