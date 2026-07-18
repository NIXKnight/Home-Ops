# Seeds KV v2 SECRET VALUES into OpenBao for the cluster. First (and only) secret today:
# the authentik proxy outpost token, written to <mount>/authentik-outpost so the Phase-4
# ExternalSecret can resolve it and hand it to the cluster proxy outpost. <mount> is the ESO
# KV v2 mount, single-sourced at runtime from the sibling openbao/kv unit's config.
#
# LOCAL MODULE: like the sibling openbao/kv and openbao/kubernetes-auth units, the OpenTofu
# lives in THIS directory (main.tf / providers.tf / variables.tf / versions.tf) -- it is NOT
# a remote module, so there is no terraform{source} block and no common.hcl remote_modules
# entry.
#
# AUTH FROM ENV: address + token come from the environment via the openbao unit family's
# get_env bridge -- OPENBAO_ADDR / OPENBAO_TOKEN are read in the inputs below and passed to
# the vault provider (providers.tf), exactly as the sibling kv / kubernetes-auth units do.
# Empty-string defaults keep offline validate/plan working; apply requires both set. No
# credentials in sops or files.
#
# STATE CAVEAT: with data_json (see main.tf) the written secret values are stored in this
# unit's OpenTofu state (pbkdf2-encrypted PostgreSQL backend). Accepted repo posture; the
# data_json_wo hardening option and its trade-off are documented in main.tf.

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

locals {
  # This unit's non-secret seeding config (configurable paths + static entries), authored as
  # HCL in the Internal repo -- same mechanism the kv / kubernetes-auth / argocd units use.
  # internal_repo_path is exposed by root.hcl from TERRAGRUNT_INTERNAL_REPO_PATH.
  seeds = read_terragrunt_config("${include.root.locals.internal_repo_path}/terragrunt/openbao/secrets/unit.hcl")

  # kv_mount is single-sourced from the sibling openbao/kv unit's config so the mount this
  # unit writes into can never drift from the mount that unit creates -- identical
  # single-sourcing to how the kv unit pulls auth_path from the kubernetes-auth unit.
  kv = read_terragrunt_config("${include.root.locals.internal_repo_path}/terragrunt/openbao/kv/unit.hcl")
}

# Output-passing dependency: the proxy outpost token comes from the authentik unit's
# outpost_tokens output (map keyed by outpost name; sensitive). mock_outputs let this unit
# parse / validate / plan before authentik is applied; the placeholder is obviously fake and
# is never written anywhere real. Mirrors the kubernetes-auth unit's dependency pattern.
dependency "authentik" {
  config_path = "../../authentik"

  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan"]
  # Mocking is binary by default: once the authentik unit has ANY real state outputs,
  # mock_outputs would be ignored wholesale and a not-yet-existing output hard-errors.
  # Shallow-merge keeps real outputs authoritative while backfilling missing ones.
  mock_outputs_merge_strategy_with_state = "shallow"
  mock_outputs = {
    outpost_tokens = {
      "cluster-proxy" = "mock-outpost-token-not-real"
    }
  }
}

# Ordering only: the ESO KV v2 mount created by the sibling openbao/kv unit must exist
# before this unit writes secrets into it. That unit exposes no outputs, so a plain
# dependencies block (not an output-passing dependency) is the right tool -- identical
# reasoning to the kv unit's own dependency on kubernetes-auth.
dependencies {
  paths = ["../kv"]
}

# The `secrets` input is a map keyed by secret name (path under the mount). It merges:
#   (a) dependency-derived entries wired HERE (the authentik outpost token), and
#   (b) static entries authored in the Internal unit.hcl (empty today).
# NON-secret static secrets need only an Internal edit; the first sops-fed secret needs a
# one-time sops_decrypt_file wiring here (see the Internal unit.hcl FUTURE note).
inputs = {
  # OpenBao API address + auth token, read from the environment via the openbao unit
  # family's get_env bridge (OPENBAO_ADDR / OPENBAO_TOKEN) and passed to the vault provider
  # in providers.tf. Empty-string defaults keep offline validate/plan working; real apply
  # requires both to be set.
  openbao_addr  = get_env("OPENBAO_ADDR", "")
  openbao_token = get_env("OPENBAO_TOKEN", "")

  # KV v2 mount, single-sourced from the kv unit's eso_kv_mount (non-secret path only).
  kv_mount = local.kv.locals.openbao_kv.eso_kv_mount

  secrets = merge(
    # (a) authentik proxy outpost token -> <mount>/<authentik_outpost_path>, key "token".
    {
      (local.seeds.locals.openbao_secrets.authentik_outpost_path) = {
        data = {
          token = dependency.authentik.outputs.outpost_tokens["cluster-proxy"]
        }
      }
    },
    # (b) Internal static entries (future sops-fed secrets live here / are merged here).
    local.seeds.locals.openbao_secrets.static_secrets,
  )
}
