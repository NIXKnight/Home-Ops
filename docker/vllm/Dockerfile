# syntax=docker/dockerfile:1.7
#
# vLLM OpenAI-compatible server for GGUF models on one RTX 3090 (sm_86).
# GGUF was REMOVED from vLLM core (vllm-project/vllm#39583) and now lives in the
# out-of-tree vllm-gguf-plugin, so the stock image cannot load a .gguf at all;
# everything below is that plugin and its build plumbing.
#
# Three further capabilities ride along: the out-of-tree vllm-bnb-plugin for
# bitsandbytes, LMCache for CPU/disk KV offload, and vLLM's in-tree LoRA
# filesystem resolver, which ships with vLLM and installs nothing. All three sit
# inert until the runtime flags at the foot of this file turn them on.
#
# No secrets, weights or credentials are baked in - models and serve flags come
# from the compose manifest at runtime. vLLM's server has no real auth of its
# own, so the perimeter in front of it is the authentication boundary.
#
# The build context carries exactly one file beside this one, sanity_check.py.
# There is no COPY anywhere here: it is bind-mounted for the gate RUN and is in
# no layer of the finished image.

# Pins; override with --build-arg to re-pin. CI greps the VLLM_IMAGE,
# VLLM_VERSION, PLUGIN_VERSION, BNB_PLUGIN_VERSION and LMCACHE_VERSION lines for
# the tag it publishes, so keep each on one line, double-quoted, with nothing
# trailing.
ARG VLLM_IMAGE="vllm/vllm-openai:v0.27.1"
ARG VLLM_VERSION="v0.27.1"

# Pinned by release tag AND by the commit it resolves to, so a moved tag cannot
# silently ship different code; the builder cross-checks the pair.
ARG PLUGIN_VERSION="0.0.5"
ARG PLUGIN_COMMIT="d358f564fc8f470cddd7c141a149b4ebafafa01f"
ARG PLUGIN_REPO="https://github.com/vllm-project/vllm-gguf-plugin"

# vLLM 0.27.1 dropped `gguf` when GGUF left core, so it is installed explicitly.
ARG GGUF_VERSION="0.19.0"

# bitsandbytes, pinned tag AND commit like the GGUF plugin above. Pure Python:
# no CUDA extension is compiled for it, and the builder asserts that holds.
ARG BNB_PLUGIN_VERSION="0.0.2"
ARG BNB_PLUGIN_COMMIT="85de847cd6e78e749fb2f93cee7de5174c71e95e"
ARG BNB_PLUGIN_REPO="https://github.com/vllm-project/vllm-bnb-plugin"

# Floor is the plugin's own bitsandbytes>=0.48.1 on CUDA. 0.50.1 is the latest
# stable and carries libbitsandbytes_cuda130.so, matching this base's CUDA 13.0.
# The gate below picks that file by torch's own CUDA version and dlopens it.
ARG BITSANDBYTES_VERSION="0.50.1"

# LMCache KV offload. The cp312 wheel matches the base's Python, and its bundled
# extensions link this image's libtorch directly - hence the torch pin below.
ARG LMCACHE_VERSION="0.5.3"

# The only imports on LMCache's connector path that this image lacks. Everything
# else it declares is either already here or never imported on that path.
ARG AIOFILE_VERSION="3.12.3"
ARG CAIO_VERSION="0.12.2"
ARG SORTEDCONTAINERS_VERSION="2.4.0"

# The base image's torch, declared rather than derived so the gate has something
# to compare against. Nothing below installs with a resolver, but a future edit
# that did could swap torch and leave LMCache's extensions silently unloadable.
ARG TORCH_VERSION="2.13.0"

# Build-only. setuptools is bounded by the plugin's own build-system requirement.
ARG SETUPTOOLS_SPEC="setuptools>=77.0.3,<81.0.0"
ARG NINJA_VERSION="1.11.1.3"
ARG WHEEL_VERSION="0.45.1"

# 8.6 is Ampere / RTX 3090, deliberately without "+PTX" for this one fixed card.
# NOT named TORCH_CUDA_ARCH_LIST: the base image's ENV of that name would shadow
# an ARG of the same name, so it is assigned to the ENV in each stage instead.
ARG CUDA_ARCH_LIST="8.6"


# ---------------------------------------------------------------------------
# Builder stage, FROM the vLLM image itself so the CUDA extension is compiled
# against the exact torch, CUDA and Python it will run beside. The PyPI wheel is
# not used because its CUDA target is not contractual (upstream's cu129 and
# cu130 builds collide on one filename) and a mismatch degrades silently.
FROM ${VLLM_IMAGE} AS builder

# The pins above are global ARGs, in scope only for FROM lines, so each is
# re-declared inside the stage - immediately above the layer that consumes it,
# never in one block at the top, so re-pinning one cannot invalidate the cached
# layers that precede it.

# UV_CACHE_DIR is explicit: nothing in the base image points uv's cache at
# /opt/uv/cache, so without it the cache mounts below are silent no-ops.
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    UV_CACHE_DIR=/opt/uv/cache

# The base (nvidia/cuda:*-base) has no host compiler and only the cudart
# runtime. The dev package version is derived from the base's own CUDA_VERSION
# so a VLLM_IMAGE re-pin keeps working.
RUN set -eux; \
    CUDA_VERSION_DASH="$(echo "${CUDA_VERSION}" | cut -d. -f1,2 | tr '.' '-')"; \
    apt-get update; \
    apt-get install -y --no-install-recommends --allow-change-held-packages \
        build-essential \
        "cuda-cudart-dev-${CUDA_VERSION_DASH}"; \
    rm -rf /var/lib/apt/lists/*; \
    gcc --version; \
    nvcc --version

ARG PLUGIN_VERSION
ARG PLUGIN_COMMIT
ARG PLUGIN_REPO
# Fetched by commit, not by tag. pyproject's base_version is what setup.py
# stamps onto the wheel, so asserting it against PLUGIN_VERSION proves the
# pinned commit is the pinned release before a mislabelled wheel exists.
RUN set -eux; \
    mkdir -p /src; \
    curl -fsSL "${PLUGIN_REPO}/archive/${PLUGIN_COMMIT}.tar.gz" \
        | tar -xz --strip-components=1 -C /src; \
    cd /src; \
    test -f pyproject.toml; \
    test -f vllm_gguf_plugin/csrc/gguf/gguf_kernel.cu; \
    BASE_VERSION="$(python3 -c "import tomllib,pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['tool']['vllm_gguf_plugin']['base_version'])")"; \
    test "${BASE_VERSION}" = "${PLUGIN_VERSION}"; \
    echo "vllm-gguf-plugin ${BASE_VERSION} at ${PLUGIN_COMMIT}"

ARG SETUPTOOLS_SPEC
ARG NINJA_VERSION
ARG WHEEL_VERSION
# PEP 517 build requirements seeded by hand, because the build below runs
# --no-build-isolation: isolation would fetch a fresh torch from PyPI instead of
# using this image's, which is the mismatch this stage exists to prevent.
RUN --mount=type=cache,target=/opt/uv/cache,sharing=locked \
    set -eux; \
    uv pip install --system \
        "${SETUPTOOLS_SPEC}" \
        "wheel==${WHEEL_VERSION}" \
        "ninja==${NINJA_VERSION}"; \
    python3 -c "import setuptools, torch; print('setuptools', setuptools.__version__, 'torch', torch.__version__)"

ARG CUDA_ARCH_LIST
# --no-deps builds ONLY this project; without it pip would build vllm and torch
# too. No GPU is needed, nvcc cross-compiles. This ENV is what torch's extension
# builder reads, and it overrides the base's seven-arch value for this stage.
ENV TORCH_CUDA_ARCH_LIST="${CUDA_ARCH_LIST}"
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    set -eux; \
    cd /src; \
    echo "Building for TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}"; \
    python3 -m pip wheel . --no-build-isolation --no-deps --wheel-dir /wheels; \
    ls -la /wheels; \
    test "$(find /wheels -name 'vllm_gguf_plugin-*.whl' | wc -l | tr -d '[:space:]')" = "1"

# Gate the artefact before it leaves this stage: the embedded ELF and PTX
# architecture sets must equal TORCH_CUDA_ARCH_LIST exactly, in both directions.
# A presence-only check would pass a build that silently inherited the base's
# seven architectures, and wrong SASS only surfaces as a launch failure on-GPU.
# Both halves are fail-closed: the wheel must hold exactly one extension, and a
# cuobjdump that errors fails the build instead of parsing as an empty arch set.
RUN set -eux; \
    mkdir -p /wheel-check; \
    cd /wheel-check; \
    python3 -c "import glob, zipfile; zipfile.ZipFile(glob.glob('/wheels/vllm_gguf_plugin-*.whl')[0]).extractall('.')"; \
    SO_MATCHES="$(find . -name '_C_gguf*.so')"; \
    test -n "${SO_MATCHES}" \
        || { echo "FAIL: no compiled extension (_C_gguf*.so) in the wheel"; exit 1; }; \
    SO_COUNT="$(printf '%s\n' "${SO_MATCHES}" | wc -l | tr -d '[:space:]')"; \
    test "${SO_COUNT}" = "1" \
        || { echo "FAIL: expected exactly one compiled extension in the wheel, found ${SO_COUNT}:"; printf '%s\n' "${SO_MATCHES}"; exit 1; }; \
    SO="${SO_MATCHES}"; \
    echo "Embedded device code in ${SO}:"; \
    cuobjdump --list-elf "${SO}"; \
    EXPECTED_ELF=""; \
    EXPECTED_PTX=""; \
    for ARCH in ${TORCH_CUDA_ARCH_LIST}; do \
        SM="sm_$(echo "${ARCH}" | sed 's/+PTX$//' | tr -d '.')"; \
        EXPECTED_ELF="${EXPECTED_ELF} ${SM}"; \
        case "${ARCH}" in *+PTX) EXPECTED_PTX="${EXPECTED_PTX} ${SM}";; esac; \
    done; \
    EXPECTED_ELF="$(printf '%s' "${EXPECTED_ELF}" | tr ' ' '\n' | grep -v '^$' | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//')"; \
    EXPECTED_PTX="$(printf '%s' "${EXPECTED_PTX}" | tr ' ' '\n' | grep -v '^$' | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//')"; \
    ACTUAL_ELF="$(cuobjdump --list-elf "${SO}" | grep -oE 'sm_[0-9]+[a-zA-Z]*' | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//')"; \
    echo "ELF architectures: expected [${EXPECTED_ELF}] actual [${ACTUAL_ELF}]"; \
    test "${ACTUAL_ELF}" = "${EXPECTED_ELF}" \
        || { echo "FAIL: embedded ELF architecture set [${ACTUAL_ELF}] is not exactly [${EXPECTED_ELF}]; TORCH_CUDA_ARCH_LIST was not honoured"; exit 1; }; \
    if ! PTX_RAW="$(cuobjdump --list-ptx "${SO}" 2>&1)"; then \
        echo "FAIL: cuobjdump --list-ptx failed: ${PTX_RAW}"; exit 1; \
    fi; \
    echo "PTX listing: ${PTX_RAW:-<empty>}"; \
    ACTUAL_PTX="$(printf '%s\n' "${PTX_RAW}" | grep -oE 'sm_[0-9]+[a-zA-Z]*' | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//')"; \
    PTX_COUNT="$(printf '%s\n' "${PTX_RAW}" | grep -c '\.ptx' || true)"; \
    EXPECTED_PTX_COUNT="$(printf '%s' "${EXPECTED_PTX}" | wc -w | tr -d '[:space:]')"; \
    echo "PTX architectures: expected [${EXPECTED_PTX}] actual [${ACTUAL_PTX}] (entries ${PTX_COUNT}, expected ${EXPECTED_PTX_COUNT})"; \
    test "${ACTUAL_PTX}" = "${EXPECTED_PTX}" \
        || { echo "FAIL: embedded PTX architecture set [${ACTUAL_PTX}] is not exactly [${EXPECTED_PTX}]"; exit 1; }; \
    test "${PTX_COUNT}" = "${EXPECTED_PTX_COUNT}" \
        || { echo "FAIL: ${PTX_COUNT} PTX entries embedded, expected ${EXPECTED_PTX_COUNT}"; exit 1; }; \
    echo "OK: device code is exactly ELF [${EXPECTED_ELF}] with PTX [${EXPECTED_PTX:-none}]"; \
    rm -rf /wheel-check

ARG BNB_PLUGIN_VERSION
ARG BNB_PLUGIN_COMMIT
ARG BNB_PLUGIN_REPO
# The bitsandbytes plugin, fetched by commit the same way. It stamps its version
# from [project].version, not the GGUF plugin's [tool.*].base_version, so the
# tag/commit cross-check reads that key instead. The .cu assertion is
# fail-closed: if upstream ever grows a CUDA extension, this stage must start
# compiling it rather than shipping a wheel with the kernels quietly missing.
RUN set -eux; \
    mkdir -p /src-bnb; \
    curl -fsSL "${BNB_PLUGIN_REPO}/archive/${BNB_PLUGIN_COMMIT}.tar.gz" \
        | tar -xz --strip-components=1 -C /src-bnb; \
    cd /src-bnb; \
    test -f pyproject.toml; \
    test -f vllm_bnb_plugin/__init__.py; \
    CUDA_SOURCES="$(find . -name '*.cu' -o -name '*.cuh')"; \
    test -z "${CUDA_SOURCES}" \
        || { echo "FAIL: CUDA sources appeared in vllm-bnb-plugin, which this stage builds as pure Python:"; printf '%s\n' "${CUDA_SOURCES}"; exit 1; }; \
    PROJECT_VERSION="$(python3 -c "import tomllib,pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])")"; \
    test "${PROJECT_VERSION}" = "${BNB_PLUGIN_VERSION}"; \
    echo "vllm-bnb-plugin ${PROJECT_VERSION} at ${BNB_PLUGIN_COMMIT}"

# Built into a wheel here rather than pip-installed from source in the final
# stage, so that stage keeps its no-source-tree invariant and both plugins
# arrive by the same route. --no-build-isolation reuses the setuptools seeded
# above; the plugin's own build-system floor (>=69) is inside SETUPTOOLS_SPEC.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    set -eux; \
    cd /src-bnb; \
    python3 -m pip wheel . --no-build-isolation --no-deps --wheel-dir /wheels; \
    test "$(find /wheels -name 'vllm_bnb_plugin-*.whl' | wc -l | tr -d '[:space:]')" = "1"; \
    WHEEL_SO="$(python3 -c "import glob, zipfile; print(' '.join(n for n in zipfile.ZipFile(glob.glob('/wheels/vllm_bnb_plugin-*.whl')[0]).namelist() if n.endswith('.so')))")"; \
    test -z "${WHEEL_SO}" \
        || { echo "FAIL: vllm_bnb_plugin wheel carries a compiled extension (${WHEEL_SO}); it is meant to be pure Python"; exit 1; }


# ---------------------------------------------------------------------------
# Final stage: stock vLLM plus the plugins. No compiler, headers or source tree.
FROM ${VLLM_IMAGE}

# As in the builder: the global ARGs are re-declared per consuming layer, not
# in one block here, so a re-pin invalidates only what actually reads it. The
# LABEL block at the foot of this stage reads them all; ARG scope runs to the
# end of the stage, so every declaration below is still live there.

# As in the builder: without this the uv cache mounts below are no-ops.
ENV UV_CACHE_DIR=/opt/uv/cache

ARG GGUF_VERSION
# Pinned explicitly because the plugin wheel is installed --no-deps below.
RUN --mount=type=cache,target=/opt/uv/cache,sharing=locked \
    set -eux; \
    uv pip install --system "gguf==${GGUF_VERSION}"

ARG BITSANDBYTES_VERSION
# --no-deps: bitsandbytes declares torch, numpy and packaging, all already here
# at versions it accepts, so a resolver could only do harm - it is the one thing
# that could replace this image's torch. The gate audits the set instead.
RUN --mount=type=cache,target=/opt/uv/cache,sharing=locked \
    set -eux; \
    uv pip install --system --no-deps "bitsandbytes==${BITSANDBYTES_VERSION}"

ARG LMCACHE_VERSION
ARG AIOFILE_VERSION
ARG CAIO_VERSION
ARG SORTEDCONTAINERS_VERSION
# LMCache, --no-deps with its import-time gaps filled by hand. It declares a far
# wider set than the in-process CPU/disk offload path touches - cupy-cuda13x,
# awscrt, google-cloud-bigtable, even pytest - and carries ceilings
# (numpy<=2.2.6, prometheus_client<=0.24.1, opentelemetry-api<=1.40.0) that a
# resolver would satisfy by DOWNGRADING what vLLM resolved for itself. aiofile
# (via caio) and sortedcontainers are the only imports on that path this image
# lacks; the gate walks the chain to prove nothing else is missing.
RUN --mount=type=cache,target=/opt/uv/cache,sharing=locked \
    set -eux; \
    uv pip install --system --no-deps \
        "lmcache==${LMCACHE_VERSION}" \
        "aiofile==${AIOFILE_VERSION}" \
        "caio==${CAIO_VERSION}" \
        "sortedcontainers==${SORTEDCONTAINERS_VERSION}"

# Bind-mounted from the builder so the wheels never become a layer here.
# --no-deps: vllm, torch, gguf and bitsandbytes are already satisfied, and a
# resolver let near this image would reinstall gigabytes of CUDA wheels. The
# gate below audits both dependency sets instead, so nothing is silently
# skipped.
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    --mount=type=cache,target=/opt/uv/cache,sharing=locked \
    set -eux; \
    uv pip install --system --no-deps \
        /wheels/vllm_gguf_plugin-*.whl \
        /wheels/vllm_bnb_plugin-*.whl

ARG VLLM_VERSION
ARG PLUGIN_VERSION
ARG BNB_PLUGIN_VERSION
ARG TORCH_VERSION
# Sanity gate. Every addition in this image degrades silently when it is broken:
# upstream swallows the CUDA extension's ImportError and falls back to Triton,
# so a plugin built against the wrong CUDA or architecture still imports,
# registers and SERVES, just slower, with nothing in the logs; the bitsandbytes
# plugin overrides an in-tree implementation that will happily serve in its
# place; and LMCache reports a failed native bind as an INFO line. The seven
# GPU-free checks in sanity_check.py exist to catch exactly that.
#
# That script is the one file this build reads out of the context, and it is
# bind-mounted for this RUN alone: it runs inside the image without ever being
# committed to a layer of it.
#
# The pins are exported onto this one RUN rather than set as ENV: an image-wide
# ENV VLLM_VERSION would join vLLM's own VLLM_-prefixed namespace and be flagged
# as unrecognised for the life of the container, so the values reach the script
# through os.environ instead.
RUN --mount=type=bind,source=sanity_check.py,target=/tmp/sanity_check.py \
    VLLM_VERSION="${VLLM_VERSION}" PLUGIN_VERSION="${PLUGIN_VERSION}" \
    BNB_PLUGIN_VERSION="${BNB_PLUGIN_VERSION}" LMCACHE_VERSION="${LMCACHE_VERSION}" \
    TORCH_VERSION="${TORCH_VERSION}" python3 /tmp/sanity_check.py

ARG CUDA_ARCH_LIST
# Narrow the base's seven-architecture value: vLLM JIT-compiles FlashInfer and
# DeepGEMM at run time and torch.compile reads the same variable.
ENV TORCH_CUDA_ARCH_LIST="${CUDA_ARCH_LIST}"

# Documentation only; the real port is whatever --port the runtime args carry.
EXPOSE 8000

# Read by the HEALTHCHECK below and nothing else, so a deployment on a
# non-default --port can keep its probe honest. NOT named VLLM_*: vLLM flags
# unrecognised members of its own VLLM_-prefixed namespace.
ENV HEALTHCHECK_PORT=8000

# This runs under docker compose, where `depends_on: service_healthy` has
# nothing else to read. The start period is huge because loading a large GGUF
# and capturing CUDA graphs takes many minutes on one 3090.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15m --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${HEALTHCHECK_PORT:-8000}/health" || exit 1

# ENTRYPOINT and CMD are deliberately unset: the base's ENTRYPOINT
# ["vllm", "serve"] takes the model reference and every serve flag as container
# arguments from the compose manifest. USER stays root - confine from the
# compose file (read_only, cap_drop, user:) rather than baking a UID in here.
#
# Runtime escape hatches, neither set here: VLLM_GGUF_USE_CUDA=0 forces the
# plugin's Triton kernels instead of the compiled CUDA ones.
# NVIDIA_DISABLE_REQUIRE=1 clears the base's NVIDIA_REQUIRE_CUDA driver clauses,
# which this host's 610.x branch trips under the legacy nvidia-container-runtime
# hook but not under CDI.
#
# bitsandbytes: --quantization bitsandbytes (in-flight 4-bit) or --load-format
# bitsandbytes (pre-quantized 4/8-bit); 8-bit forces eager mode, no CUDA graphs.
#
# LMCache CPU/disk KV offload, in-process, single node:
# --kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_both"}'
# with LMCACHE_LOCAL_CPU=True, LMCACHE_MAX_LOCAL_CPU_SIZE=<GB>, LMCACHE_CHUNK_SIZE
# and optionally LMCACHE_LOCAL_DISK=file:///<dir> for the second tier.
#
# LoRA: VLLM_PLUGINS=gguf,bitsandbytes,lora_filesystem_resolver with
# VLLM_LORA_RESOLVER_CACHE_DIR=<dir> and --enable-lora. VLLM_PLUGINS is an
# ALLOWLIST, so naming only the resolver silently disables both quantization
# plugins above; leave it unset unless all three are listed.

# These five are read by the LABEL block and by nothing else in this stage.
ARG VLLM_IMAGE
ARG PLUGIN_COMMIT
ARG PLUGIN_REPO
ARG BNB_PLUGIN_COMMIT
ARG BNB_PLUGIN_REPO
LABEL org.opencontainers.image.title="vllm-gguf-bnb-lmcache" \
      org.opencontainers.image.description="vLLM OpenAI-compatible server with the out-of-tree vllm-gguf-plugin compiled for sm_86 (RTX 3090), the vllm-bnb-plugin for bitsandbytes, and LMCache KV offload" \
      org.opencontainers.image.version="${VLLM_VERSION}-gguf${PLUGIN_VERSION}-bnb${BNB_PLUGIN_VERSION}-lmcache${LMCACHE_VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.base.name="${VLLM_IMAGE}" \
      pk.nixknight.vllm.version="${VLLM_VERSION}" \
      pk.nixknight.vllm-gguf-plugin.version="${PLUGIN_VERSION}" \
      pk.nixknight.vllm-gguf-plugin.commit="${PLUGIN_COMMIT}" \
      pk.nixknight.vllm-gguf-plugin.source="${PLUGIN_REPO}" \
      pk.nixknight.vllm-bnb-plugin.version="${BNB_PLUGIN_VERSION}" \
      pk.nixknight.vllm-bnb-plugin.commit="${BNB_PLUGIN_COMMIT}" \
      pk.nixknight.vllm-bnb-plugin.source="${BNB_PLUGIN_REPO}" \
      pk.nixknight.bitsandbytes.version="${BITSANDBYTES_VERSION}" \
      pk.nixknight.lmcache.version="${LMCACHE_VERSION}" \
      pk.nixknight.torch.version="${TORCH_VERSION}" \
      pk.nixknight.torch-cuda-arch-list="${CUDA_ARCH_LIST}"
# NOTE: .source/.revision/.url are inherited from the base and describe vLLM
# upstream; base.name above identifies what this was built on.
