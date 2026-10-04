#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-shot Deye hybrid inverter Modbus poller for Telegraf inputs.exec.

RENAMED .sh -> .py (2026-07-13)
-------------------------------
This file replaces the previous bash ``get-deye-data`` reader that shelled out
to ``deye-read`` (the deye-controller tool, which targets the THREE-PHASE high-
address Modbus map). This site is a SINGLE-PHASE 10 kW Deye hybrid
(SUN-10K-SG04LP1 class), whose telemetry lives in the LOW-address map. This
script reads that map directly via pysolarmanv5 and emits InfluxDB line protocol.

It first shipped deliberately keeping the old bash script's ``.sh`` name as a
drop-in; after running that way in production it was renamed to
``get-deye-data.py`` on 2026-07-13, and the deployed path is now
``/usr/local/bin/get-deye-data.py``.

It is invoked by Telegraf as two exec inputs -- a fast telemetry poll every 5s
and a slow settings/config-drift poll every 300s -- selected with ``--groups``::

    [[inputs.exec]]              # fast telemetry (every run)
      commands = ["/usr/local/bin/get-deye-data.py <LOGGER_IP> <LOGGER_SERIAL> --groups telemetry"]
      data_format = "influx"
      name_override = "deye_inverter"

    [[inputs.exec]]              # slow config-drift watch
      commands = ["/usr/local/bin/get-deye-data.py <LOGGER_IP> <LOGGER_SERIAL> --groups settings"]
      data_format = "influx"
      name_override = "deye_inverter"

Telegraf runs the command DIRECTLY (the shebang is honoured). Inside the
nixknight/telegraf container, ``/usr/bin/env python3`` resolves to the
``/opt/venv`` virtualenv, which pins pysolarmanv5==3.0.6 (pulling pyserial and
uModbus transitively) -- the only dependency this script needs.

OUTPUT CONTRACT (must match the existing InfluxDB schema)
---------------------------------------------------------
The previous script emitted, one metric per line and with NO trailing
timestamp::

    deye_inverter,host=<hostname> <field>=<value>

with every field a bare float (line-protocol default type). This script keeps
that contract byte-for-byte: same measurement (``deye_inverter``), same single
tag (``host`` = ``socket.gethostname()`` == the old ``$(hostname)``), all
fields emitted as floats (NO ``i`` suffix), no timestamp (Telegraf stamps it).
Matching the existing field TYPES is mandatory: InfluxDB rejects a float->int
type change on an existing field and dashboards would break.

SCOPE / SAFETY
--------------
Strictly READ-ONLY: only ``read_holding_registers`` (Modbus function code 3) is
ever called. No write/coil functions exist here. Deye hybrids expose their whole
data map as holding registers; reading them as input registers (FC4) makes the
inverter reject every request.

SINGLE-SHOT for Telegraf
------------------------
One poll cycle (batched group reads with per-register fallback), print, exit.
No interval loop, no long backoff: Telegraf exec has its own timeout (4s for the
5s telemetry input, 30s for the 300s settings input). Short socket timeout and
at most one reconnect so the collector is never hung.

POLL GROUPS (``--groups telemetry|settings|all``)
-------------------------------------------------
  * ``telemetry`` (default): the fast every-run set -- power, energy, battery/BMS
    telemetry, faults. Batches into 3 FC3 reads, well inside the 5s cadence.
  * ``settings``: slow-changing config/limits (work mode, energy pattern, zero-
    export power, charge/discharge current limits, SOC floors, generator config)
    polled every 5 min as a config-drift signal.
  * ``all``: both sets in one run (manual/probe use).
  ``--plan`` prints the batched read plan for the selection and exits WITHOUT any
  device I/O.

  * Total read failure  -> print nothing to stdout, exit non-zero (Telegraf
    records an error instead of ingesting partial garbage).
  * Single register failure -> omit that field, continue (line protocol cannot
    carry null/empty fields).

CONNECTION PARAMETERS (precedence: env > positional arg > default)
-----------------------------------------------------------------
  IP       : DEYE_LOGGER_IP      | argv[1] | <DATALOGGER_IP>
  SERIAL   : DEYE_LOGGER_SERIAL  | argv[2] | (site default carried forward)
  PORT     : DEYE_PORT           | 8899
  SLAVE_ID : DEYE_SLAVE_ID       | 1
  TIMEOUT  : DEYE_TIMEOUT        | 4   (seconds; LAN logger, matches the 4s telemetry exec timeout)
"""

from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
from dataclasses import dataclass

try:
    from pysolarmanv5 import (
        NoSocketAvailableError,
        PySolarmanV5,
        V5FrameError,
    )
except Exception as exc:  # noqa: BLE001 - dep missing/broken: fail closed for Telegraf
    sys.stderr.write(f"get-deye-data: pysolarmanv5 import failed: {exc}\n")
    sys.exit(3)


# ---------------------------------------------------------------------------
# Defaults / connection parameters
# ---------------------------------------------------------------------------
# Carried forward from the live Telegraf exec invocation so behaviour is
# unchanged if Telegraf ever calls the script without positional args.
DEFAULT_IP = "<DATALOGGER_IP>"
# Redacted placeholder for <DATALOGGER_SERIAL>. 0 is a non-functional sentinel so
# this file stays syntactically valid and secret-free; the real serial is always
# supplied at runtime via argv[2] / $DEYE_LOGGER_SERIAL (Telegraf passes it from
# the MONITORING_TELEGRAF_INVERTER_SN Ansible var). This default is never used in
# the deployed path.
DEFAULT_SERIAL = 0
DEFAULT_PORT = 8899
DEFAULT_SLAVE_ID = 1
DEFAULT_TIMEOUT = 4  # seconds; sized for the Telegraf exec timeouts (4s / 30s)

# Read-batching tuning. Deye loggers accept ~one TCP connection at a time and
# dislike chatter, so a few contiguous-ish spans beat one read per register.
MAX_READ_QUANTITY = 64  # max 16-bit registers in one FC3 read
MAX_READ_GAP = 8  # max unused registers bridged when merging nearby ranges

MEASUREMENT = "deye_inverter"


# ---------------------------------------------------------------------------
# Register definition + the single-phase Deye map (inlined; no local imports)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RegisterDef:
    """One decodable Deye holding-register metric (single-phase low-address map).

    Attributes:
        name: Stable field name emitted in line protocol.
        address: Starting Modbus holding-register address (decimal, FC3).
        words: Contiguous 16-bit registers (1 or 2). 2 => 32-bit LOW-word-first.
        signed: Interpret as two's-complement (16- or 32-bit per ``words``).
        scale: Multiplier applied to the raw integer (e.g. 0.1, 0.01).
        offset: Physical value subtracted AFTER scaling. Non-zero only for the
            temperature registers that use ``raw * 0.1 - 100`` (offset 100).
        unit: Engineering unit (display/provenance only).
        group: Logical grouping (provenance only).
        word_addrs: Explicit register addresses for a non-contiguous value (low
            word first). Empty => contiguous ``address .. address+words-1``. Used
            only for Deye's split 32-bit counters (total_energy_bought 78/80,
            total_generator_energy 92/95) whose two halves straddle an unrelated
            register; ``words`` must equal ``len(word_addrs)`` when set.
    """

    name: str
    address: int
    words: int
    signed: bool
    scale: float
    offset: float
    unit: str
    group: str
    word_addrs: tuple[int, ...] = ()


# Single-phase Deye hybrid (SUN-*K-SG04LP1 class). LOW-address generation.
# Transcribed from the live-verified local reference (deye_registers.py). Sign
# conventions for this site: battery_power + = discharge; grid_total_power
# + = import.
#
# TELEMETRY set: polled every run (``--groups telemetry``, the default).
TELEMETRY_REGISTERS: list[RegisterDef] = [
    # --- battery -----------------------------------------------------------
    RegisterDef("battery_soc", 184, 1, False, 1.0, 0.0, "%", "battery"),
    RegisterDef("battery_voltage", 183, 1, False, 0.01, 0.0, "V", "battery"),
    RegisterDef("battery_current", 191, 1, True, 0.01, 0.0, "A", "battery"),
    RegisterDef("battery_power", 190, 1, True, 1.0, 0.0, "W", "battery"),
    # --- battery / BMS block (312-323) -------------------------------------
    RegisterDef("battery_charge_voltage", 312, 1, False, 0.01, 0.0, "V", "battery"),
    RegisterDef("bms_discharge_voltage", 313, 1, False, 0.01, 0.0, "V", "battery"),
    # 314/315: BMS-REQUESTED charge/discharge current limits -- dynamic, scale 0.1 A.
    # ADJUDICATED 2026-07-18 against the ha-solarman map (which lists them as
    # "BMS Charging/Discharging Current", scale 1) on two live observations:
    #   (1) they do NOT track the actual pack current (318, signed, read -22 A
    #       while these sat at raw 300) -- so they are limits, not currents;
    #   (2) raw moved 60 -> 300 intra-day, i.e. 6.0 -> 30.0 A at scale 0.1
    #       (plausible dynamic BMS limit) vs 60 -> 300 A at scale 1 (absurd for
    #       this pack). 0.1-A units are the common BMS-protocol convention.
    # 320/321 ("Max" limits in the map) read 0 on this unit; 318 is the real
    # instantaneous current. Keep the original series names -- dashboards depend
    # on them and their semantics ("limit") are the correct reading.
    RegisterDef("battery_charge_current_limit", 314, 1, False, 0.1, 0.0, "A", "battery"),
    RegisterDef("battery_discharge_current_limit", 315, 1, False, 0.1, 0.0, "A", "battery"),
    RegisterDef("bms_soc", 316, 1, False, 1.0, 0.0, "%", "battery"),
    RegisterDef("bms_voltage", 317, 1, False, 0.01, 0.0, "V", "battery"),
    RegisterDef("bms_current", 318, 1, True, 1.0, 0.0, "A", "battery"),
    RegisterDef("bms_temperature", 319, 1, False, 0.1, 100.0, "degC", "battery"),
    RegisterDef("bms_max_charge_current", 320, 1, False, 1.0, 0.0, "A", "battery"),
    RegisterDef("bms_max_discharge_current", 321, 1, False, 1.0, 0.0, "A", "battery"),
    RegisterDef("battery_alarm", 322, 1, False, 1.0, 0.0, "", "battery"),
    RegisterDef("battery_fault", 323, 1, False, 1.0, 0.0, "", "battery"),
    # --- PV ----------------------------------------------------------------
    RegisterDef("pv1_power", 186, 1, False, 1.0, 0.0, "W", "pv"),
    RegisterDef("pv2_power", 187, 1, False, 1.0, 0.0, "W", "pv"),
    RegisterDef("pv1_voltage", 109, 1, False, 0.1, 0.0, "V", "pv"),
    RegisterDef("pv1_current", 110, 1, False, 0.1, 0.0, "A", "pv"),
    RegisterDef("pv2_voltage", 111, 1, False, 0.1, 0.0, "V", "pv"),
    RegisterDef("pv2_current", 112, 1, False, 0.1, 0.0, "A", "pv"),
    # --- grid --------------------------------------------------------------
    RegisterDef("grid_total_power", 169, 1, True, 1.0, 0.0, "W", "grid"),
    RegisterDef("grid_frequency", 79, 1, False, 0.01, 0.0, "Hz", "grid"),
    RegisterDef("grid_voltage", 150, 1, False, 0.1, 0.0, "V", "grid"),
    RegisterDef("grid_current", 160, 1, False, 0.01, 0.0, "A", "grid"),
    RegisterDef("grid_connected_status", 194, 1, False, 1.0, 0.0, "", "grid"),
    # External CT meter (s16 W). Reads 0 until the external CT is wired/configured.
    RegisterDef("external_ct1_power", 170, 1, True, 1.0, 0.0, "W", "grid"),
    RegisterDef("external_ct2_power", 171, 1, True, 1.0, 0.0, "W", "grid"),
    RegisterDef("external_ct_power", 172, 1, True, 1.0, 0.0, "W", "grid"),
    # --- load --------------------------------------------------------------
    RegisterDef("load_total_power", 178, 1, False, 1.0, 0.0, "W", "load"),
    RegisterDef("load_frequency", 192, 1, False, 0.01, 0.0, "Hz", "load"),
    RegisterDef("load_voltage", 157, 1, False, 0.1, 0.0, "V", "load"),
    # Generator port power (u16 W). Reads 0 until the gen port is used/configured.
    RegisterDef("generator_power", 166, 1, False, 1.0, 0.0, "W", "generator"),
    # --- temperatures (physical = raw*0.1 - 100) ---------------------------
    RegisterDef("battery_temperature", 182, 1, False, 0.1, 100.0, "degC", "temp"),
    RegisterDef("dc_temperature", 90, 1, True, 0.1, 100.0, "degC", "temp"),
    RegisterDef("ac_temperature", 91, 1, True, 0.1, 100.0, "degC", "temp"),
    # --- inverter ----------------------------------------------------------
    RegisterDef("inverter_total_power", 175, 1, True, 1.0, 0.0, "W", "inverter"),
    # --- energy (cumulative; 2-word values are LOW-word-first) -------------
    RegisterDef("daily_production", 108, 1, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("total_production", 96, 2, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("daily_battery_charge", 70, 1, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("daily_battery_discharge", 71, 1, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("total_battery_charge", 72, 2, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("total_battery_discharge", 74, 2, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("daily_energy_bought", 76, 1, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("daily_energy_sold", 77, 1, False, 0.1, 0.0, "kWh", "energy"),
    # 32-bit SPLIT counter: low word @78, high word @80 (79 is grid_frequency,
    # NOT the high half). Read as a u32 pair to end the 6553.5 kWh u16 rollover.
    RegisterDef("total_energy_bought", 78, 2, False, 0.1, 0.0, "kWh", "energy", word_addrs=(78, 80)),
    RegisterDef("total_energy_sold", 81, 2, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("total_grid_production", 63, 2, True, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("daily_load_consumption", 84, 1, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("total_load_consumption", 85, 2, False, 0.1, 0.0, "kWh", "energy"),
    # --- energy roll-ups: month / year (added 2026-06-07) ------------------
    RegisterDef("month_pv_production", 65, 1, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("month_load_consumption", 66, 1, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("month_grid_energy", 67, 1, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("year_pv_production", 68, 2, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("year_load_consumption", 87, 2, False, 0.1, 0.0, "kWh", "energy"),
    RegisterDef("year_grid_energy", 98, 2, False, 0.1, 0.0, "kWh", "energy"),
    # --- generator energy (reads 0 until the gen port is used) -------------
    RegisterDef("daily_generator_energy", 62, 1, False, 0.1, 0.0, "kWh", "energy"),
    # 32-bit SPLIT counter: low word @92, high word @95 (93/94 interposed).
    RegisterDef("total_generator_energy", 92, 2, False, 0.1, 0.0, "kWh", "energy", word_addrs=(92, 95)),
    # --- status / diagnostics ----------------------------------------------
    RegisterDef("running_status", 59, 1, False, 1.0, 0.0, "", "status"),
    RegisterDef("fault_1", 101, 1, False, 1.0, 0.0, "", "status"),
    RegisterDef("fault_2", 102, 1, False, 1.0, 0.0, "", "status"),
    RegisterDef("fault_3", 103, 1, False, 1.0, 0.0, "", "status"),
    RegisterDef("fault_4", 104, 1, False, 1.0, 0.0, "", "status"),
    RegisterDef("fault_5", 105, 1, False, 1.0, 0.0, "", "status"),
    RegisterDef("fault_6", 106, 1, False, 1.0, 0.0, "", "status"),
]

# SETTINGS set: slow-changing R/W config + limits, polled on a long interval
# (``--groups settings``) as a config-drift signal, NOT every run. Read-only:
# these are writable holding registers but this collector only ever reads them.
SETTINGS_REGISTERS: list[RegisterDef] = [
    # --- work mode / energy policy -----------------------------------------
    RegisterDef("energy_pattern", 243, 1, False, 1.0, 0.0, "", "settings"),
    RegisterDef("work_mode", 244, 1, False, 1.0, 0.0, "", "settings"),
    RegisterDef("zero_export_power", 206, 1, False, 1.0, 0.0, "W", "settings"),
    # --- battery charge/discharge current limits + SOC floors --------------
    RegisterDef("set_max_charge_current", 210, 1, False, 1.0, 0.0, "A", "settings"),
    RegisterDef("set_max_discharge_current", 211, 1, False, 1.0, 0.0, "A", "settings"),
    RegisterDef("battery_shutdown_soc", 217, 1, False, 1.0, 0.0, "%", "settings"),
    RegisterDef("battery_low_soc", 219, 1, False, 1.0, 0.0, "%", "settings"),
    # --- per-program SOC floors (Program 1..6 = regs 268..273) -------------
    RegisterDef("program1_soc", 268, 1, False, 1.0, 0.0, "%", "settings"),
    RegisterDef("program2_soc", 269, 1, False, 1.0, 0.0, "%", "settings"),
    RegisterDef("program3_soc", 270, 1, False, 1.0, 0.0, "%", "settings"),
    RegisterDef("program4_soc", 271, 1, False, 1.0, 0.0, "%", "settings"),
    RegisterDef("program5_soc", 272, 1, False, 1.0, 0.0, "%", "settings"),
    RegisterDef("program6_soc", 273, 1, False, 1.0, 0.0, "%", "settings"),
    # --- generator config --------------------------------------------------
    RegisterDef("generator_voltage", 181, 1, False, 0.1, 0.0, "V", "generator"),
    RegisterDef("generator_ready", 195, 1, False, 1.0, 0.0, "", "generator"),
    RegisterDef("generator_frequency", 196, 1, False, 0.01, 0.0, "Hz", "generator"),
]

# Logical poll groups -> register defs. The ``--groups`` CLI flag selects which
# set(s) a run emits; Telegraf drives telemetry (5s) and settings (300s) as two
# separate exec inputs.
GROUP_REGISTERS: dict[str, list[RegisterDef]] = {
    "telemetry": TELEMETRY_REGISTERS,
    "settings": SETTINGS_REGISTERS,
}

# Fault registers feeding the derived ``fault_active`` field (see main()).
FAULT_REGISTER_NAMES: tuple[str, ...] = (
    "fault_1",
    "fault_2",
    "fault_3",
    "fault_4",
    "fault_5",
    "fault_6",
)
DERIVED_FAULT_ACTIVE = "fault_active"


# ---------------------------------------------------------------------------
# Decode (Deye rules: low-word-first 32-bit; temperature offset)
# ---------------------------------------------------------------------------
def _precision_for_scale(scale: float) -> int:
    """Decimal places implied by ``scale`` (0.1->1, 0.01->2, 1.0->0, cap 4)."""
    if scale >= 1.0:
        return 0
    ndigits = 0
    s = scale
    while s < 1.0 and ndigits < 4:
        s *= 10.0
        ndigits += 1
    return ndigits


def decode(regs: list[int], rd: RegisterDef) -> float | int:
    """Decode raw 16-bit register words into a physical value.

    Rules:
      * 1 word unsigned  -> regs[0]
      * 1 word signed    -> 16-bit two's-complement of regs[0]
      * 2 words unsigned -> LOW-word-first 32-bit: regs[0] | (regs[1] << 16)
      * 2 words signed   -> as above, then 32-bit two's-complement
    then ``phys = value * scale - offset``. Integral values with no fractional
    transform are returned as int; otherwise rounded to the scale's precision.
    """
    if len(regs) < rd.words:
        raise ValueError(f"decode({rd.name!r}): need {rd.words} word(s), got {len(regs)}")

    if rd.words == 1:
        raw = regs[0] & 0xFFFF
        if rd.signed:
            value = struct.unpack(">h", struct.pack(">H", raw))[0]
        else:
            value = raw
    else:  # 2 words: Deye 32-bit LOW-word-first
        low = regs[0] & 0xFFFF
        high = regs[1] & 0xFFFF
        value = low | (high << 16)
        if rd.signed and value >= 2**31:
            value -= 2**32

    phys = value * rd.scale - rd.offset

    if rd.scale == 1.0 and rd.offset == 0.0:
        return int(phys)
    return round(phys, _precision_for_scale(rd.scale))


# ---------------------------------------------------------------------------
# Read planning: batch contiguous-ish registers into few FC3 reads
# ---------------------------------------------------------------------------
def _word_addresses(rd: RegisterDef) -> tuple[int, ...]:
    """Ordered register addresses backing this metric (low word first).

    Contiguous metrics (``word_addrs`` empty) occupy ``address ..
    address+words-1``. Split metrics carry an explicit non-contiguous
    ``word_addrs`` (total_energy_bought 78/80, total_generator_energy 92/95):
    Deye stores those 32-bit counters as a low word and a high word separated by
    an unrelated register, so a contiguous read would fold that register into the
    high half and corrupt the value.
    """
    if rd.word_addrs:
        return rd.word_addrs
    return tuple(rd.address + i for i in range(rd.words))


def plan_reads(
    regs: list[RegisterDef],
    max_quantity: int = MAX_READ_QUANTITY,
    max_gap: int = MAX_READ_GAP,
) -> list[tuple[int, int, list[RegisterDef]]]:
    """Group defs into a few ``(start, quantity, members)`` batched reads.

    Walking defs in address order, a def joins the current group when the gap
    from the group's covered end is ``<= max_gap`` AND the merged span stays
    ``<= max_quantity``; otherwise a new group starts. A def's covered extent is
    ``min(word_addrs) .. max(word_addrs)``, so a split counter (78/80, 92/95)
    correctly pulls its high word into the batch. The word at address ``a`` lives
    at batch index ``a - start`` (see poll_once / _word_addresses).
    """
    if not regs:
        return []

    ordered = sorted(regs, key=lambda r: (r.address, r.words))

    def _extent(rd: RegisterDef) -> tuple[int, int]:
        wa = _word_addresses(rd)
        return min(wa), max(wa) + 1  # (inclusive start, exclusive end)

    groups: list[tuple[int, int, list[RegisterDef]]] = []
    g_start, g_end = _extent(ordered[0])
    g_members = [ordered[0]]

    for rd in ordered[1:]:
        rd_start, rd_end = _extent(rd)
        gap = rd_start - g_end
        span = rd_end - g_start
        if gap <= max_gap and span <= max_quantity:
            g_members.append(rd)
            g_end = max(g_end, rd_end)
        else:
            groups.append((g_start, g_end - g_start, list(g_members)))
            g_start, g_end = rd_start, rd_end
            g_members = [rd]

    groups.append((g_start, g_end - g_start, list(g_members)))
    return groups


# ---------------------------------------------------------------------------
# Modbus interaction (read-only, holding registers / FC3 only)
# ---------------------------------------------------------------------------
# Failures that mean "read failed but retrying is reasonable": frame/CRC
# corruption, no-socket, timeouts, transient socket errors. socket.timeout
# subclasses OSError; TimeoutError is also caught explicitly.
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    V5FrameError,
    NoSocketAvailableError,
    TimeoutError,
    OSError,
)


@dataclass
class Config:
    ip: str
    serial: int
    port: int = DEFAULT_PORT
    slave_id: int = DEFAULT_SLAVE_ID
    timeout: int = DEFAULT_TIMEOUT


def _resolve_config(args: argparse.Namespace) -> Config:
    """Resolve config with precedence env > positional arg > default.

    Telegraf invokes ``get-deye-data.py <IP> <SERIAL> --groups <g>``; the IP and
    serial land in ``args.ip`` / ``args.serial`` (both optional, env can supply).
    """
    ip = os.getenv("DEYE_LOGGER_IP") or args.ip or DEFAULT_IP

    serial_raw = os.getenv("DEYE_LOGGER_SERIAL") or args.serial or ""
    if serial_raw:
        try:
            serial = int(str(serial_raw).strip())
        except ValueError:
            sys.stderr.write("get-deye-data: serial must be an integer\n")
            sys.exit(2)
    else:
        serial = DEFAULT_SERIAL

    def _int_env(name: str, default: int) -> int:
        val = os.getenv(name)
        if not val:
            return default
        try:
            return int(val.strip())
        except ValueError:
            return default

    return Config(
        ip=ip,
        serial=serial,
        port=_int_env("DEYE_PORT", DEFAULT_PORT),
        slave_id=_int_env("DEYE_SLAVE_ID", DEFAULT_SLAVE_ID),
        timeout=_int_env("DEYE_TIMEOUT", DEFAULT_TIMEOUT),
    )


def _connect(cfg: Config) -> PySolarmanV5:
    """Construct the (read-only) PySolarmanV5 client.

    pysolarmanv5 3.0.x opens the socket eagerly in ``__init__`` (it calls
    ``_socket_setup``), so construction itself can raise the transient/connect
    errors. The caller wraps this accordingly. Only the stable 3.0.x kwargs are
    used (port, mb_slave_id, socket_timeout; verified against the pinned
    3.0.6); no newer features.
    """
    return PySolarmanV5(
        cfg.ip,
        cfg.serial,
        port=cfg.port,
        mb_slave_id=cfg.slave_id,
        socket_timeout=cfg.timeout,
    )


def _read_single(modbus: PySolarmanV5, rd: RegisterDef) -> int | float | None:
    """Read+decode one register on its own (per-register fallback). None on fail.

    Reads the whole low..high span and picks out just this metric's words, so a
    split 32-bit counter (78/80, 92/95) never folds in the register between its
    halves. For a contiguous def this is exactly ``read(address, words)``.
    """
    try:
        word_addrs = _word_addresses(rd)
        lo = min(word_addrs)
        span = max(word_addrs) - lo + 1
        raw = modbus.read_holding_registers(lo, span)
        words = [raw[a - lo] for a in word_addrs]
        return decode(words, rd)
    except Exception:  # noqa: BLE001 - one bad register must not kill the cycle
        return None


def poll_once(modbus: PySolarmanV5, registers: list[RegisterDef]) -> dict[str, float | int]:
    """One batched FC3 poll. Returns {name: value} for every register that read OK.

    Each group is read in one FC3 request; on a group failure we fall back to
    reading its members individually so a single bad address cannot blank the
    whole group. Failed registers are simply absent from the returned dict.
    """
    values: dict[str, float | int] = {}
    for start, quantity, members in plan_reads(registers):
        try:
            batch = modbus.read_holding_registers(start, quantity)
        except Exception:  # noqa: BLE001 - degrade this group to per-register reads
            batch = None

        if batch is not None:
            for rd in members:
                try:
                    words = [batch[a - start] for a in _word_addresses(rd)]
                    values[rd.name] = decode(words, rd)
                except Exception:  # noqa: BLE001 - skip undecodable, keep the rest
                    pass
        else:
            for rd in members:
                v = _read_single(modbus, rd)
                if v is not None:
                    values[rd.name] = v
    return values


# ---------------------------------------------------------------------------
# Output: InfluxDB line protocol (matches the previous script's contract)
# ---------------------------------------------------------------------------
def _fmt_field(value: float | int) -> str:
    """Format a numeric field value as a FLOAT line-protocol token.

    The previous bash script emitted every field as a bare number (line-protocol
    float). We must preserve that type, so even integral metrics (battery_soc,
    *_power) are rendered with a decimal point and NO ``i`` suffix. Trailing
    zeros are trimmed for readability while keeping a float form (e.g. 720 ->
    "720.0", 53.25 -> "53.25").
    """
    f = float(value)
    if f == int(f):
        return f"{int(f)}.0"
    return repr(round(f, 4))


def compute_fault_active(values: dict[str, float | int]) -> float | None:
    """Derive ``fault_active`` from the fault_N registers actually read.

    Returns 1.0 if any present ``fault_N`` value is > 0, else 0.0. Returns
    ``None`` when NONE of the fault registers were read this cycle, so we never
    emit a misleading "no fault" 0.0 when those reads simply failed.
    """
    present = [values[name] for name in FAULT_REGISTER_NAMES if name in values]
    if not present:
        return None
    return 1.0 if any(v > 0 for v in present) else 0.0


def build_lines(
    values: dict[str, float | int], host: str, registers: list[RegisterDef]
) -> list[str]:
    """Build one line-protocol line per metric, preserving register order.

    ``registers``-driven metrics are emitted first, in list order; any derived
    field (e.g. ``fault_active``) present in ``values`` is appended after them.

    Format (no timestamp; Telegraf stamps it)::

        deye_inverter,host=<host> <field>=<value>
    """
    host_tag = host.replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")
    lines: list[str] = []
    for rd in registers:
        if rd.name in values:
            lines.append(f"{MEASUREMENT},host={host_tag} {rd.name}={_fmt_field(values[rd.name])}")
    # Derived fields (not backed by a register) are appended after the
    # register-driven lines, preserving the existing line-protocol contract.
    if DERIVED_FAULT_ACTIVE in values:
        lines.append(
            f"{MEASUREMENT},host={host_tag} "
            f"{DERIVED_FAULT_ACTIVE}={_fmt_field(values[DERIVED_FAULT_ACTIVE])}"
        )
    return lines


# ---------------------------------------------------------------------------
# CLI: argument parsing, group selection, read-plan preview
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI args, preserving the legacy ``<IP> <SERIAL>`` positional form."""
    parser = argparse.ArgumentParser(
        prog="get-deye-data.py",
        description="Single-shot read-only Deye hybrid poller -> InfluxDB line protocol.",
    )
    parser.add_argument("ip", nargs="?", default=None, help="logger IP (or $DEYE_LOGGER_IP)")
    parser.add_argument(
        "serial", nargs="?", default=None, help="logger serial (or $DEYE_LOGGER_SERIAL)"
    )
    parser.add_argument(
        "--groups",
        choices=("telemetry", "settings", "all"),
        default="telemetry",
        help="register set to poll (default: telemetry, the fast every-run set)",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="print the batched FC3 read plan for --groups and exit (no device I/O)",
    )
    return parser.parse_args(argv[1:])


def _groups_in(selection: str) -> list[str]:
    """Logical poll groups covered by a ``--groups`` selection, in poll order."""
    if selection == "all":
        return ["telemetry", "settings"]
    return [selection]


def _select_registers(selection: str) -> list[RegisterDef]:
    """Flatten the register defs for a ``--groups`` selection, preserving order."""
    regs: list[RegisterDef] = []
    for gname in _groups_in(selection):
        regs.extend(GROUP_REGISTERS[gname])
    return regs


def _print_plan(selection: str) -> None:
    """Print the batched FC3 read plan per logical group. No device I/O.

    Each group is planned independently because Telegraf runs the telemetry and
    settings polls as separate exec inputs (5s vs 300s), so the per-run read
    cost is what matters.
    """
    for gname in _groups_in(selection):
        regs = GROUP_REGISTERS[gname]
        plan = plan_reads(regs)
        words_read = sum(qty for _, qty, _ in plan)
        print(
            f"plan: group={gname} registers={len(regs)} "
            f"reads={len(plan)} words_read={words_read}"
        )
        for i, (start, qty, members) in enumerate(plan):
            names = ",".join(m.name for m in members)
            print(f"  read[{i}] start={start} qty={qty} members={len(members)}: {names}")


# ---------------------------------------------------------------------------
# Main: single-shot for Telegraf
# ---------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    # --plan is a pure preview: emit the read plan and exit without any socket.
    if args.plan:
        _print_plan(args.groups)
        return 0

    cfg = _resolve_config(args)
    registers = _select_registers(args.groups)
    host = socket.gethostname()  # matches the old script's $(hostname)

    modbus: PySolarmanV5 | None = None
    values: dict[str, float | int] = {}

    # At most one reconnect attempt; never loop/backoff (Telegraf has its own
    # timeout). Eager-connect errors surface from _connect() in 3.0.4.
    for attempt in (1, 2):
        try:
            modbus = _connect(cfg)
        except TRANSIENT_ERRORS as exc:
            if attempt == 2:
                sys.stderr.write(f"get-deye-data: connect failed: {type(exc).__name__}: {exc}\n")
                return 2
            continue  # one retry

        try:
            values = poll_once(modbus, registers)
        finally:
            try:
                modbus.disconnect()
            except Exception:  # noqa: BLE001 - disconnect must never raise upward
                pass

        if values:
            break  # got data; done

        # Empty result => connection likely stale (logger dropped us / cloud
        # grabbed the single slot). Try exactly once more, then give up.
        modbus = None

    if not values:
        # Total read failure: print NOTHING to stdout so Telegraf records an
        # error rather than ingesting partial/garbage data.
        sys.stderr.write("get-deye-data: no registers read; emitting nothing\n")
        return 1

    # Derived field: fault_active. Only injected when at least one fault_N
    # register was actually read (compute_fault_active returns None otherwise),
    # so a failed fault read never masquerades as "no fault".
    fault_active = compute_fault_active(values)
    if fault_active is not None:
        values[DERIVED_FAULT_ACTIVE] = fault_active

    sys.stdout.write("\n".join(build_lines(values, host, registers)) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
