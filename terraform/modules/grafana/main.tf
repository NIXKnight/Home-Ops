resource "grafana_data_source" "data_source" {
  type = "prometheus"
  name = "prometheus"
  url  = "http://localhost:9090"
}
