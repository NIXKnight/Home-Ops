# openbao-unseal

Container image packaging the Python **OpenBao auto-unseal daemon**. One instance
runs per OpenBao node (the 2-node deployment runs two instances, one beside each
node) to optionally initialise the node and keep it unsealed, storing the unseal
material in PostgreSQL.

## Image

| | |
|---|---|
| Name : tag (integration contract) | `openbao-unseal:1.0.0` |
| Registry-qualified (matches repo convention) | `<DOCKER_HUB_USERNAME>/openbao-unseal:1.0.0` (+ `:latest`) |
| Base image | `python:3.12-slim-bookworm` (Debian bookworm, matches repo's `bookworm-slim` style) |
| Runtime deps | `requests==2.32.3`, `psycopg2-binary==2.9.10` (see `requirements.txt`) |
| User | non-root system account `openbao` (uid/gid 1001) |
| Entrypoint | `python3 /app/vault_auto_unseal.py` |

The Ansible compose manifest references the image via
`DOCKER_COMPOSE_SERVICE_IMAGES` (`name` + `tag`). `openbao-unseal:1.0.0` is the
contract; bump the semver tag on every script/Dockerfile/deps change (the repo's
`docker-compose-service` role pulls by tag, so a moving tag would not redeploy).

## Build

The daemon (`vault_auto_unseal.py`) lives **in this build context** and is the
single source of truth for the script (it was moved here from
`ansible/scripts/vault/` so the context is self-contained; no vendoring or
pre-build copy step is needed). The build context is this directory, matching the
repo convention (`context: docker/<svc>/`, script `COPY`ed from the local
context).

```bash
# from the repository root
docker build -t openbao-unseal:1.0.0 docker/openbao-unseal/
```

No secrets are required, read, or baked in at build time.

## Distribution (how both nodes get the image)

Matches the existing custom images (`powerdns`, `kea`): build once in CI, push to
Docker Hub, and let each node **pull** it. The `docker-compose-service` role pulls
images (`source: pull`) — there is no on-node build — so **both OpenBao nodes
obtain the image identically: each pulls `<DOCKER_HUB_USERNAME>/openbao-unseal:1.0.0`
from Docker Hub** when its play runs.

The GitHub Actions workflow
`.github/workflows/openbao-unseal-container-image-build.yml` builds + pushes this
image. It is a direct clone of
`.github/workflows/powerdns-container-image-build.yml` (same runner, actions,
secrets, and tag scheme), differing only in name, paths, context, and tags.
Because the daemon now lives in-context, no pre-build copy step is needed and the
`docker/openbao-unseal/**` path glob covers every source file:

```yaml
name: Build OpenBao Unseal Container Image
on:
  push:
    branches:
      - main
    paths:
      - "docker/openbao-unseal/**"
      - ".github/workflows/openbao-unseal-container-image-build.yml"
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v3

      - name: Log in to Docker Hub
        uses: docker/login-action@v2.1.0
        with:
          username: ${{ secrets.DOCKER_HUB_USERNAME }}
          password: ${{ secrets.DOCKER_HUB_TOKEN }}

      - name: Build and push Docker image
        uses: docker/build-push-action@v3.2.0
        with:
          context: docker/openbao-unseal/
          push: true
          tags: "${{ secrets.DOCKER_HUB_USERNAME }}/openbao-unseal:latest,${{ secrets.DOCKER_HUB_USERNAME }}/openbao-unseal:1.0.0"
```

## Integration contract (runtime env vars)

The image sets **none** of these — they are supplied at runtime by the compose
manifest. Secrets must be delivered via the manifest's env/secret mechanism, never
baked into the image.

| Var | Purpose |
|---|---|
| `OPENBAO_ADDR` | OpenBao API address |
| `OPENBAO_TLS_SKIP_VERIFY` | Skip TLS verification (self-signed) |
| `INIT_ENABLED` | Allow this instance to initialise the node |
| `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` / `POSTGRES_USER` | Postgres connection |
| `POSTGRES_PASSWORD` | Postgres password (secret) |
| `POSTGRES_ENCRYPTION_KEY` | Key for encrypting stored unseal data (secret) |
| `ENCRYPT_VAULT_INIT_DATA` | Encrypt init data at rest |
| `SECRET_SHARES` / `SECRET_THRESHOLD` | Shamir split parameters |
| `UNSEAL_POLL_INTERVAL` | Poll cadence for the unseal loop |
| `DEBUG` | Verbose logging |

> Note: the daemon is being adapted in parallel to this contract (e.g.
> `OPENBAO_ADDR` in place of `VAULT_ADDR`, a polling loop, `INIT_ENABLED`). The
> image is agnostic to the variable names — it only runs the script.

## Notes / open questions

- **Location:** this image lives at repo-root `docker/openbao-unseal/`, matching
  the other custom images (`powerdns`, `kea`) and the `docker/**` CI trigger. The
  daemon (`vault_auto_unseal.py`) now lives in this directory and is its single
  source of truth (moved here from `ansible/scripts/vault/` so the build context
  is self-contained; see "Build").
- **Healthcheck:** left to the compose manifest (repo convention), since a
  meaningful check queries OpenBao.
- Optional supply-chain hardening: pin the base image by digest
  (`python:3.12-slim-bookworm@sha256:...`); the repo currently pins by tag only.
