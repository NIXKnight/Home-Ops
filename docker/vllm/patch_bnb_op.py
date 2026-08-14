"""Make the vllm::apply_bnb_4bit custom-op registration idempotent, in place.

vLLM 0.27.1's in-tree bitsandbytes module and vllm-bnb-plugin both define that
op unguarded, so whichever imports second raises - at serve time inside engine
config resolution, where nothing catches it. Each define is wrapped in a hasattr
guard here. Fail-closed: the pre-image must appear exactly once per file, so a
VLLM_IMAGE or BNB_PLUGIN_COMMIT re-pin that moves either block fails the build
instead of silently patching nothing. Bind-mounted for one RUN; in no layer.
"""

import importlib.util
import py_compile
import sys
from pathlib import Path

OLD = '''try:
    direct_register_custom_op(
        op_name="apply_bnb_4bit",
        op_func=_apply_bnb_4bit,
        mutates_args=["out"],
        fake_impl=_apply_bnb_4bit_fake,
        dispatch_key=current_platform.dispatch_key,
    )
    apply_bnb_4bit = torch.ops.vllm.apply_bnb_4bit
'''

NEW = '''try:
    if not hasattr(torch.ops.vllm, "apply_bnb_4bit"):
        direct_register_custom_op(
            op_name="apply_bnb_4bit",
            op_func=_apply_bnb_4bit,
            mutates_args=["out"],
            fake_impl=_apply_bnb_4bit_fake,
            dispatch_key=current_platform.dispatch_key,
        )
    apply_bnb_4bit = torch.ops.vllm.apply_bnb_4bit
'''

# package -> the file inside it that registers vllm::apply_bnb_4bit
TARGETS = {
    "vllm": "model_executor/layers/quantization/bitsandbytes.py",
    "vllm_bnb_plugin": "quantization/linear.py",
}

for package, relative in TARGETS.items():
    # find_spec locates a top-level package without importing it, so nothing
    # here registers the op it is about to make re-registrable.
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        sys.exit(f"FAIL: {package} is not an importable package in this image")
    path = Path(list(spec.submodule_search_locations)[0]) / relative
    if not path.is_file():
        sys.exit(f"FAIL: {path} does not exist; upstream moved the registration")
    source = path.read_text()
    found = source.count(OLD)
    if found != 1:
        sys.exit(
            f"FAIL: expected exactly one unguarded apply_bnb_4bit registration "
            f"in {path}, found {found}. Upstream rewrote the block this patch "
            "targets; re-read it and update this RUN before shipping."
        )
    path.write_text(source.replace(OLD, NEW))
    py_compile.compile(str(path), doraise=True)
    print(f"apply_bnb_4bit registration made idempotent: {path}")
