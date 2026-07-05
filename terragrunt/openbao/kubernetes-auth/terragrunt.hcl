include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

locals {
  # OpenBao unit config (reviewer SA name/namespace, auth backend path) lives in the
  # Internal repo as HCL -- same mechanism the talos-cluster and argocd units use.
  # internal_repo_path is exposed by root.hcl from TERRAGRUNT_INTERNAL_REPO_PATH.
  openbao = read_terragrunt_config("${include.root.locals.internal_repo_path}/terragrunt/openbao/kubernetes-auth/unit.hcl")
}

# Wire the live Kubernetes API connection details from the Talos cluster unit.
# kubeconfig_data carries already base64-DECODED PEM strings (host + CA + client
# cert/key), so they pass straight to the kubernetes provider -- NO base64decode.
# mock_outputs cover offline init/validate/plan; they are dummy values only.
dependency "talos_cluster" {
  config_path = "../../talos-cluster"

  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan"]
  mock_outputs = {
    kubeconfig_data = {
      host                   = "https://127.0.0.1:6443"
      cluster_ca_certificate = "mock"
      client_certificate     = "mock"
      client_key             = "mock"
    }
  }
}

inputs = {
  # OpenBao API address + auth token come from env vars at apply time. Empty-string
  # defaults keep offline validate/plan working; real apply requires OPENBAO_ADDR
  # and OPENBAO_TOKEN to be set (Operator decrypts the root token from the escrowed
  # secrets-openbao.yml). The vault provider in providers.tf reads these vars.
  openbao_addr  = get_env("OPENBAO_ADDR", "")
  openbao_token = get_env("OPENBAO_TOKEN", "")

  kube_host        = dependency.talos_cluster.outputs.kubeconfig_data.host
  kube_ca          = dependency.talos_cluster.outputs.kubeconfig_data.cluster_ca_certificate
  kube_client_cert = dependency.talos_cluster.outputs.kubeconfig_data.client_certificate
  kube_client_key  = dependency.talos_cluster.outputs.kubeconfig_data.client_key

  reviewer_sa_name      = local.openbao.locals.openbao_config.reviewer_sa_name
  reviewer_sa_namespace = local.openbao.locals.openbao_config.reviewer_sa_namespace
  auth_path             = local.openbao.locals.openbao_config.auth_path
}
