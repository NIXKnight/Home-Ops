#!/usr/bin/env python3
"""Read-only, offline validation for the Hindsight activation configuration.

``--scaffolding`` validates authored structure without evaluating activation gates.
Default mode additionally reports fixed activation categories. Neither mode reads
secret values, contacts a cluster, or proves runtime readiness. Only explicitly named
files below caller-approved roots are read.
"""

from __future__ import annotations

import argparse
import copy
import ipaddress
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import yaml

SERVER = "https://kubernetes.default.svc"
MANIFEST_FILES = (
    "namespace.yaml",
    "serviceaccounts.yaml",
    "deployment-api.yaml",
    "deployment-ui.yaml",
    "service-api.yaml",
    "service-ui.yaml",
    "ingress.yaml",
    "externalsecret-auth.yaml",
    "externalsecret-inference.yaml",
    "externalsecret-postgres.yaml",
    "networkpolicies.yaml",
)
BASE_POLICIES = {
    "default-deny",
    "allow-dns",
    "allow-ui-to-api",
    "allow-api-from-ui",
    "allow-api-to-db",
    "allow-api-to-bifrost",
    "allow-ui-from-traefik",
}
EXPECTED_IMAGES = {
    "api": "ghcr.io/vectorize-io/hindsight-api@sha256:1ba631f950a04460feff6b1e212bad3cdf127b0cfb3d765bdcba12f512481d7b",
    "ui": "ghcr.io/vectorize-io/hindsight-control-plane@sha256:f6e3802e558a0c57feff84d52dfc276d0a841bc21de114629a90444eadc5a3f3",
}
EXPECTED_IMAGE_PROVENANCE = {
    "version": "0.10.2",
    "imageSourceRevision": "5fc4ce20917b916240cef27c212c387a177f115b",
    "manifestCompatibilityReviewRevision": "f7dd3f4fd7420f7beec60c32c965e5e5cf7be066",
    "sourceMismatchApproved": True,
    "imageProvenanceApproved": True,
}
EXPECTED_INFERENCE = {
    "expectedInferenceProvider": "openai",
    "expectedInferenceModel": "llamacpp/qwen3.8-27b",
    "expectedInferenceBaseURL": "http://bifrost.bifrost.svc.cluster.local:8080/v1",
}
FORBIDDEN_HINDSIGHT_KINDS = {
    "Cluster",
    "Database",
    "DatabaseRole",
    "StorageClass",
}
ACTIVATION_GATE_CODES = {
    "hindsight/inference-egress-approved": "INFERENCE_EGRESS_UNAPPROVED",
    "hindsight/shared-database-approved": "SHARED_DATABASE_UNAPPROVED",
    "hindsight/operator-preflight-approved": (
        "OPERATOR_PREFLIGHT_AND_RUNTIME_ACCEPTANCE_UNAPPROVED"
    ),
}
Document = dict[str, Any]
Catalog = dict[str, Any]
ResourceIndex = dict[tuple[str, str], Document]

API_LITERAL_KEYS = {
    "HOME",
    "HINDSIGHT_API_HOST",
    "HINDSIGHT_API_PORT",
    "HINDSIGHT_API_MODEL_INIT_TIMEOUT",
    "HINDSIGHT_API_DATABASE_SCHEMA",
    "HINDSIGHT_API_DB_POOL_MIN_SIZE",
    "HINDSIGHT_API_DB_POOL_MAX_SIZE",
    "PGSSLMODE",
    "HINDSIGHT_API_TENANT_EXTENSION",
    "HINDSIGHT_API_EMBEDDINGS_PROVIDER",
    "HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL",
    "HINDSIGHT_API_RERANKER_PROVIDER",
    "HINDSIGHT_API_RERANKER_LOCAL_MODEL",
    "HINDSIGHT_API_EMBEDDINGS_LOCAL_FORCE_CPU",
    "HINDSIGHT_API_RERANKER_LOCAL_FORCE_CPU",
    "HINDSIGHT_API_EMBEDDINGS_LOCAL_TRUST_REMOTE_CODE",
    "HINDSIGHT_API_RERANKER_LOCAL_TRUST_REMOTE_CODE",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "HINDSIGHT_API_LLM_TRACE_ENABLED",
    "HINDSIGHT_API_LLM_DEBUG_DUMP_4XX",
    "PYTHONDONTWRITEBYTECODE",
}
UI_LITERAL_KEYS = {
    "HOME",
    "HINDSIGHT_CP_DATAPLANE_API_URL",
    "HINDSIGHT_CP_PORT",
    "HINDSIGHT_CP_HOSTNAME",
}
API_SECRET_KEYS = {
    "HINDSIGHT_API_DATABASE_URL",
    "HINDSIGHT_API_TENANT_API_KEY",
    "HINDSIGHT_API_LLM_PROVIDER",
    "HINDSIGHT_API_LLM_MODEL",
    "HINDSIGHT_API_LLM_BASE_URL",
    "HINDSIGHT_API_LLM_API_KEY",
}
UI_SECRET_KEYS = {
    "HINDSIGHT_CP_DATAPLANE_API_KEY",
    "HINDSIGHT_CP_ACCESS_KEY",
}
RESOURCE_SHAPE = {key: {"cpu": str, "memory": str} for key in ("requests", "limits")}
REFERENCE_SHAPE = {"name": str, "key": str}
WORKLOAD_SHAPE = {
    "replicas": int,
    "strategy": str,
    "port": int,
    "resources": RESOURCE_SHAPE,
    "startupBudgetSeconds": int,
    "runAsUser": int,
    "image": str,
    "literalEnv": ("map", str),
    "secretEnv": ("map", REFERENCE_SHAPE),
}
CONTRACT_SHAPE = {
    "contractVersion": int,
    "imageProvenance": {
        "version": str,
        "imageSourceRevision": str,
        "manifestCompatibilityReviewRevision": str,
        "sourceMismatchApproved": bool,
        "imageProvenanceApproved": bool,
    },
    "workloads": {"api": WORKLOAD_SHAPE, "ui": WORKLOAD_SHAPE},
    "database": {
        "namespace": str,
        "cluster": str,
        "name": str,
        "owner": str,
        "ownerSecret": str,
        "schema": str,
        "databaseReclaimPolicy": str,
        "databaseRoleReclaimPolicy": str,
        "roleConnectionLimit": int,
        "pool": {"minimum": int, "maximum": int},
        "service": {"host": str, "port": int},
        "extensions": ("list", {"name": str, "ensure": str, "schema": str}),
    },
    "externalSecrets": dict,
    "rotation": {
        "automaticWorkloadRestart": bool,
        "consumers": dict,
        "requireControlledRestart": bool,
        "requirePostRestartReconnectTest": bool,
    },
    "network": {
        "policyApiVersion": str,
        "policyKind": str,
        "namespaceIdentityLabel": str,
        "dnsNamespace": str,
        "dnsPodLabels": ("map", str),
        "databaseNamespace": str,
        "databasePodLabels": ("map", str),
        "inferenceNamespace": str,
        "inferencePodLabels": ("map", str),
        "ingressNamespace": str,
        "ingressPodLabels": ("map", str),
        "dnsPort": int,
        "databasePort": int,
        "inferencePort": int,
        "ingressPort": int,
        "expectedInferenceProvider": str,
        "expectedInferenceModel": str,
        "expectedInferenceBaseURL": str,
    },
    "exposure": {
        "ingressClassName": str,
        "hostname": str,
        "serviceName": str,
        "servicePort": int,
        "tlsDefaultStore": bool,
        "authentication": str,
    },
    "activationGates": {"namespaceAnnotations": ("map", str)},
}


class Invalid(ValueError):
    """Validation failure carrying only an authored, non-sensitive code."""


class UniqueLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def require(condition: object, code: str) -> None:
    """Raise a fixed diagnostic when ``condition`` is false."""
    if not condition:
        raise Invalid(code)


def unique_mapping(
    loader: UniqueLoader, node: yaml.MappingNode, deep: bool = False
) -> Document:
    """Construct a mapping without shadowed or non-string keys."""
    result: Document = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        require(isinstance(key, str), "YAML_STRING_KEY_REQUIRED")
        require(key not in result, "YAML_DUPLICATE_KEY")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def ensure_safe_path(path: Path, approved_root: Path) -> Path:
    """Require lexical containment and reject symlinks before a file read."""
    require(approved_root.is_absolute(), "APPROVED_ROOT_MUST_BE_ABSOLUTE")
    root = approved_root.absolute()
    candidate = path.absolute()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise Invalid("PATH_OUTSIDE_APPROVED_ROOT") from None

    current = Path(candidate.anchor)
    for component in candidate.parts[1:]:
        current /= component
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            break
        except OSError:
            raise Invalid("PATH_INSPECTION_FAILED") from None
        require(not stat.S_ISLNK(mode), "SYMLINK_NOT_ALLOWED")
    return candidate


def safe_child(approved_root: Path, *parts: str) -> Path:
    """Build and validate a path under an approved root."""
    require(
        all(isinstance(part, str) and part for part in parts), "PATH_COMPONENT_INVALID"
    )
    return ensure_safe_path(approved_root.joinpath(*parts), approved_root)


def read_yaml(path: Path, approved_root: Path, multiple: bool = False) -> Any:
    """Read an explicitly named YAML file after safe-path validation."""
    safe = ensure_safe_path(path, approved_root)
    try:
        documents = list(yaml.load_all(safe.read_text(), Loader=UniqueLoader))
    except (OSError, yaml.YAMLError, TypeError, ValueError):
        raise Invalid("YAML_UNREADABLE_OR_INVALID") from None
    require(
        all(type(document) is dict for document in documents), "YAML_OBJECT_REQUIRED"
    )
    if multiple:
        return documents
    require(len(documents) == 1, "YAML_SINGLE_DOCUMENT_REQUIRED")
    return documents[0]


def contract_shape(value: Any, shape: Any) -> None:
    """Validate exact keys and types for the supported private contract."""
    if isinstance(shape, type):
        require(type(value) is shape, "CONTRACT_STRUCTURE_INVALID")
        return
    if isinstance(shape, tuple):
        collection, item_shape = shape
        expected = dict if collection == "map" else list
        require(type(value) is expected, "CONTRACT_STRUCTURE_INVALID")
        if collection == "map":
            require(
                all(isinstance(key, str) and key for key in value),
                "CONTRACT_STRUCTURE_INVALID",
            )
            items = value.values()
        else:
            items = value
        for item in items:
            contract_shape(item, item_shape)
        return
    require(
        type(value) is dict and set(value) == set(shape),
        "CONTRACT_STRUCTURE_INVALID",
    )
    for key, item_shape in shape.items():
        contract_shape(value[key], item_shape)


def _valid_identifier(value: str) -> bool:
    """Return whether a value is a DNS-compatible authored identifier."""
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*", value))


def validate_contract(contract: Document) -> None:
    """Validate references and safe settings without reading secret values."""
    contract_shape(contract, CONTRACT_SHAPE)
    require(contract["contractVersion"] == 5, "CONTRACT_VERSION_UNSUPPORTED")
    require(
        contract["imageProvenance"] == EXPECTED_IMAGE_PROVENANCE,
        "CONTRACT_IMAGE_PROVENANCE_INVALID",
    )

    database = contract["database"]
    identifiers = (
        database["namespace"],
        database["cluster"],
        database["name"],
        database["owner"],
        database["ownerSecret"],
    )
    require(
        all(_valid_identifier(item) for item in identifiers)
        and bool(re.fullmatch(r"[a-z_][a-z0-9_]*", database["name"]))
        and bool(re.fullmatch(r"[a-z_][a-z0-9_]*", database["owner"]))
        and database["owner"] != "postgres",
        "CONTRACT_DATABASE_IDENTITY_INVALID",
    )
    require(
        database["namespace"] == "postgresql"
        and database["cluster"] == "postgresql"
        and database["databaseReclaimPolicy"] == "retain"
        and database["databaseRoleReclaimPolicy"] == "retain"
        and database["roleConnectionLimit"] == 20
        and database["pool"] == {"minimum": 1, "maximum": 10}
        and database["schema"] == "public",
        "CONTRACT_DATABASE_SAFETY_INVALID",
    )
    require(
        database["service"]
        == {
            "host": "postgresql-rw.postgresql.svc.cluster.local",
            "port": 5432,
        }
        and contract["network"]["databaseNamespace"] == "postgresql"
        and contract["network"]["databasePodLabels"]
        == {"cnpg.io/cluster": "postgresql"}
        and contract["network"]["databasePort"] == 5432,
        "CONTRACT_DATABASE_ENDPOINT_INVALID",
    )
    require(
        database["extensions"]
        == [
            {"name": name, "ensure": "present", "schema": "public"}
            for name in ("vector", "pg_trgm", "btree_gin")
        ],
        "CONTRACT_DATABASE_EXTENSIONS_INVALID",
    )

    secrets = contract["externalSecrets"]
    require(
        secrets
        and all(isinstance(name, str) and _valid_identifier(name) for name in secrets),
        "CONTRACT_SECRET_INVENTORY_INVALID",
    )
    owner_secret = database["ownerSecret"]
    for name, secret in secrets.items():
        expected_keys = {"namespace", "remoteKey", "properties", "storeRef"}
        if name == owner_secret:
            expected_keys.add("targetType")
        require(
            type(secret) is dict and set(secret) == expected_keys,
            "CONTRACT_STRUCTURE_INVALID",
        )
        require(
            isinstance(secret["namespace"], str)
            and _valid_identifier(secret["namespace"])
            and isinstance(secret["remoteKey"], str)
            and bool(re.fullmatch(r"[A-Za-z0-9_./-]+", secret["remoteKey"]))
            and type(secret["properties"]) is list
            and bool(secret["properties"])
            and len(secret["properties"]) == len(set(secret["properties"]))
            and all(
                isinstance(prop, str) and bool(re.fullmatch(r"[A-Za-z0-9_.-]+", prop))
                for prop in secret["properties"]
            )
            and type(secret["storeRef"]) is dict
            and set(secret["storeRef"]) == {"kind", "name"}
            and secret["storeRef"]["kind"] == "ClusterSecretStore"
            and isinstance(secret["storeRef"]["name"], str)
            and bool(secret["storeRef"]["name"]),
            "CONTRACT_SECRET_INVENTORY_INVALID",
        )
    require(
        owner_secret in secrets
        and secrets[owner_secret]["namespace"] == database["namespace"]
        and secrets[owner_secret]["properties"] == ["username", "password"]
        and secrets[owner_secret]["targetType"] == "kubernetes.io/basic-auth",
        "CONTRACT_OWNER_SECRET_INVALID",
    )
    require(
        contract["workloads"]["api"]["secretEnv"]["HINDSIGHT_API_DATABASE_URL"]["name"]
        != owner_secret,
        "CONTRACT_DATABASE_REFERENCES_NOT_SEPARATE",
    )

    for role, literal_keys, secret_keys in (
        ("api", API_LITERAL_KEYS, API_SECRET_KEYS),
        ("ui", UI_LITERAL_KEYS, UI_SECRET_KEYS),
    ):
        workload = contract["workloads"][role]
        require(
            set(workload["literalEnv"]) == literal_keys
            and set(workload["secretEnv"]) == secret_keys,
            "CONTRACT_ENV_INVENTORY_INVALID",
        )
        require(all(workload["literalEnv"].values()), "CONTRACT_EMPTY_RUNTIME_SETTING")
        require(
            workload["replicas"] == 1
            and workload["runAsUser"] == 1000
            and 1 <= workload["port"] <= 65535
            and workload["startupBudgetSeconds"] > 0
            and workload["startupBudgetSeconds"] % 10 == 0
            and workload["strategy"]
            == ("Recreate" if role == "api" else "RollingUpdate"),
            "CONTRACT_WORKLOAD_INVALID",
        )
        require(
            workload["image"] == EXPECTED_IMAGES[role],
            "CONTRACT_WORKLOAD_IMAGE_INVALID",
        )
        for budget in workload["resources"].values():
            require(
                all(
                    bool(
                        re.fullmatch(
                            r"[1-9][0-9]*(?:\.[0-9]+)?(?:m|Ki|Mi|Gi|Ti)?", value
                        )
                    )
                    for value in budget.values()
                ),
                "CONTRACT_RESOURCES_INVALID",
            )
        for reference in workload["secretEnv"].values():
            require(
                reference["name"] in secrets
                and reference["key"] in secrets[reference["name"]]["properties"],
                "CONTRACT_SECRET_REFERENCE_INVALID",
            )

    api = contract["workloads"]["api"]
    ui = contract["workloads"]["ui"]
    api_refs = api["secretEnv"]
    ui_refs = ui["secretEnv"]
    dsn_name = api_refs["HINDSIGHT_API_DATABASE_URL"]["name"]
    auth_name = api_refs["HINDSIGHT_API_TENANT_API_KEY"]["name"]
    inference_names = {
        api_refs[key]["name"]
        for key in API_SECRET_KEYS
        if key.startswith("HINDSIGHT_API_LLM_")
    }
    require(
        len(inference_names) == 1
        and dsn_name != owner_secret
        and secrets[dsn_name]["namespace"] == "hindsight"
        and secrets[owner_secret]["namespace"] != secrets[dsn_name]["namespace"],
        "CONTRACT_DATABASE_REFERENCES_NOT_SEPARATE",
    )
    expected_properties: dict[str, set[str]] = {name: set() for name in secrets}
    for workload in contract["workloads"].values():
        for reference in workload["secretEnv"].values():
            expected_properties[reference["name"]].add(reference["key"])
    expected_properties[owner_secret] = {"username", "password"}
    require(
        set(secrets) == set(expected_properties)
        and all(
            set(secrets[name]["properties"]) == properties
            for name, properties in expected_properties.items()
        ),
        "CONTRACT_SECRET_INVENTORY_INVALID",
    )
    require(
        api_refs["HINDSIGHT_API_TENANT_API_KEY"]
        == ui_refs["HINDSIGHT_CP_DATAPLANE_API_KEY"]
        and ui_refs["HINDSIGHT_CP_ACCESS_KEY"]
        != ui_refs["HINDSIGHT_CP_DATAPLANE_API_KEY"],
        "CONTRACT_AUTH_KEYS_NOT_INDEPENDENT",
    )

    literals = api["literalEnv"]
    safe_literals = {
        "HOME": "/home/hindsight",
        "HINDSIGHT_API_HOST": "0.0.0.0",
        "HINDSIGHT_API_PORT": str(api["port"]),
        "HINDSIGHT_API_MODEL_INIT_TIMEOUT": "540",
        "HINDSIGHT_API_DATABASE_SCHEMA": "public",
        "HINDSIGHT_API_DB_POOL_MIN_SIZE": "1",
        "HINDSIGHT_API_DB_POOL_MAX_SIZE": "10",
        "PGSSLMODE": "disable",
        "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "local",
        "HINDSIGHT_API_RERANKER_PROVIDER": "local",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HINDSIGHT_API_LLM_TRACE_ENABLED": "false",
        "HINDSIGHT_API_LLM_DEBUG_DUMP_4XX": "false",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    safe_literals.update(
        {
            "HINDSIGHT_API_" + name + suffix: value
            for name in ("EMBEDDINGS", "RERANKER")
            for suffix, value in (
                ("_LOCAL_FORCE_CPU", "true"),
                ("_LOCAL_TRUST_REMOTE_CODE", "false"),
            )
        }
    )
    require(
        all(literals[key] == value for key, value in safe_literals.items()),
        "CONTRACT_UNSAFE_RUNTIME_SETTING",
    )
    require(
        literals["HINDSIGHT_API_TENANT_EXTENSION"]
        == "hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension"
        and int(literals["HINDSIGHT_API_MODEL_INIT_TIMEOUT"])
        < api["startupBudgetSeconds"],
        "CONTRACT_AUTH_EXTENSION_INVALID",
    )
    require(
        ui["literalEnv"]
        == {
            "HOME": "/home/node",
            "HINDSIGHT_CP_DATAPLANE_API_URL": (
                f"http://hindsight-api.hindsight.svc.cluster.local:{api['port']}"
            ),
            "HINDSIGHT_CP_PORT": str(ui["port"]),
            "HINDSIGHT_CP_HOSTNAME": "0.0.0.0",
        },
        "CONTRACT_UI_RUNTIME_INVALID",
    )

    inference_name = next(iter(inference_names))
    require(
        contract["rotation"]
        == {
            "automaticWorkloadRestart": False,
            "consumers": {
                dsn_name: ["hindsight-api"],
                auth_name: ["hindsight-api", "hindsight-ui"],
                inference_name: ["hindsight-api"],
            },
            "requireControlledRestart": True,
            "requirePostRestartReconnectTest": True,
        },
        "CONTRACT_ROTATION_INVALID",
    )
    require(
        contract["activationGates"]["namespaceAnnotations"]
        == {
            "hindsight/inference-egress-approved": "true",
            "hindsight/shared-database-approved": "true",
            "hindsight/operator-preflight-approved": "true",
        },
        "CONTRACT_ACTIVATION_GATES_INVALID",
    )
    network = contract["network"]
    exposure = contract["exposure"]
    require(
        network["policyApiVersion"] == "cilium.io/v2"
        and network["policyKind"] == "CiliumNetworkPolicy"
        and network["namespaceIdentityLabel"] == "k8s:io.kubernetes.pod.namespace"
        and network["dnsNamespace"]
        and network["dnsPodLabels"]
        and network["dnsPort"] == 53
        and network["inferenceNamespace"] == "bifrost"
        and network["inferencePodLabels"]
        == {
            "app.kubernetes.io/component": "server",
            "app.kubernetes.io/instance": "bifrost",
            "app.kubernetes.io/name": "bifrost",
        }
        and network["inferencePort"] == 8080
        and {key: network[key] for key in EXPECTED_INFERENCE} == EXPECTED_INFERENCE
        and network["ingressNamespace"] == "traefik"
        and network["ingressPodLabels"]
        == {
            "app.kubernetes.io/instance": "traefik-traefik",
            "app.kubernetes.io/name": "traefik",
        }
        and network["ingressPort"] == ui["port"],
        "CONTRACT_NETWORK_INVALID",
    )
    require(
        exposure["ingressClassName"] == "traefik"
        and bool(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", exposure["hostname"])
        )
        and exposure["serviceName"] == "hindsight-ui"
        and exposure["servicePort"] == ui["port"]
        and exposure["tlsDefaultStore"] is True
        and exposure["authentication"] == "hindsight-ui-access-key",
        "CONTRACT_EXPOSURE_INVALID",
    )


def _decode_pointer_token(token: str) -> str:
    """Decode one JSON Pointer token and reject malformed escapes."""
    require(re.search(r"~(?:[^01]|$)", token) is None, "OVERLAY_PATCH_PATH_TOKEN")
    return token.replace("~1", "/").replace("~0", "~")


def _array_index(token: str, length: int) -> int:
    """Parse a supported existing JSON Patch array index."""
    require(
        token != "-" and bool(re.fullmatch(r"0|[1-9][0-9]*", token)),
        "OVERLAY_ARRAY_INDEX",
    )
    index = int(token)
    require(index < length, "OVERLAY_ARRAY_INDEX")
    return index


def _apply_patch(application: Document, operation: Document) -> None:
    """Apply the supported add/replace JSON Patch subset."""
    require(
        type(operation) is dict and set(operation) == {"op", "path", "value"},
        "OVERLAY_PATCH_STRUCTURE",
    )
    require(operation["op"] in {"replace", "add"}, "OVERLAY_PATCH_OPERATION")
    require(
        isinstance(operation["path"], str) and operation["path"].startswith("/spec/"),
        "OVERLAY_PATCH_SCOPE",
    )
    raw_parts = operation["path"].split("/")[1:]
    require(all(raw_parts), "OVERLAY_PATCH_PATH_TOKEN")
    parts = [_decode_pointer_token(part) for part in raw_parts]
    parent: Any = application
    for part in parts[:-1]:
        if isinstance(parent, list):
            parent = parent[_array_index(part, len(parent))]
        else:
            require(
                type(parent) is dict and part in parent,
                "OVERLAY_PATCH_PARENT_MISSING",
            )
            parent = parent[part]
    token = parts[-1]
    if isinstance(parent, list):
        require(operation["op"] != "add", "OVERLAY_ARRAY_ADD_UNSUPPORTED")
        parent[_array_index(token, len(parent))] = copy.deepcopy(operation["value"])
        return
    require(type(parent) is dict, "OVERLAY_PATCH_PARENT_MISSING")
    if operation["op"] == "replace":
        require(token in parent, "OVERLAY_REPLACE_MISSING_FIELD")
    parent[token] = copy.deepcopy(operation["value"])


def local_application(
    public: Path, internal: Path, environment: str, name: str
) -> tuple[Document, Document]:
    """Resolve one exact JSON-patch-only local Application overlay."""
    base_directory = safe_child(public, "argocd", "apps", name, "app")
    require(
        read_yaml(base_directory / "kustomization.yaml", public).get("resources")
        == ["app.yaml"],
        "PUBLIC_BASE_RESOURCES",
    )
    application = read_yaml(base_directory / "app.yaml", public)
    require(
        application.get("apiVersion") == "argoproj.io/v1alpha1"
        and application.get("kind") == "Application"
        and type(application.get("metadata")) is dict
        and application["metadata"].get("name") == name,
        "PUBLIC_APPLICATION_IDENTITY",
    )
    overlay = read_yaml(
        safe_child(
            internal,
            "argocd",
            "apps",
            name,
            "environments",
            environment,
            "kustomization.yaml",
        ),
        internal,
    )
    require(
        set(overlay) <= {"apiVersion", "kind", "resources", "patches"}
        and overlay.get("apiVersion") == "kustomize.config.k8s.io/v1beta1"
        and overlay.get("kind") == "Kustomization",
        "OVERLAY_UNSUPPORTED_TRANSFORMER",
    )
    require(
        overlay.get("resources")
        == [f"github.com/NIXKnight/Home-Ops//argocd/apps/{name}/app?ref=main"],
        "REMOTE_BASE_DIRECTION",
    )
    patches = overlay.get("patches", [])
    require(type(patches) is list, "OVERLAY_PATCH_STRUCTURE")
    original = copy.deepcopy(application)
    target = {
        "group": "argoproj.io",
        "version": "v1alpha1",
        "kind": "Application",
        "name": name,
    }
    for patch in patches:
        require(
            type(patch) is dict
            and set(patch) == {"target", "patch"}
            and patch["target"] == target
            and isinstance(patch["patch"], str),
            "OVERLAY_TARGET",
        )
        try:
            operations = yaml.load(patch["patch"], Loader=UniqueLoader)
        except (yaml.YAMLError, TypeError, ValueError):
            raise Invalid("OVERLAY_PATCH_INVALID") from None
        require(type(operations) is list, "OVERLAY_PATCH_STRUCTURE")
        for operation in operations:
            _apply_patch(application, operation)
    return original, application


def _directory_inventory(directory: Path, root: Path) -> set[str]:
    """List one approved directory without reading unapproved children."""
    safe = ensure_safe_path(directory, root)
    try:
        return set(os.listdir(safe))
    except OSError:
        raise Invalid("DIRECTORY_UNREADABLE") from None


def load_catalog(public: Path, internal: Path, environment: str) -> Catalog:
    """Load only named safe wiring, manifests, and the private contract."""
    require(
        bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*", environment)),
        "ENVIRONMENT_ARGUMENT",
    )
    ensure_safe_path(public, public)
    ensure_safe_path(internal, internal)
    environment_directory = safe_child(
        internal, "argocd", "apps", "hindsight", "environments", environment
    )
    contract = read_yaml(environment_directory / "validation-contract.yaml", internal)
    validate_contract(contract)

    manifest_directory = safe_child(
        internal, *environment_directory.relative_to(internal).parts, "manifests"
    )
    kustomization = read_yaml(manifest_directory / "kustomization.yaml", internal)
    require(
        kustomization.get("apiVersion") == "kustomize.config.k8s.io/v1beta1"
        and kustomization.get("kind") == "Kustomization"
        and set(kustomization) == {"apiVersion", "kind", "resources"},
        "MANIFEST_KUSTOMIZATION_STRUCTURE",
    )
    require(
        sorted(kustomization["resources"]) == sorted(MANIFEST_FILES),
        "MANIFEST_MEMBERSHIP",
    )
    require(
        _directory_inventory(manifest_directory, internal)
        == set(MANIFEST_FILES) | {"kustomization.yaml"},
        "MANIFEST_FILE_INVENTORY",
    )
    documents = [
        document
        for filename in MANIFEST_FILES
        for document in read_yaml(
            manifest_directory / filename, internal, multiple=True
        )
    ]

    database = contract["database"]
    postgres_directory = safe_child(
        internal,
        "argocd",
        "apps",
        "postgresql",
        "environments",
        environment,
        "manifests",
    )
    postgres_files = (
        "cluster.yaml",
        f"database-{database['name']}.yaml",
        f"databaserole-{database['name']}.yaml",
        f"externalsecret-{database['ownerSecret']}.yaml",
    )
    postgres_documents = [
        document
        for filename in postgres_files
        for document in read_yaml(
            postgres_directory / filename, internal, multiple=True
        )
    ]

    bases: dict[str, Document] = {}
    applications: dict[str, Document] = {}
    for name in ("hindsight", "postgresql"):
        bases[name], applications[name] = local_application(
            public, internal, environment, name
        )

    environment_root = safe_child(internal, "argocd", "environments", environment)
    return {
        "docs": documents,
        "postgres_docs": postgres_documents,
        "bases": bases,
        "apps": applications,
        "environment": environment,
        "contract": contract,
        "root": read_yaml(environment_root / "base/kustomization.yaml", internal),
        "project": read_yaml(
            environment_root / "projects/infrastructure.yaml", internal
        ),
        "raw_values": read_yaml(environment_directory / "values.yaml", internal),
    }


def resource_metadata(document: Document) -> Document:
    """Return structurally checked Kubernetes metadata."""
    metadata = document.get("metadata")
    require(
        type(metadata) is dict
        and isinstance(metadata.get("name"), str)
        and bool(metadata["name"]),
        "RESOURCE_METADATA_INVALID",
    )
    if "namespace" in metadata:
        require(
            isinstance(metadata["namespace"], str) and bool(metadata["namespace"]),
            "RESOURCE_METADATA_INVALID",
        )
    for key in ("labels", "annotations"):
        if key in metadata:
            require(
                type(metadata[key]) is dict
                and all(
                    isinstance(item_key, str) and isinstance(value, str)
                    for item_key, value in metadata[key].items()
                ),
                (
                    "RESOURCE_ANNOTATIONS_INVALID"
                    if key == "annotations"
                    else "RESOURCE_METADATA_INVALID"
                ),
            )
    sync_options = metadata.get("annotations", {}).get(
        "argocd.argoproj.io/sync-options"
    )
    if sync_options is not None:
        parts = sync_options.split(",")
        require(
            all(part and part == part.strip() and "=" in part for part in parts),
            "ARGO_SYNC_OPTIONS_INVALID",
        )
    return metadata


def annotations(document: Document) -> dict[str, str]:
    """Return type-checked resource annotations."""
    return resource_metadata(document).get("annotations", {})


def index_documents(
    documents: list[Document], namespace: str, *, hindsight: bool = False
) -> ResourceIndex:
    """Reject literal secrets and duplicate or cross-namespace resources."""
    result: ResourceIndex = {}
    for document in documents:
        require(
            isinstance(document.get("apiVersion"), str)
            and isinstance(document.get("kind"), str),
            "RESOURCE_IDENTITY",
        )
        kind = document["kind"]
        if hindsight:
            require(
                kind not in FORBIDDEN_HINDSIGHT_KINDS,
                "HINDSIGHT_OWNED_DATA_RESOURCE_FORBIDDEN",
            )
            require(
                kind != "NetworkPolicy",
                "KUBERNETES_NETWORK_POLICY_FORBIDDEN",
            )
        require(kind != "Secret", "LITERAL_SECRET_FORBIDDEN")
        require(
            "data" not in document and "stringData" not in document,
            "LITERAL_SECRET_DATA_FORBIDDEN",
        )
        metadata = resource_metadata(document)
        identity = (kind, metadata["name"])
        require(identity not in result, "DUPLICATE_RESOURCE")
        expected_namespace = None if kind == "Namespace" else namespace
        require(metadata.get("namespace") == expected_namespace, "WORKLOAD_NAMESPACE")
        result[identity] = document
    return result


def protected(document: Document) -> bool:
    """Return whether Argo prune and cascade deletion are disabled."""
    options = annotations(document).get("argocd.argoproj.io/sync-options", "")
    parts = options.split(",") if options else []
    return {"Prune=false", "Delete=false"} <= set(parts)


def _external_secret_spec(
    document: Document, expected: Document, *, owner: bool = False
) -> None:
    """Validate a reference-only ExternalSecret without reading remote values."""
    spec = document.get("spec")
    require(type(spec) is dict, "EXTERNAL_SECRET_STRUCTURE")
    require(spec.get("secretStoreRef") == expected["storeRef"], "EXTERNAL_STORE_REF")
    target = spec.get("target")
    require(
        type(target) is dict
        and target.get("name") == resource_metadata(document)["name"]
        and target.get("creationPolicy") == "Owner",
        "EXTERNAL_TARGET",
    )
    require(
        spec.get("data")
        == [
            {
                "secretKey": key,
                "remoteRef": {"key": expected["remoteKey"], "property": key},
            }
            for key in expected["properties"]
        ]
        and "dataFrom" not in spec,
        "EXTERNAL_PROPERTY_CONTRACT",
    )
    template = target.get("template", {})
    require(
        "data" not in template and "templateFrom" not in template,
        "EXTERNAL_TEMPLATE_VALUES_FORBIDDEN",
    )
    if owner:
        require(
            template
            == {
                "type": "kubernetes.io/basic-auth",
                "metadata": {"labels": {"cnpg.io/reload": "true"}},
            },
            "CNPG_OWNER_BASIC_AUTH",
        )
    else:
        require(not template, "EXTERNAL_UNEXPECTED_TEMPLATE")


def _validate_shared_database(catalog: Catalog, postgres: ResourceIndex) -> None:
    """Validate the PostgreSQL-owned database, role, and owner reference."""
    contract = catalog["contract"]
    database = contract["database"]
    database_resource_name = f"{database['cluster']}-{database['name']}"
    role_resource_name = database["ownerSecret"]
    require(
        set(postgres)
        == {
            ("Cluster", database["cluster"]),
            ("Database", database_resource_name),
            ("DatabaseRole", role_resource_name),
            ("ExternalSecret", database["ownerSecret"]),
        },
        "POSTGRES_RESOURCE_INVENTORY",
    )

    cluster_spec = postgres["Cluster", database["cluster"]].get("spec")
    require(type(cluster_spec) is dict, "POSTGRES_CLUSTER_STRUCTURE")
    managed_roles = cluster_spec.get("managed", {}).get("roles", [])
    initdb = cluster_spec.get("bootstrap", {}).get("initdb", {})
    require(
        type(managed_roles) is list
        and all(
            type(role) is not dict or role.get("name") != database["owner"]
            for role in managed_roles
        )
        and type(initdb) is dict
        and initdb.get("database") != database["name"]
        and initdb.get("owner") != database["owner"],
        "SHARED_CLUSTER_HINDSIGHT_OWNERSHIP_FORBIDDEN",
    )

    database_document = postgres["Database", database_resource_name]
    role_document = postgres["DatabaseRole", role_resource_name]
    require(
        protected(database_document) and protected(role_document),
        "DATABASE_PRUNE_DELETE_PROTECTION",
    )
    require(
        database_document.get("spec")
        == {
            "cluster": {"name": database["cluster"]},
            "name": database["name"],
            "owner": database["owner"],
            "ensure": "present",
            "databaseReclaimPolicy": "retain",
            "extensions": database["extensions"],
        },
        "SHARED_DATABASE_CONTRACT",
    )
    role_spec = role_document.get("spec")
    require(type(role_spec) is dict, "DB_NONSUPERUSER_ROLE")
    require(
        set(role_spec)
        == {
            "cluster",
            "name",
            "comment",
            "ensure",
            "login",
            "inherit",
            "superuser",
            "createdb",
            "createrole",
            "replication",
            "bypassrls",
            "connectionLimit",
            "passwordSecret",
            "databaseRoleReclaimPolicy",
        }
        and role_spec["cluster"] == {"name": database["cluster"]}
        and role_spec["name"] == database["owner"]
        and isinstance(role_spec["comment"], str)
        and role_spec["ensure"] == "present"
        and role_spec["login"] is True
        and role_spec["inherit"] is True
        and all(
            role_spec[key] is False
            for key in (
                "superuser",
                "createdb",
                "createrole",
                "replication",
                "bypassrls",
            )
        )
        and role_spec["connectionLimit"] == 20
        and role_spec["passwordSecret"] == {"name": database["ownerSecret"]}
        and role_spec["databaseRoleReclaimPolicy"] == "retain",
        "DB_NONSUPERUSER_ROLE",
    )
    owner_document = postgres["ExternalSecret", database["ownerSecret"]]
    require(protected(owner_document), "OWNER_SECRET_PRUNE_DELETE_PROTECTION")
    _external_secret_spec(
        owner_document,
        contract["externalSecrets"][database["ownerSecret"]],
        owner=True,
    )


def _network_policy_spec(policy: Document) -> Document:
    """Return a structurally checked, namespaced Cilium policy spec."""
    require(
        policy.get("apiVersion") == "cilium.io/v2"
        and policy.get("kind") == "CiliumNetworkPolicy",
        "CILIUM_POLICY_IDENTITY",
    )
    spec = policy.get("spec")
    require(
        type(spec) is dict
        and set(spec)
        <= {
            "endpointSelector",
            "enableDefaultDeny",
            "ingress",
            "egress",
        }
        and type(spec.get("endpointSelector")) is dict,
        "NETWORK_STRUCTURE_INVALID",
    )
    selector = spec["endpointSelector"]
    require(
        set(selector) <= {"matchLabels"}
        and type(selector.get("matchLabels", {})) is dict
        and all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in selector.get("matchLabels", {}).items()
        )
        and "k8s:io.kubernetes.pod.namespace" not in selector.get("matchLabels", {}),
        "NETWORK_SELECTOR_UNSUPPORTED",
    )
    if "enableDefaultDeny" in spec:
        require(
            type(spec["enableDefaultDeny"]) is dict
            and set(spec["enableDefaultDeny"]) <= {"ingress", "egress"}
            and bool(spec["enableDefaultDeny"])
            and all(
                type(value) is bool for value in spec["enableDefaultDeny"].values()
            ),
            "NETWORK_STRUCTURE_INVALID",
        )
    return spec


def _cidr_network(cidr: object) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    """Return one strict, bounded Cilium CIDR."""
    require(isinstance(cidr, str), "NETWORK_CIDR_INVALID")
    try:
        network = ipaddress.ip_network(cidr, strict=True)
    except (TypeError, ValueError):
        raise Invalid("NETWORK_CIDR_INVALID") from None
    require(
        network.prefixlen > 0
        and not network.is_unspecified
        and not network.is_multicast,
        "NETWORK_UNRESTRICTED_CIDR",
    )
    return network


def _endpoint_peer(peer: object) -> None:
    """Validate one explicitly namespaced Cilium endpoint identity selector."""
    require(type(peer) is dict and set(peer) == {"matchLabels"}, "NETWORK_BROAD_PEER")
    labels = peer["matchLabels"]
    namespace_key = "k8s:io.kubernetes.pod.namespace"
    require(
        type(labels) is dict
        and len(labels) >= 2
        and all(
            isinstance(key, str) and isinstance(value, str) and bool(value)
            for key, value in labels.items()
        )
        and isinstance(labels.get(namespace_key), str)
        and bool(labels[namespace_key])
        and any(key.startswith("k8s:") and key != namespace_key for key in labels),
        "NETWORK_BROAD_PEER",
    )


def _cilium_ports(rule: Document) -> list[tuple[str, int]]:
    """Validate bounded Cilium toPorts blocks and return protocol/port pairs."""
    blocks = rule.get("toPorts")
    require(type(blocks) is list and bool(blocks), "NETWORK_BLANKET_ALLOW")
    result: list[tuple[str, int]] = []
    for block in blocks:
        require(
            type(block) is dict and set(block) == {"ports"},
            "NETWORK_PORT_SCOPE",
        )
        ports = block["ports"]
        require(type(ports) is list and bool(ports), "NETWORK_PORT_SCOPE")
        for port in ports:
            require(
                type(port) is dict
                and set(port) <= {"protocol", "port"}
                and isinstance(port.get("port"), str)
                and bool(re.fullmatch(r"[0-9]+", port["port"]))
                and 1 <= int(port["port"]) <= 65535
                and port.get("protocol", "TCP") in {"TCP", "UDP"},
                "NETWORK_PORT_SCOPE",
            )
            result.append((port.get("protocol", "TCP"), int(port["port"])))
    return result


def narrow_network_policies(
    policies: list[Document], workload_labels: dict[str, dict[str, str]]
) -> None:
    """Reject broad Cilium rules and collectively universal CIDR coverage."""
    aggregate: dict[
        tuple[str, str, int, int],
        list[ipaddress.IPv4Network | ipaddress.IPv6Network],
    ] = {}
    for policy in policies:
        spec = _network_policy_spec(policy)
        selected = [
            name
            for name, labels in workload_labels.items()
            if all(
                labels.get(key.removeprefix("k8s:")) == value
                for key, value in spec["endpointSelector"]
                .get("matchLabels", {})
                .items()
            )
        ]
        for direction, endpoint_key, cidr_key in (
            ("ingress", "fromEndpoints", "fromCIDR"),
            ("egress", "toEndpoints", "toCIDR"),
        ):
            rules = spec.get(direction, [])
            require(type(rules) is list, "NETWORK_STRUCTURE_INVALID")
            for rule in rules:
                require(type(rule) is dict, "NETWORK_BLANKET_ALLOW")
                if not rule:
                    require(
                        resource_metadata(policy)["name"] == "default-deny"
                        and spec["endpointSelector"] == {}
                        and spec.get("enableDefaultDeny", {}).get(direction) is True,
                        "NETWORK_BLANKET_ALLOW",
                    )
                    continue
                peer_keys = {endpoint_key, cidr_key} & set(rule)
                require(
                    len(peer_keys) == 1
                    and set(rule) == {next(iter(peer_keys)), "toPorts"},
                    "NETWORK_BLANKET_ALLOW",
                )
                peer_key = next(iter(peer_keys))
                peers = rule[peer_key]
                require(type(peers) is list and bool(peers), "NETWORK_BLANKET_ALLOW")
                networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
                if peer_key == endpoint_key:
                    for peer in peers:
                        _endpoint_peer(peer)
                else:
                    networks = [_cidr_network(cidr) for cidr in peers]
                for protocol, port in _cilium_ports(rule):
                    if direction == "egress":
                        for workload in selected:
                            for network in networks:
                                key = (workload, protocol, port, network.version)
                                aggregate.setdefault(key, []).append(network)
    for networks in aggregate.values():
        collapsed = list(ipaddress.collapse_addresses(networks))
        require(
            not (
                len(collapsed) == 1
                and collapsed[0].prefixlen == 0
                and collapsed[0].network_address.is_unspecified
            ),
            "NETWORK_AGGREGATE_UNRESTRICTED_CIDR",
        )


def canonical_ui_probe(port: int) -> list[str]:
    """Return the reviewed UI readiness command."""
    script = (
        f"fetch('http://127.0.0.1:{port}/api/health',"
        "{signal:AbortSignal.timeout(4500)}).then(async "
        "r=>{if(!r.ok)process.exit(1);const b=await r.json();"
        "process.exit(b.status==='ok'&&b.dataplane?.status==='connected'?0:1)})"
        ".catch(()=>process.exit(1))"
    )
    return ["node", "-e", script]


def _validate_networks(resources: ResourceIndex, contract: Document) -> None:
    """Validate Cilium default deny, DNS, UI/API, and API/database flows."""
    policies = [
        document
        for (kind, _), document in resources.items()
        if kind == "CiliumNetworkPolicy"
    ]
    workload_labels = {
        role: resources["Deployment", f"hindsight-{role}"]["spec"]["template"][
            "metadata"
        ]["labels"]
        for role in ("api", "ui")
    }
    narrow_network_policies(policies, workload_labels)
    require(
        resources["CiliumNetworkPolicy", "default-deny"].get("spec")
        == {
            "endpointSelector": {},
            "enableDefaultDeny": {"ingress": True, "egress": True},
            "ingress": [{}],
            "egress": [{}],
        },
        "DEFAULT_DENY",
    )

    namespace_key = contract["network"]["namespaceIdentityLabel"]

    def cilium_labels(labels: dict[str, str], namespace: str) -> dict[str, str]:
        return {
            namespace_key: namespace,
            **{f"k8s:{key}": value for key, value in labels.items()},
        }

    def endpoint_selector(labels: dict[str, str]) -> Document:
        return {"matchLabels": {f"k8s:{key}": value for key, value in labels.items()}}

    def peer(labels: dict[str, str], namespace: str) -> Document:
        return {"matchLabels": cilium_labels(labels, namespace)}

    def ports(*items: tuple[str, int]) -> list[Document]:
        return [
            {
                "ports": [
                    {"protocol": protocol, "port": str(port)}
                    for protocol, port in items
                ]
            }
        ]

    require(
        resources["CiliumNetworkPolicy", "allow-dns"].get("spec")
        == {
            "endpointSelector": {},
            "egress": [
                {
                    "toEndpoints": [
                        peer(
                            contract["network"]["dnsPodLabels"],
                            contract["network"]["dnsNamespace"],
                        )
                    ],
                    "toPorts": ports(
                        ("UDP", contract["network"]["dnsPort"]),
                        ("TCP", contract["network"]["dnsPort"]),
                    ),
                }
            ],
        },
        "DNS_TCP_UDP_53",
    )

    def rule(
        direction: str,
        selected_peer: Document,
        port: int,
    ) -> Document:
        return {
            direction: [selected_peer],
            "toPorts": ports(("TCP", port)),
        }

    api_labels = {"app.kubernetes.io/name": "hindsight-api"}
    ui_labels = {"app.kubernetes.io/name": "hindsight-ui"}
    expected = (
        (
            "allow-ui-to-api",
            ui_labels,
            "egress",
            [
                rule(
                    "toEndpoints",
                    peer(api_labels, "hindsight"),
                    contract["workloads"]["api"]["port"],
                )
            ],
        ),
        (
            "allow-api-from-ui",
            api_labels,
            "ingress",
            [
                rule(
                    "fromEndpoints",
                    peer(ui_labels, "hindsight"),
                    contract["workloads"]["api"]["port"],
                )
            ],
        ),
        (
            "allow-api-to-db",
            api_labels,
            "egress",
            [
                rule(
                    "toEndpoints",
                    peer(
                        contract["network"]["databasePodLabels"],
                        contract["network"]["databaseNamespace"],
                    ),
                    contract["network"]["databasePort"],
                )
            ],
        ),
        (
            "allow-api-to-bifrost",
            api_labels,
            "egress",
            [
                rule(
                    "toEndpoints",
                    peer(
                        contract["network"]["inferencePodLabels"],
                        contract["network"]["inferenceNamespace"],
                    ),
                    contract["network"]["inferencePort"],
                )
            ],
        ),
        (
            "allow-ui-from-traefik",
            ui_labels,
            "ingress",
            [
                rule(
                    "fromEndpoints",
                    peer(
                        contract["network"]["ingressPodLabels"],
                        contract["network"]["ingressNamespace"],
                    ),
                    contract["network"]["ingressPort"],
                )
            ],
        ),
    )
    for name, selector, direction, rules in expected:
        require(
            resources["CiliumNetworkPolicy", name].get("spec")
            == {
                "endpointSelector": endpoint_selector(selector),
                direction: rules,
            },
            "BASE_NETWORK_FLOW_CONTRACT",
        )
    require(
        annotations(resources["CiliumNetworkPolicy", "allow-api-to-bifrost"])
        == {
            "hindsight/egress-purpose": "inference",
            "hindsight/source-evidence": "operator-approved-live-bifrost-service",
        },
        "INFERENCE_POLICY_EVIDENCE",
    )
    require(
        annotations(resources["CiliumNetworkPolicy", "allow-ui-from-traefik"])
        == {
            "hindsight/ingress-purpose": "lan-ui-via-traefik",
            "hindsight/source-evidence": "live-traefik-pod-labels-read-only",
        },
        "INGRESS_POLICY_EVIDENCE",
    )


def _validate_ingress(resources: ResourceIndex, contract: Document) -> None:
    """Validate the single LAN UI route and keep the API unexposed."""
    ingress = resources["Ingress", "hindsight-ui"]
    metadata = resource_metadata(ingress)
    exposure = contract["exposure"]
    require(
        metadata.get("labels") == {"app.kubernetes.io/name": "hindsight-ui"}
        and not metadata.get("annotations"),
        "INGRESS_METADATA_CONTRACT",
    )
    require(
        ingress.get("apiVersion") == "networking.k8s.io/v1"
        and ingress.get("spec")
        == {
            "ingressClassName": exposure["ingressClassName"],
            "rules": [
                {
                    "host": exposure["hostname"],
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": exposure["serviceName"],
                                        "port": {"number": exposure["servicePort"]},
                                    }
                                },
                            }
                        ]
                    },
                }
            ],
            "tls": [{"hosts": [exposure["hostname"]]}],
        },
        "INGRESS_UI_ONLY_CONTRACT",
    )
    require(
        all((kind, name) != ("Ingress", "hindsight-api") for kind, name in resources),
        "API_INGRESS_FORBIDDEN",
    )


def _validate_probe_contract(container: Document, role: str, budget: int) -> None:
    """Validate exact handlers and timing for each workload probe."""
    if role == "api":
        expected = {
            "startupProbe": {
                "httpGet": {"path": "/health/live", "port": "http"},
                "periodSeconds": 10,
                "timeoutSeconds": 5,
                "failureThreshold": budget // 10,
            },
            "livenessProbe": {
                "httpGet": {"path": "/health/live", "port": "http"},
                "periodSeconds": 10,
                "timeoutSeconds": 5,
                "failureThreshold": 3,
            },
            "readinessProbe": {
                "httpGet": {"path": "/health", "port": "http"},
                "periodSeconds": 10,
                "timeoutSeconds": 5,
                "failureThreshold": 3,
            },
        }
    else:
        expected = {
            "startupProbe": {
                "tcpSocket": {"port": "http"},
                "periodSeconds": 10,
                "timeoutSeconds": 3,
                "failureThreshold": budget // 10,
            },
            "livenessProbe": {
                "tcpSocket": {"port": "http"},
                "periodSeconds": 10,
                "timeoutSeconds": 3,
                "failureThreshold": 3,
            },
            "readinessProbe": {
                "exec": {
                    "command": canonical_ui_probe(
                        container["ports"][0]["containerPort"]
                    )
                },
                "periodSeconds": 10,
                "timeoutSeconds": 6,
                "failureThreshold": 3,
            },
        }
    require(
        all(container.get(name) == probe for name, probe in expected.items()),
        "CANONICAL_PROBE_CONTRACT",
    )


def _validate_application_wiring(catalog: Catalog) -> None:
    """Validate manual activation, source consistency, and exact project permission."""
    environment = catalog["environment"]
    base = catalog["bases"]["hindsight"]["spec"]
    application = catalog["apps"]["hindsight"]["spec"]
    root_members = catalog["root"].get("resources")
    require(type(root_members) is list, "ROOT_KUSTOMIZATION_STRUCTURE")
    require(
        "automated" not in base.get("syncPolicy", {})
        and "automated" not in application.get("syncPolicy", {}),
        "HINDSIGHT_AUTOSYNC_FORBIDDEN",
    )
    require(
        sum(
            isinstance(member, str) and "/apps/hindsight/" in member
            for member in root_members
        )
        == 1,
        "HINDSIGHT_ROOT_MEMBERSHIP_REQUIRED",
    )
    require(
        application.get("project") == "infrastructure"
        and application.get("destination")
        == {"server": SERVER, "namespace": "hindsight"},
        "APPLICATION_PROJECT_DESTINATION",
    )
    sources = application.get("sources")
    require(type(sources) is list and len(sources) == 3, "APPLICATION_SOURCE_SHAPE")
    require(
        sources[0].get("repoURL") == "https://bedag.github.io/helm-charts"
        and sources[0].get("chart") == "raw"
        and bool(
            re.fullmatch(
                r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?",
                sources[0].get("targetRevision", ""),
            )
        )
        and sources[0]["targetRevision"] != "0.0.0"
        and sources[0].get("helm", {}).get("valueFiles")
        == [f"$values/argocd/apps/hindsight/environments/{environment}/values.yaml"],
        "APPLICATION_CHART_PIN_SHAPE",
    )
    require(
        sources[1].get("ref") == "values"
        and "path" not in sources[1]
        and sources[1].get("targetRevision") == "main"
        and sources[2].get("repoURL") == sources[1].get("repoURL")
        and sources[2].get("targetRevision") == "main"
        and sources[2].get("path")
        == f"argocd/apps/hindsight/environments/{environment}/manifests",
        "APPLICATION_PRIVATE_SOURCE_CONSISTENCY",
    )
    require(
        base["sources"][0]["targetRevision"] == "0.0.0"
        and base["sources"][1]["repoURL"] == "PLACEHOLDER"
        and base["sources"][2]["repoURL"] == "PLACEHOLDER",
        "PUBLIC_TEMPLATE_NO_PRIVATE_REFERENCE",
    )

    project = catalog["project"].get("spec")
    require(type(project) is dict, "PROJECT_STRUCTURE_INVALID")
    cilium_cluster_blacklist = {
        (item.get("group"), item.get("kind"))
        for item in project.get("clusterResourceBlacklist", [])
        if type(item) is dict and item.get("group") == "cilium.io"
    }
    cilium_namespace_blacklist = {
        (item.get("group"), item.get("kind"))
        for item in project.get("namespaceResourceBlacklist", [])
        if type(item) is dict and item.get("group") == "cilium.io"
    }
    require(
        project.get("namespaceResourceWhitelist") is None
        and cilium_cluster_blacklist == {("cilium.io", "*")}
        and cilium_namespace_blacklist
        == {
            ("cilium.io", "CiliumEndpoint"),
            ("cilium.io", "CiliumNodeConfig"),
        },
        "PROJECT_CILIUM_PERMISSION",
    )
    destinations = project.get("destinations")
    repositories = project.get("sourceRepos")
    require(
        type(destinations) is list
        and type(repositories) is list
        and destinations.count(application["destination"]) == 1
        and all(
            type(destination) is dict
            and set(destination) == {"server", "namespace"}
            and "*" not in destination.values()
            for destination in destinations
        )
        and all(
            isinstance(repository, str) and "*" not in repository
            for repository in repositories
        )
        and all(source["repoURL"] in repositories for source in sources),
        "PROJECT_EXACT_SOURCE_DESTINATION_PERMISSION",
    )
    require(
        catalog["raw_values"] == {"resources": []},
        "NULL_RENDERER_NO_DUPLICATE_WORKLOAD",
    )

    postgres = catalog["apps"]["postgresql"]["spec"]
    require(
        postgres.get("destination") == {"server": SERVER, "namespace": "postgresql"}
        and postgres.get("syncPolicy", {}).get("automated")
        == {"selfHeal": True, "prune": True}
        and any(
            isinstance(member, str) and "/apps/postgresql/" in member
            for member in root_members
        ),
        "ACTIVE_POSTGRESQL_APPLICATION_REQUIRED",
    )
    postgres_sources = postgres.get("sources")
    require(
        type(postgres_sources) is list
        and len(postgres_sources) == 3
        and postgres_sources[2].get("path")
        == f"argocd/apps/postgresql/environments/{environment}/manifests",
        "POSTGRESQL_MANIFEST_SOURCE_PATH",
    )


def validate_scaffolding(catalog: Catalog) -> ResourceIndex:
    """Validate shared-database scaffolding and activation GitOps wiring."""
    contract = catalog["contract"]
    validate_contract(contract)
    resources = index_documents(catalog["docs"], "hindsight", hindsight=True)
    postgres = index_documents(
        catalog["postgres_docs"], contract["database"]["namespace"]
    )

    workload_secret_names = {
        name
        for name, secret in contract["externalSecrets"].items()
        if secret["namespace"] == "hindsight"
    }
    expected: set[tuple[str, str]] = {("Namespace", "hindsight")}
    expected |= {("ServiceAccount", f"hindsight-{role}") for role in ("api", "ui")}
    expected |= {
        (kind, f"hindsight-{role}")
        for kind in ("Deployment", "Service")
        for role in ("api", "ui")
    }
    expected.add(("Ingress", "hindsight-ui"))
    expected |= {("ExternalSecret", name) for name in workload_secret_names}
    expected |= {("CiliumNetworkPolicy", name) for name in BASE_POLICIES}
    require(expected <= set(resources), "RESOURCE_INVENTORY_MISSING")
    require(set(resources) == expected, "UNEXPECTED_RESOURCE")
    require(len(expected) == 18, "RESOURCE_INVENTORY_CONTRACT_INVALID")

    namespace = resources["Namespace", "hindsight"]
    require(protected(namespace), "NAMESPACE_PRUNE_DELETE_PROTECTION")
    require(
        resource_metadata(namespace)
        .get("labels", {})
        .get("pod-security.kubernetes.io/enforce")
        == "restricted",
        "POD_SECURITY_NAMESPACE",
    )
    namespace_annotations = annotations(namespace)
    require(
        all(
            namespace_annotations.get(key) == value
            for key, value in contract["activationGates"][
                "namespaceAnnotations"
            ].items()
        ),
        "ACTIVATION_GATE_MANIFEST_DRIFT",
    )

    for role in ("api", "ui"):
        workload = contract["workloads"][role]
        name = f"hindsight-{role}"
        deployment = resources["Deployment", name]
        deployment_spec = deployment.get("spec")
        require(type(deployment_spec) is dict, "DEPLOYMENT_STRUCTURE")
        pod_template = deployment_spec.get("template")
        require(
            type(pod_template) is dict and type(pod_template.get("spec")) is dict,
            "DEPLOYMENT_STRUCTURE",
        )
        pod = pod_template["spec"]
        require(
            deployment_spec.get("replicas") == workload["replicas"]
            and deployment_spec.get("strategy", {}).get("type") == workload["strategy"],
            "DEPLOYMENT_REPLICA_STRATEGY",
        )
        provenance = contract["imageProvenance"]
        require(
            annotations(deployment)
            == {
                "hindsight/version": provenance["version"],
                "hindsight/image-source-revision": provenance["imageSourceRevision"],
                "hindsight/manifest-compatibility-review-revision": provenance[
                    "manifestCompatibilityReviewRevision"
                ],
                "hindsight/source-mismatch-approved": str(
                    provenance["sourceMismatchApproved"]
                ).lower(),
                "hindsight/image-provenance-approved": str(
                    provenance["imageProvenanceApproved"]
                ).lower(),
            },
            "IMAGE_PROVENANCE_CONTRACT",
        )
        account = resources["ServiceAccount", name]
        require(
            pod.get("serviceAccountName") == name
            and pod.get("automountServiceAccountToken") is False
            and account.get("automountServiceAccountToken") is False,
            "SERVICE_ACCOUNT_TOKEN",
        )
        require(pod.get("enableServiceLinks") is False, "SERVICE_LINK_ENV_COLLISION")
        containers = pod.get("containers")
        require(
            type(containers) is list
            and len(containers) == 1
            and not pod.get("initContainers"),
            "CONTAINER_INVENTORY",
        )
        pod_security = pod.get("securityContext")
        require(
            type(pod_security) is dict
            and pod_security.get("runAsNonRoot") is True
            and pod_security.get("runAsUser") == workload["runAsUser"]
            and pod_security.get("seccompProfile") == {"type": "RuntimeDefault"},
            "POD_SECURITY_CONTEXT",
        )
        container = containers[0]
        container_security = container.get("securityContext")
        require(
            type(container_security) is dict
            and container_security.get("allowPrivilegeEscalation") is False
            and container_security.get("capabilities") == {"drop": ["ALL"]}
            and container_security.get("readOnlyRootFilesystem") is False,
            "CONTAINER_SECURITY_CONTEXT",
        )
        require(not container.get("envFrom"), "ENVFROM_FORBIDDEN")
        require(
            container.get("resources") == workload["resources"]
            and container.get("ports")
            == [{"name": "http", "containerPort": workload["port"]}],
            "DEPLOYMENT_RESOURCES_PORTS",
        )
        entries = container.get("env")
        require(
            type(entries) is list
            and all(
                type(entry) is dict and isinstance(entry.get("name"), str)
                for entry in entries
            )
            and len({entry["name"] for entry in entries}) == len(entries),
            "DUPLICATE_ENV",
        )
        env = {entry["name"]: entry for entry in entries}
        references = workload["secretEnv"]
        require(
            {key for key, item in env.items() if "valueFrom" in item}
            == set(references),
            "SECRET_REF_INVENTORY",
        )
        for key, reference in references.items():
            require(
                env[key]
                == {
                    "name": key,
                    "valueFrom": {"secretKeyRef": {**reference, "optional": False}},
                },
                "INDIVIDUAL_REQUIRED_SECRET_REF",
            )
        literals = workload["literalEnv"]
        require(set(env) == set(literals) | set(references), "ENV_INVENTORY")
        require(
            all(
                env[key] == {"name": key, "value": value}
                for key, value in literals.items()
            ),
            "EXPLICIT_RUNTIME_CONFIG",
        )
        require(container.get("image") == workload["image"], "WORKLOAD_IMAGE_CONTRACT")

        service = resources["Service", name].get("spec")
        require(type(service) is dict, "SERVICE_STRUCTURE")
        selector = {"app.kubernetes.io/name": name}
        require(
            service.get("type") == "ClusterIP" and not service.get("externalIPs"),
            "PRIVATE_SERVICE_ONLY",
        )
        require(
            service.get("selector")
            == deployment_spec.get("selector", {}).get("matchLabels")
            == selector
            and all(
                pod_template.get("metadata", {}).get("labels", {}).get(key) == value
                for key, value in selector.items()
            ),
            "SERVICE_POD_SELECTOR",
        )
        require(
            service.get("ports")
            == [
                {
                    "name": "http",
                    "port": workload["port"],
                    "targetPort": "http",
                    "protocol": "TCP",
                }
            ],
            "SERVICE_PORT_MAPPING",
        )
        _validate_probe_contract(container, role, workload["startupBudgetSeconds"])

        if role == "api":
            require(
                not pod.get("volumes") and not container.get("volumeMounts"),
                "API_VOLUME_INVENTORY",
            )
        else:
            require(
                pod.get("volumes")
                == [{"name": "next-cache", "emptyDir": {"sizeLimit": "256Mi"}}]
                and container.get("volumeMounts")
                == [
                    {
                        "name": "next-cache",
                        "mountPath": "/app/control-plane/.next/cache",
                    }
                ],
                "UI_CACHE_VOLUME_CONTRACT",
            )

    for name in workload_secret_names:
        _external_secret_spec(
            resources["ExternalSecret", name], contract["externalSecrets"][name]
        )
    _validate_shared_database(catalog, postgres)
    _validate_networks(resources, contract)
    _validate_ingress(resources, contract)
    _validate_application_wiring(catalog)
    return resources


def _approved_purpose_policy(policies: list[Document], purpose: str) -> bool:
    """Return whether each matching policy covers the actual API consumer."""
    expected_selector = {"matchLabels": {"k8s:app.kubernetes.io/name": "hindsight-api"}}
    candidates: list[bool] = []
    for policy in policies:
        policy_annotations = annotations(policy)
        if policy_annotations.get("hindsight/egress-purpose") != purpose:
            continue
        spec = _network_policy_spec(policy)
        rules = spec.get("egress", [])
        candidates.append(
            bool(policy_annotations.get("hindsight/source-evidence"))
            and spec["endpointSelector"] == expected_selector
            and "ingress" not in spec
            and bool(rules)
            and all(
                protocol == "TCP"
                for rule in rules
                for protocol, _ in _cilium_ports(rule)
            )
        )
    return bool(candidates) and all(candidates)


def activation_failures(catalog: Catalog) -> list[str]:
    """Return fixed offline activation categories without claiming live readiness."""
    resources = validate_scaffolding(catalog)
    failures: list[str] = []

    def gate(ok: object, code: str) -> None:
        if not ok:
            failures.append(code)

    for role in ("api", "ui"):
        deployment = resources["Deployment", f"hindsight-{role}"]
        image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
        match = re.fullmatch(r"[^\s@]+@sha256:([0-9a-f]{64})", image)
        gate(
            match is not None and match[1] != "0" * 64,
            f"IMAGE_{role.upper()}_IMMUTABLE_OPERATOR_IMAGE_REQUIRED",
        )
        gate(
            annotations(deployment).get("hindsight/image-provenance-approved")
            == "true",
            f"IMAGE_{role.upper()}_SOURCE_AND_OFFLINE_CACHE_PROVENANCE_UNAPPROVED",
        )

    namespace_annotations = annotations(resources["Namespace", "hindsight"])
    for annotation, code in ACTIVATION_GATE_CODES.items():
        gate(namespace_annotations.get(annotation) == "true", code)
    policies = [
        document
        for (kind, _), document in resources.items()
        if kind == "CiliumNetworkPolicy"
    ]
    gate(
        _approved_purpose_policy(policies, "inference"),
        "INFERENCE_EGRESS_EXCEPTION_MISSING",
    )
    return failures


def main(argv: list[str] | None = None) -> int:
    """Run offline checks while emitting only fixed categories."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--internal-root", type=Path, required=True)
    parser.add_argument(
        "--environment",
        required=True,
        help="Explicit private environment; no discovery",
    )
    parser.add_argument(
        "--public-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--scaffolding",
        action="store_true",
        help="Structural authoring only, NOT deployment readiness",
    )
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog(args.public_root, args.internal_root, args.environment)
        validate_scaffolding(catalog)
        if args.scaffolding:
            print(
                "PASS: offline shared-database scaffolding contract; "
                "NOT deployment-ready."
            )
            return 0
        failures = activation_failures(catalog)
        if failures:
            for code in failures:
                print("BLOCKED: " + code)
            print(
                "Activation prerequisites FAIL; no cluster, secret values, "
                "resource readiness, or runtime acceptance checked."
            )
            return 1
        print(
            "PASS: offline activation prerequisites only; independently verify "
            "live acceptance before use."
        )
        return 0
    except Invalid as exc:
        print("FAIL: " + str(exc))
    except (
        AttributeError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        OSError,
        yaml.YAMLError,
    ):
        print("FAIL: STRUCTURE_OR_INPUT_INVALID (details withheld)")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
