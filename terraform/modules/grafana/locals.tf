locals {
  grafana_config = yamldecode(file("${var.grafana_config_file}"))
}
