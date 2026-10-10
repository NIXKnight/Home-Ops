#!/usr/bin/env python3
"""Offline structural checks for the isolated Hermes CABAL Ansible deployment."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re
import ssl
import sys

from jinja2 import Environment, StrictUndefined
import yaml


ANSIBLE = Path(__file__).resolve().parents[1]
REPO = ANSIBLE.parent
INTERNAL = REPO.parent / "Home-Ops-Internal" / "ansible"
PRIVATE_VARS = INTERNAL / "inventories/group_vars/ai_hosts/config-hermes-cabal-mcp.yml"
PRIVATE_ASSETS = INTERNAL / "files/hermes-cabal"
AGENT_ROLE = ANSIBLE / "roles/hermes_cabal_agent"
WHATSAPP_ROLE = ANSIBLE / "roles/hermes_cabal_whatsapp"
EXPECTED_CNPG_CA_SHA256 = "53169e2cc2fce8090a056422e01b01c13724bbe845fedcaf0fe665898f945e27"
EXPECTED_CNPG_SERVICE = "postgresql-lan"
EXPECTED_CNPG_HOST = "postgresql.h.nixknight.pk"
EXPECTED_CNPG_VIP = "192.168.0.172"
EXPECTED_CNPG_TRUSTED_LAN = "192.168.0.0/23"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    require(isinstance(data, dict), f"{path} must contain a YAML mapping")
    return data


def recursive_combine(base: dict, overlay: dict, recursive: bool = False) -> dict:
    result = deepcopy(base)
    for key, value in overlay.items():
        if recursive and isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = recursive_combine(result[key], value, recursive=True)
        else:
            result[key] = deepcopy(value)
    return result


def nice_yaml(value: object, indent: int = 2, sort_keys: bool = False) -> str:
    return yaml.safe_dump(value, indent=indent, sort_keys=sort_keys, default_flow_style=False)


def main() -> int:
    require((ANSIBLE / "playbooks/hermes.yml").is_file(), "standalone playbook missing")
    require(PRIVATE_VARS.is_file(), "private non-secret vars missing")

    whatsapp = load_yaml(WHATSAPP_ROLE / "defaults/main.yml")
    agent = load_yaml(AGENT_ROLE / "defaults/main.yml")
    private = load_yaml(PRIVATE_VARS)

    path_context = {**whatsapp, **agent, **private}
    path_environment = Environment(undefined=StrictUndefined)

    def resolve_path(key: str) -> str:
        value = str(path_context[key]).strip()
        for _ in range(8):
            rendered = path_environment.from_string(value).render(**path_context).strip()
            if rendered == value:
                return rendered
            value = rendered
        raise AssertionError(f"path variable did not resolve: {key}")

    expected_paths = {
        "hermes_cabal_agent_application_root": "/opt/CABAL",
        "hermes_cabal_whatsapp_application_root": "/opt/CABAL",
        "hermes_cabal_agent_root": "/opt/CABAL/hermes",
        "hermes_cabal_agent_source_dir": (
            "/opt/CABAL/hermes/src/f97608f178d1ffeca59860195ab7da295f7c8e5f"
        ),
        "hermes_cabal_agent_venv": (
            "/opt/CABAL/hermes/venvs/0.21.5-"
            "f97608f178d1ffeca59860195ab7da295f7c8e5f"
        ),
        "hermes_cabal_agent_home": "/opt/CABAL/hermes/home",
        "hermes_cabal_agent_config_dir": "/opt/CABAL/config",
        "hermes_cabal_agent_env_file": "/opt/CABAL/config/hermes.env",
        "hermes_cabal_whatsapp_root": "/opt/CABAL/whatsapp",
        "hermes_cabal_whatsapp_source_dir": (
            "/opt/CABAL/whatsapp/src/0fab6208fd6e457efcd348d97f3e84e4832357e2"
        ),
        "hermes_cabal_whatsapp_bin_dir": "/opt/CABAL/whatsapp/bin",
        "hermes_cabal_whatsapp_mcp_venv": (
            "/opt/CABAL/whatsapp/venvs/mcp-"
            "0fab6208fd6e457efcd348d97f3e84e4832357e2"
        ),
        "hermes_cabal_whatsapp_go_root": "/opt/CABAL/toolchains/go/1.25.0",
        "hermes_cabal_whatsapp_config_dir": "/opt/CABAL/config",
        "hermes_cabal_whatsapp_env_file": "/opt/CABAL/config/whatsapp-bridge.env",
        "hermes_cabal_whatsapp_database_ca_path": (
            "/opt/CABAL/config/postgresql-ca.crt"
        ),
        "hermes_cabal_whatsapp_data_dir": "/var/lib/hermes-cabal-whatsapp",
        "hermes_cabal_whatsapp_go_cache_dir": "/var/cache/hermes-cabal/go",
        "hermes_cabal_whatsapp_go_module_cache_dir": "/var/cache/hermes-cabal/mod",
    }
    for key, expected in expected_paths.items():
        require(resolve_path(key) == expected, f"deployment path drifted: {key}")
    require(
        private["hermes_cabal_agent_application_root"] == "/opt/CABAL"
        and private["hermes_cabal_whatsapp_application_root"] == "/opt/CABAL",
        "private application roots drifted",
    )

    require(
        whatsapp["hermes_cabal_whatsapp_commit"]
        == "0fab6208fd6e457efcd348d97f3e84e4832357e2",
        "WhatsApp source revision drifted",
    )
    require(
        agent["hermes_cabal_agent_commit"]
        == "f97608f178d1ffeca59860195ab7da295f7c8e5f",
        "Hermes source revision drifted",
    )
    require(agent["hermes_cabal_agent_package_version"] == "0.21.5", "Hermes package drifted")
    require(whatsapp["hermes_cabal_whatsapp_go_version"] == "1.25.0", "Go version drifted")
    require(whatsapp["hermes_cabal_whatsapp_uv_version"] == "0.9.30", "uv version drifted")

    external_database_contract = {
        "hermes_cabal_whatsapp_database_host": EXPECTED_CNPG_HOST,
        "hermes_cabal_whatsapp_database_address": EXPECTED_CNPG_VIP,
        "hermes_cabal_whatsapp_database_port": 5432,
        "hermes_cabal_whatsapp_database_name": "whatsapp_bridge",
        "hermes_cabal_whatsapp_database_user": "whatsapp_bridge",
        "hermes_cabal_whatsapp_database_sslmode": "verify-full",
        "hermes_cabal_whatsapp_database_ca_path": "/opt/CABAL/config/postgresql-ca.crt",
    }
    for key, expected in external_database_contract.items():
        require(whatsapp.get(key) == expected, f"external CNPG default drifted: {key}")
        require(private.get(key) == expected, f"external CNPG private policy drifted: {key}")
    require(whatsapp["hermes_cabal_whatsapp_database_ca_mode"] == "0644", "unsafe CA mode")
    require(
        private["hermes_cabal_whatsapp_database_ca_source"]
        == "{{ inventory_dir }}/../files/hermes-cabal/postgresql-ca.crt",
        "external CNPG CA input path drifted",
    )
    expected_ca_checksum = f"sha256:{EXPECTED_CNPG_CA_SHA256}"
    require(
        private["hermes_cabal_whatsapp_database_ca_checksum"] == expected_ca_checksum,
        "external CNPG CA checksum drifted",
    )
    ca_path = PRIVATE_ASSETS / "postgresql-ca.crt"
    require(ca_path.is_file(), "approved external CNPG public CA is absent")
    require(
        sha256(ca_path.read_bytes()).hexdigest() == EXPECTED_CNPG_CA_SHA256,
        "external CNPG public CA does not match its declared checksum",
    )
    try:
        ssl._ssl._test_decode_cert(str(ca_path))
    except (OSError, ssl.SSLError, ValueError) as exc:
        raise AssertionError("external CNPG public CA is not valid X.509") from exc
    require(
        not any("password" in key.lower() for key in private),
        "private non-secret policy contains a password field",
    )

    require(agent["hermes_cabal_agent_hindsight_tools"] == ["retain", "recall", "reflect"], "Hindsight allowlist drifted")
    require(agent["hermes_cabal_agent_whatsapp_tools"] == ["get_messages", "get_contact"], "WhatsApp allowlist drifted")

    for key in (
        "hermes_cabal_whatsapp_database_prepare",
        "hermes_cabal_whatsapp_database_accepted",
        "hermes_cabal_whatsapp_database_backup_accepted",
        "hermes_cabal_whatsapp_database_tls_accepted",
        "hermes_cabal_whatsapp_database_pg18_migration_accepted",
        "hermes_cabal_whatsapp_bridge_enabled",
        "hermes_cabal_whatsapp_pairing_accepted",
        "hermes_cabal_whatsapp_readiness_accepted",
        "hermes_cabal_agent_service_enabled",
        "hermes_cabal_agent_activation_accepted",
        "hermes_cabal_agent_whatsapp_mcp_enabled",
    ):
        require(private.get(key) is False, f"unsafe default in private policy: {key}")

    base = private["hermes_cabal_agent_profile_base"]
    require(not ({"plugins", "memory", "mcp_servers"} & set(base)), "unsanitized profile root copied")
    require(base["providers"]["inference"]["api"] == "https://bifrost.h.nixknight.pk/v1", "Bifrost route drifted")
    require(base["providers"]["inference"]["key_env"] == "BIFROST_HERMES_VIRTUAL_KEY", "Bifrost key reference drifted")
    require(base["model"]["default"] == "llamacpp/qwen3.8-27b", "model drifted")
    require(base["fallback_providers"] == [], "fallback providers must remain empty")
    required_disabled = set(agent["hermes_cabal_agent_required_disabled_toolsets"])
    require(required_disabled <= set(base["agent"]["disabled_toolsets"]), "prepared safeguards were weakened")

    expected_agent_assets = {
        "AGENTS.md",
        "SOUL.md",
        "memories/USER.md",
        "skills/pi-roles/ansible-engineer/SKILL.md",
        "skills/pi-roles/kubernetes-specialist/SKILL.md",
        "skills/pi-roles/security-engineer/SKILL.md",
        "skills/pi-roles/terraform-engineer/SKILL.md",
    }
    expected_private_assets = expected_agent_assets | {"postgresql-ca.crt"}
    actual_assets = {
        str(path.relative_to(PRIVATE_ASSETS))
        for path in PRIVATE_ASSETS.rglob("*")
        if path.is_file()
    }
    require(actual_assets == expected_private_assets, "private asset allowlist is not exact")
    declared_assets = {item["dest"] for item in private["hermes_cabal_agent_private_assets"]}
    require(declared_assets == expected_agent_assets, "private copy declarations are not exact")

    template = (AGENT_ROLE / "templates/config.yaml.j2").read_text(encoding="utf-8")
    for token in (
        "Bearer ${env:",
        "'trust': 'untrusted'",
        "'resources': false",
        "'prompts': false",
        "'sampling'",
        "'elicitation'",
        "'MCP_TRANSPORT': 'stdio'",
        "'LOG_LEVEL': 'WARNING'",
        "'--no-sync'",
    ):
        require(token in template, f"managed MCP policy token missing: {token}")

    render_context = {
        **agent,
        **private,
        "ansible_managed": "offline contract test",
        "hermes_cabal_agent_whatsapp_uv": "/home/saadali/.local/bin/uv",
        "hermes_cabal_agent_whatsapp_python": "/usr/bin/python3.13",
        "hermes_cabal_agent_whatsapp_project": (
            "/opt/CABAL/whatsapp/src/"
            "0fab6208fd6e457efcd348d97f3e84e4832357e2/mcp-server"
        ),
        "hermes_cabal_agent_whatsapp_venv": (
            "/opt/CABAL/whatsapp/venvs/mcp-"
            "0fab6208fd6e457efcd348d97f3e84e4832357e2"
        ),
    }
    environment = Environment(undefined=StrictUndefined)
    environment.filters.update(combine=recursive_combine, to_nice_yaml=nice_yaml, bool=bool)
    rendered = environment.from_string(template).render(**render_context)
    rendered_config = yaml.safe_load(rendered)
    require(rendered_config["plugins"]["enabled"] == [], "plugins must remain disabled")
    require("provider" not in rendered_config["memory"], "native memory provider leaked into config")
    require(rendered_config["memory"]["memory_enabled"] is False, "native memory capture must be off")
    require(
        rendered_config["mcp_servers"]["hindsight"]["headers"]["Authorization"]
        == "Bearer ${env:HINDSIGHT_API_KEY}",
        "Hindsight header must remain an environment reference",
    )
    for server_name, expected_tools in (
        ("hindsight", ["retain", "recall", "reflect"]),
        ("whatsapp_readonly", ["get_messages", "get_contact"]),
    ):
        server = rendered_config["mcp_servers"][server_name]
        require(server["trust"] == "untrusted", f"{server_name} trust widened")
        require(server["tools"]["include"] == expected_tools, f"{server_name} allowlist drifted")
        require(server["tools"]["resources"] is False, f"{server_name} resources enabled")
        require(server["tools"]["prompts"] is False, f"{server_name} prompts enabled")
        require(server["sampling"]["enabled"] is False, f"{server_name} sampling enabled")
        require(server["elicitation"]["enabled"] is False, f"{server_name} elicitation enabled")

    created_text = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (AGENT_ROLE, WHATSAPP_ROLE)
        for path in root.rglob("*")
        if path.is_file()
    )
    for rejected in (
        "roles/whatsapp_mcp_server",
        "roles/hermes_whatsapp_receiver",
        "playbooks/whatsapp-hermes.yml",
        "config-whatsapp-hermes.yml",
    ):
        require(rejected not in created_text, f"new roles depend on rejected architecture: {rejected}")
    require(":3000" not in created_text, "forbidden WhatsApp MCP HTTP listener found")

    private_text = PRIVATE_VARS.read_text(encoding="utf-8")
    endpoint_contract_surfaces = {
        "deployment guide": (ANSIBLE / "docs/hermes-cabal.md").read_text(
            encoding="utf-8"
        ),
        "role README": (WHATSAPP_ROLE / "README.md").read_text(encoding="utf-8"),
        "role defaults comments": (WHATSAPP_ROLE / "defaults/main.yml").read_text(
            encoding="utf-8"
        ),
        "private non-secret policy comments": private_text,
    }
    for surface, text in endpoint_contract_surfaces.items():
        require(EXPECTED_CNPG_SERVICE in text, f"generic CNPG Service missing: {surface}")
        require(EXPECTED_CNPG_HOST in text, f"CNPG DNS name missing: {surface}")
        require(EXPECTED_CNPG_VIP in text, f"CNPG VIP missing: {surface}")
        require(
            EXPECTED_CNPG_TRUSTED_LAN in text,
            f"trusted CNPG LAN source range missing: {surface}",
        )
        require("whatsapp_bridge" in text, f"bridge database identity missing: {surface}")
    for stale_endpoint_contract in (
        "postgresql-whatsapp-lan",
        "192.168.0.154/32",
        "admits only cawl-inferior",
    ):
        require(
            all(
                stale_endpoint_contract not in text
                for text in endpoint_contract_surfaces.values()
            ),
            f"cawl-specific CNPG endpoint contract remains: {stale_endpoint_contract}",
        )

    deployment_text = created_text + "\n" + private_text
    for obsolete_path in (
        "/opt/hermes-cabal-agent",
        "/opt/hermes-cabal-whatsapp",
        "/opt/hermes-toolchains",
        "/etc/hermes-cabal",
        "/etc/whatsapp-hermes",
        "/home/saadali/.hermes-cabal",
    ):
        require(obsolete_path not in deployment_text, f"obsolete deployment path remains: {obsolete_path}")

    agent_preflight_data = yaml.safe_load(
        (AGENT_ROLE / "tasks/preflight.yml").read_text(encoding="utf-8")
    )
    agent_dirs_task = next(
        task
        for task in agent_preflight_data
        if task.get("name") == "Create versioned Hermes directories"
    )
    agent_dirs = {item["path"]: item for item in agent_dirs_task["loop"]}
    for path_key in (
        "{{ hermes_cabal_agent_application_root }}",
        "{{ hermes_cabal_agent_root }}",
        "{{ hermes_cabal_agent_root }}/src",
        "{{ hermes_cabal_agent_config_dir }}",
    ):
        item = agent_dirs[path_key]
        require(
            item["owner"] == "root"
            and item["group"] == "root"
            and item["mode"] == "0755",
            f"Hermes shared path is not root-owned and traversable: {path_key}",
        )
    agent_venvs = agent_dirs["{{ hermes_cabal_agent_root }}/venvs"]
    require(
        agent_venvs["owner"] == "{{ hermes_cabal_agent_user }}"
        and agent_venvs["group"] == "{{ hermes_cabal_agent_group }}"
        and agent_venvs["mode"] == "0755",
        "Hermes venv hierarchy is not writable by saadali",
    )

    agent_home_tasks = (AGENT_ROLE / "tasks/home.yml").read_text(encoding="utf-8")
    agent_home_data = yaml.safe_load(agent_home_tasks)
    home_layout_task = next(
        task
        for task in agent_home_data
        if task.get("name") == "Create the isolated Hermes home layout"
    )
    home_file = home_layout_task["ansible.builtin.file"]
    require(home_file["owner"] == "{{ hermes_cabal_agent_user }}", "Hermes home owner drifted")
    require(home_file["group"] == "{{ hermes_cabal_agent_group }}", "Hermes home group drifted")
    require(home_file["mode"] == "0700", "Hermes home is not private")

    agent_env_task = next(
        task
        for task in agent_home_data
        if task.get("name") == "Install the root-owned Hermes service environment"
    )
    agent_env_copy = agent_env_task["ansible.builtin.copy"]
    require(
        agent_env_copy["owner"] == "root"
        and agent_env_copy["group"] == "root"
        and agent_env_copy["mode"] == "0600",
        "Hermes environment is not root-only",
    )

    agent_service_template = (
        AGENT_ROLE / "templates/hermes-cabal.service.j2"
    ).read_text(encoding="utf-8")
    require(
        "EnvironmentFile={{ hermes_cabal_agent_env_file }}" in agent_service_template,
        "Hermes unit does not use the relocated environment",
    )
    agent_service_data = yaml.safe_load(
        (AGENT_ROLE / "tasks/service.yml").read_text(encoding="utf-8")
    )
    agent_unit_task = next(
        task
        for task in agent_service_data
        if task.get("name") == "Install the Ansible-owned Hermes gateway system unit"
    )
    require(
        agent_unit_task["ansible.builtin.template"]["dest"]
        == "/etc/systemd/system/{{ hermes_cabal_agent_service_name }}",
        "Hermes system unit destination drifted",
    )

    database_tasks = (WHATSAPP_ROLE / "tasks/database.yml").read_text(encoding="utf-8")
    whatsapp_preflight = (WHATSAPP_ROLE / "tasks/preflight.yml").read_text(encoding="utf-8")
    whatsapp_preflight_data = yaml.safe_load(whatsapp_preflight)
    service_template = (
        WHATSAPP_ROLE / "templates/hermes-cabal-whatsapp-bridge.service.j2"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "pgvector",
        "5433",
        "community.docker",
        "docker_volume",
        "docker_container",
        "sslmode=disable",
        "ALTER ROLE",
        "CREATE EXTENSION",
    ):
        require(forbidden not in created_text, f"removed local database contract remains: {forbidden}")
    require("docker.service" not in service_template, "bridge service still requires Docker")
    require(
        "EnvironmentFile={{ hermes_cabal_whatsapp_env_file }}" in service_template,
        "bridge unit does not use the relocated environment",
    )
    require(
        "User={{ hermes_cabal_whatsapp_service_user }}" in service_template,
        "bridge unit does not drop to its service account",
    )
    bridge_task_data = yaml.safe_load(
        (WHATSAPP_ROLE / "tasks/bridge.yml").read_text(encoding="utf-8")
    )
    bridge_build_task = next(
        task
        for task in bridge_task_data
        if task.get("name") == "Build the reproducible static WhatsApp bridge"
    )
    require(
        bridge_build_task["environment"]["GOCACHE"]
        == "{{ hermes_cabal_whatsapp_go_cache_dir }}"
        and bridge_build_task["environment"]["GOMODCACHE"]
        == "{{ hermes_cabal_whatsapp_go_module_cache_dir }}",
        "Go caches escaped /var/cache/hermes-cabal",
    )
    bridge_unit_task = next(
        task
        for task in bridge_task_data
        if task.get("name") == "Install the isolated bridge system service"
    )
    require(
        bridge_unit_task["ansible.builtin.template"]["dest"]
        == "/etc/systemd/system/{{ hermes_cabal_whatsapp_service_name }}",
        "bridge system unit destination drifted",
    )
    whatsapp_dirs_task = next(
        task
        for task in whatsapp_preflight_data
        if task.get("name") == "Create private WhatsApp stack directories"
    )
    whatsapp_dirs = {
        item["path"]: item for item in whatsapp_dirs_task["loop"]
    }
    for path_key in (
        "{{ hermes_cabal_whatsapp_application_root }}",
        "{{ hermes_cabal_whatsapp_root }}",
        "{{ hermes_cabal_whatsapp_root }}/src",
        "{{ hermes_cabal_whatsapp_bin_dir }}",
        "{{ hermes_cabal_whatsapp_config_dir }}",
    ):
        item = whatsapp_dirs[path_key]
        require(
            item["owner"] == "root"
            and item["group"] == "root"
            and item["mode"] == "0755",
            f"shared path is not root-owned and traversable: {path_key}",
        )
    whatsapp_venvs = whatsapp_dirs["{{ hermes_cabal_whatsapp_root }}/venvs"]
    require(
        whatsapp_venvs["owner"] == "{{ hermes_cabal_whatsapp_owner }}"
        and whatsapp_venvs["group"] == "{{ hermes_cabal_whatsapp_owner }}"
        and whatsapp_venvs["mode"] == "0755",
        "WhatsApp MCP venv hierarchy is not writable by saadali",
    )
    bridge_state = whatsapp_dirs["{{ hermes_cabal_whatsapp_data_dir }}"]
    require(
        bridge_state["owner"] == "{{ hermes_cabal_whatsapp_service_user }}"
        and bridge_state["group"] == "{{ hermes_cabal_whatsapp_service_group }}"
        and bridge_state["mode"] == "0700",
        "bridge state is not private to its service account",
    )
    bridge_binary_task = next(
        task
        for task in bridge_task_data
        if task.get("name") == "Set immutable bridge binary ownership"
    )
    bridge_binary_file = bridge_binary_task["ansible.builtin.file"]
    require(
        bridge_binary_file["owner"] == "root"
        and bridge_binary_file["group"] == "root"
        and bridge_binary_file["mode"] == "0755",
        "bridge binary is not root-owned and executable",
    )
    bridge_activation_task = next(
        task
        for task in whatsapp_preflight_data
        if task.get("name") == "Require explicit bridge activation gates"
    )
    require(
        bridge_activation_task.get("when") == "hermes_cabal_whatsapp_bridge_enabled | bool"
        and "hermes_cabal_whatsapp_database_prepare | bool"
        in bridge_activation_task["ansible.builtin.assert"]["that"],
        "bridge activation is not bound to database preparation",
    )
    for required in (
        "# {mark} ANSIBLE MANAGED HERMES CABAL WHATSAPP CNPG",
        "-verify_hostname",
        "-verify_return_error",
        "PGPASSWORD:",
        "PGSSLMODE:",
        "PGSSLROOTCERT:",
        "SELECT 1;",
        "'?sslmode='",
        "'&sslrootcert='",
        "unsafe_writes: false",
        "SECURITY (binding, Section 14)",
    ):
        require(required in database_tasks, f"external CNPG contract token missing: {required}")
    require(
        "hermes_cabal_whatsapp_database_password }}" not in "\n".join(
            line for line in database_tasks.splitlines() if line.lstrip().startswith("-")
        ),
        "database password appears in a command argv item",
    )
    require(
        "hermes_cabal_whatsapp_database_pg18_migration_accepted"
        in (AGENT_ROLE / "tasks/preflight.yml").read_text(encoding="utf-8"),
        "WhatsApp MCP is not bound to the PostgreSQL 18 migration gate",
    )

    database_task_data = yaml.safe_load(database_tasks)
    hosts_cleanup_task = next(
        task
        for task in database_task_data
        if task.get("name") == "Remove the obsolete external CNPG marked hosts block"
    )
    hosts_cleanup = hosts_cleanup_task["ansible.builtin.blockinfile"]
    require(
        hosts_cleanup == {
            "path": "/etc/hosts",
            "marker": "# {mark} ANSIBLE MANAGED HERMES CABAL WHATSAPP CNPG",
            "state": "absent",
        }
        and hosts_cleanup_task.get("when")
        == "hermes_cabal_whatsapp_database_prepare | bool",
        "legacy CNPG hosts cleanup contract drifted",
    )
    require(
        database_tasks.count("/etc/hosts") == 1,
        "/etc/hosts must remain cleanup-only system integration",
    )
    dns_probe_task = next(
        task
        for task in database_task_data
        if task.get("name") == "Resolve the external CNPG DNS target"
    )
    require(
        dns_probe_task["ansible.builtin.command"]["argv"]
        == [
            "getent",
            "ahostsv4",
            "{{ hermes_cabal_whatsapp_database_host }}",
        ]
        and dns_probe_task.get("when")
        == "hermes_cabal_whatsapp_database_prepare | bool",
        "external CNPG DNS target probe drifted",
    )
    dns_status_task = next(
        task
        for task in database_task_data
        if task.get("name") == "Record external CNPG DNS target status"
    )
    dns_status = dns_status_task["ansible.builtin.set_fact"][
        "hermes_cabal_whatsapp_database_resolution_valid"
    ]
    require(
        "regex_findall" in dns_status
        and "== [hermes_cabal_whatsapp_database_address]" in dns_status
        and dns_status_task.get("when")
        == "hermes_cabal_whatsapp_database_prepare | bool",
        "external CNPG DNS target is not restricted to the approved VIP",
    )
    dns_assert_task = next(
        task
        for task in database_task_data
        if task.get("name") == "Require the approved external CNPG DNS target"
    )
    require(
        dns_assert_task["ansible.builtin.assert"]["that"]
        == ["hermes_cabal_whatsapp_database_resolution_valid | bool"]
        and "DNS target" in dns_assert_task["ansible.builtin.assert"]["fail_msg"],
        "external CNPG DNS target assertion drifted",
    )
    require(
        "hosts mapping" not in database_tasks.lower(),
        "database diagnostics still describe an active hosts mapping",
    )
    ca_input_task = next(
        task
        for task in database_task_data
        if task.get("name") == "Require the approved external CNPG CA inputs"
    )
    require(
        ca_input_task.get("when") == "hermes_cabal_whatsapp_database_prepare | bool",
        "CA input validation is not bound to database preparation",
    )
    require(
        "hermes_cabal_whatsapp_database_ca_source | length > 0"
        in ca_input_task["ansible.builtin.assert"]["that"],
        "database preparation permits an absent CA source",
    )
    require(
        any(
            "^sha256:[0-9a-f]{64}$" in condition
            for condition in ca_input_task["ansible.builtin.assert"]["that"]
        ),
        "database preparation permits a malformed CA checksum",
    )
    ca_match_task = next(
        task
        for task in database_task_data
        if task.get("name") == "Require the approved external CNPG CA source and checksum to match"
    )
    ca_match_conditions = ca_match_task["ansible.builtin.assert"]["that"]
    require(
        ca_match_task.get("when") == "hermes_cabal_whatsapp_database_prepare | bool"
        and any(".stat.exists" in condition for condition in ca_match_conditions)
        and any(".stat.isreg" in condition for condition in ca_match_conditions)
        and any(".stat.checksum" in condition for condition in ca_match_conditions),
        "database preparation does not reject an absent or mismatched CA file",
    )
    tls_probe_task = next(
        task
        for task in database_task_data
        if task.get("name") == "Probe external CNPG TLS hostname verification"
    )
    tls_argv = tls_probe_task["ansible.builtin.command"]["argv"]
    tls_assert_task = next(
        task
        for task in database_task_data
        if task.get("name") == "Require external CNPG TLS hostname verification"
    )
    require(
        "-CAfile" in tls_argv
        and "{{ hermes_cabal_whatsapp_database_ca_path }}" in tls_argv
        and tls_probe_task.get("when") == "hermes_cabal_whatsapp_database_prepare | bool"
        and tls_assert_task.get("when") == "hermes_cabal_whatsapp_database_prepare | bool"
        and "hermes_cabal_whatsapp_database_tls_valid | bool"
        in tls_assert_task["ansible.builtin.assert"]["that"],
        "database preparation does not reject a malformed CA during TLS verification",
    )
    ca_install_task = next(
        task
        for task in database_task_data
        if task.get("name") == "Install the approved external CNPG CA atomically"
    )
    ca_copy = ca_install_task["ansible.builtin.copy"]
    require(
        ca_copy["owner"] == "root"
        and ca_copy["group"] == "root"
        and whatsapp["hermes_cabal_whatsapp_database_ca_mode"] == "0644",
        "public CA is not root-owned and bridge-readable",
    )
    env_task = next(
        task
        for task in database_task_data
        if task.get("name") == "Render the root-only external CNPG bridge environment"
    )
    env_copy = env_task["ansible.builtin.copy"]
    synthetic_password = "A" * 32
    rendered_env = environment.from_string(env_copy["content"]).render(
        **whatsapp,
        hermes_cabal_whatsapp_database_password=synthetic_password,
    )
    expected_dsn = (
        "DATABASE_URL=postgresql://whatsapp_bridge:"
        f"{synthetic_password}@{EXPECTED_CNPG_HOST}:5432/"
        "whatsapp_bridge?sslmode=verify-full&"
        "sslrootcert=/opt/CABAL/config/postgresql-ca.crt"
    )
    require(rendered_env.splitlines()[0] == expected_dsn, "external CNPG DSN rendering drifted")
    require(env_copy["owner"] == "root", "bridge environment is not root-owned")
    require(env_copy["group"] == "root", "bridge environment group is not root")
    require(env_copy["mode"] == "0600", "bridge environment is not root-only")
    require(env_task.get("no_log") is True, "bridge environment rendering is not no_log")
    require(env_task.get("diff") is False, "bridge environment rendering allows diff output")

    agent_home_tasks = (AGENT_ROLE / "tasks/home.yml").read_text(encoding="utf-8")
    whatsapp_database_tasks = (WHATSAPP_ROLE / "tasks/database.yml").read_text(
        encoding="utf-8"
    )
    require(
        "vars.get(hermes_cabal_agent_bifrost_key_env, '')" in agent_home_tasks
        and "vars.get(hermes_cabal_agent_hindsight_key_env, '')" in agent_home_tasks,
        "Hermes encrypted-inventory precedence is missing",
    )
    require(
        "vars.get(hermes_cabal_whatsapp_database_password_env, '')"
        in whatsapp_database_tasks,
        "WhatsApp database encrypted-inventory precedence is missing",
    )

    lookup_files = []
    for path in (AGENT_ROLE / "tasks").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if "lookup('ansible.builtin.env'" in text:
            lookup_files.append(path)
            require(re.search(r"no_log:\s*true", text), f"controller lookup lacks no_log: {path}")
    for path in (WHATSAPP_ROLE / "tasks").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if "lookup('ansible.builtin.env'" in text:
            lookup_files.append(path)
            require(re.search(r"no_log:\s*true", text), f"controller lookup lacks no_log: {path}")
    require(len(lookup_files) == 2, "unexpected controller environment lookup surface")

    print("Hermes CABAL offline contract checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"contract check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
