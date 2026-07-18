# OpenBao (Vault-compatible) provider. AUTH FROM ENV via the openbao unit family's get_env
# bridge: the terragrunt.hcl inputs read OPENBAO_ADDR / OPENBAO_TOKEN from the environment
# and pass them as var.openbao_addr / var.openbao_token, matching the sibling openbao/kv and
# openbao/kubernetes-auth units exactly. Credentials are never sourced from sops or files.
# Empty-string input defaults keep offline validate/plan working without credentials.
#
# No kubernetes provider here: this unit only writes into OpenBao KV.
provider "vault" {
  address = var.openbao_addr
  token   = var.openbao_token
}
