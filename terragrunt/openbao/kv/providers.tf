# OpenBao (Vault-compatible) provider. address + token are supplied via env vars
# at apply time: OPENBAO_ADDR (https://openbao.<domain>) and OPENBAO_TOKEN (root
# or auth token). The Operator decrypts the root token from the escrowed
# secrets-openbao.yml before running terragrunt apply. Empty-string defaults in
# the terragrunt inputs keep offline validate/plan working without credentials.
# No kubernetes provider here: this unit only writes into OpenBao. The ESO
# ServiceAccount + auth-delegator binding it targets are delivered by the ESO
# chart, and the reviewer SA/backend by the sibling kubernetes-auth unit.
provider "vault" {
  address = var.openbao_addr
  token   = var.openbao_token
}
