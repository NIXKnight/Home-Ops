# OpenBao auto-unseal daemon.
#
# Single-purpose image: optionally initialises an OpenBao node and then keeps it
# unsealed, persisting / reading the unseal material in PostgreSQL. One instance
# runs per OpenBao node (2-node deployment -> 2 sidecar instances).
#
# Build context: THIS directory (docker/openbao-unseal/).
# The daemon (vault_auto_unseal.py) lives IN this context and is its single
# source of truth (moved here from ansible/scripts/vault/). See README.md.
#
# SECURITY: no secrets are baked into this image. Every credential / key
# (Postgres password, Postgres encryption key, etc.) arrives at runtime via
# environment variables supplied by the Ansible compose manifest. Nothing below
# sets, reads, or persists a secret value.
FROM python:3.12-slim-bookworm

# Non-secret runtime hygiene for a long-running daemon:
#   - unbuffered stdio so log lines reach `docker logs` immediately
#   - no .pyc clutter
#   - quiet, cache-less pip
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dedicated, fixed-UID non-root system account.
RUN set -eux; \
    groupadd --system --gid 1001 openbao; \
    useradd --system --gid openbao --uid 1001 --no-create-home --shell /usr/sbin/nologin openbao; \
    mkdir -p /app; \
    chown openbao:openbao /app

WORKDIR /app

# Dependencies first (change rarely) for layer-cache friendliness.
# psycopg2-binary ships manylinux wheels and requests bundles its CA store via
# certifi, so no apt/build-toolchain layer is needed on python:slim.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Daemon script (changes most often -> last layer).
COPY --chown=openbao:openbao vault_auto_unseal.py /app/vault_auto_unseal.py

USER openbao:openbao

ENTRYPOINT ["python3", "/app/vault_auto_unseal.py"]
