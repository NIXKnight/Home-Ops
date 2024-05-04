locals {
  dns_zones = yamldecode(file(var.dns_config_file))["dns_zones"]

  # Flatten records with reference to their zone
  dns_records = toset(
    flatten(
      [
        for zone in local.dns_zones : [
          for record in zone.records : {
            zone_name = zone.name
            record_id = "${record.name}.${zone.name}"
            name      = record.name
            type      = record.type
            ttl       = record.ttl
            content   = record.content
          }
        ]
      ]
    )
  )
}
