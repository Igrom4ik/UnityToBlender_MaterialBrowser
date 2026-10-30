"""Import the pipeline core from wherever this copy of it lives.

A Blender extension has to be self contained, so packaging vendors the core
into `_vendor/` next to this file, and that folder goes on `sys.path`. The
repository checkout has no such copy and the core is an ordinary sibling
package. Both spellings resolve here, once, so no other module has to care
which one it got -- or whether what it got is Python source or a compiled
module.

Vendoring it under its own name rather than as a submodule is not a style
choice. A compiled extension carries its full name inside it: the initialiser
of `unity_pipeline_core` is literally `PyInit_unity_pipeline_core`, so the same
binary placed at `unity_material_drop/core/` is asked for `PyInit_core` and
refuses to load. The name has to survive packaging, and keeping the folder is
the only way to keep it.
"""

from __future__ import annotations

from pathlib import Path
import sys

from ._vendor_compat import ensure_compatible_core


_VENDOR = Path(__file__).resolve().parent / "_vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from unity_pipeline_core import (  # noqa: E402
    converter,
    fast_index,
    material_json,
    profiles,
    texture_meta,
    unity_library,
)
from unity_pipeline_core.shader_parser import PARSER_VERSION, ShaderResolver  # noqa: E402


if _VENDOR.is_dir():
    # Sibling add-ons deliberately ship the same compiled package. Reusing an
    # already loaded copy is safe only when the complete distributions match.
    import unity_pipeline_core as _loaded_core

    ensure_compatible_core(_VENDOR, _loaded_core)


__all__ = [
    "PARSER_VERSION",
    "ShaderResolver",
    "converter",
    "fast_index",
    "material_json",
    "profiles",
    "texture_meta",
    "unity_library",
]
