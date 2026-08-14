"""Unity material pipeline core: pure Python, never imports bpy.

Phase A (extract) turns Unity assets into a JSON tree; the Blender add-on adds
phase B on top. Nothing here assumes a project location -- callers pass paths.
"""

from __future__ import annotations

__version__ = "0.12.0"

from . import extract, material_json, profiles, sync, texture_meta, unity_library  # noqa: F401
from .shader_parser import ShaderResolver  # noqa: F401
