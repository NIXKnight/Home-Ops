locals {
  authentik_config = yamldecode(file("${var.authentik_config_file}"))
}
