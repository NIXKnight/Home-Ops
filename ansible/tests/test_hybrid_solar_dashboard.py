#!/usr/bin/env python3
"""Offline dashboard contract tests; --prometheus adds read-only cluster GET checks.

No gateway exports, credentials, listeners, deployments or live mutations. The
optional Prometheus tests use existing kubectl authentication without inspecting it.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import itertools
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("solar_renderer", ROOT / "utils/render-solar-dashboard.py")
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)
MANIFEST = ROOT.parent / "Home-Ops-Internal/argocd/apps/grafana/environments/belisarius/manifests/grafanadashboard-hybrid-solar.yaml"
LIVE = False
ENGINE_NOTICES = {"warnings": set(), "infos": set()}
NOTICE_LOCK = threading.Lock()
PROXY = "/api/v1/namespaces/monitoring/services/http:prometheus-server:80/proxy/api/v1/query?"
PROXY_RANGE = "/api/v1/namespaces/monitoring/services/http:prometheus-server:80/proxy/api/v1/query_range?"
DEVICE_DAY_END_OFFSET_S = 4 * 3600 + 59 * 60
DEVICE_DAY_WINDOW_S = 23 * 3600 + 59 * 60

CURRENT_TARGETS = {
    8: "A", 19: "A", 1: "ABCDEF", 2: "ABCD", 33: "ACDFGHIJK", 62: "AB",
    28: "ABCDEFG", 32: "ABCDE", 61: "ADE", 29: "ABCD", 38: "ABC",
    17: "ABCDE", 18: "ABDE", 39: "BCDE", 53: "ABCDEFG",
}
HISTORICAL_TARGETS = {
    35: "A", 12: "ABC", 50: "AB", 51: "ABCD", 13: "AB", 34: "A", 37: "A",
    46: "ABC", 14: "A", 22: "ABCD", 16: "ABC", 36: "AB", 48: "A",
    43: "ABC", 49: "ABCD",
}
AGGREGATE_TARGETS = {
    39: "A", 44: "A", 47: "ABC", 41: "ACDEFGH", 45: "ABC", 40: "ABC",
    52: "ABCDE",
}
SETTINGS_TARGETS = {61: "BC", 64: "ABCDEFG", 65: "ABCDEF"}
DIAGNOSTIC_TARGETS = {1: "GHI", 41: "BI", 42: "ABCDEFGH"}
COVERAGE_TARGETS = {(39, "F"), (44, "B"), (47, "D"), (45, "D"),
                    (40, "D"), (52, "F")}
DIAGNOSTIC_METRICS = (
    "telemetry_poll_timestamp_seconds", "telemetry_poll_success",
    "telemetry_poll_fields_received", "telemetry_poll_fields_expected",
    "grid_total_power_read_success", "fault_state_known",
    "settings_poll_timestamp_seconds", "settings_poll_success",
    "settings_poll_fields_received", "settings_poll_fields_expected",
)


def panels(dashboard):
    return {p["id"]: p for p in renderer.panels(dashboard["panels"]) if p.get("targets")}


def target(dashboard, panel, ref):
    return next(t for t in panels(dashboard)[panel]["targets"] if t["refId"] == ref)


def substitute(expr, cadence):
    for key, value in {"${solar_sample_interval_s}": str(cadence),
                       "$__range_s": "21600", "$__range": "6h",
                       "$__rate_interval": "1m", "$__interval": "1m"}.items():
        expr = expr.replace(key, value)
    if "$" in expr:
        raise ValueError("unsubstituted dashboard variable")
    return expr


def prometheus_request(route, params, timeout=45):
    url = route + urllib.parse.urlencode(params)
    result = subprocess.run(["kubectl", "--request-timeout=" + str(timeout - 5) + "s",
                             "get", "--raw", url], text=True, capture_output=True,
                            timeout=timeout)
    if result.returncode:
        # Never reproduce authentication plugin diagnostics or response dumps.
        raise AssertionError("read-only solar Prometheus GET failed; diagnostics withheld")
    body = json.loads(result.stdout)
    if body.get("status") != "success":
        raise AssertionError("solar query rejected: " + str(body.get("errorType")))
    with NOTICE_LOCK:
        for kind in ENGINE_NOTICES:
            ENGINE_NOTICES[kind].update(body.get(kind, []))
    return body["data"]["result"]


def prometheus(expr, at=None):
    params = {"query": expr}
    if at is not None:
        params["time"] = str(at)
    return prometheus_request(PROXY, params)


def prometheus_range(expr, start, end, step):
    return prometheus_request(PROXY_RANGE, {"query": expr, "start": str(start),
                                           "end": str(end), "step": str(step)}, 185)


def fresh(now, timestamp, maximum=30):
    return timestamp is not None and -5 <= now - timestamp < maximum


def qualified(value, now, timestamp, success):
    return value if value is not None and fresh(now, timestamp) and success == 1 else None


def completed_device_day(samples, utc_marker):
    end = utc_marker - DEVICE_DAY_END_OFFSET_S
    start = end - DEVICE_DAY_WINDOW_S
    observed = [value for stamp, value in samples
                if start <= stamp <= end and value is not None]
    return max(observed) if observed else None


def last(samples, now):
    visible = [(stamp, value) for stamp, value in samples if now - 600 < stamp <= now]
    return max(visible)[1] if visible else None


def settings_value(now, values, timestamps, successes):
    timestamp = last(timestamps, now)
    if not fresh(now, timestamp, 600) or last(successes, now) != 1:
        return None
    return last(values, now)


class Profiles(unittest.TestCase):
    def test_canonical_and_cluster_manifest_parity(self):
        self.assertEqual(renderer.load_source(), renderer.render("cluster"))
        self.assertTrue(MANIFEST.is_file(), "companion manifest required for parity")
        self.assertEqual(renderer.manifest_dashboard(MANIFEST), renderer.render("cluster"))

    def test_gateway_requires_both_confirmed_identities(self):
        for kwargs in [{}, {"dashboard_uid": "fixture-dashboard"}, {"datasource_uid": "fixture-datasource"}]:
            with self.assertRaises(ValueError):
                renderer.render("gateway", **kwargs)
        with self.assertRaises(ValueError):
            renderer.render("cluster", dashboard_uid="override")
        with self.assertRaises(ValueError):
            renderer.render("gateway", "bad/uid", "fixture-datasource")

    def test_gateway_profile_diff_is_only_explicit_identity_and_cadence(self):
        cluster = renderer.render("cluster")
        # Test fixtures only, in memory; no guessed gateway export is written.
        gateway = renderer.render("gateway", "fixture-dashboard", "fixture-datasource")
        self.assertEqual(gateway["uid"], "fixture-dashboard")
        self.assertNotIn("id", gateway)
        gateway["uid"] = cluster["uid"]

        def normalize(value):
            if isinstance(value, dict):
                if value.get("type") == "prometheus" and "uid" in value:
                    self.assertEqual(value["uid"], "fixture-datasource")
                    value["uid"] = "prometheus"
                for child in value.values():
                    normalize(child)
            elif isinstance(value, list):
                for child in value:
                    normalize(child)
        normalize(gateway)
        self.assertEqual(gateway["templating"]["list"][0]["query"], "10")
        gateway["templating"] = cluster["templating"]
        self.assertEqual(gateway, cluster)

    def test_manifest_rejects_unknown_trailing_yaml(self):
        # The real reader must not silently discard trailing resources or an
        # unexpected layout; all fixtures remain in memory.
        with patch.object(Path, "read_text", return_value='spec:\n  json: |\n    {"panels": []}\n---\nkind: Other\n'):
            with self.assertRaises(json.JSONDecodeError):
                renderer.manifest_dashboard("fixture.yaml")
        with patch.object(Path, "read_text", return_value='spec:\n  json: {}\n'):
            with self.assertRaises(ValueError):
                renderer.manifest_dashboard("fixture.yaml")


class DashboardContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboard = renderer.render("cluster")
        cls.ps = panels(cls.dashboard)

    def test_explicit_query_policy_matrix_is_complete(self):
        expected = {}
        for policy, groups in [
            ("current", CURRENT_TARGETS), ("historical", HISTORICAL_TARGETS),
            ("aggregate", AGGREGATE_TARGETS), ("settings", SETTINGS_TARGETS),
            ("diagnostic", DIAGNOSTIC_TARGETS),
        ]:
            for pid, refs in groups.items():
                for ref in refs:
                    self.assertNotIn((pid, ref), expected)
                    expected[(pid, ref)] = policy
        for key in COVERAGE_TARGETS:
            self.assertNotIn(key, expected)
            expected[key] = "coverage"
        actual = {(pid, t["refId"]) for pid, panel in self.ps.items()
                  for t in panel["targets"]}
        self.assertEqual(actual, set(expected))
        self.assertEqual(len(actual), 157)
        self.assertEqual({p: list(expected.values()).count(p) for p in set(expected.values())},
                         {"current": 65, "historical": 35, "aggregate": 23,
                          "settings": 15, "diagnostic": 13, "coverage": 6})

    def test_current_settings_historical_and_aggregate_gate_contracts(self):
        for pid, refs in CURRENT_TARGETS.items():
            for ref in refs:
                q = target(self.dashboard, pid, ref)["expr"]
                with self.subTest(policy="current", panel=pid, ref=ref):
                    self.assertIn('host="ASTRONOMICAN"', q)
                    self.assertIn("telemetry_poll_timestamp_seconds", q)
                    self.assertRegex(q, r"telemetry_poll_success|fault_state_known|grid_total_power_read_success")
        for pid, refs in SETTINGS_TARGETS.items():
            for ref in refs:
                q = target(self.dashboard, pid, ref)["expr"]
                with self.subTest(policy="settings", panel=pid, ref=ref):
                    self.assertIn("last_over_time", q)
                    self.assertIn("settings_poll_timestamp_seconds", q)
                    self.assertIn("settings_poll_success", q)
                    self.assertIn("< 600", q)
        for policy, groups in [("historical", HISTORICAL_TARGETS),
                               ("aggregate", AGGREGATE_TARGETS)]:
            for pid, refs in groups.items():
                for ref in refs:
                    q = target(self.dashboard, pid, ref)["expr"]
                    with self.subTest(policy=policy, panel=pid, ref=ref):
                        self.assertIn('host="ASTRONOMICAN"', q)
                        self.assertFalse(any(metric in q for metric in DIAGNOSTIC_METRICS))
        for pid in HISTORICAL_TARGETS:
            self.assertFalse(self.ps[pid]["fieldConfig"]["defaults"]["custom"]["spanNulls"])
            self.assertIn("legacy/unqualified", self.ps[pid]["description"])

    def test_aggregate_coverage_is_explicit_and_never_fabricated(self):
        for pid, ref in COVERAGE_TARGETS | {(41, "I")}:
            q = target(self.dashboard, pid, ref)["expr"]
            with self.subTest(panel=pid, ref=ref):
                self.assertIn("sum_over_time(deye_inverter_telemetry_poll_success", q)
                self.assertIn("clamp_max", q)
                self.assertNotIn("or vector", q)
        self.assertIn("count_over_time(deye_inverter_telemetry_poll_timestamp_seconds",
                      target(self.dashboard, 41, "B")["expr"])

    def test_only_current_fault_requires_fault_known(self):
        current = target(self.dashboard, 29, "D")["expr"]
        historical = target(self.dashboard, 43, "A")["expr"]
        self.assertIn("fault_state_known", current)
        self.assertNotIn("telemetry_poll_success", current)
        self.assertIn("< 30", current)
        self.assertNotIn("fault_state_known", historical)
        self.assertIn("legacy raw collector output", self.ps[43]["description"])

    def test_grid_zero_is_not_filtered_out(self):
        for pid, ref in [(1, "E"), (28, "A")]:
            q = target(self.dashboard, pid, ref)["expr"]
            self.assertIn("grid_total_power_read_success", q)
            self.assertNotRegex(q, r"grid_total_power\{[^}]*\}\s*>\s*0")
        for pid, ref in [(14, "A"), (22, "C")]:
            q = target(self.dashboard, pid, ref)["expr"]
            self.assertNotIn("grid_total_power_read_success", q)
            self.assertNotRegex(q, r"grid_total_power\{[^}]*\}\s*>\s*0")
        self.assertEqual(qualified(0, 110, 100, 1), 0)

    def test_no_null_bridging_or_fallback_zeros(self):
        for p in self.ps.values():
            self.assertFalse(p.get("fieldConfig", {}).get("defaults", {}).get("custom", {}).get("spanNulls", False))
            for t in p["targets"]:
                self.assertNotIn("or vector(0)", t["expr"])
                self.assertNotIn("timestamp(", t["expr"])
                self.assertNotIn("first_over_time", t["expr"])
                self.assertNotIn("ts_of_", t["expr"])
        self.assertNotIn("return 0;", self.ps[1]["options"]["getOption"])

    def test_time_hot_boolean_observations_and_missing_stays_missing(self):
        for ref in "ABC":
            q = target(self.dashboard, 45, ref)["expr"]
            self.assertIn("sum_over_time", q)
            self.assertIn("> bool", q)
            self.assertNotIn("or vector", q)
        def observed_hot(values):
            known = [v for v in values if v is not None]
            return sum(v > 45 for v in known) if known else None
        self.assertEqual(observed_hot([40, 42]), 0)
        self.assertIsNone(observed_hot([None, None]))
        self.assertEqual(observed_hot([40, None, 46]), 1)

    def test_increases_have_corresponding_reset_guards(self):
        for p in self.ps.values():
            for t in p["targets"]:
                q = t["expr"]
                for name in re.findall(r"increase\((deye_inverter_\w+)\{", q):
                    self.assertIn("resets(" + name + "{", q)
        def guarded_change(values):
            if any(b < a for a, b in zip(values, values[1:])):
                return None
            return values[-1] - values[0]
        self.assertIsNone(guarded_change([3833, 0, 3833]))
        self.assertEqual(guarded_change([0, 0, 0]), 0)
        self.assertEqual(guarded_change([3833, 3833.1]), 3833.1 - 3833)

    def test_device_day_energy_is_five_raw_bars_plus_diagnostic_coverage(self):
        panel = self.ps[52]
        expected = [
            ("A", "daily_load_consumption", "Load consumed"),
            ("B", "daily_production", "PV produced"),
            ("C", "daily_energy_bought", "Grid imported"),
            ("D", "daily_battery_discharge", "Battery discharged"),
            ("E", "daily_battery_charge", "Battery charged"),
        ]
        self.assertEqual(panel["title"], "Device-Day Energy — Gross Flows (not load attribution)")
        self.assertEqual(panel["timeFrom"], "30d")
        self.assertIn("absolute dashboard range", panel["description"])
        self.assertIn("19:01:35", panel["description"])
        self.assertIn("immediately preceding Asia/Karachi device day", panel["description"])
        self.assertIn("partial observations", panel["description"])
        self.assertEqual([(t["refId"], t["legendFormat"]) for t in panel["targets"][:5]],
                         [(ref, legend) for ref, _, legend in expected])
        self.assertEqual(panel["targets"][5]["legendFormat"],
                         "Poll coverage (diagnostics era)")
        for t, (ref, metric, _) in zip(panel["targets"][:5], expected):
            q = t["expr"]
            self.assertEqual(q, "max_over_time(deye_inverter_" + metric +
                             '{host="ASTRONOMICAN"}[23h59m] offset 4h59m)')
            self.assertEqual(t["interval"], "1d")
            self.assertFalse(t["instant"])
            self.assertTrue(t["range"])
        coverage = target(self.dashboard, 52, "F")
        self.assertIn("sum_over_time(deye_inverter_telemetry_poll_success", coverage["expr"])
        self.assertIn("[23h59m] offset 4h59m", coverage["expr"])
        self.assertIn("/ 86340", coverage["expr"])
        self.assertEqual(coverage["interval"], "1d")
        custom = panel["fieldConfig"]["defaults"]["custom"]
        self.assertEqual(custom["drawStyle"], "bars")
        self.assertEqual(custom["fillOpacity"], 65)
        self.assertEqual(custom["barAlignment"], 0)
        self.assertEqual(custom["lineWidth"], 1)
        self.assertFalse(custom["spanNulls"])
        self.assertEqual(custom["stacking"]["mode"], "none")
        overrides = {x["matcher"]["options"]: {p["id"]: p["value"]
                     for p in x["properties"]} for x in panel["fieldConfig"]["overrides"]}
        self.assertEqual(set(overrides), set("ABCDEF"))
        for ref in "ABCDE":
            self.assertEqual(overrides[ref]["color"]["mode"], "fixed")
        self.assertEqual(overrides["F"]["custom.drawStyle"], "line")
        self.assertEqual(overrides["F"]["custom.axisPlacement"], "right")
        self.assertEqual(overrides["F"]["custom.fillOpacity"], 0)
        self.assertEqual(overrides["F"]["unit"], "percent")

    def test_device_day_window_is_complete_reset_safe_and_preserves_zero(self):
        marker = 2 * 86400
        end = marker - DEVICE_DAY_END_OFFSET_S
        start = end - DEVICE_DAY_WINDOW_S
        self.assertEqual(start % 86400, 19 * 3600 + 2 * 60)
        self.assertEqual(end % 86400, 19 * 3600 + 60)
        self.assertEqual(completed_device_day([
            (start - 1, 90), (start, 0), (start + 60, 4),
            (start + 120, 0), (end, 8), (end + 1, 70),
        ], marker), 8)
        self.assertEqual(completed_device_day([(start, 0), (end, 0)], marker), 0)
        self.assertIsNone(completed_device_day([(start, None), (end, None)], marker))

    def test_device_day_missing_and_zero_contract(self):
        marker = 2 * 86400
        end = marker - DEVICE_DAY_END_OFFSET_S
        start = end - DEVICE_DAY_WINDOW_S
        self.assertEqual(completed_device_day([(start, 0), (end, 0)], marker), 0)
        self.assertIsNone(completed_device_day([(start, None), (end, None)], marker))
        self.assertEqual(completed_device_day([(start, 0), (start + 60, None),
                                               (end, 7.5)], marker), 7.5)

    def test_no_hardcoded_health_cutoff_variable_or_branch(self):
        for profile, args in [("cluster", ()),
                              ("gateway", ("fixture-dashboard", "fixture-datasource"))]:
            rendered = renderer.render(profile, *args)
            self.assertNotIn("solar_health_start_s",
                             [v["name"] for v in rendered["templating"]["list"]])
            self.assertNotIn("solar_health_start_s", json.dumps(rendered))
            self.assertNotIn("vector(time()) <", json.dumps(rendered))

    def test_truthful_titles_and_labels(self):
        names = [p["title"] for p in self.ps.values()]
        names += [t.get("legendFormat", "") for p in self.ps.values() for t in p["targets"]]
        for name in names:
            self.assertNotRegex(name.lower(), r"round.trip|self.sufficiency|conversion efficiency|ups load|cycled today|hours.*empty|outages|uptime")
        self.assertEqual(len(self.ps[44]["targets"]), 2)
        self.assertIn("SOC Trend", self.ps[44]["title"])
        self.assertNotIn("30%", self.ps[47]["description"])
        expressions = " ".join(t["expr"] for p in self.ps.values() for t in p["targets"])
        for unsupported in ["daily_energy_sold", "total_energy_sold", "total_grid_production",
                            "generator_ready", "external_ct", "fault_1", "fault_2", "fault_3",
                            "fault_4", "fault_5", "fault_6"]:
            self.assertNotIn("deye_inverter_" + unsupported, expressions)

    def test_observed_disconnections_all_binary_sequences(self):
        q = target(self.dashboard, 41, "H")["expr"]
        self.assertIn("floor((changes(", q)
        self.assertIn("last_over_time", q)
        tested = 0
        for length in range(1, 11):
            for states in itertools.product([0, 1], repeat=length):
                changes = sum(a != b for a, b in zip(states, states[1:]))
                count = math.floor((changes + 1 - states[-1]) / 2)
                falling = sum(a == 1 and b == 0 for a, b in zip(states, states[1:]))
                self.assertEqual(count, falling)
                tested += 1
        self.assertEqual(tested, 2046)

    def test_earlier_day_uses_raw_offset_history(self):
        for ref in ["B", "D"]:
            q = target(self.dashboard, 49, ref)["expr"]
            self.assertIn("offset 1d", q)
            self.assertFalse(any(metric in q for metric in DIAGNOSTIC_METRICS))
            self.assertNotIn("time() - 86400", q)


class ProducerIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Execute only pure functions from the actual collector AST. Never import
        # its vendor/network/configuration paths or inspect runtime arguments.
        tree = ast.parse((ROOT / "ansible/scripts/monitoring/get-deye-data.py").read_text())
        cls.groups = {}
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                for group in ["telemetry", "settings"]:
                    if node.target.id == group.upper() + "_REGISTERS":
                        cls.groups[group] = [SimpleNamespace(name=entry.args[0].value) for entry in node.value.elts]
        cls.ns = {"GROUP_REGISTERS": cls.groups, "FAULT_REGISTER_NAMES": tuple("fault_" + str(i) for i in range(1, 7))}
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in
                    {"_groups_in", "compute_fault_active", "_build_poll_status"}]
        exec(compile(ast.Module(body=selected, type_ignores=[]), "<pure-collector-contract>", "exec"), cls.ns)

    def test_queries_match_actual_producer_fields(self):
        self.assertEqual([len(self.groups[g]) for g in ["telemetry", "settings"]], [66, 16])
        status = self.ns["_build_poll_status"]({}, "all", 1000)
        self.assertEqual(len(status), 10)
        available = {"deye_inverter_" + r.name for registers in self.groups.values() for r in registers}
        available |= {"deye_inverter_fault_active"} | {"deye_inverter_" + k for k in status}
        for p in panels(renderer.render()).values():
            for t in p["targets"]:
                self.assertTrue(set(re.findall(r"deye_inverter_[a-z0-9_]+", t["expr"])) <= available)

    def test_actual_contract_complete_partial_active_zero_and_empty(self):
        build, fault = self.ns["_build_poll_status"], self.ns["compute_fault_active"]
        full = {r.name: 0 for r in self.groups["telemetry"]}
        status = build(full, "telemetry", 1000)
        self.assertEqual(status["telemetry_poll_success"], 1)
        self.assertEqual(status["grid_total_power_read_success"], 1)
        self.assertEqual(status["fault_state_known"], 1)
        self.assertEqual(fault(full), 0)
        partial = {"fault_1": 0, "grid_total_power": 0}
        status = build(partial, "telemetry", 1000)
        self.assertEqual(status["telemetry_poll_success"], 0)
        self.assertEqual(status["fault_state_known"], 0)
        self.assertIsNone(fault(partial))
        self.assertEqual(qualified(0, 1010, status["telemetry_poll_timestamp_seconds"], status["grid_total_power_read_success"]), 0)
        partial["fault_1"] = 1
        status = build(partial, "telemetry", 1000)
        self.assertEqual(status["telemetry_poll_success"], 0)
        self.assertEqual(qualified(fault(partial), 1010, status["telemetry_poll_timestamp_seconds"], status["fault_state_known"]), 1)
        failed = build({}, "telemetry", 1000)
        self.assertEqual(failed["fault_state_known"], 0)
        self.assertEqual(failed["grid_total_power_read_success"], 0)
        self.assertEqual(failed["telemetry_poll_fields_received"], 0)

    def test_private_telegraf_only_opts_in_both_inputs(self):
        path = ROOT.parent / "Home-Ops-Internal/ansible/inventories/group_vars/monitoring/telegraf_config.yml"
        text = path.read_text()
        self.assertEqual(text.count("--emit-poll-status"), 2)
        for group, interval, timeout in [("telemetry", "5s", "4s"), ("settings", "300s", "30s")]:
            block = next(b for b in text.split("[[inputs.exec]]")[1:] if "--groups " + group in b)
            self.assertIn("--groups " + group + " --emit-poll-status", block)
            self.assertIn('interval = "' + interval + '"', block)
            self.assertIn('timeout = "' + timeout + '"', block)
        self.assertNotIn("expiration_interval =", text)
        self.assertNotIn("export_timestamp =", text)


class Freshness(unittest.TestCase):
    def test_fast_absent_partial_failed_stale_and_future(self):
        self.assertEqual(qualified(0, 110, 100, 1), 0)
        for value, now, ts, success in [(1, 110, None, 1), (1, 130, 100, 1), (1, 110, 100, 0),
                                         (None, 110, 100, 1), (1, 90, 100, 1)]:
            self.assertIsNone(qualified(value, now, ts, success))
        self.assertEqual(qualified(1, 95, 100, 1), 1)  # documented 5s tolerance

    def test_known_zero_invalidates_cached_fault_and_partial_active_survives(self):
        cached = 1
        known = 0
        self.assertIsNone(qualified(cached, 110, 100, known))
        self.assertEqual(qualified(1, 110, 100, 1), 1)  # independent of complete flag
        self.assertEqual(qualified(0, 110, 100, 1), 0)

    def test_settings_cache_expiry_poll_jitter_failure_and_age(self):
        cluster_values = [(0, 7), (307, 8)]
        cluster_ts = [(0, 0), (307, 307)]
        cluster_ok = [(0, 1), (307, 1)]
        gateway_values = [(t, 7) for t in range(0, 60, 10)] + [(t, 8) for t in range(310, 370, 10)]
        gateway_ts = [(t, 0) for t in range(0, 60, 10)] + [(t, 307) for t in range(310, 370, 10)]
        gateway_ok = [(t, 1) for t, _ in gateway_ts]
        for values, ts, ok in [(cluster_values, cluster_ts, cluster_ok), (gateway_values, gateway_ts, gateway_ok)]:
            with self.subTest(path="cluster" if values is cluster_values else "gateway"):
                self.assertEqual(settings_value(61, values, ts, ok), 7)
                self.assertEqual(settings_value(306, values, ts, ok), 7)  # >5m lookback, before delayed poll
                self.assertEqual(settings_value(370, values, ts, ok), 8)
                self.assertEqual(settings_value(906, values, ts, ok), 8)
                self.assertIsNone(settings_value(907, values, ts, ok))  # exactly 600s
        self.assertIsNone(settings_value(620, cluster_values, cluster_ts + [(614, 614)], cluster_ok + [(614, 0)]))
        self.assertIsNone(settings_value(100, [(0, 7)], [], []))  # no fabricated pre-health state


@unittest.skipUnless(shutil.which("node"), "Node required for real ECharts JavaScript execution")
class PowerFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = panels(renderer.render())[1]["options"]["getOption"]
        script = "const render = new Function('context', " + json.dumps(code) + ");\n" + r"""
function frame(ref, value, time=1010000) {
  return {refId:ref,fields:[{type:'time',name:'Time',values:[time]}, {type:'number',name:'Value',values:[value]}]};
}
function frames() {return [frame('A',200),frame('B',150),frame('C',400),frame('D',80),frame('E',100),frame('F',800),frame('G',1000),frame('H',1),frame('I',1)];}
function replace(fs,ref,value) {fs.find(f=>f.refId===ref).fields[1].values=[value];return fs;}
const cases={valid:frames(),zero:frames(),absent:[],partial:replace(frames(),'H',0),
 stale:replace(frames(),'G',900),future:replace(frames(),'G',1020),
 missing:frames().filter(f=>f.refId!=='E'),duplicate:frames().concat([frame('A',999)]),
 nonfinite:replace(frames(),'B',NaN),unknownGrid:replace(frames(),'I',0),missingHeartbeat:frames().filter(f=>f.refId!=='G'),
 trailingNull:frames(),error:frames(),loading:frames(),ambiguousFields:frames(),legacy:frames()};
for (const ref of ['A','B','C','D','E','F']) replace(cases.zero,ref,0);
cases.trailingNull.find(f=>f.refId==='A').fields[1].values=[200,null];
cases.ambiguousFields.find(f=>f.refId==='A').fields.push({type:'number',name:'Other',values:[99]});
const old=cases.legacy.find(f=>f.refId==='A').fields[1];old.values={length:1,get:i=>200};
for(const [name,fs] of Object.entries(cases)) {
 const out=render({panel:{data:{series:fs,state:name==='error'?'Error':name==='loading'?'Loading':'Done'}}});
 console.log(JSON.stringify({name,arrows:out.series[1].data.length,labels:out.series[2].data.map(n=>n.label.formatter)}));
}
"""
        result = subprocess.run(["node"], input=script, text=True, capture_output=True, timeout=15)
        if result.returncode:
            raise AssertionError("Power Flow JavaScript execution failed")
        cls.results = {x["name"]: x for x in map(json.loads, result.stdout.splitlines())}

    def test_valid_and_real_zero(self):
        self.assertEqual(self.results["valid"]["arrows"], 4)
        self.assertEqual(self.results["legacy"]["arrows"], 4)
        self.assertEqual(self.results["zero"]["arrows"], 0)
        self.assertNotIn("Unavailable", str(self.results["zero"]["labels"]))
        self.assertIn("0 W", str(self.results["zero"]["labels"]))
        self.assertIn("0 %", str(self.results["zero"]["labels"]))

    def test_unavailable_never_fabricates_measurement(self):
        for case in ["absent", "partial", "stale", "future", "missing", "duplicate", "nonfinite", "unknownGrid", "trailingNull", "error", "loading", "ambiguousFields", "missingHeartbeat"]:
            with self.subTest(case=case):
                self.assertEqual(self.results[case]["arrows"], 0)
                self.assertIn("Unavailable", str(self.results[case]["labels"]))
        self.assertNotIn("0 W", str(self.results["absent"]["labels"]))
        self.assertIn("SoC unavailable", str(self.results["absent"]["labels"]))


class LivePrometheus(unittest.TestCase):
    def setUp(self):
        if not LIVE:
            self.skipTest("opt-in --prometheus uses read-only authenticated kubectl GETs")

    def test_every_profile_query_parses_and_evaluates(self):
        queries = set()
        for profile, cadence in [("cluster", 5), ("gateway", 10)]:
            d = renderer.render(profile, *( ["fixture-dashboard", "fixture-datasource"] if profile == "gateway" else []))
            for p in panels(d).values():
                for t in p["targets"]:
                    queries.add(substitute(t["expr"], cadence))
        def check(q):
            try:
                prometheus(q)
            except Exception as exc:
                raise AssertionError("Solar PromQL failed: " + q + " (" + str(exc) + ")") from None
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(check, sorted(queries)))
        print("Validated", len(queries), "unique profile queries with read-only cluster GETs;",
              len(ENGINE_NOTICES["warnings"]), "warnings,", len(ENGINE_NOTICES["infos"]), "infos")
        self.assertEqual(ENGINE_NOTICES["warnings"], set(), "Prometheus warnings require investigation")

    def test_device_day_range_is_one_utc_marker_per_retained_complete_day(self):
        end = int(time.time()) // 86400 * 86400
        start = end - 30 * 86400
        dashboard = renderer.render("cluster")
        for ref in "ABCDE":
            rows = prometheus_range(substitute(target(dashboard, 52, ref)["expr"], 5),
                                    start, end, 86400)
            self.assertEqual(len(rows), 1, ref)
            stamps = [int(float(point[0])) for point in rows[0]["values"]]
            self.assertGreater(len(stamps), 20, ref)
            self.assertEqual(stamps, sorted(set(stamps)), ref)
            self.assertTrue(all(stamp % 86400 == 0 for stamp in stamps), ref)
            self.assertEqual(stamps[-1], end, ref)

    def test_boolean_zero_absence_and_reset_guard_in_engine(self):
        below = 'label_replace(vector(40), "host", "ASTRONOMICAN", "", "")'
        zero = prometheus('sum_over_time(((' + below + ') > bool 45)[5m:1m])')
        self.assertEqual(len(zero), 1)
        self.assertEqual(float(zero[0]["value"][1]), 0)
        self.assertEqual(sorted(zero[0]["metric"]), ["host"])
        absent = prometheus('sum_over_time((((' + below + ') and on(host) (vector(0) == 1)) > bool 45)[5m:1m])')
        self.assertEqual(absent, [])
        # Time-dependent synthetic counter goes 3833 -> 0 -> 3833 inside range.
        glitch = '(vector(3833) * ((time() % 300 < bool 120) + (time() % 300 >= bool 180)))'
        reset = prometheus('resets((' + glitch + ')[10m:10s])')
        self.assertGreater(float(reset[0]["value"][1]), 0)
        guarded = prometheus('increase((' + glitch + ')[10m:10s]) unless (resets((' + glitch + ')[10m:10s]) > 0)')
        self.assertEqual(guarded, [])

    def test_pre_diagnostic_history_is_raw_while_current_snapshots_fail_closed(self):
        # Discovery timestamp with legacy measurements but no poll diagnostics.
        at = 1792508756
        if not prometheus('deye_inverter_load_total_power{host="ASTRONOMICAN"}', at):
            self.skipTest("baseline measurement has aged out of retention")
        self.assertEqual(prometheus('deye_inverter_telemetry_poll_timestamp_seconds{host="ASTRONOMICAN"}', at), [])
        dashboard = renderer.render()
        for pid, ref in [(1, "A"), (1, "E"), (29, "D"), (64, "A")]:
            self.assertEqual(prometheus(substitute(target(dashboard, pid, ref)["expr"], 5), at), [])
        for pid, ref in [(35, "A"), (14, "A"), (43, "B"), (41, "H"), (45, "A")]:
            self.assertTrue(prometheus(substitute(target(dashboard, pid, ref)["expr"], 5), at),
                            (pid, ref))
        for ref in "ABCDE":
            self.assertEqual(len(prometheus(substitute(target(dashboard, 52, ref)["expr"], 5), at)), 1)
        for pid, ref in COVERAGE_TARGETS | {(41, "I")}:
            self.assertEqual(prometheus(substitute(target(dashboard, pid, ref)["expr"], 5), at), [])

    def test_real_zero_survives_filter_matching_and_unknown_does_not(self):
        host = lambda n: 'label_replace(vector(' + str(n) + '), "host", "ASTRONOMICAN", "", "")'
        measured_zero = host(0)
        self.assertEqual(float(prometheus(measured_zero + ' and on(host) (' + host(1) + ' == 1)')[0]["value"][1]), 0)
        self.assertEqual(prometheus(measured_zero + ' and on(host) (' + host(0) + ' == 1)'), [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--prometheus", action="store_true")
    options, remaining = parser.parse_known_args()
    LIVE = options.prometheus
    unittest.main(argv=[sys.argv[0], *remaining])
