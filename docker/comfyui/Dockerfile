# syntax=docker/dockerfile:1.7
#
# ComfyUI + ComfyUI-GGUF inference image for MiniMax-H3 (video + audio).
#
# Single-purpose image: serves the MiniMax-H3 packed-DiT (video latents + audio
# latents) from GGUF-quantised weights on a single 24 GB RTX 3090 (Ampere,
# sm_86), running as a Kubernetes pod on Talos with the NVIDIA runtime class.
#
# Build context: THIS directory (docker/comfyui/). Nothing is COPYed from it -
# every artefact is fetched from an upstream pin - so the context stays empty
# and the build is fully reproducible from the ARGs below. See README.md.
#
# Runs as root by design: the Talos hostPath volumes mounted under /opt/comfyui
# are root-owned and Talos cannot reconcile them with an arbitrary container
# UID, so a non-root USER would make every one of those mounts read-only in
# practice. Confine the process with the pod securityContext instead
# (allowPrivilegeEscalation: false, capabilities drop ALL). See README.md.
#
# SECURITY: no secrets are baked into this image. No model weights, no tokens,
# no HuggingFace credentials. Weights arrive at runtime on the hostPath volume
# mounted at /opt/comfyui/models. Nothing below sets, reads, or persists a
# secret value.

# Version pins, all live-verified against upstream at authoring time. Override
# any of them at build time with --build-arg to re-pin.

# Base: CUDA 12.8 userspace on Ubuntu 24.04. Why this exact pairing:
#   - the container ships its OWN CUDA userspace and takes only the kernel
#     driver from the host; the driver is backward compatible with older
#     toolkits, so a 12.8 userspace is safe under this node's newer driver
#   - cu128 is PyTorch's newest STABLE wheel channel for this class, and
#     matching the base image's CUDA minor to the wheel's keeps any custom node
#     that dlopen()s a system CUDA library ABI-consistent with torch's own libs
#   - cu128 still emits sm_86 SASS, so the 3090 runs native kernels with no PTX
#     JIT stall on first inference
#   - Ubuntu 24.04 ships Python 3.12, which is what the cp312 wheels target; a
#     22.04 base (Python 3.10) would silently fall back to older cp310 builds
#   - the plain -runtime variant, not -cudnn-runtime: the torch wheels pull
#     their own pinned nvidia-cudnn-cu12, so a system cuDNN is ~1 GB of dead
#     weight and a second, conflicting copy
ARG CUDA_BASE="nvidia/cuda:12.8.2-runtime-ubuntu24.04"

# ComfyUI release tag. v0.30.0 (2026-08-03) is the FIRST release carrying
# native, local-weight MiniMax-H3 support. Every earlier tag (v0.29.2 and below)
# ships only the hosted-API partner node, which cannot touch local GGUF weights.
# Do NOT downgrade this pin. See README.md.
#
# The CI workflow greps this exact line for the tag it publishes, so keep it on
# one line, double-quoted, with nothing trailing.
ARG COMFYUI_VERSION="v0.30.0"

# city96/ComfyUI-GGUF, pinned by commit. Supplies UnetLoaderGGUF (the H3 DiT)
# and CLIPLoaderGGUF (the Qwen3-VL-32B text encoder - the component that makes
# H3 fit in 24 GB at all). The commit predates MiniMax-H3 by design and is still
# correct: CLIPLoaderGGUF derives its type list from whatever ComfyUI core it is
# loaded against, so it inherits "minimax" with no node-pack change. See
# README.md.
ARG COMFYUI_GGUF_COMMIT="6ea2651e7df66d7585f6ffee804b20e92fb38b8a"

# PyTorch trio from the cu128 stable channel. Kept as ARGs so the trio is
# re-pinned atomically - mismatched torch/torchvision/torchaudio is the single
# most common ComfyUI import failure.
ARG TORCH_VERSION="2.11.0+cu128"
ARG TORCHVISION_VERSION="0.26.0+cu128"
ARG TORCHAUDIO_VERSION="2.11.0+cu128"
ARG TORCH_INDEX_URL="https://download.pytorch.org/whl/cu128"

# GGUF reader. Pinned explicitly; ComfyUI-GGUF only asks for >=0.13.0.
ARG GGUF_VERSION="0.19.0"

ARG COMFYUI_REPO="https://github.com/Comfy-Org/ComfyUI.git"
ARG COMFYUI_GGUF_REPO="https://github.com/city96/ComfyUI-GGUF.git"

# Structural, NOT tunable. ENTRYPOINT is exec-form and therefore cannot expand
# build args, so these two paths are also written literally at the bottom of the
# file. /opt/comfyui in particular is a contract with the deployment manifest,
# which mounts hostPath volumes underneath it. Changing either means editing
# ENTRYPOINT too.
ARG VENV="/opt/venv"
ARG COMFYUI_DIR="/opt/comfyui"


# Builder stage: holds git, a C toolchain and Python headers so any dependency
# without a manylinux wheel can still build. None of it survives into the final
# image.
FROM ${CUDA_BASE} AS builder

ARG COMFYUI_VERSION
ARG COMFYUI_GGUF_COMMIT
ARG TORCH_VERSION
ARG TORCHVISION_VERSION
ARG TORCHAUDIO_VERSION
ARG TORCH_INDEX_URL
ARG GGUF_VERSION
ARG COMFYUI_REPO
ARG COMFYUI_GGUF_REPO
ARG VENV
ARG COMFYUI_DIR

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        python3 \
        python3-dev \
        python3-venv \
        build-essential; \
    rm -rf /var/lib/apt/lists/*

# Virtualenv, so the whole Python world is one self-contained directory that the
# runtime stage can take with a single COPY.
ENV PATH="${VENV}/bin:${PATH}"
RUN set -eux; \
    python3 -m venv "${VENV}"; \
    "${VENV}/bin/pip" install --upgrade pip setuptools wheel

# Torch first, and alone. --index-url (not --extra-index-url) pins the ENTIRE
# resolution to the PyTorch channel for this step, so the +cu128 local-version
# wheels win and the CPU-only PyPI builds can never shadow them.
#
# The constraints file written here is then handed to every later pip run.
# ComfyUI's requirements.txt lists bare torch/torchvision/torchaudio; the
# constraints guarantee that a later resolver pass can never quietly swap the
# ~3 GB CUDA build for a different one.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    set -eux; \
    pip install --index-url "${TORCH_INDEX_URL}" \
        "torch==${TORCH_VERSION}" \
        "torchvision==${TORCHVISION_VERSION}" \
        "torchaudio==${TORCHAUDIO_VERSION}"; \
    { \
        echo "torch==${TORCH_VERSION}"; \
        echo "torchvision==${TORCHVISION_VERSION}"; \
        echo "torchaudio==${TORCHAUDIO_VERSION}"; \
    } > /opt/torch-constraints.txt; \
    python3 -c "import torch, torchvision, torchaudio; print(torch.__version__, torchvision.__version__, torchaudio.__version__)"

# ComfyUI at the pinned tag. Shallow clone, then .git is dropped: the release
# tarball carries comfyui_version.py, so ComfyUI still reports its own version
# without the repository metadata.
RUN set -eux; \
    git clone --depth 1 --branch "${COMFYUI_VERSION}" "${COMFYUI_REPO}" "${COMFYUI_DIR}"; \
    rm -rf "${COMFYUI_DIR}/.git"; \
    grep -q '__version__' "${COMFYUI_DIR}/comfyui_version.py"

# ComfyUI-GGUF at the pinned commit. init+fetch rather than clone+checkout so a
# single object is transferred instead of the full history.
RUN set -eux; \
    mkdir -p "${COMFYUI_DIR}/custom_nodes/ComfyUI-GGUF"; \
    cd "${COMFYUI_DIR}/custom_nodes/ComfyUI-GGUF"; \
    git init -q .; \
    git remote add origin "${COMFYUI_GGUF_REPO}"; \
    git fetch -q --depth 1 origin "${COMFYUI_GGUF_COMMIT}"; \
    git checkout -q FETCH_HEAD; \
    test "$(git rev-parse HEAD)" = "${COMFYUI_GGUF_COMMIT}"; \
    rm -rf .git

# Both dependency sets, under the torch constraints. Installed in one resolver
# pass so pip reconciles ComfyUI's and ComfyUI-GGUF's shared pins (sentencepiece,
# protobuf) instead of thrashing them against each other.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    set -eux; \
    pip install \
        -c /opt/torch-constraints.txt \
        -r "${COMFYUI_DIR}/requirements.txt" \
        -r "${COMFYUI_DIR}/custom_nodes/ComfyUI-GGUF/requirements.txt" \
        "gguf==${GGUF_VERSION}"; \
    pip check

# Runtime directories:
#   - models / output / input / user are REPLACED by hostPath volume mounts on
#     Talos and exist here only so the mount targets are present and a plain
#     `docker run` with no volumes still starts. Anything written into them at
#     build time would be masked at runtime, which is exactly why no weights are
#     staged
#   - temp is deliberately NOT a mount target: H3 scratch latents are large and
#     short-lived, so an emptyDir (or the container layer) is the right home
#
# See README.md.
RUN set -eux; \
    mkdir -p \
        "${COMFYUI_DIR}/models" \
        "${COMFYUI_DIR}/output" \
        "${COMFYUI_DIR}/input" \
        "${COMFYUI_DIR}/user" \
        "${COMFYUI_DIR}/temp"

# Byte-compile ahead of time. ComfyUI + torch is ~40k source files; paying the
# compile cost here rather than on first request keeps container start inside
# the readiness budget and lets the runtime stage run with a read-only-friendly
# PYTHONDONTWRITEBYTECODE. Non-zero exit is expected on a handful of vendored
# py2/template files, hence the `|| true`.
RUN set -eux; \
    python3 -m compileall -q -j 0 "${COMFYUI_DIR}" "${VENV}/lib" || true

# Trim what only a build needs. __pycache__ is deliberately KEPT - it is the
# whole point of the compileall above. The nvidia-* wheels, however, ship static
# archives (libcublas_static.a and friends) that nothing can consume without a
# compiler, and this image has none; dropping them is a multi-hundred-megabyte
# win for zero runtime cost. pip's dist-info is kept so `pip check` and SBOM
# scanners can still read the dependency graph.
RUN set -eux; \
    find "${VENV}" -type f -name "*.a" -delete; \
    rm -rf "${VENV}/share/man" "${VENV}/share/doc"


# Runtime stage: same base as the builder so the glibc/Python ABI the venv was
# linked against is byte-identical. Carries no git, no compiler, no headers.
FROM ${CUDA_BASE} AS runtime

ARG VENV
ARG COMFYUI_DIR

ENV DEBIAN_FRONTEND=noninteractive

# Runtime OS surface, deliberately small:
#   - python3: the interpreter the venv symlinks to (the venv carries no binary)
#   - libgomp1: OpenMP runtime that torch's CPU kernels link against
#   - libgl1 / libglib2.0-0t64: X-less GL + glib stubs; ComfyUI's optional image
#     stack (PyOpenGL, spandrel) imports these at node-load time, and a missing
#     libGL.so.1 surfaces as a whole node pack silently failing to register
#   - tini: see ENTRYPOINT; ComfyUI installs a SIGINT handler but no SIGTERM
#     handler, so as PID 1 it would IGNORE SIGTERM and every pod deletion would
#     stall to the full grace period before SIGKILL
#
# NOTE: the package is libglib2.0-0t64, NOT libglib2.0-0. Ubuntu 24.04's 64-bit
# time_t transition renamed it and left NO transitional package behind, so the
# pre-noble name fails the build outright with "unable to locate package". Do
# not "correct" this back. It must be revisited if the base ever moves to a
# release where the t64 suffix has been retired.
#
# ffmpeg(1) is intentionally absent: ComfyUI's video/audio muxing goes through
# PyAV, which vendors its own FFmpeg shared libraries.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        libgl1 \
        libglib2.0-0t64 \
        libgomp1 \
        python3 \
        tini; \
    rm -rf /var/lib/apt/lists/*

# The two artefacts the builder produced.
COPY --from=builder ${VENV} ${VENV}
COPY --from=builder ${COMFYUI_DIR} ${COMFYUI_DIR}

# Non-secret runtime settings:
#   - the venv ahead of the system interpreter on PATH
#   - unbuffered stdio so log lines reach `docker logs` immediately
#   - no .pyc clutter; the builder already byte-compiled everything
#   - TORCH_CUDA_ARCH_LIST=8.6 (Ampere), consulted only when something JITs
#     (torch.compile, Triton); setting it stops those paths from compiling for
#     every arch torch knows about
#   - HF_HOME on the persistent user volume, so a transformers/tokenizers fetch
#     survives a pod restart instead of landing in the writable container layer
#   - `video` added to the stock compute,utility so NVENC/NVDEC stay reachable
#     for anyone who later swaps PyAV's CPU encode for a hardware one
#
# NVIDIA_VISIBLE_DEVICES is deliberately NOT set: on Kubernetes the device
# plugin injects it per container, and a baked-in value only muddies that
# contract.
ENV PATH="${VENV}/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TORCH_CUDA_ARCH_LIST="8.6" \
    HF_HOME="${COMFYUI_DIR}/user/.cache/huggingface" \
    NVIDIA_DRIVER_CAPABILITIES="compute,utility,video"

WORKDIR ${COMFYUI_DIR}

EXPOSE 8188

# Not setting up USER since the hostPath mounts this image consumes are
# root-owned on Talos; see the header and README.md.
#
# Not setting up HEALTHCHECK since the Kubernetes liveness/readiness probes own
# that signal, and a duplicate in-image check just burns a GPU-adjacent process
# on every interval.

# tini reaps zombies and, critically, forwards SIGTERM to ComfyUI (which as PID 1
# would otherwise ignore it - see the tini note above). ComfyUI's main.py is
# still the ENTRYPOINT proper; CMD holds defaults that a Kubernetes `args:` list
# replaces wholesale.
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/venv/bin/python3", "-u", "/opt/comfyui/main.py"]

CMD ["--listen", "0.0.0.0", "--port", "8188", "--disable-smart-memory"]
