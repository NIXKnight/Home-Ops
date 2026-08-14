"""Build-time sanity gate for this image, run by the Dockerfile's gate RUN.

GPU-free by design: every check below is metadata, import or dlopen work that a
builder with no device can do. The script is bind-mounted from the build context
for that one RUN and is absent from every layer of the finished image; the pins
it checks against arrive through os.environ.
"""

import ctypes
import importlib
import json
import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution, entry_points, version
from pathlib import Path

GROUP = "vllm.general_plugins"
NAME = "gguf"
TARGET = "vllm_gguf_plugin:register"

BNB_NAME = "bitsandbytes"
BNB_TARGET = "vllm_bnb_plugin:register"

# vLLM registers this name itself; the lmcache import is lazy inside it.
LMCACHE_CONNECTOR = "LMCacheConnectorV1"
LMCACHE_CONNECTOR_MODULE = "vllm.distributed.kv_transfer.kv_connector.v1.lmcache_connector"
LMCACHE_IMPL_MODULE = "lmcache.integration.vllm.vllm_v1_adapter"
LMCACHE_IMPL_ATTR = "LMCacheConnectorV1Impl"

def fail(msg):
    sys.exit(f"SANITY CHECK FAILED: {msg}")

# packaging is a hard vLLM dependency; its absence means this is not the image
# this file was written against, so it fails the gate rather than degrading it.
try:
    from packaging.requirements import Requirement
    from packaging.version import InvalidVersion, Version
except ImportError as exc:
    fail(f"packaging is not importable ({exc}); vLLM depends on it, so this image is not what it claims to be")


def audit_requires(dist, label):
    """Prove a --no-deps install left nothing unsatisfied.

    Full specifier checking with markers evaluated, so extras and
    platform-conditional requirements are skipped rather than demanded.
    """
    unsatisfied = []
    for raw in dist.requires or []:
        req = Requirement(raw)
        if req.marker is not None and not req.marker.evaluate():
            continue
        name, spec = req.name, req.specifier
        try:
            have = version(name)
        except PackageNotFoundError:
            unsatisfied.append(f"{name} (not installed)")
            continue
        if spec and not spec.contains(have, prereleases=True):
            unsatisfied.append(f"{name} {have} does not satisfy {spec}")
        else:
            print(f"dependency OK: {label} needs {name}, has {have}")
    if unsatisfied:
        fail(f"unsatisfied {label} dependencies: " + "; ".join(unsatisfied))


EXPECTED_VLLM = os.environ.get("VLLM_VERSION", "").strip()
EXPECTED_PLUGIN = os.environ.get("PLUGIN_VERSION", "").strip()
EXPECTED_BNB = os.environ.get("BNB_PLUGIN_VERSION", "").strip()
EXPECTED_LMCACHE = os.environ.get("LMCACHE_VERSION", "").strip()
EXPECTED_TORCH = os.environ.get("TORCH_VERSION", "").strip()
if not all((EXPECTED_VLLM, EXPECTED_PLUGIN, EXPECTED_BNB, EXPECTED_LMCACHE, EXPECTED_TORCH)):
    fail("the version pins were not all passed into this gate; the pin coherence checks cannot run")

# This script loads both plugins itself, by hand, in an order check 6 depends
# on: vLLM's in-tree bitsandbytes has to still own the registry at the moment
# the plugin's entry point is resolved. vLLM's own autoloader would get there
# first and make that observation meaningless, so it is switched off for this
# process. Unset means "load every plugin discovered"; the empty string parses
# to [""], an allowlist matching no plugin name - vllm/envs.py says "if this is
# set to an empty string, no plugins will be loaded" and vllm/plugins/__init__
# repeats it. Asserted rather than assumed, because a gate that silently lost
# this would still pass while testing something other than what it claims.
if os.environ.get("VLLM_PLUGINS") != "":
    fail(
        "VLLM_PLUGINS must reach this gate as the empty string so vLLM's "
        "autoloader stays out of it; got "
        f"{os.environ.get('VLLM_PLUGINS')!r}. The check 7 subprocess drops it "
        "again to exercise the autoloading path a real serve takes."
    )

# 1. Entry point metadata. Pure metadata - nothing is imported yet.
try:
    dist = distribution("vllm-gguf-plugin")
except PackageNotFoundError:
    fail("vllm-gguf-plugin is not installed")
print(f"vllm-gguf-plugin {dist.version} installed")

# Installed versions must match the pins the published tag is composed from.
try:
    vllm_installed = version("vllm")
except PackageNotFoundError:
    fail("vllm is not installed")
try:
    want_vllm = Version(EXPECTED_VLLM.removeprefix("v"))
    have_vllm = Version(vllm_installed)
    want_plugin = Version(EXPECTED_PLUGIN)
    have_plugin = Version(dist.version)
except InvalidVersion as exc:
    fail(f"unparseable version in the pin/metadata pair: {exc}")

# .public drops only the local segment, so 0.27.1+cu130 matches a v0.27.1 pin.
if have_vllm.public != want_vllm.public:
    fail(
        f"installed vllm {vllm_installed} does not match the VLLM_VERSION pin "
        f"{EXPECTED_VLLM!r}; VLLM_IMAGE and VLLM_VERSION have drifted apart"
    )
print(f"pin OK: vllm {vllm_installed} matches VLLM_VERSION {EXPECTED_VLLM}")

# torch is the pin nothing else here would catch. LMCache's bundled extensions
# link libtorch and libc10 directly, and bitsandbytes picks its CUDA binary to
# match; a torch swapped out from under them leaves both importable and broken.
# Same .public comparison, for the same +cu130 local segment.
try:
    torch_installed = version("torch")
except PackageNotFoundError:
    fail("torch is not installed")
try:
    want_torch = Version(EXPECTED_TORCH)
    have_torch = Version(torch_installed)
except InvalidVersion as exc:
    fail(f"unparseable torch version in the pin/metadata pair: {exc}")
if have_torch.public != want_torch.public:
    fail(
        f"installed torch {torch_installed} does not match the TORCH_VERSION "
        f"pin {EXPECTED_TORCH!r}; something in this file replaced the base "
        "image's torch"
    )
print(f"pin OK: torch {torch_installed} matches TORCH_VERSION {EXPECTED_TORCH}")

# .base_version is a prefix match anchored at a component boundary: it accepts
# the wheel's stamped suffixes but not "0.0.55" against a "0.0.5" pin.
if have_plugin.base_version != want_plugin.base_version:
    fail(
        f"installed vllm-gguf-plugin {dist.version} does not match the "
        f"PLUGIN_VERSION pin {EXPECTED_PLUGIN!r}"
    )
print(f"pin OK: vllm-gguf-plugin {dist.version} matches PLUGIN_VERSION {EXPECTED_PLUGIN}")

eps = [ep for ep in entry_points(group=GROUP) if ep.name == NAME]
if not eps:
    fail(f"no {NAME!r} entry point in group {GROUP!r}; vLLM will never load the plugin")
ep = eps[0]
if ep.value != TARGET:
    fail(f"entry point target is {ep.value!r}, expected {TARGET!r}")
print(f"entry point OK: [{GROUP}] {ep.name} = {ep.value}")

# 2. Declared dependencies are satisfied despite the --no-deps install;
#    packaging was proven present above.
audit_requires(dist, "vllm-gguf-plugin")

# 3. The entry point resolves, importing the plugin against THIS vLLM, so
#    upstream API drift surfaces here as a build failure.
register = ep.load()
if not callable(register):
    fail(f"entry point target resolved to {type(register).__name__}, not a callable")
print("entry point loaded")

# 4. The compiled CUDA extension imports for real. torch first, so its CUDA
#    libraries are in the process when the extension is dlopened.
import torch  # noqa: E402

print(f"torch {torch.__version__}")
try:
    import vllm_gguf_plugin._C_gguf  # noqa: F401
except ImportError as exc:
    fail(
        "the compiled CUDA extension vllm_gguf_plugin._C_gguf will not import "
        f"({exc}). vLLM would still serve, silently, on the slower Triton "
        "kernels. Rebuild the plugin against this image's torch and CUDA."
    )
from vllm_gguf_plugin import ops  # noqa: E402

cuda_available = getattr(ops, "_CUDA_AVAILABLE", None)
if cuda_available is None:
    fail("vllm_gguf_plugin.ops._CUDA_AVAILABLE is gone; upstream renamed the "
         "CUDA-backend flag and this check needs updating")
if not cuda_available:
    fail("vllm_gguf_plugin.ops reports the CUDA backend unavailable")
print("CUDA extension OK: _C_gguf imported, ops._CUDA_AVAILABLE is True")

# 5. Registration: QUANTIZATION_METHODS, then the get_quantization_config()
#    lookup vLLM runs for --quantization gguf. Any failure there is fatal.
register()
from vllm.model_executor.layers.quantization import (  # noqa: E402
    QUANTIZATION_METHODS,
    get_quantization_config,
)

if NAME not in QUANTIZATION_METHODS:
    fail(f"{NAME!r} is absent from vLLM's QUANTIZATION_METHODS after register()")
print(f"registration OK: {NAME!r} present in QUANTIZATION_METHODS")

try:
    cfg = get_quantization_config(NAME)
except Exception as exc:  # noqa: BLE001
    fail(
        f"get_quantization_config({NAME!r}) raised {type(exc).__name__}: {exc}. "
        "This is the lookup vLLM performs for --quantization gguf, so the "
        "plugin cannot serve a GGUF model in this image."
    )
if cfg.__module__.split(".")[0] != "vllm_gguf_plugin":
    fail(f"{NAME!r} resolves to {cfg.__module__}.{cfg.__name__}, not the plugin's config")
print(f"config class OK: {NAME!r} -> {cfg.__module__}.{cfg.__name__}")

# 6. The bitsandbytes plugin. This one does NOT mirror GGUF: vLLM 0.27.1 still
#    ships an in-tree bitsandbytes, so the name is already in
#    QUANTIZATION_METHODS before any plugin loads and a presence check proves
#    nothing. register_quantization_config and register_model_loader do not
#    reject a duplicate name either - they log and overwrite - so the plugin
#    SHADOWS the in-tree implementation instead of filling a gap. What has to
#    be proven is therefore which side wins, so resolution is read before and
#    after register() and the two must differ.
#
#    Shadowing is also why the Dockerfile patches both copies of the
#    vllm::apply_bnb_4bit registration before this runs. Fork and original
#    define that op into the same torch library, at import time, unguarded, and
#    torch rejects the second define - so the two cannot be imported into one
#    process at all unless the registration is idempotent. get_quantization_config
#    above has already imported the in-tree module (it imports every config
#    module on every call), which makes the entry point load below the second
#    half of that pair and this section the place the collision would surface.
try:
    bnb_dist = distribution("vllm-bnb-plugin")
except PackageNotFoundError:
    fail("vllm-bnb-plugin is not installed")
try:
    want_bnb = Version(EXPECTED_BNB)
    have_bnb = Version(bnb_dist.version)
except InvalidVersion as exc:
    fail(f"unparseable vllm-bnb-plugin version in the pin/metadata pair: {exc}")
if have_bnb.base_version != want_bnb.base_version:
    fail(
        f"installed vllm-bnb-plugin {bnb_dist.version} does not match the "
        f"BNB_PLUGIN_VERSION pin {EXPECTED_BNB!r}"
    )
print(f"pin OK: vllm-bnb-plugin {bnb_dist.version} matches BNB_PLUGIN_VERSION {EXPECTED_BNB}")

bnb_eps = [ep for ep in entry_points(group=GROUP) if ep.name == BNB_NAME]
if not bnb_eps:
    fail(f"no {BNB_NAME!r} entry point in group {GROUP!r}; vLLM will never load the plugin")
bnb_ep = bnb_eps[0]
if bnb_ep.value != BNB_TARGET:
    fail(f"entry point target is {bnb_ep.value!r}, expected {BNB_TARGET!r}")
print(f"entry point OK: [{GROUP}] {bnb_ep.name} = {bnb_ep.value}")

# Same audit as check 2, and the reason BITSANDBYTES_VERSION is pinned by hand:
# this is what proves the wheel's bitsandbytes floor is actually met.
# bitsandbytes itself is audited on the same terms - it too was installed
# --no-deps, and its own torch, numpy and packaging floors are otherwise
# unchecked by anything in this image.
try:
    bnb_lib_dist = distribution("bitsandbytes")
except PackageNotFoundError:
    fail("bitsandbytes is not installed")
audit_requires(bnb_dist, "vllm-bnb-plugin")
audit_requires(bnb_lib_dist, "bitsandbytes")

# Before register(): must be vLLM's own class. This doubles as the assertion
# that in-tree bitsandbytes is still there, so the day upstream removes it the
# build fails and this comment gets rewritten rather than quietly going stale.
if BNB_NAME not in QUANTIZATION_METHODS:
    fail(
        f"{BNB_NAME!r} is absent from QUANTIZATION_METHODS before register(); "
        "vLLM dropped in-tree bitsandbytes and the override check below no "
        "longer describes this image"
    )
before_cfg = get_quantization_config(BNB_NAME)
if before_cfg.__module__.split(".")[0] != "vllm":
    fail(
        f"{BNB_NAME!r} resolves to {before_cfg.__module__}.{before_cfg.__name__} "
        "before register(); expected vLLM's in-tree config"
    )
print(f"in-tree bitsandbytes present: {BNB_NAME!r} -> {before_cfg.__module__}.{before_cfg.__name__}")

# The import that would raise "Tried to register an operator (vllm::
# apply_bnb_4bit) with the same name and overload name multiple times" if the
# Dockerfile's idempotency patch were missing or had stopped matching: the
# in-tree module registered that op a few lines ago, and importing the plugin
# runs the fork's copy of the same registration.
bnb_register = bnb_ep.load()
if not callable(bnb_register):
    fail(f"entry point target resolved to {type(bnb_register).__name__}, not a callable")
print("entry point loaded: plugin imported alongside vLLM's in-tree bitsandbytes")

# One op, one owner. torch caches an OpOverloadPacket per registered op and
# hands the same object back on every lookup, so both modules being bound to
# that one object is what proves a single registration is in force and that
# both implementations dispatch through it. A guard that skipped the define but
# also skipped the binding would leave a module holding something else, or
# nothing, and only fail on the first quantized layer mid-serve.
try:
    import vllm.model_executor.layers.quantization.bitsandbytes as vllm_intree_bnb  # noqa: E402
    from vllm_bnb_plugin.quantization import linear as plugin_bnb_linear  # noqa: E402
except ImportError as exc:
    fail(
        f"one of the two modules that register vllm::apply_bnb_4bit will no "
        f"longer import ({exc}); upstream moved a registration and the "
        "idempotency patch in the Dockerfile needs re-reading"
    )

registered_op = getattr(torch.ops.vllm, "apply_bnb_4bit", None)
if registered_op is None:
    fail(
        "vllm::apply_bnb_4bit is not registered after both bitsandbytes "
        "implementations were imported; the op both sides dispatch 4-bit "
        "matmuls through is missing and neither could serve"
    )
if plugin_bnb_linear.apply_bnb_4bit is not registered_op:
    fail(
        "vllm_bnb_plugin.quantization.linear.apply_bnb_4bit is not the "
        "registered vllm::apply_bnb_4bit op; the plugin's 4-bit path is bound "
        "to something else"
    )
if vllm_intree_bnb.apply_bnb_4bit is not registered_op:
    fail(
        "vllm.model_executor.layers.quantization.bitsandbytes.apply_bnb_4bit "
        "is not the registered vllm::apply_bnb_4bit op; the in-tree module is "
        "bound to something else"
    )
print("custom op OK: vllm::apply_bnb_4bit registered once, both implementations bound to it")

bnb_register()

after_cfg = get_quantization_config(BNB_NAME)
if after_cfg.__module__.split(".")[0] != "vllm_bnb_plugin":
    fail(
        f"after register(), {BNB_NAME!r} still resolves to "
        f"{after_cfg.__module__}.{after_cfg.__name__}; the plugin did not "
        "override the in-tree implementation, and --quantization bitsandbytes "
        "would serve vLLM's copy instead of the plugin's"
    )
print(f"override OK: {BNB_NAME!r} -> {after_cfg.__module__}.{after_cfg.__name__}")

# The plugin replaces the model loader too, which is what --load-format
# bitsandbytes selects. The registry is private, so a rename upstream fails the
# build rather than silently skipping the check.
try:
    from vllm.model_executor.model_loader import (  # noqa: E402
        _LOAD_FORMAT_TO_MODEL_LOADER,
    )
except ImportError:
    fail(
        "vllm.model_executor.model_loader._LOAD_FORMAT_TO_MODEL_LOADER is gone; "
        "upstream renamed the loader registry and this check needs updating"
    )
loader_cls = _LOAD_FORMAT_TO_MODEL_LOADER.get(BNB_NAME)
if loader_cls is None:
    fail(f"no model loader registered for load format {BNB_NAME!r}")
if loader_cls.__module__.split(".")[0] != "vllm_bnb_plugin":
    fail(
        f"load format {BNB_NAME!r} resolves to {loader_cls.__module__}."
        f"{loader_cls.__name__}, not the plugin's loader"
    )
print(f"loader OK: load format {BNB_NAME!r} -> {loader_cls.__module__}.{loader_cls.__name__}")

# Everything above proves which side owns the registry; the kernels themselves
# live in bitsandbytes' own CUDA backend, one .so per CUDA minor picked at
# import time from torch.version.cuda. Nothing here has touched it: importing
# bitsandbytes on this GPU-free builder takes the CPU library instead, because
# its get_cuda_specs() returns None without a visible device, and a failed load
# is caught and replaced by a mock that raises only when a kernel is finally
# called - mid-serve, on the host. The file is therefore selected by the same
# rule bitsandbytes uses, then dlopened directly.
if not torch.version.cuda:
    fail(
        "torch reports no CUDA build, so bitsandbytes would bind its CPU "
        "library; this image is not what it claims to be"
    )
cuda_major, cuda_minor = torch.version.cuda.split(".")[:2]
bnb_cuda_lib = f"libbitsandbytes_cuda{cuda_major}{cuda_minor}.so"
bnb_recorded = bnb_lib_dist.files
if bnb_recorded is None:
    fail("the installed bitsandbytes has no RECORD; its files cannot be enumerated")
bnb_lib_matches = [f for f in bnb_recorded if f.name == bnb_cuda_lib]
if len(bnb_lib_matches) != 1:
    fail(
        f"expected exactly one {bnb_cuda_lib} in bitsandbytes "
        f"{bnb_lib_dist.version}, found {len(bnb_lib_matches)}. This wheel ships "
        f"no backend for the CUDA {torch.version.cuda} torch was built against, "
        "and bitsandbytes would fall back to a neighbouring minor or to nothing "
        "at all, with one log line either way"
    )
bnb_lib_path = Path(bnb_lib_dist.locate_file(bnb_lib_matches[0]))
if not bnb_lib_path.is_file():
    fail(f"{bnb_lib_path} is in the bitsandbytes RECORD but absent from the image")

# torch first, as with _C_gguf above: this links libcudart, libcublas and
# libcublasLt, and resolves them against the copies torch has already loaded.
# ctypes forces RTLD_NOW, so an unresolved symbol fails here rather than at the
# first kernel launch.
try:
    bnb_lib = ctypes.CDLL(str(bnb_lib_path))
except OSError as exc:
    fail(
        f"{bnb_cuda_lib} will not load ({exc}). bitsandbytes swallows this at "
        "import and substitutes a mock, so --quantization bitsandbytes would "
        "load a model and then die on the first quantized layer"
    )
if not hasattr(bnb_lib, "get_context"):
    fail(
        f"{bnb_cuda_lib} exports no get_context; that is the symbol bitsandbytes "
        "reads to decide a library is CUDA-built, so it would treat this one as "
        "CPU-only"
    )
print(f"bitsandbytes backend OK: {bnb_lib_path.name} loads and is CUDA-built")

# 7. The order a serve actually takes. Everything above ran in this script's
#    order - vLLM's in-tree bitsandbytes first, because get_quantization_config
#    imports it, then the plugin - and that is the reverse of what a container
#    does. `vllm serve` calls load_general_plugins() from
#    AsyncEngineArgs.add_cli_args(), while the CLI parser is still being built,
#    so the plugin is imported before any quantization config is resolved and
#    the in-tree module arrives second, inside engine config resolution where
#    nothing catches an exception. The two orders are not the same test: the
#    idempotency patch has to hold in both, and only one of them can be run per
#    interpreter, so this one is a child process.
#
#    It is also the only check here driven by vLLM's own plugin machinery
#    rather than by this script: VLLM_PLUGINS is removed from the child's
#    environment, so load_general_plugins() takes its default "load everything"
#    path exactly as an unconfigured serve does, and the documented
#    VLLM_PLUGINS=gguf,bitsandbytes,lora_filesystem_resolver allowlist is a
#    filtered subset of it. load_plugins_by_group() swallows and logs a failed
#    plugin load, so a plugin that died on import leaves no trace in the exit
#    status - the registry lookups below are what would catch it.
# The child reports through a file rather than stdout: vLLM logs freely there,
# including from threads it starts itself, and a gate that had to find its
# answer in that stream would be one interleaved line away from failing a good
# image. sys.argv[1] under `python3 -c` is the first argument after the script.
RUNTIME_ORDER_PROBE = """
import json
import sys

from vllm.plugins import load_general_plugins

load_general_plugins()

from vllm.model_executor.layers.quantization import get_quantization_config
from vllm.model_executor.model_loader import _LOAD_FORMAT_TO_MODEL_LOADER

bnb_cfg = get_quantization_config("bitsandbytes")
gguf_cfg = get_quantization_config("gguf")
loader = _LOAD_FORMAT_TO_MODEL_LOADER.get("bitsandbytes")

with open(sys.argv[1], "w") as handle:
    json.dump(
        {
            "bitsandbytes_config": f"{bnb_cfg.__module__}.{bnb_cfg.__name__}",
            "gguf_config": f"{gguf_cfg.__module__}.{gguf_cfg.__name__}",
            "bitsandbytes_loader": (
                None if loader is None else f"{loader.__module__}.{loader.__name__}"
            ),
        },
        handle,
    )
"""

# The child gets a serve's environment, not this gate's: VLLM_PLUGINS goes so
# vLLM autoloads, and the pin variables go with it because they exist only for
# the checks above and VLLM_VERSION would otherwise sit in vLLM's own
# VLLM_-prefixed namespace being reported as unrecognised.
GATE_ONLY_ENV = frozenset(
    (
        "VLLM_PLUGINS",
        "VLLM_VERSION",
        "PLUGIN_VERSION",
        "BNB_PLUGIN_VERSION",
        "LMCACHE_VERSION",
        "TORCH_VERSION",
    )
)
probe_env = {
    key: value for key, value in os.environ.items() if key not in GATE_ONLY_ENV
}
# Written under /tmp, which this RUN's bind mount already occupies, and removed
# before the layer is committed - like sanity_check.py itself, nothing this gate
# creates belongs in the finished image.
probe_report = Path("/tmp/vllm_serve_order_probe.json")
probe = subprocess.run(  # noqa: S603
    [sys.executable, "-c", RUNTIME_ORDER_PROBE, str(probe_report)],
    env=probe_env,
    capture_output=True,
    text=True,
    check=False,
)
if probe.returncode != 0:
    tail = "\n".join((probe.stderr or probe.stdout).strip().splitlines()[-25:])
    fail(
        "a fresh interpreter that autoloads the plugins the way `vllm serve` "
        "does, then resolves a quantization config the way engine startup "
        f"does, exited {probe.returncode}. This is the serve path, so the "
        f"image cannot start a quantized model:\n{tail}"
    )
if not probe_report.is_file():
    fail(
        "the serve-order child exited 0 but wrote no report; it did not reach "
        "the end of the probe"
    )
try:
    probe_result = json.loads(probe_report.read_text())
except ValueError as exc:
    fail(f"the serve-order child's report is not JSON ({exc})")
probe_report.unlink()

for probe_key, probe_owner in (
    ("bitsandbytes_config", "vllm_bnb_plugin"),
    ("bitsandbytes_loader", "vllm_bnb_plugin"),
    ("gguf_config", "vllm_gguf_plugin"),
):
    resolved = probe_result.get(probe_key)
    if not isinstance(resolved, str) or resolved.split(".")[0] != probe_owner:
        fail(
            f"under vLLM's own plugin autoloading, {probe_key} resolves to "
            f"{resolved}, not to {probe_owner}. load_plugins_by_group logs a "
            "failed plugin load and carries on, so this is what a plugin that "
            "died on import looks like from the outside: the server comes up "
            "and serves something else"
        )
print(
    "serve order OK: autoloaded plugins own "
    f"{probe_result['bitsandbytes_config']}, {probe_result['bitsandbytes_loader']} "
    f"and {probe_result['gguf_config']} in a fresh interpreter"
)

# 8. LMCache. No entry point is involved: vLLM's KVConnectorFactory already
#    knows LMCacheConnectorV1, and the lmcache import sits inside that
#    connector's __init__, so a missing dependency surfaces only when an engine
#    is built with --kv-transfer-config, minutes into a serve. Every import on
#    that path is pulled forward to build time here.
try:
    lmcache_installed = version("lmcache")
except PackageNotFoundError:
    fail("lmcache is not installed")
try:
    want_lmcache = Version(EXPECTED_LMCACHE)
    have_lmcache = Version(lmcache_installed)
except InvalidVersion as exc:
    fail(f"unparseable lmcache version in the pin/metadata pair: {exc}")
if have_lmcache.base_version != want_lmcache.base_version:
    fail(
        f"installed lmcache {lmcache_installed} does not match the "
        f"LMCACHE_VERSION pin {EXPECTED_LMCACHE!r}"
    )
print(f"pin OK: lmcache {lmcache_installed} matches LMCACHE_VERSION {EXPECTED_LMCACHE}")

import lmcache  # noqa: E402

# Necessary but nowhere near sufficient. lmcache catches a failed platform bind,
# logs "CLI-only mode" and stays importable with the c_ops attribute simply
# absent, so its absence still means the platform layer never came up at all.
if not hasattr(lmcache, "c_ops"):
    fail(
        "lmcache imported but its c_ops backend never bound; the platform layer "
        "fell back to CLI-only mode and KV offload would be dead at run time"
    )

# The attribute proves nothing about the compiled extension, which is why the
# .so is loaded from disk here instead: lmcache.c_ops is a shim module installed
# by lmcache's __init__ that forwards to whichever DeviceOps instance resolved,
# and CudaDeviceOps.ensure_native() catches the extension's ImportError and
# keeps the pure-torch fallback - so the attribute is present and identical
# whether the native half loaded or died. dlopen with ctypes forces RTLD_NOW,
# so an unresolved symbol fails the build with the loader's own error. torch was
# imported at check 4 and has to be: the extension links libtorch, libc10_cuda
# and libcudart.so.13 with no RUNPATH, and resolves them only against a torch
# already in the process.
pkg_dir = Path(lmcache.__file__).parent
c_ops_matches = sorted(pkg_dir.glob("c_ops*.so"))
if len(c_ops_matches) != 1:
    fail(
        f"expected exactly one c_ops*.so in {pkg_dir}, found "
        f"{[p.name for p in c_ops_matches]}; the wheel the builder compiled no "
        "longer carries exactly one CUDA extension under that name, and this "
        "check is testing something other than what it claims"
    )
c_ops_path = c_ops_matches[0]
try:
    c_ops_lib = ctypes.CDLL(str(c_ops_path))
except OSError as exc:
    fail(
        f"the compiled extension {c_ops_path.name} will not load ({exc}). "
        "lmcache logs one line about it and serves KV offload on its torch "
        "fallback, so this is the only place it can be caught. An undefined "
        "c10:: or at:: symbol here means the extension was compiled against a "
        "different libtorch than this image's, which is the state every wheel "
        "LMCache publishes is in and the reason the builder compiles its own."
    )
if not hasattr(c_ops_lib, "PyInit_c_ops"):
    fail(
        f"{c_ops_path.name} loads but exports no PyInit_c_ops, so it is not an "
        "importable CPython extension for this interpreter"
    )
print(f"lmcache OK: {lmcache_installed}, {c_ops_path.name} loads")

try:
    impl_module = importlib.import_module(LMCACHE_IMPL_MODULE)
except ImportError as exc:
    fail(
        f"{LMCACHE_IMPL_MODULE} will not import ({exc}). This is the module "
        "vLLM imports lazily inside LMCacheConnectorV1.__init__, so without "
        "this check the failure would appear at engine start, not here. "
        "A dependency of the connector path is missing from this image."
    )
impl_cls = getattr(impl_module, LMCACHE_IMPL_ATTR, None)
if impl_cls is None:
    fail(
        f"{LMCACHE_IMPL_MODULE} has no {LMCACHE_IMPL_ATTR}; upstream moved the "
        "connector implementation vLLM imports"
    )
if not isinstance(impl_cls, type):
    fail(
        f"{LMCACHE_IMPL_MODULE}.{LMCACHE_IMPL_ATTR} is a "
        f"{type(impl_cls).__name__}, not the implementation class the connector "
        "instantiates"
    )
print(f"lmcache adapter OK: {LMCACHE_IMPL_MODULE}.{LMCACHE_IMPL_ATTR}")

from vllm.distributed.kv_transfer.kv_connector.factory import (  # noqa: E402
    KVConnectorFactory,
)

try:
    connector_cls = KVConnectorFactory.get_connector_class_by_name(LMCACHE_CONNECTOR)
except Exception as exc:  # noqa: BLE001
    fail(
        f"KVConnectorFactory cannot resolve {LMCACHE_CONNECTOR!r}: "
        f"{type(exc).__name__}: {exc}. This is the name --kv-transfer-config "
        "carries, so KV offload could not be switched on in this image."
    )

# The registry entry is a lazy loader, so the lookup above imported vLLM's shim
# module; it is imported here by name as well and the two classes compared by
# identity, which is what proves the name --kv-transfer-config carries resolves
# to THIS image's connector and not to something else answering to it.
try:
    connector_module = importlib.import_module(LMCACHE_CONNECTOR_MODULE)
except ImportError as exc:
    fail(
        f"{LMCACHE_CONNECTOR_MODULE} will not import ({exc}); this is the module "
        f"KVConnectorFactory loads for {LMCACHE_CONNECTOR!r}"
    )
if getattr(connector_module, LMCACHE_CONNECTOR, None) is not connector_cls:
    fail(
        f"{LMCACHE_CONNECTOR!r} resolved to {connector_cls.__module__}."
        f"{connector_cls.__name__}, not {LMCACHE_CONNECTOR_MODULE}."
        f"{LMCACHE_CONNECTOR}"
    )

# That is as far as this gate goes: LMCacheConnectorV1.__init__ imports the
# adapter module and reads LMCacheConnectorV1Impl off it - both proven above, so
# every import on the constructor path has now run - but constructing one needs
# a populated VllmConfig (model config, tokenizer, KV cache config, a
# kv_transfer_config it asserts on) and the impl it builds allocates pinned
# device memory. Neither is available on a GPU-free builder with no model, so
# the construction itself is left to the first serve. This is the default path;
# "use_native": true in the connector's extra config would take vLLM's own
# vendored adapter instead, which is not what this image installs LMCache for.
print(
    f"connector OK: {LMCACHE_CONNECTOR!r} -> {connector_cls.__module__}."
    f"{connector_cls.__name__}, impl {impl_cls.__module__}.{impl_cls.__name__}"
)

print("sanity check passed: vllm-gguf-plugin, vllm-bnb-plugin, lmcache")
