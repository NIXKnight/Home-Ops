# OpenBao (Vault-compatible) provider. address + token are supplied via env vars
# at apply time: OPENBAO_ADDR (https://openbao.<domain>) and OPENBAO_TOKEN (root
# or auth token). The Operator decrypts the root token from the escrowed
# secrets-openbao.yml before running terragrunt apply. Empty-string defaults in
# the terragrunt inputs keep offline validate/plan working without credentials.
provider "vault" {
  address = var.openbao_addr
  token   = var.openbao_token
}

# kubernetes provider wired from the talos-cluster dependency outputs. The inputs
# block in terragrunt.hcl passes kubeconfig_data fields (host + decoded PEM CA +
# client cert/key) to var.kube_host / var.kube_ca / var.kube_client_cert /
# var.kube_client_key. Variables hold multi-line PEM at runtime -- no heredoc
# interpolation into generated HCL, so no "Invalid multi-line string" parse error.
# Same pattern the argocd unit uses for its helm provider.
provider "kubernetes" {
  host                   = var.kube_host
  cluster_ca_certificate = var.kube_ca
  client_certificate     = var.kube_client_cert
  client_key             = var.kube_client_key
}
