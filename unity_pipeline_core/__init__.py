"""Unity material pipeline core: pure Python, never imports bpy.

Everything that decides what a Unity material becomes lives here -- reading the
`.mat`, resolving GUIDs, tracing the shader body, and turning all of it into a
`ConversionPlan`. The Blender add-on on top only carries that plan out.

The split is not decoration. This package has no Blender dependency, so it can
be tested without Blender, reused by other DCC tools, and compiled to a native
module without dragging bpy along.
"""

from __future__ import annotations

__version__ = "0.3.0"

from . import (  # noqa: F401
    converter,
    extract,
    fast_index,
    material_json,
    profiles,
    sync,
    texture_meta,
    unity_library,
)
from .shader_parser import PARSER_VERSION, ShaderResolver  # noqa: F401
