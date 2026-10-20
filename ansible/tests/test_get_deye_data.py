#!/usr/bin/env python3
"""Offline collector tests using a stubbed vendor module and in-memory reads."""

from __future__ import annotations

import builtins
import contextlib
import importlib.util
import io
import itertools
import os
from pathlib import Path
import runpy
import sys
import types
import unittest
from dataclasses import dataclass, field
from typing import Any, cast
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts/monitoring/get-deye-data.py"
POLL_TIMESTAMP = 1_800_000_000.25
# Synthetic marker, never a real device identifier or authentication value.
PRIVATE_EXCEPTION = "FIXTURE_EXCEPTION_DETAIL_MUST_NOT_ESCAPE"


class StubFrameError(Exception):
    """Represent an expected vendor frame failure without importing the vendor."""


class StubSocketError(Exception):
    """Represent an expected vendor connection failure."""


class ForbiddenClient:
    """Prevent every test from constructing a real network client."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("vendor client construction is forbidden in offline tests")


VENDOR = types.ModuleType("pysolarmanv5")
VENDOR.PySolarmanV5 = ForbiddenClient  # type: ignore[attr-defined]
VENDOR.V5FrameError = StubFrameError  # type: ignore[attr-defined]
VENDOR.NoSocketAvailableError = StubSocketError  # type: ignore[attr-defined]
SPEC = importlib.util.spec_from_file_location("_offline_get_deye_data", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load collector test fixture")
COLLECTOR = importlib.util.module_from_spec(SPEC)
with (
    patch.dict(sys.modules, {SPEC.name: COLLECTOR, "pysolarmanv5": VENDOR}),
    patch("socket.create_connection", side_effect=AssertionError("network forbidden")),
):
    SPEC.loader.exec_module(COLLECTOR)

REAL_POLL_ONCE = COLLECTOR.poll_once
TELEMETRY_STATUS = {
    "telemetry_poll_timestamp_seconds",
    "telemetry_poll_fields_received",
    "telemetry_poll_fields_expected",
    "telemetry_poll_success",
    "fault_state_known",
    "grid_total_power_read_success",
}
SETTINGS_STATUS = {
    "settings_poll_timestamp_seconds",
    "settings_poll_fields_received",
    "settings_poll_fields_expected",
    "settings_poll_success",
}


def decoded_values(selection: str) -> dict[str, int]:
    """Return successful synthetic zero readings for the selected definitions."""
    return {rd.name: 0 for rd in COLLECTOR._select_registers(selection)}


def parse_fields(output: str) -> dict[str, float]:
    """Parse fixture-host output, rejecting integers and native timestamps."""
    fields: dict[str, float] = {}
    for line in output.splitlines():
        measurement, assignment = line.split(" ")
        if measurement != "deye_inverter,host=collector-test":
            raise AssertionError("unexpected measurement or host tag")
        name, token = assignment.split("=")
        if "." not in token or token.endswith("i"):
            raise AssertionError("collector output must retain float field types")
        if name in fields:
            raise AssertionError("duplicate output field")
        fields[name] = float(token)
    return fields


@dataclass
class MemoryClient:
    """Serve only in-memory Modbus words, with controllable read failures."""

    words: dict[int, int] = field(default_factory=dict)
    failures: set[tuple[int, int]] = field(default_factory=set)
    responses: dict[tuple[int, int], object] = field(default_factory=dict)
    calls: list[tuple[int, int]] = field(default_factory=list)
    disconnect_calls: int = 0

    def read_holding_registers(self, start: int, quantity: int) -> list[int]:
        """Return synthetic words or deliberately malformed fixture responses."""
        key = (start, quantity)
        self.calls.append(key)
        if key in self.failures:
            raise TimeoutError(PRIVATE_EXCEPTION)
        if key in self.responses:
            return cast(list[int], self.responses[key])
        return [
            self.words.get(address, 0) for address in range(start, start + quantity)
        ]

    def disconnect(self) -> None:
        """Record cleanup without operating on sockets."""
        self.disconnect_calls += 1


class FaultTests(unittest.TestCase):
    """Require complete zero evidence for health, but not for observed faults."""

    def test_all_64_zero_presence_combinations(self) -> None:
        names = COLLECTOR.FAULT_REGISTER_NAMES
        for mask in itertools.product((False, True), repeat=len(names)):
            values = {name: 0 for name, present in zip(names, mask) if present}
            with self.subTest(mask=mask):
                expected = 0.0 if all(mask) else None
                self.assertEqual(COLLECTOR.compute_fault_active(values), expected)

    def test_positive_fault_in_every_nonempty_presence_combination(self) -> None:
        names = COLLECTOR.FAULT_REGISTER_NAMES
        for mask in itertools.product((False, True), repeat=len(names)):
            present = [name for name, included in zip(names, mask) if included]
            for active in present:
                values = {name: float(name == active) for name in present}
                with self.subTest(mask=mask, active=active):
                    self.assertEqual(COLLECTOR.compute_fault_active(values), 1.0)
                    status = COLLECTOR._build_poll_status(
                        values, "telemetry", POLL_TIMESTAMP
                    )
                    self.assertEqual(status["fault_state_known"], 1.0)

    def test_unrelated_fields_do_not_establish_fault_health(self) -> None:
        self.assertIsNone(COLLECTOR.compute_fault_active({"grid_total_power": 0}))

    def test_invalid_negative_word_does_not_establish_health(self) -> None:
        values = dict.fromkeys(COLLECTOR.FAULT_REGISTER_NAMES, 0)
        values["fault_1"] = -1
        self.assertIsNone(COLLECTOR.compute_fault_active(values))


class ReadTests(unittest.TestCase):
    """Exercise real collector read/decoding logic against memory-only clients."""

    def setUp(self) -> None:
        self.faults: list[Any] = [
            rd
            for rd in COLLECTOR.TELEMETRY_REGISTERS
            if rd.name in COLLECTOR.FAULT_REGISTER_NAMES
        ]

    def test_complete_zero_fault_batch(self) -> None:
        client = MemoryClient()
        values = COLLECTOR.poll_once(client, self.faults)
        self.assertEqual(values, dict.fromkeys(COLLECTOR.FAULT_REGISTER_NAMES, 0))
        self.assertEqual(client.calls, [(101, 6)])
        self.assertEqual(COLLECTOR.compute_fault_active(values), 0.0)

    def test_short_batch_omits_missing_words_without_fabricating_health(self) -> None:
        client = MemoryClient(responses={(101, 6): [0]})
        values = COLLECTOR.poll_once(client, self.faults)
        self.assertEqual(values, {"fault_1": 0})
        self.assertIsNone(COLLECTOR.compute_fault_active(values))
        self.assertEqual(client.calls, [(101, 6)])

    def test_malformed_batch_word_does_not_blank_other_words(self) -> None:
        client = MemoryClient(responses={(101, 6): [0, "invalid", 0, 0, 0, 0]})
        values = COLLECTOR.poll_once(client, self.faults)
        self.assertEqual(len(values), 5)
        self.assertNotIn("fault_2", values)
        self.assertIsNone(COLLECTOR.compute_fault_active(values))

    def test_non_indexable_batch_omits_all_values(self) -> None:
        client = MemoryClient(responses={(101, 6): object()})
        self.assertEqual(COLLECTOR.poll_once(client, self.faults), {})

    def test_batch_failure_recovers_all_words_via_fallback(self) -> None:
        client = MemoryClient(failures={(101, 6)})
        values = COLLECTOR.poll_once(client, self.faults)
        self.assertEqual(len(values), 6)
        self.assertEqual(len(client.calls), 7)
        self.assertEqual(COLLECTOR.compute_fault_active(values), 0.0)

    def test_partial_fallback_keeps_observed_fault(self) -> None:
        client = MemoryClient(
            words={101: 8}, failures={(101, 6), (102, 1)}, responses={(103, 1): []}
        )
        values = COLLECTOR.poll_once(client, self.faults)
        self.assertEqual(len(values), 4)
        self.assertEqual(COLLECTOR.compute_fault_active(values), 1.0)

    def test_none_batch_uses_fallback(self) -> None:
        client = MemoryClient(responses={(101, 6): None})
        self.assertEqual(len(COLLECTOR.poll_once(client, self.faults)), 6)
        self.assertEqual(len(client.calls), 7)

    def test_split_counter_ignores_intervening_frequency_word(self) -> None:
        rd = next(
            rd
            for rd in COLLECTOR.TELEMETRY_REGISTERS
            if rd.name == "total_energy_bought"
        )
        client = MemoryClient(words={78: 1, 79: 65535, 80: 2})
        self.assertEqual(COLLECTOR._read_single(client, rd), 13107.3)
        self.assertEqual(client.calls, [(78, 3)])
        self.assertEqual(COLLECTOR.poll_once(client, [rd]), {rd.name: 13107.3})

    def test_current_read_plans_are_unchanged(self) -> None:
        self.assertEqual(COLLECTOR.plan_reads([]), [])
        for group, expected_fields, expected_reads in (
            ("telemetry", 66, 3),
            ("settings", 16, 5),
        ):
            with self.subTest(group=group):
                registers = COLLECTOR.GROUP_REGISTERS[group]
                plan = COLLECTOR.plan_reads(registers)
                self.assertEqual(len(registers), expected_fields)
                self.assertEqual(len(plan), expected_reads)
                self.assertTrue(all(quantity <= 64 for _, quantity, _ in plan))
                members = [rd for _, _, batch in plan for rd in batch]
                self.assertCountEqual(members, registers)
                for start, quantity, batch in plan:
                    for rd in batch:
                        self.assertTrue(
                            all(
                                start <= address < start + quantity
                                for address in COLLECTOR._word_addresses(rd)
                            )
                        )

    def test_decoding_preserves_signed_scaling_and_word_order(self) -> None:
        cases = (
            ((1, True, 0.01, 0.0), [65535], -0.01),
            ((2, False, 1.0, 0.0), [0x5678, 0x1234], 0x12345678),
            ((2, True, 1.0, 0.0), [65535, 65535], -1),
            ((1, False, 0.1, 100.0), [1250], 25.0),
        )
        for (words, signed, scale, offset), raw, expected in cases:
            with self.subTest(words=words, signed=signed, scale=scale):
                rd = COLLECTOR.RegisterDef(
                    "fixture", 0, words, signed, scale, offset, "", "fixture"
                )
                self.assertEqual(COLLECTOR.decode(raw, rd), expected)
        with self.assertRaises(ValueError):
            COLLECTOR.decode([], self.faults[0])
        self.assertEqual(COLLECTOR._precision_for_scale(1.0), 0)
        self.assertEqual(COLLECTOR._precision_for_scale(0.00001), 4)


class StatusTests(unittest.TestCase):
    """Check the exact additive schema independently of read/CLI machinery."""

    def test_complete_counts_exclude_derived_and_diagnostic_fields(self) -> None:
        values = decoded_values("telemetry")
        values.update({"fault_active": 0, "telemetry_poll_fields_received": 999})
        status = COLLECTOR._build_poll_status(values, "telemetry", POLL_TIMESTAMP)
        self.assertEqual(set(status), TELEMETRY_STATUS)
        self.assertEqual(status["telemetry_poll_fields_received"], 66)
        self.assertEqual(status["telemetry_poll_fields_expected"], 66)
        self.assertEqual(status["telemetry_poll_success"], 1.0)
        self.assertEqual(status["fault_state_known"], 1.0)
        self.assertEqual(status["grid_total_power_read_success"], 1.0)

    def test_settings_status_never_contains_fast_indicators(self) -> None:
        status = COLLECTOR._build_poll_status(
            decoded_values("settings"), "settings", POLL_TIMESTAMP
        )
        self.assertEqual(set(status), SETTINGS_STATUS)
        self.assertEqual(status["settings_poll_fields_received"], 16)
        self.assertEqual(status["settings_poll_fields_expected"], 16)
        self.assertEqual(status["settings_poll_success"], 1.0)

    def test_all_group_counts_are_partitioned(self) -> None:
        values = {"battery_soc": 0, "energy_pattern": 0, "work_mode": 0}
        status = COLLECTOR._build_poll_status(values, "all", POLL_TIMESTAMP)
        self.assertEqual(set(status), TELEMETRY_STATUS | SETTINGS_STATUS)
        self.assertEqual(status["telemetry_poll_fields_received"], 1)
        self.assertEqual(status["settings_poll_fields_received"], 2)
        self.assertEqual(status["telemetry_poll_fields_expected"], 66)
        self.assertEqual(status["settings_poll_fields_expected"], 16)
        self.assertEqual(status["telemetry_poll_success"], 0.0)
        self.assertEqual(status["settings_poll_success"], 0.0)
        self.assertEqual(status["fault_state_known"], 0.0)
        self.assertEqual(status["grid_total_power_read_success"], 0.0)

    def test_missing_grid_differs_from_genuine_zero(self) -> None:
        for values, success in (({}, 0.0), ({"grid_total_power": 0}, 1.0)):
            with self.subTest(present=bool(values)):
                status = COLLECTOR._build_poll_status(
                    values, "telemetry", POLL_TIMESTAMP
                )
                self.assertEqual(status["grid_total_power_read_success"], success)

    def test_output_preserves_order_escaping_float_types_and_whitelist(self) -> None:
        rd = next(
            rd for rd in COLLECTOR.TELEMETRY_REGISTERS if rd.name == "grid_total_power"
        )
        values = {"grid_total_power": 0, "fault_active": 1, "unsupported": 123}
        values.update(COLLECTOR._build_poll_status(values, "telemetry", POLL_TIMESTAMP))
        lines = COLLECTOR.build_lines(values, "fixture host,role=test", [rd])
        prefix = r"deye_inverter,host=fixture\ host\,role\=test "
        self.assertEqual(lines[0], prefix + "grid_total_power=0.0")
        self.assertEqual(lines[1], prefix + "fault_active=1.0")
        self.assertTrue(all(line.startswith(prefix) for line in lines))
        self.assertFalse(any("unsupported" in line for line in lines))
        self.assertEqual(len(lines), 8)
        for value, expected in ((720, "720.0"), (53.25, "53.25"), (0, "0.0")):
            self.assertEqual(COLLECTOR._fmt_field(value), expected)


class MainTests(unittest.TestCase):
    """Exercise invocation behavior with vendor construction and reads mocked."""

    client: Mock
    connect: Mock
    poll: Mock
    clock: Mock

    def setUp(self) -> None:
        self.client = Mock(spec=["disconnect"])
        self.connect = self.enterContext(
            patch.object(COLLECTOR, "_connect", return_value=self.client)
        )
        self.poll = self.enterContext(
            patch.object(
                COLLECTOR, "poll_once", return_value=decoded_values("telemetry")
            )
        )
        self.clock = self.enterContext(
            patch.object(COLLECTOR.time, "time", return_value=POLL_TIMESTAMP)
        )
        self.enterContext(
            patch.object(COLLECTOR.socket, "gethostname", return_value="collector-test")
        )
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def invoke(self, *arguments: str, boundary: bool = False) -> tuple[int, str, str]:
        """Capture one fixture invocation without converting unexpected failures."""
        stdout, stderr = io.StringIO(), io.StringIO()
        entrypoint = COLLECTOR._run_cli if boundary else COLLECTOR.main
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = entrypoint(["get-deye-data.py", *arguments])
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_legacy_complete_output_shape_is_unchanged(self) -> None:
        code, stdout, stderr = self.invoke()
        fields = parse_fields(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            set(fields), set(decoded_values("telemetry")) | {"fault_active"}
        )
        self.assertEqual(fields["fault_active"], 0.0)
        self.clock.assert_not_called()
        self.client.disconnect.assert_called_once_with()

    def test_opt_in_complete_telemetry(self) -> None:
        code, stdout, stderr = self.invoke("--emit-poll-status")
        fields = parse_fields(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            set(fields),
            set(decoded_values("telemetry")) | {"fault_active"} | TELEMETRY_STATUS,
        )
        self.assertEqual(fields["telemetry_poll_timestamp_seconds"], POLL_TIMESTAMP)
        self.assertEqual(fields["telemetry_poll_fields_received"], 66.0)
        self.assertEqual(fields["telemetry_poll_success"], 1.0)
        self.assertEqual(fields["grid_total_power"], 0.0)
        self.assertEqual(fields["grid_total_power_read_success"], 1.0)
        self.clock.assert_called_once_with()

    def test_recovered_batch_failure_still_reports_complete_poll(self) -> None:
        client = MemoryClient(failures={(59, 54)})
        self.connect.return_value = client
        self.poll.side_effect = REAL_POLL_ONCE
        code, stdout, stderr = self.invoke("--emit-poll-status")
        fields = parse_fields(stdout)
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(fields["telemetry_poll_fields_received"], 66.0)
        self.assertEqual(fields["telemetry_poll_success"], 1.0)
        self.assertEqual(fields["fault_state_known"], 1.0)
        self.assertEqual(fields["fault_active"], 0.0)
        self.assertEqual(fields["grid_total_power"], 0.0)
        self.assertEqual(fields["grid_total_power_read_success"], 1.0)
        self.assertEqual(client.disconnect_calls, 1)
        self.assertGreater(len(client.calls), 3)

    def test_failed_batches_and_fallbacks_emit_only_failure_status(self) -> None:
        registers = COLLECTOR.TELEMETRY_REGISTERS
        failures = {
            (start, quantity) for start, quantity, _ in COLLECTOR.plan_reads(registers)
        }
        for rd in registers:
            addresses = COLLECTOR._word_addresses(rd)
            failures.add((min(addresses), max(addresses) - min(addresses) + 1))
        client = MemoryClient(failures=failures)
        self.connect.return_value = client
        self.poll.side_effect = REAL_POLL_ONCE
        code, stdout, stderr = self.invoke("--emit-poll-status")
        fields = parse_fields(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(set(fields), TELEMETRY_STATUS)
        self.assertEqual(fields["telemetry_poll_fields_received"], 0.0)
        self.assertEqual(fields["telemetry_poll_success"], 0.0)
        self.assertEqual(fields["fault_state_known"], 0.0)
        self.assertEqual(fields["grid_total_power_read_success"], 0.0)
        self.assertNotIn(PRIVATE_EXCEPTION, stderr)
        self.assertEqual(client.disconnect_calls, 2)
        self.assertEqual(self.connect.call_count, 2)

    def test_partial_zero_fault_does_not_emit_healthy_in_legacy_mode(self) -> None:
        self.poll.return_value = {"fault_1": 0, "grid_total_power": 0}
        code, stdout, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(
            parse_fields(stdout), {"fault_1": 0.0, "grid_total_power": 0.0}
        )
        self.assertEqual(self.connect.call_count, 1)

    def test_partial_status_marks_unknown_fault_but_valid_grid_zero(self) -> None:
        self.poll.return_value = {"fault_1": 0, "grid_total_power": 0}
        code, stdout, stderr = self.invoke("--emit-poll-status")
        fields = parse_fields(stdout)
        self.assertEqual((code, stderr), (0, ""))
        self.assertNotIn("fault_active", fields)
        self.assertEqual(fields["fault_state_known"], 0.0)
        self.assertEqual(fields["telemetry_poll_fields_received"], 2.0)
        self.assertEqual(fields["telemetry_poll_success"], 0.0)
        self.assertEqual(fields["grid_total_power_read_success"], 1.0)

    def test_partial_positive_fault_is_known_active(self) -> None:
        self.poll.return_value = {"fault_6": 8}
        code, stdout, _ = self.invoke("--emit-poll-status")
        fields = parse_fields(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(fields["fault_active"], 1.0)
        self.assertEqual(fields["fault_state_known"], 1.0)
        self.assertEqual(fields["grid_total_power_read_success"], 0.0)
        self.assertEqual(fields["telemetry_poll_fields_received"], 1.0)

    def test_cached_previous_healthy_or_active_is_invalidated(self) -> None:
        for previous in (0, 1):
            with self.subTest(previous=previous):
                self.poll.return_value = decoded_values("telemetry")
                self.poll.return_value["fault_1"] = previous
                self.clock.return_value = POLL_TIMESTAMP
                _, stdout, _ = self.invoke("--emit-poll-status")
                cache = parse_fields(stdout)
                self.poll.return_value = {"battery_soc": 50, "fault_2": 0}
                self.clock.return_value = POLL_TIMESTAMP + 5
                _, stdout, _ = self.invoke("--emit-poll-status")
                cache.update(parse_fields(stdout))
                self.assertEqual(cache["fault_active"], float(previous))
                self.assertEqual(cache["fault_state_known"], 0.0)
                self.assertEqual(cache["grid_total_power_read_success"], 0.0)
                self.assertEqual(
                    cache["telemetry_poll_timestamp_seconds"], POLL_TIMESTAMP + 5
                )

    def test_total_read_failure_preserves_legacy_nonzero_empty_stdout(self) -> None:
        self.poll.return_value = {}
        code, stdout, stderr = self.invoke()
        self.assertEqual((code, stdout), (1, ""))
        self.assertIn("emitting nothing", stderr)
        self.assertEqual(self.connect.call_count, 2)
        self.assertEqual(self.poll.call_count, 2)
        self.assertEqual(self.client.disconnect.call_count, 2)

    def test_total_read_failure_status_is_accepted_by_default_exec(self) -> None:
        self.poll.return_value = {}
        code, stdout, stderr = self.invoke("--emit-poll-status")
        fields = parse_fields(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(set(fields), TELEMETRY_STATUS)
        self.assertEqual(fields["telemetry_poll_fields_received"], 0.0)
        self.assertEqual(fields["telemetry_poll_fields_expected"], 66.0)
        self.assertEqual(fields["telemetry_poll_success"], 0.0)
        self.assertEqual(fields["fault_state_known"], 0.0)
        self.assertEqual(fields["grid_total_power_read_success"], 0.0)
        self.assertIn("poll status only", stderr)
        self.assertEqual(self.poll.call_count, 2)

    def test_handled_connect_failures_legacy_and_status_modes(self) -> None:
        for error in (StubFrameError, StubSocketError, TimeoutError, OSError):
            for status_mode in (False, True):
                with self.subTest(error=error.__name__, status_mode=status_mode):
                    self.connect.reset_mock()
                    self.connect.side_effect = error(PRIVATE_EXCEPTION)
                    arguments = ["--emit-poll-status"] if status_mode else []
                    code, stdout, stderr = self.invoke(*arguments)
                    self.assertEqual(self.connect.call_count, 2)
                    self.assertNotIn(PRIVATE_EXCEPTION, stderr)
                    self.assertEqual(stderr, "get-deye-data: connect failed\n")
                    self.assertEqual(code, 0 if status_mode else 2)
                    if status_mode:
                        fields = parse_fields(stdout)
                        self.assertEqual(set(fields), TELEMETRY_STATUS)
                        self.assertEqual(fields["fault_state_known"], 0.0)
                        self.assertEqual(fields["grid_total_power_read_success"], 0.0)
                        self.assertEqual(fields["telemetry_poll_fields_received"], 0.0)
                    else:
                        self.assertEqual(stdout, "")
        self.poll.assert_not_called()

    def test_timestamp_precedes_first_connection_and_is_not_reset_on_retry(
        self,
    ) -> None:
        events: list[str] = []

        def timestamp() -> float:
            events.append("timestamp")
            return POLL_TIMESTAMP

        def connect(config: object) -> Mock:
            events.append("connect")
            if events.count("connect") == 1:
                raise TimeoutError(PRIVATE_EXCEPTION)
            return self.client

        self.clock.side_effect = timestamp
        self.connect.side_effect = connect
        code, stdout, _ = self.invoke("--emit-poll-status")
        self.assertEqual(code, 0)
        self.assertEqual(events, ["timestamp", "connect", "connect"])
        self.assertEqual(
            parse_fields(stdout)["telemetry_poll_timestamp_seconds"], POLL_TIMESTAMP
        )

    def test_empty_first_attempt_does_not_contaminate_retried_counts(self) -> None:
        self.poll.side_effect = [{}, {"grid_total_power": 0}]
        code, stdout, _ = self.invoke("--emit-poll-status")
        fields = parse_fields(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(fields["telemetry_poll_fields_received"], 1.0)
        self.assertEqual(fields["grid_total_power_read_success"], 1.0)
        self.assertEqual(self.poll.call_count, 2)
        self.clock.assert_called_once_with()

    def test_connect_failure_after_empty_read_keeps_legacy_connection_exit(
        self,
    ) -> None:
        self.poll.return_value = {}
        self.connect.side_effect = [self.client, TimeoutError(PRIVATE_EXCEPTION)]
        code, stdout, stderr = self.invoke()
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(stderr, "get-deye-data: connect failed\n")
        self.client.disconnect.assert_called_once_with()

    def test_settings_complete_partial_and_failed_never_refresh_telemetry(self) -> None:
        for values, received in (
            (decoded_values("settings"), 16),
            ({"work_mode": 0}, 1),
            ({}, 0),
        ):
            with self.subTest(received=received):
                self.poll.return_value = values
                code, stdout, _ = self.invoke(
                    "--groups", "settings", "--emit-poll-status"
                )
                fields = parse_fields(stdout)
                self.assertEqual(code, 0)
                self.assertEqual(set(fields), set(values) | SETTINGS_STATUS)
                self.assertEqual(
                    fields["settings_poll_fields_received"], float(received)
                )
                self.assertEqual(fields["settings_poll_fields_expected"], 16.0)
                self.assertEqual(fields["settings_poll_success"], float(received == 16))
                self.assertEqual(
                    fields["settings_poll_timestamp_seconds"], POLL_TIMESTAMP
                )
                self.assertTrue(TELEMETRY_STATUS.isdisjoint(fields))
                self.assertNotIn("fault_active", fields)

    def test_settings_legacy_shape_is_unchanged(self) -> None:
        self.poll.return_value = decoded_values("settings")
        code, stdout, _ = self.invoke("--groups", "settings")
        self.assertEqual(code, 0)
        self.assertEqual(set(parse_fields(stdout)), set(decoded_values("settings")))
        self.clock.assert_not_called()

    def test_all_groups_complete_and_independently_incomplete(self) -> None:
        for selection, telemetry_received, settings_received in (
            ("all", 66, 16),
            ("telemetry", 66, 0),
            ("settings", 0, 16),
        ):
            with self.subTest(selection=selection):
                self.poll.return_value = decoded_values(selection)
                code, stdout, _ = self.invoke("--groups", "all", "--emit-poll-status")
                fields = parse_fields(stdout)
                self.assertEqual(code, 0)
                self.assertEqual(
                    fields["telemetry_poll_fields_received"], float(telemetry_received)
                )
                self.assertEqual(
                    fields["settings_poll_fields_received"], float(settings_received)
                )
                self.assertEqual(
                    fields["telemetry_poll_success"], float(telemetry_received == 66)
                )
                self.assertEqual(
                    fields["settings_poll_success"], float(settings_received == 16)
                )
                self.assertEqual(
                    fields["settings_poll_timestamp_seconds"],
                    fields["telemetry_poll_timestamp_seconds"],
                )

    def test_all_groups_total_failure_emits_only_both_status_families(self) -> None:
        self.poll.return_value = {}
        code, stdout, _ = self.invoke("--groups", "all", "--emit-poll-status")
        fields = parse_fields(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(set(fields), TELEMETRY_STATUS | SETTINGS_STATUS)
        self.assertEqual(fields["settings_poll_success"], 0.0)
        self.assertEqual(fields["telemetry_poll_success"], 0.0)

    def test_plan_is_network_and_configuration_free_and_does_not_echo_arguments(
        self,
    ) -> None:
        with patch.object(COLLECTOR, "_resolve_config") as resolve:
            code, stdout, stderr = self.invoke(
                "fixture.invalid",
                PRIVATE_EXCEPTION,
                "--groups",
                "all",
                "--emit-poll-status",
                "--plan",
            )
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("group=telemetry registers=66 reads=3", stdout)
        self.assertIn("group=settings registers=16 reads=5", stdout)
        self.assertNotIn("fixture.invalid", stdout)
        self.assertNotIn(PRIVATE_EXCEPTION, stdout)
        resolve.assert_not_called()
        self.connect.assert_not_called()
        self.poll.assert_not_called()
        self.clock.assert_not_called()

    def test_invalid_configuration_remains_error_without_echoing_value(self) -> None:
        code, stdout, stderr = self.invoke(
            "fixture.invalid", PRIVATE_EXCEPTION, "--emit-poll-status"
        )
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(stderr, "get-deye-data: serial must be an integer\n")
        self.assertNotIn(PRIVATE_EXCEPTION, stderr)
        self.connect.assert_not_called()

    def test_argument_errors_do_not_echo_invalid_values(self) -> None:
        for arguments in (
            ("--groups", PRIVATE_EXCEPTION),
            ("--unknown=" + PRIVATE_EXCEPTION,),
        ):
            with self.subTest(arguments_kind=arguments[0].split("=")[0]):
                code, stdout, stderr = self.invoke(*arguments)
                self.assertEqual((code, stdout), (2, ""))
                self.assertNotIn(PRIVATE_EXCEPTION, stderr)
                self.assertIn("invalid arguments", stderr)
        self.connect.assert_not_called()

    def test_unexpected_connection_bug_is_not_treated_as_handled_failure(self) -> None:
        self.connect.side_effect = RuntimeError(PRIVATE_EXCEPTION)
        with self.assertRaises(RuntimeError):
            self.invoke("--emit-poll-status")
        self.assertEqual(self.connect.call_count, 1)
        self.poll.assert_not_called()

    def test_unexpected_poll_bug_propagates_after_disconnect(self) -> None:
        self.poll.side_effect = RuntimeError(PRIVATE_EXCEPTION)
        with self.assertRaises(RuntimeError):
            self.invoke("--emit-poll-status")
        self.client.disconnect.assert_called_once_with()
        self.assertEqual(self.connect.call_count, 1)

    def test_cli_crash_is_nonzero_sanitized_and_emits_no_diagnostics(self) -> None:
        self.poll.side_effect = RuntimeError(PRIVATE_EXCEPTION)
        code, stdout, stderr = self.invoke("--emit-poll-status", boundary=True)
        self.assertEqual((code, stdout), (1, ""))
        self.assertEqual(stderr, "get-deye-data: unexpected collector error\n")
        self.assertNotIn(PRIVATE_EXCEPTION, stderr)

    def test_disconnect_failure_does_not_change_successful_output(self) -> None:
        self.client.disconnect.side_effect = OSError(PRIVATE_EXCEPTION)
        code, stdout, stderr = self.invoke("--emit-poll-status")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(parse_fields(stdout)["telemetry_poll_success"], 1.0)


class ConfigurationTests(unittest.TestCase):
    """Cover existing configuration precedence without reading real environment values."""

    def test_configuration_precedence_and_invalid_integer_fallbacks(self) -> None:
        args = COLLECTOR._parse_args(["get-deye-data.py", "argument.invalid", "0"])
        environment = {
            "DEYE_LOGGER_IP": "environment.invalid",
            "DEYE_LOGGER_SERIAL": "0",
            "DEYE_PORT": "not-an-integer",
            "DEYE_SLAVE_ID": "2",
            "DEYE_TIMEOUT": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = COLLECTOR._resolve_config(args)
        self.assertEqual(config.ip, "environment.invalid")
        self.assertEqual(config.serial, 0)
        self.assertEqual(config.port, COLLECTOR.DEFAULT_PORT)
        self.assertEqual(config.slave_id, 2)
        self.assertEqual(config.timeout, COLLECTOR.DEFAULT_TIMEOUT)
        with patch.dict(os.environ, {}, clear=True):
            config = COLLECTOR._resolve_config(args)
            defaults = COLLECTOR._resolve_config(
                COLLECTOR._parse_args(["get-deye-data.py"])
            )
        self.assertEqual(config.ip, "argument.invalid")
        self.assertEqual(defaults.ip, COLLECTOR.DEFAULT_IP)
        self.assertEqual(defaults.serial, COLLECTOR.DEFAULT_SERIAL)

    def test_connect_uses_existing_vendor_arguments_only(self) -> None:
        config = COLLECTOR.Config("fixture.invalid", 0)
        with patch.object(COLLECTOR, "PySolarmanV5") as constructor:
            result = COLLECTOR._connect(config)
        constructor.assert_called_once_with(
            config.ip,
            config.serial,
            port=config.port,
            mb_slave_id=config.slave_id,
            socket_timeout=config.timeout,
        )
        self.assertIs(result, constructor.return_value)

    def test_import_failure_is_nonzero_and_does_not_echo_exception(self) -> None:
        original_import = builtins.__import__

        def importing(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "pysolarmanv5":
                raise RuntimeError(PRIVATE_EXCEPTION)
            return original_import(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location("_deye_import_failure", SCRIPT)
        if spec is None or spec.loader is None:
            self.fail("unable to prepare import failure fixture")
        module = importlib.util.module_from_spec(spec)
        stderr = io.StringIO()
        with (
            patch.dict(sys.modules, {spec.name: module}),
            patch("builtins.__import__", side_effect=importing),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            spec.loader.exec_module(module)
        self.assertEqual(raised.exception.code, 3)
        self.assertEqual(
            stderr.getvalue(), "get-deye-data: pysolarmanv5 import failed\n"
        )
        self.assertNotIn(PRIVATE_EXCEPTION, stderr.getvalue())

    def test_real_cli_entrypoint_plan_never_constructs_vendor_client(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.dict(sys.modules, {"pysolarmanv5": VENDOR}),
            patch.object(sys, "argv", ["get-deye-data.py", "--plan"]),
            patch(
                "socket.create_connection",
                side_effect=AssertionError("network forbidden"),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            runpy.run_path(str(SCRIPT), run_name="__main__")
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("registers=66 reads=3", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
