resource "powerdns_zone" "primary" {
  for_each = { for zone in local.dns_zones : zone.name => zone }

  name       = each.value.name
  kind       = each.value.kind
  nameservers = each.value.nameservers
}

resource "powerdns_record" "record" {
  for_each = { for rec in local.dns_records : rec.record_id => rec }

  zone    = powerdns_zone.primary[each.value.zone_name].name
  name    = each.key
  type    = each.value.type
  ttl     = each.value.ttl
  records = [each.value.content]
}
