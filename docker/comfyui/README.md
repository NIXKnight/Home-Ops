# comfyui

Container image packaging **ComfyUI** plus the **city96/ComfyUI-GGUF** custom node pack, built to serve the **MiniMax-H3** video + audio generation model from GGUF-quantised weights.
The target is a single Kubernetes pod on Talos with the NVIDIA runtime class, scheduled onto an RTX 3090 (Ampere, sm_86, 24 GB).

GGUF is the point of this image rather than an optional extra.
MiniMax-H3 conditions on Qwen3-VL-32B hidden states, and a 32B text encoder at full precision does not coexist with the H3 DiT in 24 GB of VRAM.
Quantising the text encoder and the DiT through ComfyUI-GGUF is what makes the model fit on this card at all.

## Image

| | |
|---|---|
| Name : tag (integration contract) | `comfyui:v0.30.0` |
| Registry-qualified (matches repo convention) | `<DOCKER_HUB_USERNAME>/comfyui:v0.30.0` (+ `:latest`) |
| Tag convention | the ComfyUI release the image pins, verbatim |
| Base image | `nvidia/cuda:12.8.2-runtime-ubuntu24.04` |
| Python | 3.12 (Ubuntu 24.04 system interpreter, in a venv at `/opt/venv`) |
| Runtime deps | `torch 2.11.0+cu128`, `torchvision 0.26.0+cu128`, `torchaudio 2.11.0+cu128`, `gguf==0.19.0` |
| ComfyUI | `v0.30.0` (released 2026-08-03) at `/opt/comfyui` |
| ComfyUI-GGUF | commit `6ea2651e7df66d7585f6ffee804b20e92fb38b8a` at `/opt/comfyui/custom_nodes/ComfyUI-GGUF` |
| Port | 8188 |
| User | root (deliberate — see [Why root](#why-root)) |
| Entrypoint | `tini -- /opt/venv/bin/python3 -u /opt/comfyui/main.py` |
| Approximate size | ~10 GB uncompressed, dominated by the CUDA base and the bundled `nvidia-*` wheels |

The Kubernetes manifest references the image by name and tag.
`comfyui:v0.30.0` is the contract; the tag is the ComfyUI release the image pins, verbatim, so a tag can never describe a stack the image does not contain.
Bump it on every Dockerfile or pin change, since a moving tag would not redeploy reliably.

Every version above is a `--build-arg`, so any of them can be re-pinned without editing the Dockerfile.

## What is in the image

The ComfyUI server, its full Python dependency set, and a CUDA userspace matched to the PyTorch wheels.

The ComfyUI-GGUF node pack, which contributes `Unet Loader (GGUF)` and `CLIPLoader (GGUF)` under the `bootleg` node category.

Empty `models/`, `output/`, `input/`, `user/` and `temp/` directories under `/opt/comfyui`, present purely as mount targets.

Byte-compiled `.pyc` for both ComfyUI and every installed package, so container start is not spent compiling roughly 40k source files.

`tini` as PID 1.

## What is deliberately excluded

**Model weights.**
No MiniMax-H3 DiT, no Qwen3-VL-32B text encoder, no VAEs, no GGUF files of any kind.
Nothing is fetched from HuggingFace at build time, so the build needs no HF token and stays reproducible and legally clean.
Weights arrive at runtime on the hostPath volume mounted at `/opt/comfyui/models`.

**Secrets.**
No tokens, no credentials, no API keys.
Nothing in the Dockerfile sets, reads, or persists a secret value.

**ComfyUI-Manager.**
A node pack that mutates its own install at runtime is the wrong shape for an immutable image, and it would let a workflow author install arbitrary code into a GPU pod.
Add custom nodes by editing the Dockerfile and rebuilding.

**`git` and the C toolchain.**
Both live only in the builder stage and are discarded.

**`ffmpeg(1)`.**
ComfyUI muxes video and audio through PyAV, which vendors its own FFmpeg shared libraries.
Install the CLI only if a custom node shells out to it.

**A `HEALTHCHECK`.**
Kubernetes liveness and readiness probes own that signal, matching the repo convention of leaving the check to the deployment manifest.

## Build

The build context is this directory and it is intentionally empty of build inputs.
Every artefact is fetched from a pinned upstream, so no file is `COPY`ed in.

```bash
# from the repository root
docker build -t comfyui:v0.30.0 docker/comfyui/
```

No secrets are required, read, or baked in at build time.

Re-pin any component without touching the Dockerfile.

```bash
docker build \
  --build-arg COMFYUI_VERSION=v0.31.0 \
  --build-arg COMFYUI_GGUF_COMMIT=<sha> \
  --build-arg TORCH_VERSION=2.11.0+cu128 \
  -t comfyui:v0.31.0 \
  docker/comfyui/
```

The build is CPU-only and needs no GPU on the builder.
Expect the first build to take a while, since the PyTorch trio alone is several gigabytes; the pip cache mount makes subsequent builds much faster.

## Distribution (how the node gets the image)

Matches the existing custom images (`powerdns`, `kea`, `openbao-unseal`): build once in CI, push to Docker Hub, and let the node **pull** it.
There is no on-node build.

The GitHub Actions workflow `.github/workflows/comfyui-container-image-build.yml` builds and pushes this image, publishing `<DOCKER_HUB_USERNAME>/comfyui:latest` alongside the version tag.
It fires on any change under `docker/comfyui/**` except this README, which cannot affect the resulting image because the build context has no `COPY` sources.

The workflow derives the published tag from the Dockerfile itself:

```bash
sed -n 's/^ARG COMFYUI_VERSION="\(.*\)"$/\1/p' docker/comfyui/Dockerfile | head -n 1
```

> Note: that expression is a contract with the Dockerfile.
> `ARG COMFYUI_VERSION` must stay on one line, double-quoted, with nothing trailing, or the workflow resolves an empty version and fails the run by design.

Unlike the sibling image workflows, this one also carries a `workflow_dispatch` input to build a different ComfyUI tag without an empty commit, a `concurrency` group so two runs cannot race for the same registry tag, a disk-reclaim step before the build, a container-driver buildx builder that exports straight to the registry, and a raised timeout.
All five exist because this image is roughly an order of magnitude larger than every other image in the repo.
No Actions layer cache is wired up, since the torch layers alone would evict everything else from the repository's cache quota.

Manual publish, if CI is not the path:

```bash
docker login
docker tag comfyui:v0.30.0 <DOCKER_HUB_USERNAME>/comfyui:v0.30.0
docker push <DOCKER_HUB_USERNAME>/comfyui:v0.30.0
```

## Runtime contract

`ENTRYPOINT` is `tini -- /opt/venv/bin/python3 -u /opt/comfyui/main.py`.

`CMD` holds `--listen 0.0.0.0 --port 8188 --disable-smart-memory`, which a Kubernetes `args:` list replaces wholesale.

Because `args:` replaces `CMD` entirely rather than appending, the deployment must restate `--listen` and `--port`.
Omitting `--listen` drops ComfyUI back to its `127.0.0.1` default, and the pod then fails every readiness probe while appearing perfectly healthy in its own logs.

### Volumes

| Container path | Purpose |
|---|---|
| `/opt/comfyui/models` | model weights, including all GGUF files |
| `/opt/comfyui/output` | rendered video and audio |
| `/opt/comfyui/input` | source images and audio for reference-conditioned runs |
| `/opt/comfyui/user` | ComfyUI settings, saved workflows, and the HuggingFace cache (`HF_HOME`) |

All four exist as empty directories in the image and are expected to be replaced by hostPath mounts.

`/opt/comfyui/temp` is intentionally *not* in that list.
H3 scratch latents are large and worthless across restarts, so back it with an `emptyDir` rather than persisting it.

### Model layout on the host

ComfyUI-GGUF registers its `unet_gguf` and `clip_gguf` folder keys against ComfyUI's modern directory names, so use these paths.

| Host path under the models volume | Contents |
|---|---|
| `diffusion_models/` | the MiniMax-H3 packed-DiT `.gguf`, loaded by `Unet Loader (GGUF)` |
| `text_encoders/` | the Qwen3-VL-32B `.gguf`, loaded by `CLIPLoader (GGUF)` with type `minimax` |
| `vae/` | the H3 video VAE and audio VAE |

These subdirectories must be created on the host.
ComfyUI tolerates them being absent, but it reports no models rather than an error, which reads as a broken image when it is really an empty volume.

### Environment

Unlike the compose-deployed images in this repo, this one **sets** its runtime variables itself, and the deployment overrides them only if it must.
None of them is a secret, and none needs to be supplied for the image to start.

| Var | Purpose |
|---|---|
| `HF_HOME` | transformers/tokenizers cache, defaulted to `/opt/comfyui/user/.cache/huggingface` so a fetch persists on the user volume instead of the writable container layer |
| `NVIDIA_DRIVER_CAPABILITIES` | `compute,utility,video`, keeping NVENC and NVDEC reachable |
| `NVIDIA_VISIBLE_DEVICES` | deliberately unset, because the Kubernetes device plugin injects it per container |
| `TORCH_CUDA_ARCH_LIST` | `8.6` (Ampere), consulted only when something JIT-compiles |
| `PATH` | the venv at `/opt/venv/bin`, ahead of the system interpreter |
| `PYTHONUNBUFFERED` / `PYTHONDONTWRITEBYTECODE` | immediate log lines, and no `.pyc` writes at runtime since the builder compiled everything already |

## Design notes

### Base image and torch pairing

The container ships its own CUDA userspace and takes only the kernel driver from the host.
The node's driver is far newer than 12.8, which is fine because the CUDA driver is backward compatible with older toolkits.

CUDA 12.8 was chosen because `cu128` is PyTorch's newest *stable* wheel channel in that class.
Matching the base image's CUDA minor version to the wheel's keeps any custom node that `dlopen()`s a system CUDA library ABI-consistent with the libraries torch bundles.
`cu128` builds still emit real sm_86 SASS, so the 3090 runs native kernels instead of stalling on a PTX JIT during the first inference.

Ubuntu 24.04 was chosen for its Python 3.12, which is what the cp312 wheels target.
This is load-bearing for `comfy-kitchen`, whose only Linux x86_64 wheel for Python 3.12 and above is `cp312-abi3`; a 22.04 base running Python 3.10 would silently install the older `cp310` build instead.

The plain `-runtime` variant is used rather than `-cudnn-runtime`, because the torch wheels pull their own pinned `nvidia-cudnn-cu12` and a system cuDNN would be roughly a gigabyte of dead weight and a second, conflicting copy.

### Why ComfyUI v0.30.0 specifically

`v0.30.0` is the first release with native, local-weight MiniMax-H3 support.
It adds `comfy/ldm/minimax/{model,vae,audio_vae}.py`, `comfy/text_encoders/minimax.py`, `CLIPType.MINIMAX` in `comfy/sd.py`, and the `comfy_extras/nodes_minimax_h3.py` node set.

Every earlier tag, `v0.29.2` included, ships only `comfy_api_nodes/nodes_minimax.py`.
That is a hosted-API partner node which calls MiniMax's cloud service and cannot touch local GGUF weights.
Do not downgrade this pin expecting a more settled release, because the local model support is not there.

### Why a pre-H3 ComfyUI-GGUF commit is correct

The pinned ComfyUI-GGUF commit predates MiniMax-H3 by roughly seven months, and upstream has not pushed since.
It still works, for two independently verified reasons.

`CLIPLoaderGGUF` builds its `type` dropdown from `nodes.CLIPLoader.INPUT_TYPES()` and resolves the selection with `getattr(comfy.sd.CLIPType, type.upper())`, so it inherits `minimax` from whatever ComfyUI core it is loaded against rather than from a hardcoded list.

The node pack's `update_folder_names_and_paths` already prefers ComfyUI's modern folder keys, registering `unet_gguf` against `diffusion_models` and `clip_gguf` against `text_encoders`, with the old `unet` and `clip` names only as fallbacks.
ComfyUI core independently maps the legacy names through `folder_paths.map_legacy()`, so both halves of the rename are handled.

### Why root

The Talos hostPath volumes backing `models/`, `output/`, `input/` and `user/` are root-owned, and Talos offers no mechanism to reconcile a hostPath's ownership with an arbitrary container UID.
Running as a non-root user would make every one of those mounts effectively read-only.

The process is a single-tenant inference server behind cluster ingress, not a multi-tenant surface.
Confine it from the pod spec instead, with `allowPrivilegeEscalation: false` and `capabilities: drop: [ALL]`.

### Signal handling

ComfyUI's `main.py` installs a `SIGINT` handler but no `SIGTERM` handler.
As PID 1 a process ignores any signal it has no handler for, so ComfyUI running directly as PID 1 would ignore `SIGTERM` outright and every pod deletion would stall for the full termination grace period before `SIGKILL`.

`tini` is therefore PID 1.
It reaps zombies and forwards `SIGTERM` to ComfyUI as a non-PID-1 child, where the default disposition terminates the process promptly.

### The `libglib2.0-0t64` package name

Ubuntu 24.04's 64-bit `time_t` transition renamed `libglib2.0-0` and left no transitional package behind.
The pre-noble name fails the build outright with "unable to locate package", so the `t64` suffix is mandatory here and must not be "corrected" back.
It has to be revisited only if the base image ever moves to a release where the suffix has been retired.

## Verification after build

The build already asserts a good deal: it checks the resolved torch versions, confirms `comfyui_version.py` is present, verifies the ComfyUI-GGUF checkout matches the requested SHA exactly, and runs `pip check` over the finished dependency graph.

Confirm the GPU path on the target node before wiring up the deployment.

```bash
docker run --rm --gpus all --entrypoint /opt/venv/bin/python3 \
  comfyui:v0.30.0 \
  -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))"
```

Expect `2.11.0+cu128 True NVIDIA GeForce RTX 3090 (8, 6)`.

Confirm the GGUF reader and the pinned node pack are both in place.
Note that the `gguf` package exposes no `__version__` attribute, so the version has to come from the installed distribution metadata.

```bash
docker run --rm --entrypoint /opt/venv/bin/python3 \
  comfyui:v0.30.0 \
  -c "import importlib.metadata as m, gguf, os; \
      print('gguf', m.version('gguf')); \
      print('node pack', os.path.isfile('/opt/comfyui/custom_nodes/ComfyUI-GGUF/nodes.py'))"
```

Confirm ComfyUI core carries the MiniMax-H3 pieces that this whole image exists to run.

```bash
docker run --rm --entrypoint /opt/venv/bin/python3 \
  comfyui:v0.30.0 \
  -c "import sys; sys.path.insert(0, '/opt/comfyui'); \
      from comfy.sd import CLIPType; \
      import comfyui_version; \
      print('comfyui', comfyui_version.__version__, 'MINIMAX', CLIPType.MINIMAX)"
```

## Notes / open questions

- **Location:** this image lives at repo-root `docker/comfyui/`, matching the other custom images (`powerdns`, `kea`, `openbao-unseal`) and the `docker/**` CI trigger.
- **Healthcheck:** left to the deployment (repo convention), since the meaningful check is a Kubernetes probe against port 8188.
- **Reproducibility:** the tag `v0.30.0` is immutable upstream, but `nvidia/cuda:12.8.2-runtime-ubuntu24.04` is not.
  Pin the base by digest via `--build-arg CUDA_BASE=nvidia/cuda:12.8.2-runtime-ubuntu24.04@sha256:...` when a byte-identical rebuild matters; the repo currently pins by tag only.
- **Image size:** far above the sub-100 MB target that applies to ordinary services, and not reachable for any CUDA plus PyTorch image.
  The savings available here were taken instead: multi-stage so no compiler or `git` ships, no system cuDNN, and static archives stripped from the `nvidia-*` wheels.
- **Unvalidated:** the residual risk is the quantisation itself rather than the loader plumbing.
  Whether a given H3 GGUF dequantises correctly depends on the tensor layout of whoever produced it, and this node pack has never been exercised against H3.
  Validate a short generation end to end before trusting the pod.
