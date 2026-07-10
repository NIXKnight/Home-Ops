include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

locals {
  # KV unit config (engine mounts, ESO read subtrees, policy/role/SA names, audience)
  # lives in the Internal repo as HCL -- same mechanism the kubernetes-auth and argocd
  # units use. internal_repo_path is exposed by root.hcl from TERRAGRUNT_INTERNAL_REPO_PATH.
  kv = read_terragrunt_config("${include.root.locals.internal_repo_path}/terragrunt/openbao/kv/unit.hcl")

  # auth_path is single-sourced from the sibling kubernetes-auth unit's config so the
  # role's backend and the mounted auth backend can never drift apart.
  auth = read_terragrunt_config("${include.root.locals.internal_repo_path}/terragrunt/openbao/kubernetes-auth/unit.hcl")
}

# Ordering only: the kubernetes auth backend mounted by the kubernetes-auth unit must
# exist before this unit adds a role on it. That unit exposes no outputs, so a plain
# dependencies block (not a dependency output-passing block) is the right tool.
dependencies {
  paths = ["../kubernetes-auth"]
}

inputs = {
  # OpenBao API address + auth token come from env vars at apply time. Empty-string
  # defaults keep offline validate/plan working; real apply requires OPENBAO_ADDR and
  # OPENBAO_TOKEN (Operator decrypts the root token from the escrowed secrets-openbao.yml).
  openbao_addr  = get_env("OPENBAO_ADDR", "")
  openbao_token = get_env("OPENBAO_TOKEN", "")

  engines      = local.kv.locals.openbao_kv.engines
  eso_kv_mount = local.kv.locals.openbao_kv.eso_kv_mount
  auth_path    = local.auth.locals.openbao_config.auth_path

  eso_policy_name  = local.kv.locals.openbao_kv.eso_policy_name
  eso_role_name    = local.kv.locals.openbao_kv.eso_role_name
  eso_sa_name      = local.kv.locals.openbao_kv.eso_sa_name
  eso_sa_namespace = local.kv.locals.openbao_kv.eso_sa_namespace
  eso_audience     = local.kv.locals.openbao_kv.eso_audience

  # Subtree prefixes ESO may read, passed straight through. Each entry E is rendered by
  # the policy as data-read + metadata-read/list grants on "<E>/*" (e.g. <cluster> ->
  # kv/data/<cluster>/*), so a bare cluster prefix grants the whole in-cluster subtree.
  eso_allowed_paths = local.kv.locals.openbao_kv.eso_read_prefixes
}
