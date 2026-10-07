"""Focused offline regressions for the staged Hindsight manifests.

Run with ``PYTHONDONTWRITEBYTECODE=1``, ``HINDSIGHT_TEST_INTERNAL_ROOT``, and
``HINDSIGHT_TEST_ENVIRONMENT``. Tests mutate only in-memory copies and never emit
private fixture contents.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import importlib.util
import io
import os
import re
import stat
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
PUBLIC = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "hindsight_validator", PUBLIC / "utils/validate-hindsight.py"
)
assert SPEC is not None and SPEC.loader is not None
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)


class HindsightManifests(unittest.TestCase):
    """Exercise named fixtures through isolated in-memory mutations."""

    internal: Path
    environment: str
    original: dict[str, Any]
    catalog: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Load only explicitly authorized public and private fixture roots."""
        root = os.environ.get("HINDSIGHT_TEST_INTERNAL_ROOT")
        environment = os.environ.get("HINDSIGHT_TEST_ENVIRONMENT")
        if not root or not environment:
            raise RuntimeError(
                "Explicit task fixture root/environment required; "
                "see docs/hindsight-kubernetes.md"
            )
        cls.internal = Path(root)
        cls.environment = environment
        cls.original = v.load_catalog(PUBLIC, cls.internal, environment)

    def setUp(self) -> None:
        """Copy fixtures so no case modifies files or another test."""
        self.catalog = copy.deepcopy(self.original)

    def resource(self, kind: str, name: str) -> dict[str, Any]:
        """Return one Hindsight resource without displaying its contents."""
        return next(
            document
            for document in self.catalog["docs"]
            if document["kind"] == kind and document["metadata"]["name"] == name
        )

    def postgres_resource(self, kind: str, name: str) -> dict[str, Any]:
        """Return one PostgreSQL resource without displaying its contents."""
        return next(
            document
            for document in self.catalog["postgres_docs"]
            if document["kind"] == kind and document["metadata"]["name"] == name
        )

    def container(self, role: str) -> dict[str, Any]:
        """Return the sole container for a workload role."""
        return self.resource("Deployment", f"hindsight-{role}")["spec"]["template"][
            "spec"
        ]["containers"][0]

    def rejected(self, code: str) -> None:
        """Assert one fixed diagnostic without serializing fixture data."""
        with self.assertRaisesRegex(v.Invalid, "^" + re.escape(code) + "$"):
            v.validate_scaffolding(self.catalog)

    def append_egress_policy(
        self,
        name: str,
        cidr: str,
        *,
        selector: str = "hindsight-api",
        purpose: str | None = None,
    ) -> dict[str, Any]:
        """Append a bounded synthetic policy for an explicitly named consumer."""
        metadata: dict[str, Any] = {"name": name, "namespace": "hindsight"}
        if purpose:
            metadata["annotations"] = {
                "hindsight/egress-purpose": purpose,
                "hindsight/source-evidence": "synthetic-offline-only",
            }
        policy = {
            "apiVersion": "cilium.io/v2",
            "kind": "CiliumNetworkPolicy",
            "metadata": metadata,
            "spec": {
                "endpointSelector": {
                    "matchLabels": {f"k8s:app.kubernetes.io/name": selector}
                },
                "egress": [
                    {
                        "toCIDR": [cidr],
                        "toPorts": [{"ports": [{"protocol": "TCP", "port": "443"}]}],
                    }
                ],
            },
        }
        self.catalog["docs"].append(policy)
        return policy

    def cli_args(self) -> list[str]:
        """Return explicit offline CLI arguments."""
        return [
            "--public-root",
            str(PUBLIC),
            "--internal-root",
            str(self.internal),
            "--environment",
            self.environment,
        ]

    def validate_network_policy_set(self) -> None:
        """Run broad-rule checks without invoking the exact inventory check."""
        policies = [
            document
            for document in self.catalog["docs"]
            if document["kind"] == "CiliumNetworkPolicy"
        ]
        labels = {
            role: self.resource("Deployment", f"hindsight-{role}")["spec"]["template"][
                "metadata"
            ]["labels"]
            for role in ("api", "ui")
        }
        v.narrow_network_policies(policies, labels)

    # Baseline behavior and the deliberately staged activation result.

    def test_scaffolding_has_eighteen_resources(self) -> None:
        resources = v.validate_scaffolding(self.catalog)
        self.assertEqual(len(resources), 18)
        policies = [
            document
            for (kind, _), document in resources.items()
            if kind == "CiliumNetworkPolicy"
        ]
        self.assertEqual(len(policies), 7)
        self.assertTrue(
            all(policy["apiVersion"] == "cilium.io/v2" for policy in policies)
        )
        self.assertEqual(
            self.resource("CiliumNetworkPolicy", "default-deny")["spec"],
            {
                "endpointSelector": {},
                "enableDefaultDeny": {"ingress": True, "egress": True},
                "ingress": [{}],
                "egress": [{}],
            },
        )
        dns = self.resource("CiliumNetworkPolicy", "allow-dns")["spec"]["egress"][0]
        self.assertEqual(
            dns["toEndpoints"],
            [
                {
                    "matchLabels": {
                        "k8s:io.kubernetes.pod.namespace": "kube-system",
                        "k8s:k8s-app": "kube-dns",
                    }
                }
            ],
        )
        self.assertEqual(
            dns["toPorts"],
            [
                {
                    "ports": [
                        {"protocol": "UDP", "port": "53"},
                        {"protocol": "TCP", "port": "53"},
                    ]
                }
            ],
        )

    def test_activation_has_exactly_one_live_gate(self) -> None:
        expected = {"OPERATOR_PREFLIGHT_AND_RUNTIME_ACCEPTANCE_UNAPPROVED"}
        failures = v.activation_failures(self.catalog)
        self.assertEqual(len(failures), 1)
        self.assertEqual(set(failures), expected)
        self.assertFalse(any(code.startswith("IMAGE_") for code in failures))
        self.assertNotIn("INFERENCE_EGRESS_UNAPPROVED", failures)
        self.assertNotIn("INFERENCE_EGRESS_EXCEPTION_MISSING", failures)

    def test_cli_modes_are_distinct_and_safe(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(v.main(self.cli_args() + ["--scaffolding"]), 0)
            self.assertEqual(v.main(self.cli_args()), 1)
        text = output.getvalue()
        self.assertIn("NOT deployment-ready", text)
        self.assertIn("OPERATOR_PREFLIGHT_AND_RUNTIME_ACCEPTANCE_UNAPPROVED", text)
        self.assertNotIn("SHARED_DATABASE_UNAPPROVED", text)
        self.assertNotIn("INFERENCE_EGRESS_EXCEPTION_MISSING", text)
        self.assertNotIn("remoteKey", text)

    def test_validation_does_not_network_or_write(self) -> None:
        with (
            patch("socket.socket", side_effect=RuntimeError("network forbidden")),
            patch.object(
                Path, "write_text", side_effect=RuntimeError("write forbidden")
            ),
            patch.object(
                Path, "write_bytes", side_effect=RuntimeError("write forbidden")
            ),
            patch.object(Path, "mkdir", side_effect=RuntimeError("write forbidden")),
        ):
            loaded = v.load_catalog(PUBLIC, self.internal, self.environment)
            v.validate_scaffolding(loaded)
            self.assertTrue(v.activation_failures(loaded))

    def test_missing_contract_fails_before_other_inputs(self) -> None:
        output = io.StringIO()
        with (
            patch.object(
                v, "read_yaml", side_effect=v.Invalid("YAML_UNREADABLE_OR_INVALID")
            ) as reader,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(v.main(self.cli_args() + ["--scaffolding"]), 2)
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(output.getvalue(), "FAIL: YAML_UNREADABLE_OR_INVALID\n")

    # Shared database ownership and exact references.

    def test_postgresql_task_resources_are_exact(self) -> None:
        database = self.catalog["contract"]["database"]
        resources = {
            (document["kind"], document["metadata"]["name"])
            for document in self.catalog["postgres_docs"]
            if document["kind"] != "Cluster"
        }
        self.assertEqual(
            resources,
            {
                ("Database", f"{database['cluster']}-{database['name']}"),
                ("DatabaseRole", database["ownerSecret"]),
                ("ExternalSecret", database["ownerSecret"]),
            },
        )

    def test_database_extensions_and_retention_are_exact(self) -> None:
        database = self.catalog["contract"]["database"]
        document = self.postgres_resource(
            "Database", f"{database['cluster']}-{database['name']}"
        )
        role = self.postgres_resource("DatabaseRole", database["ownerSecret"])
        self.assertEqual(document["spec"]["extensions"], database["extensions"])
        self.assertEqual(document["spec"]["databaseReclaimPolicy"], "retain")
        self.assertEqual(role["spec"]["databaseRoleReclaimPolicy"], "retain")
        self.assertTrue(v.protected(document))
        self.assertTrue(v.protected(role))

    def test_database_role_escalation_is_rejected(self) -> None:
        name = self.catalog["contract"]["database"]["ownerSecret"]
        for field in (
            "superuser",
            "createdb",
            "createrole",
            "replication",
            "bypassrls",
        ):
            with self.subTest(field=field):
                self.catalog = copy.deepcopy(self.original)
                self.postgres_resource("DatabaseRole", name)["spec"][field] = True
                self.rejected("DB_NONSUPERUSER_ROLE")

    def test_database_role_connection_limit_is_exact(self) -> None:
        name = self.catalog["contract"]["database"]["ownerSecret"]
        self.postgres_resource("DatabaseRole", name)["spec"]["connectionLimit"] = 21
        self.rejected("DB_NONSUPERUSER_ROLE")

    def test_owner_reference_and_workload_dsn_are_separate(self) -> None:
        contract = self.catalog["contract"]
        database = contract["database"]
        dsn_name = contract["workloads"]["api"]["secretEnv"][
            "HINDSIGHT_API_DATABASE_URL"
        ]["name"]
        self.assertNotEqual(database["ownerSecret"], dsn_name)
        owner = self.postgres_resource("ExternalSecret", database["ownerSecret"])
        self.assertEqual(
            owner["spec"]["target"]["template"]["type"],
            "kubernetes.io/basic-auth",
        )

    def test_combined_owner_and_dsn_reference_is_rejected(self) -> None:
        contract = self.catalog["contract"]
        contract["workloads"]["api"]["secretEnv"]["HINDSIGHT_API_DATABASE_URL"][
            "name"
        ] = contract["database"]["ownerSecret"]
        with self.assertRaisesRegex(
            v.Invalid, "^CONTRACT_DATABASE_REFERENCES_NOT_SEPARATE$"
        ):
            v.validate_contract(contract)

    def test_hindsight_data_resource_kinds_are_rejected(self) -> None:
        for kind in sorted(v.FORBIDDEN_HINDSIGHT_KINDS):
            with self.subTest(kind=kind):
                self.catalog = copy.deepcopy(self.original)
                self.catalog["docs"].append(
                    {
                        "apiVersion": "example.invalid/v1",
                        "kind": kind,
                        "metadata": {"name": "synthetic", "namespace": "hindsight"},
                    }
                )
                self.rejected("HINDSIGHT_OWNED_DATA_RESOURCE_FORBIDDEN")

    def test_literal_secret_is_rejected(self) -> None:
        self.catalog["docs"].append(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "synthetic", "namespace": "hindsight"},
            }
        )
        self.rejected("LITERAL_SECRET_FORBIDDEN")

    def test_cluster_cannot_own_the_hindsight_role(self) -> None:
        database = self.catalog["contract"]["database"]
        cluster = self.postgres_resource("Cluster", database["cluster"])
        cluster["spec"].setdefault("managed", {}).setdefault("roles", []).append(
            {"name": database["owner"]}
        )
        self.rejected("SHARED_CLUSTER_HINDSIGHT_OWNERSHIP_FORBIDDEN")

    # Workloads, settings, services, and probes.

    def test_database_runtime_settings_are_exact(self) -> None:
        env = {item["name"]: item for item in self.container("api")["env"]}
        self.assertEqual(env["PGSSLMODE"]["value"], "disable")
        self.assertEqual(env["HINDSIGHT_API_DB_POOL_MIN_SIZE"]["value"], "1")
        self.assertEqual(env["HINDSIGHT_API_DB_POOL_MAX_SIZE"]["value"], "10")
        self.assertEqual(env["HINDSIGHT_API_DATABASE_SCHEMA"]["value"], "public")
        self.assertFalse(
            self.resource("Deployment", "hindsight-api")["spec"]["template"][
                "spec"
            ].get("volumes")
        )

    def test_final_images_provenance_and_home_are_exact(self) -> None:
        self.assertEqual(self.catalog["contract"]["contractVersion"], 5)
        self.assertEqual(
            self.catalog["contract"]["imageProvenance"],
            v.EXPECTED_IMAGE_PROVENANCE,
        )
        for role in ("api", "ui"):
            with self.subTest(role=role):
                container = self.container(role)
                self.assertEqual(container["image"], v.EXPECTED_IMAGES[role])
                self.assertFalse(container["securityContext"]["readOnlyRootFilesystem"])
        api_env = {item["name"]: item for item in self.container("api")["env"]}
        ui_env = {item["name"]: item for item in self.container("ui")["env"]}
        self.assertEqual(api_env["HOME"]["value"], "/home/hindsight")
        self.assertEqual(ui_env["HOME"]["value"], "/home/node")
        self.assertEqual(api_env["HINDSIGHT_API_MODEL_INIT_TIMEOUT"]["value"], "540")

    def test_database_encryption_mode_drift_is_rejected(self) -> None:
        self.catalog["contract"]["workloads"]["api"]["literalEnv"][
            "PGSSLMODE"
        ] = "require"
        with self.assertRaisesRegex(v.Invalid, "^CONTRACT_UNSAFE_RUNTIME_SETTING$"):
            v.validate_contract(self.catalog["contract"])

    def test_database_endpoint_and_port_are_fixed(self) -> None:
        contract = self.catalog["contract"]
        contract["database"]["service"]["port"] = 15432
        contract["network"]["databasePort"] = 15432
        with self.assertRaisesRegex(v.Invalid, "^CONTRACT_DATABASE_ENDPOINT_INVALID$"):
            v.validate_contract(contract)

    def test_services_are_private_and_port_matched(self) -> None:
        self.resource("Service", "hindsight-ui")["spec"]["ports"][0]["port"] = 3001
        self.rejected("SERVICE_PORT_MAPPING")

    def test_database_network_peer_is_combined_and_bounded(self) -> None:
        policy = self.resource("CiliumNetworkPolicy", "allow-api-to-db")
        self.assertEqual(policy["apiVersion"], "cilium.io/v2")
        peer = policy["spec"]["egress"][0]["toEndpoints"][0]
        self.assertEqual(
            peer,
            {
                "matchLabels": {
                    "k8s:io.kubernetes.pod.namespace": "postgresql",
                    "k8s:cnpg.io/cluster": "postgresql",
                }
            },
        )
        self.assertEqual(
            policy["spec"]["egress"][0]["toPorts"],
            [{"ports": [{"protocol": "TCP", "port": "5432"}]}],
        )
        policy["apiVersion"] = "networking.k8s.io/v1"
        policy["kind"] = "NetworkPolicy"
        self.rejected("KUBERNETES_NETWORK_POLICY_FORBIDDEN")

    def test_split_database_peer_is_rejected(self) -> None:
        policy = self.resource("CiliumNetworkPolicy", "allow-api-to-db")
        labels = policy["spec"]["egress"][0]["toEndpoints"][0]["matchLabels"]
        policy["spec"]["egress"][0]["toEndpoints"] = [
            {
                "matchLabels": {
                    "k8s:io.kubernetes.pod.namespace": labels[
                        "k8s:io.kubernetes.pod.namespace"
                    ]
                }
            },
            {"matchLabels": {"k8s:cnpg.io/cluster": labels["k8s:cnpg.io/cluster"]}},
        ]
        self.rejected("NETWORK_BROAD_PEER")

    def test_inference_and_traefik_flows_are_exact(self) -> None:
        network = self.catalog["contract"]["network"]
        self.assertEqual(
            {key: network[key] for key in v.EXPECTED_INFERENCE},
            v.EXPECTED_INFERENCE,
        )
        inference = self.resource("CiliumNetworkPolicy", "allow-api-to-bifrost")[
            "spec"
        ]["egress"][0]
        self.assertEqual(
            inference["toEndpoints"][0]["matchLabels"],
            {
                "k8s:io.kubernetes.pod.namespace": network["inferenceNamespace"],
                **{
                    f"k8s:{key}": value
                    for key, value in network["inferencePodLabels"].items()
                },
            },
        )
        self.assertEqual(
            inference["toPorts"],
            [{"ports": [{"protocol": "TCP", "port": "8080"}]}],
        )
        ingress = self.resource("CiliumNetworkPolicy", "allow-ui-from-traefik")["spec"][
            "ingress"
        ][0]
        self.assertEqual(
            ingress["fromEndpoints"][0]["matchLabels"],
            {
                "k8s:io.kubernetes.pod.namespace": network["ingressNamespace"],
                **{
                    f"k8s:{key}": value
                    for key, value in network["ingressPodLabels"].items()
                },
            },
        )
        self.assertEqual(
            ingress["toPorts"],
            [{"ports": [{"protocol": "TCP", "port": "3000"}]}],
        )

    def test_inference_provider_model_and_destination_drift_is_rejected(self) -> None:
        for field in v.EXPECTED_INFERENCE:
            with self.subTest(field=field):
                self.catalog = copy.deepcopy(self.original)
                self.catalog["contract"]["network"][field] = "synthetic"
                with self.assertRaisesRegex(v.Invalid, "^CONTRACT_NETWORK_INVALID$"):
                    v.validate_contract(self.catalog["contract"])

    def test_lan_ingress_is_ui_only_and_uses_default_tls_store(self) -> None:
        ingress = self.resource("Ingress", "hindsight-ui")
        self.assertNotIn("annotations", ingress["metadata"])
        self.assertEqual(ingress["spec"]["ingressClassName"], "traefik")
        self.assertEqual(
            ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"][
                "name"
            ],
            "hindsight-ui",
        )
        self.assertNotIn("secretName", ingress["spec"]["tls"][0])
        self.assertFalse(
            any(
                document["kind"] == "Ingress"
                and document["metadata"]["name"] == "hindsight-api"
                for document in self.catalog["docs"]
            )
        )

    def test_api_probe_contract_is_exact(self) -> None:
        self.container("api")["livenessProbe"]["httpGet"]["path"] = "/health"
        self.rejected("CANONICAL_PROBE_CONTRACT")

    def test_ui_readiness_command_is_exact(self) -> None:
        command = self.container("ui")["readinessProbe"]["exec"]["command"]
        command[2] = "process.exit(0)"
        self.rejected("CANONICAL_PROBE_CONTRACT")

    def test_workloads_and_accounts_remain_tokenless(self) -> None:
        self.resource("ServiceAccount", "hindsight-api")[
            "automountServiceAccountToken"
        ] = True
        self.rejected("SERVICE_ACCOUNT_TOKEN")

    def test_envfrom_is_rejected(self) -> None:
        self.container("api")["envFrom"] = [{"secretRef": {"name": "synthetic"}}]
        self.rejected("ENVFROM_FORBIDDEN")

    def test_secret_references_are_individual_and_required(self) -> None:
        item = next(
            entry
            for entry in self.container("api")["env"]
            if entry["name"] == "HINDSIGHT_API_LLM_MODEL"
        )
        item["valueFrom"]["secretKeyRef"]["optional"] = True
        self.rejected("INDIVIDUAL_REQUIRED_SECRET_REF")

    def test_external_reference_drift_is_rejected_without_values(self) -> None:
        contract = self.catalog["contract"]
        name = next(
            name
            for name, secret in contract["externalSecrets"].items()
            if secret["namespace"] == "hindsight"
        )
        contract["externalSecrets"][name]["remoteKey"] = "synthetic/reference"
        self.rejected("EXTERNAL_PROPERTY_CONTRACT")

    def test_rotation_is_an_operating_contract_not_an_activation_gate(self) -> None:
        self.catalog["contract"]["rotation"]["automaticWorkloadRestart"] = True
        with self.assertRaisesRegex(v.Invalid, "^CONTRACT_ROTATION_INVALID$"):
            v.validate_contract(self.catalog["contract"])
        self.assertFalse(
            any(
                "RESTART" in failure for failure in v.activation_failures(self.original)
            )
        )

    # Effective egress checks.

    def test_collectively_unrestricted_egress_is_rejected(self) -> None:
        self.append_egress_policy("synthetic-a", "0.0.0.0/1")
        self.append_egress_policy("synthetic-b", "128.0.0.0/1")
        with self.assertRaisesRegex(v.Invalid, "^NETWORK_AGGREGATE_UNRESTRICTED_CIDR$"):
            self.validate_network_policy_set()

    def test_invalid_cidr_has_a_fixed_diagnostic(self) -> None:
        self.append_egress_policy("synthetic-invalid", "192.0.2.1/24")
        with self.assertRaisesRegex(v.Invalid, "^NETWORK_CIDR_INVALID$"):
            self.validate_network_policy_set()

    def test_wrong_consumer_cannot_clear_inference_category(self) -> None:
        self.append_egress_policy(
            "synthetic-wrong-consumer",
            "192.0.2.17/32",
            selector="hindsight-ui",
            purpose="inference",
        )
        policies = [
            document
            for document in self.catalog["docs"]
            if document["kind"] == "CiliumNetworkPolicy"
        ]
        self.assertFalse(v._approved_purpose_policy(policies, "inference"))

    def test_additional_inference_policy_is_rejected(self) -> None:
        self.append_egress_policy(
            "synthetic-inference", "192.0.2.17/32", purpose="inference"
        )
        self.rejected("UNEXPECTED_RESOURCE")

    # Overlay patch scope and safe path handling.

    def overlay_variant(self, mutator: Any, expected: str) -> None:
        """Mutate the selected overlay document and assert a fixed failure."""
        original = v.read_yaml

        def altered(path: Path, root: Path, multiple: bool = False) -> Any:
            data = original(path, root, multiple)
            if (
                path.name == "kustomization.yaml"
                and path.parent.name == self.environment
            ):
                data = copy.deepcopy(data)
                mutator(data)
            return data

        with patch.object(v, "read_yaml", side_effect=altered):
            with self.assertRaisesRegex(v.Invalid, "^" + re.escape(expected) + "$"):
                v.local_application(
                    PUBLIC, self.internal, self.environment, "hindsight"
                )

    def test_overlay_target_is_exact(self) -> None:
        for field in ("group", "version", "kind", "name"):
            with self.subTest(field=field):
                self.overlay_variant(
                    lambda data, selected=field: data["patches"][0][
                        "target"
                    ].__setitem__(selected, "synthetic"),
                    "OVERLAY_TARGET",
                )

    def test_overlay_array_add_is_rejected(self) -> None:
        operations = [{"op": "add", "path": "/spec/sources/0", "value": {}}]
        self.overlay_variant(
            lambda data: data["patches"][0].__setitem__(
                "patch", v.yaml.safe_dump(operations)
            ),
            "OVERLAY_ARRAY_ADD_UNSUPPORTED",
        )

    def test_overlay_bad_index_and_pointer_are_rejected(self) -> None:
        cases = (
            (
                [{"op": "replace", "path": "/spec/sources/-1", "value": {}}],
                "OVERLAY_ARRAY_INDEX",
            ),
            (
                [{"op": "add", "path": "/spec/synthetic~2field", "value": True}],
                "OVERLAY_PATCH_PATH_TOKEN",
            ),
        )
        for operations, code in cases:
            with self.subTest(code=code):
                self.overlay_variant(
                    lambda data, value=operations: data["patches"][0].__setitem__(
                        "patch", v.yaml.safe_dump(value)
                    ),
                    code,
                )

    def test_supported_object_add_and_array_replace(self) -> None:
        document = {"spec": {"items": ["first", "second"]}}
        v._apply_patch(
            document,
            {"op": "replace", "path": "/spec/items/1", "value": "changed"},
        )
        v._apply_patch(document, {"op": "add", "path": "/spec/enabled", "value": True})
        self.assertEqual(
            document,
            {"spec": {"items": ["first", "changed"], "enabled": True}},
        )

    def test_read_rejects_path_outside_root_before_read(self) -> None:
        reader = Mock(side_effect=AssertionError("must not read"))
        with patch.object(Path, "read_text", reader):
            with self.assertRaisesRegex(v.Invalid, "^PATH_OUTSIDE_APPROVED_ROOT$"):
                v.read_yaml(Path("/outside/input.yaml"), Path("/approved"))
        reader.assert_not_called()

    def test_read_rejects_symlink_before_read(self) -> None:
        reader = Mock(side_effect=AssertionError("must not read"))

        def fake_lstat(path: Path) -> Any:
            mode = stat.S_IFLNK if path == Path("/approved/redirect") else stat.S_IFDIR
            return type("SyntheticStat", (), {"st_mode": mode})()

        with (
            patch.object(v.os, "lstat", side_effect=fake_lstat),
            patch.object(Path, "read_text", reader),
        ):
            with self.assertRaisesRegex(v.Invalid, "^SYMLINK_NOT_ALLOWED$"):
                v.read_yaml(Path("/approved/redirect/input.yaml"), Path("/approved"))
        reader.assert_not_called()

    # Malformed inputs and staged GitOps wiring.

    def test_malformed_metadata_and_annotations_are_fixed_diagnostics(self) -> None:
        self.catalog["docs"][0]["metadata"] = None
        self.rejected("RESOURCE_METADATA_INVALID")
        self.catalog = copy.deepcopy(self.original)
        self.resource("Namespace", "hindsight")["metadata"]["annotations"] = None
        self.rejected("RESOURCE_ANNOTATIONS_INVALID")

    def test_cli_unexpected_structure_is_redacted(self) -> None:
        output = io.StringIO()
        with (
            patch.object(v, "load_catalog", side_effect=AttributeError("private")),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(v.main(self.cli_args()), 2)
        self.assertEqual(
            output.getvalue(),
            "FAIL: STRUCTURE_OR_INPUT_INVALID (details withheld)\n",
        )

    def test_hindsight_is_registered_but_initial_sync_is_manual(self) -> None:
        members = self.catalog["root"]["resources"]
        self.assertEqual(
            sum("/apps/hindsight/" in member for member in members),
            1,
        )
        self.assertNotIn(
            "automated", self.catalog["apps"]["hindsight"]["spec"]["syncPolicy"]
        )
        self.assertNotIn("finalizers", self.catalog["apps"]["hindsight"]["metadata"])

    def test_exact_project_permission_is_required(self) -> None:
        project = self.catalog["project"]["spec"]
        self.assertEqual(
            [
                item["kind"]
                for item in project["namespaceResourceBlacklist"]
                if item["group"] == "cilium.io"
            ],
            ["CiliumEndpoint", "CiliumNodeConfig"],
        )
        self.assertEqual(
            [
                item
                for item in project["clusterResourceBlacklist"]
                if item["group"] == "cilium.io"
            ],
            [{"group": "cilium.io", "kind": "*"}],
        )
        project["destinations"].append({"namespace": "*", "server": v.SERVER})
        self.rejected("PROJECT_EXACT_SOURCE_DESTINATION_PERMISSION")

        self.catalog = copy.deepcopy(self.original)
        self.catalog["project"]["spec"]["namespaceResourceBlacklist"] = [
            {"group": "cilium.io", "kind": "*"}
        ]
        self.rejected("PROJECT_CILIUM_PERMISSION")

    def test_raw_renderer_is_empty(self) -> None:
        self.assertEqual(self.catalog["raw_values"], {"resources": []})

    def test_image_provenance_drift_is_rejected(self) -> None:
        self.catalog["contract"]["imageProvenance"][
            "manifestCompatibilityReviewRevision"
        ] = ("c" * 40)
        with self.assertRaisesRegex(v.Invalid, "^CONTRACT_IMAGE_PROVENANCE_INVALID$"):
            v.validate_contract(self.catalog["contract"])

    def test_contract_shape_and_version_fail_closed(self) -> None:
        missing = copy.deepcopy(self.catalog["contract"])
        del missing["database"]
        with self.assertRaisesRegex(v.Invalid, "^CONTRACT_STRUCTURE_INVALID$"):
            v.validate_contract(missing)
        self.catalog["contract"]["contractVersion"] = 3
        with self.assertRaisesRegex(v.Invalid, "^CONTRACT_VERSION_UNSUPPORTED$"):
            v.validate_contract(self.catalog["contract"])

    def test_yaml_duplicate_scalar_and_multiple_documents_are_rejected(self) -> None:
        root = Path("/approved")
        path = root / "input.yaml"
        synthetic_stat = type("SyntheticStat", (), {"st_mode": stat.S_IFREG})()
        cases = (
            ("key: first\nkey: second\n", "YAML_UNREADABLE_OR_INVALID"),
            ("- synthetic\n", "YAML_OBJECT_REQUIRED"),
            (
                "kind: Example\n---\nkind: Example\n",
                "YAML_SINGLE_DOCUMENT_REQUIRED",
            ),
        )
        for text, code in cases:
            with self.subTest(code=code):
                with (
                    patch.object(v.os, "lstat", return_value=synthetic_stat),
                    patch.object(Path, "read_text", return_value=text),
                ):
                    with self.assertRaisesRegex(v.Invalid, "^" + code + "$"):
                        v.read_yaml(path, root)

    def test_public_validator_does_not_embed_private_references(self) -> None:
        contract = self.catalog["contract"]
        references = set(contract["externalSecrets"])
        references.update(
            item["remoteKey"] for item in contract["externalSecrets"].values()
        )
        references.update(
            item["storeRef"]["name"] for item in contract["externalSecrets"].values()
        )
        text = (PUBLIC / "utils/validate-hindsight.py").read_text()
        strings = {
            node.value
            for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertTrue(
            strings.isdisjoint(references), "PUBLIC_PRIVATE_REFERENCE_LITERAL"
        )


if __name__ == "__main__":
    unittest.main()
