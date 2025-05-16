#!/usr/bin/env python3

# Minimal Vault bootstrap helper: init + store keys in Postgres, then unseal
import logging
import os

import psycopg2
import requests
from psycopg2.extras import Json

DEBUG = os.getenv("DEBUG", "false").lower() in {"1", "true", "yes"}
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

VAULT_ADDR = os.getenv("VAULT_ADDR", "http://localhost:8200")
DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "database": os.getenv("POSTGRES_DB", "vault"),
    "user": os.getenv("POSTGRES_USER", "vault"),
    "password": os.getenv("POSTGRES_PASSWORD", "vault"),
}
POSTGRES_ENCRYPTION_KEY = os.getenv("POSTGRES_ENCRYPTION_KEY", "my-secure-encryption-key")

SECRET_SHARES = int(os.getenv("SECRET_SHARES", "5"))
SECRET_THRESHOLD = int(os.getenv("SECRET_THRESHOLD", "3"))

# Encrypt and persist the init-response JSON
def store_vault_init_data(data):
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vault_init_data (encrypted_json)
                VALUES (pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256'));
                """,
                (Json(data), POSTGRES_ENCRYPTION_KEY),
            )
        conn.commit()
    logger.info("Stored Vault init data in PostgreSQL")

# Fetch and decrypt the most-recent init-response
def get_vault_init_data():
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pgp_sym_decrypt(encrypted_json::bytea, %s)::json
                FROM vault_init_data
                ORDER BY id DESC
                LIMIT 1;
                """,
                (POSTGRES_ENCRYPTION_KEY,),
            )
            row = cur.fetchone()

    if row and DEBUG:
        logger.debug("Decrypted Vault init data: %s", row[0])
    return row[0] if row else None

# Quick status probe (initialized / sealed)
def check_vault_status():
    status_response = requests.get(f"{VAULT_ADDR}/v1/sys/seal-status", timeout=5)
    status_data = status_response.json()
    return {
        "initialized": status_data.get("initialized", False),
        "sealed": status_data.get("sealed", True),
    }

# Init Vault and record the keys
def initialize_vault():
    response = requests.post(
        f"{VAULT_ADDR}/v1/sys/init",
        json={"secret_shares": SECRET_SHARES, "secret_threshold": SECRET_THRESHOLD},
        timeout=10,
    )

    if response.status_code == 200:
        init_data = response.json()
        store_vault_init_data(init_data)
        logger.info("Vault initialized and keys stored")
        return init_data

    logger.error("Vault initialization failed: %s", response.text)
    return None

# Replay stored keys until vault is unsealed
def unseal_vault():
    vault_init_data = get_vault_init_data()
    if not vault_init_data:
        logger.warning("No Vault init data found in database — cannot unseal")
        return

    for key in vault_init_data.get("keys", []):
        response = requests.post(
            f"{VAULT_ADDR}/v1/sys/unseal", json={"key": key}, timeout=10
        )
        if response.status_code != 200:
            logger.error("Unseal operation failed: %s", response.text)
            continue

        status = response.json()
        if not status.get("sealed", True):
            logger.info("Vault unsealed successfully")
            break

if __name__ == "__main__":
    status = check_vault_status()

    if not status["initialized"]:
        logger.info("Initializing Vault …")
        initialize_vault()
    else:
        logger.info("Vault is already initialized")

    if status["sealed"]:
        logger.info("Unsealing Vault …")
        unseal_vault()
    else:
        logger.info("Vault is already unsealed")
