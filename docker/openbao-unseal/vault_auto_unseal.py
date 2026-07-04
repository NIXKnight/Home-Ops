#!/usr/bin/env python3
"""Resilient auto-unseal daemon for OpenBao (Vault-compatible /v1/sys/* API).

Runs as a long-lived sidecar next to a single OpenBao node. Each poll it reads
the node's seal status and reconciles toward "initialized and unsealed":

* uninitialized + ``INIT_ENABLED`` -> initialize, escrow the init response to
  Postgres (pgcrypto), then unseal this node;
* uninitialized + not ``INIT_ENABLED`` -> wait for the primary node to init;
* initialized + sealed -> read the escrowed init response and unseal;
* initialized + unsealed -> idle until the next poll.

Escrow model (preserved from the original one-shot helper): the full init JSON
(unseal key shares + root token) is stored in a single ``vault_init_data`` row,
optionally encrypted at rest with ``pgp_sym_encrypt``.

Security: unseal key shares, the root token, the database password and the
encryption key are NEVER logged, echoed, or written to non-secret sinks -- not
even at DEBUG level. Only high-level status (counts, booleans, endpoints) is
emitted.

Dependencies: ``requests`` and ``psycopg2`` (binary) only.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg2
import requests

# --------------------------------------------------------------------------- #
# Configuration (integration contract -- consumed by the image + Ansible wiring)
# --------------------------------------------------------------------------- #


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable (1/true/yes/on -> True)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Parse an integer environment variable, falling back to ``default``."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "Invalid integer for %s=%r; using default %d", name, raw, default
        )
        return default


DEBUG: bool = _env_bool("DEBUG", False)

# OpenBao endpoint. Prefer OPENBAO_ADDR; accept legacy VAULT_ADDR as a fallback.
OPENBAO_ADDR: str = (
    os.getenv("OPENBAO_ADDR") or os.getenv("VAULT_ADDR") or "https://127.0.0.1:8200"
).rstrip("/")

# Sidecar talks to localhost, where a wildcard cert CN will not match 127.0.0.1,
# so TLS verification defaults to off.
OPENBAO_TLS_SKIP_VERIFY: bool = _env_bool("OPENBAO_TLS_SKIP_VERIFY", True)

# Only the primary node should own initialization; secondaries set this false.
INIT_ENABLED: bool = _env_bool("INIT_ENABLED", True)

# Escrow at-rest encryption toggle.
ENCRYPT_VAULT_INIT_DATA: bool = _env_bool("ENCRYPT_VAULT_INIT_DATA", True)

SECRET_SHARES: int = _env_int("SECRET_SHARES", 5)
SECRET_THRESHOLD: int = _env_int("SECRET_THRESHOLD", 3)
UNSEAL_POLL_INTERVAL: int = _env_int("UNSEAL_POLL_INTERVAL", 10)

# Postgres escrow backend.
DB_CONFIG: dict[str, Any] = {
    "host": os.getenv("POSTGRES_HOST", "127.0.0.1"),
    "port": _env_int("POSTGRES_PORT", 5432),
    "dbname": os.getenv("POSTGRES_DB", "openbao"),
    "user": os.getenv("POSTGRES_USER", "openbao"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}
POSTGRES_ENCRYPTION_KEY: str = os.getenv("POSTGRES_ENCRYPTION_KEY", "")

# Internal tunables (not part of the public contract).
REQUEST_TIMEOUT: int = 10
DB_CONNECT_TIMEOUT: int = 5
DB_CONNECT_RETRIES: int = 3
DB_RETRY_BASE_DELAY: float = 1.0
MAX_BACKOFF: float = 120.0

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("openbao-auto-unseal")

# Suppress the InsecureRequestWarning when verification is intentionally off.
# urllib3 ships with requests; this adds no new dependency.
if OPENBAO_TLS_SKIP_VERIFY:
    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:  # pragma: no cover - defensive only
        pass

# Cooperative shutdown flag, set by signal handlers.
_shutdown = threading.Event()


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class AutoUnsealError(Exception):
    """Base class for recoverable daemon errors (loop logs and backs off)."""


class OpenBaoError(AutoUnsealError):
    """An OpenBao API request failed or returned an unexpected status."""


class EscrowError(AutoUnsealError):
    """A Postgres escrow read/write operation failed."""


class EscrowNotReady(EscrowError):
    """The escrow table is not provisioned/usable yet (transient, not fatal)."""


# --------------------------------------------------------------------------- #
# OpenBao API
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SealStatus:
    """Snapshot of an OpenBao node's seal state."""

    reachable: bool
    initialized: bool = False
    sealed: bool = True


def build_session() -> requests.Session:
    """Create a requests session honoring the TLS-verify toggle."""
    session = requests.Session()
    session.verify = not OPENBAO_TLS_SKIP_VERIFY
    return session


def _probe_initialized(session: requests.Session) -> bool:
    """Fallback initialized check via GET /v1/sys/init (older API shapes)."""
    resp = session.get(f"{OPENBAO_ADDR}/v1/sys/init", timeout=REQUEST_TIMEOUT)
    return bool(resp.json().get("initialized", False))


def get_seal_status(session: requests.Session) -> SealStatus:
    """Probe GET /v1/sys/seal-status.

    Returns a :class:`SealStatus`. On any transport/parse failure the node is
    reported unreachable (the caller backs off and retries) rather than raising.
    """
    try:
        resp = session.get(
            f"{OPENBAO_ADDR}/v1/sys/seal-status", timeout=REQUEST_TIMEOUT
        )
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("OpenBao unreachable at %s (%s)", OPENBAO_ADDR, exc)
        return SealStatus(reachable=False)
    except ValueError as exc:
        logger.warning("OpenBao seal-status returned non-JSON (%s)", exc)
        return SealStatus(reachable=False)

    initialized = data.get("initialized")
    if initialized is None:
        try:
            initialized = _probe_initialized(session)
        except (requests.RequestException, ValueError):
            return SealStatus(reachable=False)

    return SealStatus(
        reachable=True,
        initialized=bool(initialized),
        sealed=bool(data.get("sealed", True)),
    )


def initialize_node(session: requests.Session) -> dict[str, Any] | None:
    """POST /v1/sys/init.

    Returns the init response JSON (contains key shares + root token) on a fresh
    init, or ``None`` if the node was already initialized concurrently
    (HTTP 400) -- in which case the caller falls through to unseal-from-escrow.
    Raises :class:`OpenBaoError` on any other failure.
    """
    try:
        resp = session.post(
            f"{OPENBAO_ADDR}/v1/sys/init",
            json={
                "secret_shares": SECRET_SHARES,
                "secret_threshold": SECRET_THRESHOLD,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OpenBaoError(f"init request failed: {exc}") from exc

    if resp.status_code == 200:
        logger.info(
            "OpenBao initialized (shares=%d, threshold=%d)",
            SECRET_SHARES,
            SECRET_THRESHOLD,
        )
        return resp.json()

    # Race: another initializer won. Treat as already-initialized.
    if resp.status_code == 400 and "initialized" in resp.text.lower():
        logger.info("OpenBao already initialized (concurrent init); will unseal")
        return None

    raise OpenBaoError(f"init failed: HTTP {resp.status_code}")


def _select_unseal_keys(init_data: Mapping[str, Any]) -> list[str]:
    """Pick unseal key shares from escrowed init data (base64 preferred)."""
    keys = init_data.get("keys_base64") or init_data.get("keys") or []
    return [key for key in keys if key]


def unseal_node(session: requests.Session, init_data: Mapping[str, Any]) -> bool:
    """POST /v1/sys/unseal with shares until the node reports ``sealed: false``.

    Submits shares one at a time and stops as soon as the node unseals, so at
    most ``SECRET_THRESHOLD`` valid shares are sent. Never logs key material.
    """
    keys = _select_unseal_keys(init_data)
    if not keys:
        raise OpenBaoError("escrowed init data contained no unseal keys")

    accepted = 0
    for share in keys:
        try:
            resp = session.post(
                f"{OPENBAO_ADDR}/v1/sys/unseal",
                json={"key": share},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise OpenBaoError(f"unseal request failed: {exc}") from exc

        if resp.status_code != 200:
            # Do not log the share or response body (may echo the key).
            logger.error("Unseal share rejected (HTTP %s)", resp.status_code)
            continue

        accepted += 1
        if not resp.json().get("sealed", True):
            logger.info("Node unsealed after %d share(s)", accepted)
            return True

    logger.error("Submitted all available shares; node still sealed")
    return False


# --------------------------------------------------------------------------- #
# Postgres escrow
# --------------------------------------------------------------------------- #


def _connect_db() -> "psycopg2.extensions.connection":
    """Connect to Postgres with bounded retry/backoff.

    Raises :class:`EscrowError` if all attempts fail.
    """
    delay = DB_RETRY_BASE_DELAY
    last_exc: Exception | None = None
    for attempt in range(1, DB_CONNECT_RETRIES + 1):
        if _shutdown.is_set():
            raise EscrowError("shutdown requested during database connect")
        try:
            return psycopg2.connect(
                connect_timeout=DB_CONNECT_TIMEOUT, **DB_CONFIG
            )
        except psycopg2.OperationalError as exc:
            last_exc = exc
            logger.warning(
                "Postgres connect attempt %d/%d failed: %s",
                attempt,
                DB_CONNECT_RETRIES,
                exc,
            )
            if attempt < DB_CONNECT_RETRIES:
                _shutdown.wait(delay)
                delay = min(delay * 2, MAX_BACKOFF)
    raise EscrowError(f"could not connect to Postgres: {last_exc}")


def assert_escrow_table() -> None:
    """Verify the escrow table exists and is readable. Assert-only.

    The pgcrypto extension and the ``vault_init_data`` table are provisioned
    admin-owned by the Ansible PG bootstrap *before* this container starts. The
    daemon deliberately issues no schema DDL: provisioning the table from the
    non-superuser daemon role would leave it owned by that role and strip its
    admin-ownership protection. If the table is missing or unusable the backend
    is treated as not-ready -- raise :class:`EscrowNotReady` so the caller backs
    off and retries on the next poll (no crash, no creation).
    """
    try:
        conn = _connect_db()
    except EscrowError as exc:
        raise EscrowNotReady(str(exc)) from exc
    try:
        with conn.cursor() as cur:
            # Name resolution matches the escrow queries (unqualified -> search_path).
            cur.execute("SELECT to_regclass('vault_init_data');")
            if cur.fetchone()[0] is None:
                raise EscrowNotReady(
                    "escrow table 'vault_init_data' not present; awaiting "
                    "Ansible PG bootstrap"
                )
            # Confirm the table is queryable with the daemon's privileges.
            cur.execute("SELECT 1 FROM vault_init_data LIMIT 1;")
    except psycopg2.Error as exc:
        raise EscrowNotReady(f"escrow table not usable: {exc}") from exc
    finally:
        conn.close()


def escrow_exists() -> bool:
    """Return True if an init-data row is already escrowed."""
    conn = _connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM vault_init_data);")
            return bool(cur.fetchone()[0])
    except psycopg2.Error as exc:
        raise EscrowError(f"escrow existence check failed: {exc}") from exc
    finally:
        conn.close()


def store_escrow(init_data: Mapping[str, Any]) -> None:
    """Persist the init response to ``vault_init_data``.

    Encrypted: ``pgp_sym_encrypt(json, key)`` with ``is_encrypted = true``.
    Plaintext: raw JSON bytes into the BYTEA column with ``is_encrypted = false``.
    The serialized payload contains secrets and is only ever bound as a query
    parameter -- never logged.
    """
    payload = json.dumps(init_data)
    conn = _connect_db()
    try:
        with conn.cursor() as cur:
            if ENCRYPT_VAULT_INIT_DATA:
                cur.execute(
                    "INSERT INTO vault_init_data (encrypted_json, is_encrypted) "
                    "VALUES (pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256'), %s);",
                    (payload, POSTGRES_ENCRYPTION_KEY, True),
                )
            else:
                cur.execute(
                    "INSERT INTO vault_init_data (encrypted_json, is_encrypted) "
                    "VALUES (%s, %s);",
                    (psycopg2.Binary(payload.encode("utf-8")), False),
                )
        conn.commit()
        logger.info(
            "Escrowed init data to Postgres (encrypted=%s)", ENCRYPT_VAULT_INIT_DATA
        )
    except psycopg2.Error as exc:
        conn.rollback()
        raise EscrowError(f"failed to store escrow: {exc}") from exc
    finally:
        conn.close()


def read_escrow() -> dict[str, Any] | None:
    """Read and decode the most recent escrow row.

    Decryption happens server-side: ``pgp_sym_decrypt`` for encrypted rows,
    ``convert_from(..., 'UTF8')`` for plaintext BYTEA. Returns the parsed init
    JSON, or ``None`` if no row exists yet. The decoded payload (secrets) is
    never logged.
    """
    conn = _connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT CASE WHEN is_encrypted "
                "            THEN pgp_sym_decrypt(encrypted_json, %s) "
                "            ELSE convert_from(encrypted_json, 'UTF8') "
                "       END "
                "FROM vault_init_data ORDER BY id DESC LIMIT 1;",
                (POSTGRES_ENCRYPTION_KEY,),
            )
            row = cur.fetchone()
    except psycopg2.Error as exc:
        raise EscrowError(f"failed to read escrow: {exc}") from exc
    finally:
        conn.close()

    if row is None or row[0] is None:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError) as exc:
        raise EscrowError(f"escrow payload is not valid JSON: {exc}") from exc


# --------------------------------------------------------------------------- #
# Reconcile loop
# --------------------------------------------------------------------------- #


def _handle_uninitialized(session: requests.Session) -> None:
    """Reconcile a not-yet-initialized node."""
    if not INIT_ENABLED:
        logger.info("Node uninitialized; INIT_ENABLED=false, waiting for primary")
        return

    # Idempotency guard: do not generate fresh keys when an escrow already
    # exists (those keys would not match a brand-new init -- needs review).
    if escrow_exists():
        logger.warning(
            "Node reports uninitialized but an escrow row exists; "
            "skipping init (manual review required)"
        )
        return

    init_data = initialize_node(session)
    if init_data is None:
        # Already initialized concurrently; unseal-from-escrow on next poll.
        return
    store_escrow(init_data)
    unseal_node(session, init_data)


def _handle_sealed(session: requests.Session) -> None:
    """Reconcile an initialized-but-sealed node."""
    init_data = read_escrow()
    if init_data is None:
        logger.warning(
            "Node initialized and sealed but escrow row not present yet; "
            "retrying next poll"
        )
        return
    unseal_node(session, init_data)


def reconcile_once(session: requests.Session) -> bool:
    """Run one reconcile iteration.

    Returns True for a healthy poll (OpenBao reachable and, when a branch needs
    it, the escrow table usable); False when OpenBao is unreachable or the escrow
    backend is not ready, so the caller backs off and retries on the next poll.
    The idle (initialized + unsealed) branch never touches Postgres.
    """
    status = get_seal_status(session)
    if not status.reachable:
        return False

    try:
        if not status.initialized:
            assert_escrow_table()
            _handle_uninitialized(session)
        elif status.sealed:
            assert_escrow_table()
            _handle_sealed(session)
        else:
            logger.debug("Node initialized and unsealed; idle")
    except EscrowNotReady as exc:
        logger.error("Escrow backend not ready; will retry next poll: %s", exc)
        return False
    return True


def _next_backoff(current: float) -> float:
    """Exponential backoff capped at ``MAX_BACKOFF``."""
    return min(max(current, UNSEAL_POLL_INTERVAL) * 2, MAX_BACKOFF)


def _handle_signal(signum: int, _frame: Any) -> None:
    """Set the cooperative shutdown flag on SIGTERM/SIGINT."""
    logger.info("Received signal %d; shutting down gracefully", signum)
    _shutdown.set()


def run() -> int:
    """Daemon entrypoint. Returns a process exit code."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if ENCRYPT_VAULT_INIT_DATA and not POSTGRES_ENCRYPTION_KEY:
        logger.error(
            "ENCRYPT_VAULT_INIT_DATA is true but POSTGRES_ENCRYPTION_KEY is "
            "empty; refusing to start"
        )
        return 2
    if SECRET_THRESHOLD > SECRET_SHARES:
        logger.error(
            "SECRET_THRESHOLD (%d) cannot exceed SECRET_SHARES (%d)",
            SECRET_THRESHOLD,
            SECRET_SHARES,
        )
        return 2

    logger.info(
        "Starting OpenBao auto-unseal daemon: addr=%s tls_skip_verify=%s "
        "init_enabled=%s encrypt_escrow=%s shares=%d threshold=%d poll=%ds "
        "db=%s@%s:%s/%s",
        OPENBAO_ADDR,
        OPENBAO_TLS_SKIP_VERIFY,
        INIT_ENABLED,
        ENCRYPT_VAULT_INIT_DATA,
        SECRET_SHARES,
        SECRET_THRESHOLD,
        UNSEAL_POLL_INTERVAL,
        DB_CONFIG["user"],
        DB_CONFIG["host"],
        DB_CONFIG["port"],
        DB_CONFIG["dbname"],
    )

    session = build_session()

    delay: float = UNSEAL_POLL_INTERVAL
    while not _shutdown.is_set():
        try:
            healthy = reconcile_once(session)
            delay = UNSEAL_POLL_INTERVAL if healthy else _next_backoff(delay)
        except AutoUnsealError as exc:
            logger.warning("Reconcile iteration failed: %s", exc)
            delay = _next_backoff(delay)
        except Exception:  # noqa: BLE001 - daemon must never die on an iteration
            logger.exception("Unexpected error in reconcile loop")
            delay = _next_backoff(delay)

        _shutdown.wait(delay)

    logger.info("Shutdown complete")
    return 0


if __name__ == "__main__":
    sys.exit(run())
