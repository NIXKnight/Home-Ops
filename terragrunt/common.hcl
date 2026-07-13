# Common Terragrunt data (remote module sources) shared across units.
# Converted from common.yaml. Consumed in root.hcl via:
#   read_terragrunt_config(find_in_parent_folders("common.hcl")).locals
#
# The former `tags` map had zero consumers and was intentionally dropped.
#
# The module ref is pinned to the current main HEAD of
# OpenTofu-Module-Proxmox-VM (was `?ref=main`). Pinning to that exact commit
# resolves to identical module code, so the plan stays empty while gaining
# reproducibility.
locals {
  remote_modules = {
    proxmox_vm = {
      source = "git::https://github.com/NIXKnight/OpenTofu-Module-Proxmox-VM.git?ref=38a11f4badff2feb81a14cefb65f5889227613a9"
    }

    talos = {
      source = "git::https://github.com/NIXKnight/OpenTofu-Module-Talos-Baremetal.git?ref=19a768cde4aed9253da62210b492a5a1692a8b45"
    }
  }
}
