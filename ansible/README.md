# Home-Ops Ansible

Ansible automation for the Home-Ops infrastructure: base OS configuration plus
containerized network, DNS/DHCP, data, identity, and monitoring services.

## Layout

```
ansible/
├── ansible.cfg                 # inventory (default), roles_path, collections_path, vault_password_file
├── playbooks/                  # one flat playbook per service (<name>.yml)
│   ├── setup_system.yml        # base OS baseline (linux-common + motd)
│   ├── site.yml                # full orchestration (imports the deploy set)
│   ├── <svc>.yml               # one deploy playbook per service
│   ├── manage_db_users.yml     # auxiliary: PostgreSQL DB users (pgsql_dbs_users)
│   └── proxmox-vm-template.yml # Proxmox VM template build — not in site.yml
├── roles/firewall/             # custom in-repo role
├── external-roles/             # third-party roles (installed, git-ignored)
├── collections/                # Ansible collections (installed, git-ignored)
├── requirements/               # collection / role / python dependency manifests
├── scripts/                    # helper scripts (Vault auto-unseal, deye fetch)
└── templates/                  # Jinja2 templates consumed by service roles
```

The inventory and all public, non-secret variables now live **outside this
repository**, in the private sibling, as a single default inventory:

```
../../Home-Ops-Internal/ansible/inventories/
├── hosts.yml                   # all groups in one file (placeholder hosts — see caveat)
└── group_vars/                 # public, non-secret variables, keyed by group name
    ├── all.yml                 # cross-service common vars (auto-loaded on every play)
    ├── <group>.yml             # single-file group (acme, firewall, nginx, pihole, powerdns, authentik, data_services)
    ├── kea/                    # multi-file group (kea, kea_dhcp4_config, kea_dhcp6_config)
    ├── monitoring/             # multi-file group (monitoring, prometheus_config, telegraf_config, grafana_config)
    └── proxmox/                # vm_template.yml
```

`ansible.cfg` sets this directory as the default inventory
(`inventory = ../../Home-Ops-Internal/ansible/inventories`), so no `-i` is
needed. Because `group_vars/` is keyed by group name, Ansible auto-loads a
group's variables for any play scoped to that group (and `all.yml` for every
play); playbooks only declare `vars_files` for external secrets and for
cross-service public vars their own group would not auto-load.

## Prerequisites

- Python 3.12+ and `pip`.
- `git` and SSH access to the managed hosts.
- The external secrets directory (see [Secrets](#secrets)).

Install the toolchain and dependencies (run from `ansible/`):

```bash
# Python toolchain (ansible, ansible-lint, yamllint, molecule, ...)
pip install -r requirements/requirements.txt

# Ansible collections (nixknight.docker / .opentofu / .general)
ansible-galaxy collection install -p collections -r requirements/collections.yml

# Third-party roles (motd, pgsql_dbs_users, acme)
ansible-galaxy install --roles-path external-roles -r requirements/roles.yml
```

`ansible.cfg` already points `inventory` at
`../../Home-Ops-Internal/ansible/inventories`, `roles_path` at
`./roles:./external-roles`, `collections_path` at `./collections`, and
`vault_password_file` at `./.ansible_vault_password`, so the install targets
above match what Ansible loads at runtime.

## Service matrix

| Service | Group | Playbook | Deploys |
|---|---|---|---|
| (base) | `all` | `playbooks/setup_system.yml` | linux-common baseline + motd |
| acme | `acme` | `playbooks/acme.yml` | ACME / Let's Encrypt certificates |
| firewall | `firewall` | `playbooks/firewall.yml` | nftables firewall (custom `roles/firewall`) |
| kea | `kea` | `playbooks/kea.yml` | Kea DHCPv4/DHCPv6 + radvd |
| pihole | `pihole` | `playbooks/pihole.yml` | Pi-hole DNS sinkhole |
| powerdns | `powerdns` | `playbooks/powerdns.yml` | PowerDNS auth + recursor; second play on `localhost` runs OpenTofu (DNS zones) |
| nginx | `nginx` | `playbooks/nginx.yml` | nginx reverse-proxy vhosts |
| data-services | `data_services` | `playbooks/data-services.yml` | PostgreSQL + Redis (docker-compose-service); DB users via `pgsql_dbs_users` |
| (db users) | `data_services` | `playbooks/manage_db_users.yml` | Auxiliary PostgreSQL DB/users/privileges only (`pgsql_dbs_users`); no container redeploy |
| authentik | `authentik` | `playbooks/authentik.yml` | Authentik IdP; second play on `localhost` runs OpenTofu (users/groups/apps) |
| monitoring | `monitoring` | `playbooks/monitoring.yml` | Prometheus + Telegraf + Grafana |
| proxmox | `proxmox` + `chroot` | `playbooks/proxmox-vm-template.yml` | Proxmox VM template build (chroot/nbd) — **not** in `site.yml` |

Notes:

- `playbooks/manage_db_users.yml` is a focused entry point for the
  `pgsql_dbs_users` role — run it to reconcile PostgreSQL databases, users, and
  privileges without redeploying the data-services container.
- `monitoring`'s `group_vars/monitoring/grafana_config.yml` is auto-loaded with
  the rest of the `monitoring` group vars; Grafana config is applied via the
  service role's own data, not an explicit `vars_files` entry.
- `proxmox-vm-template.yml` builds a VM template via NBD mount + `chroot`
  (`connection: community.general.chroot`); its `proxmox` group is populated at
  runtime via `add_host`. It is deliberately excluded from `site.yml`. Its plays
  run on `localhost`/`chroot`/`proxmox` (not the `proxmox` group's static
  members), so they load `group_vars/proxmox/vm_template.yml` explicitly via
  `vars_files` rather than by auto-load.

Public, non-secret variables for each service live in the external inventory's
`group_vars/`, keyed by group name (see [Layout](#layout)). Which external
secret files each playbook loads is declared in that playbook's `vars_files`
(paths only — see [Secrets](#secrets)).

## Running

General run pattern (from `ansible/`). The default inventory is set in
`ansible.cfg`, so no `-i` is required:

```bash
ansible-playbook playbooks/<svc>.yml
```

Single service (example: Pi-hole):

```bash
ansible-playbook playbooks/pihole.yml
```

Full deployment set in dependency order:

```bash
ansible-playbook playbooks/site.yml
```

`site.yml` imports, in order: `setup_system`, `acme`, `firewall`,
`data-services`, `kea`, `pihole`, `powerdns`, `nginx`, `authentik`,
`monitoring`. `proxmox-vm-template.yml` is intentionally excluded — it builds an
image template (chroot + NBD) and is run on its own.

### Validate before deploying

Syntax-check any playbook without touching a host:

```bash
ansible-playbook --syntax-check playbooks/pihole.yml
ansible-playbook --syntax-check playbooks/site.yml
```

Inspect the inventory:

```bash
ansible-inventory --graph
```

## Placeholder-host caveat

The external `inventories/hosts.yml` ships **placeholder** hosts with
`ansible_host: REPLACE_ME`. They exist so the inventory parses and group
membership is documented; they do **not** point at real machines. Before
running against real infrastructure, either:

- edit `hosts.yml` and set the real host / `ansible_host`, or
- override on the command line with a real target, e.g. `-i 'real-host,'`.

(The `proxmox` and `chroot` groups are the exception: the `proxmox` group is
populated at runtime via `add_host` from an external node list, and the `chroot`
group mounts a local path — neither uses the `REPLACE_ME` placeholder.)

## Secrets

Sensitive variables live **outside this repository, as plaintext**, at:

```
../../Home-Ops-Internal/ansible/Linux-Firewall-Gateway/
```

Playbooks reference these via `vars_files` using
`{{ ansible_config_file | dirname }}/../../Home-Ops-Internal/...`. The
`Home-Ops-Internal` directory must exist alongside the repo (one level up from
`Home-Ops/`) — it now holds **both** the default inventory
(`ansible/inventories/`) and the external secrets
(`ansible/Linux-Firewall-Gateway/`) — for service playbooks to run. Each
playbook's `vars_files` lists which external secret files it loads (paths only).

`ansible.cfg` also sets `vault_password_file = ./.ansible_vault_password` for any
Ansible Vault-encrypted content. Do not read, print, or commit that file or
anything under `Home-Ops-Internal/`.

## Linting

```bash
yamllint ansible/        # style (config: ansible/.yamllint)
ansible-lint             # run from ansible/ (config: ansible/.ansible-lint)
```

CI runs both on every push / PR that touches `ansible/**`
(`.github/workflows/ansible-lint.yml` at the repo root).
