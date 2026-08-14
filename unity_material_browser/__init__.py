"""Unity Material Browser.

Reads Unity materials, shaders and texture import settings, writes a JSON
document per material, and builds a Blender asset library from those documents.
The Unity project is only ever read.
"""

from __future__ import annotations

__version__ = "0.12.0"

import bpy

from . import operators, properties, ui


_MODULES = (properties, operators, ui)


def _restore_state():
    """Read what is already on disk once Blender has finished starting up."""
    try:
        operators.restore_state_from_disk()
    except Exception:
        pass
    return None


def register():
    registered = []
    try:
        for module in _MODULES:
            module.register()
            registered.append(module)
    except Exception:
        for module in reversed(registered):
            try:
                module.unregister()
            except Exception:
                pass
        raise

    if not bpy.app.background:
        bpy.app.timers.register(_restore_state, first_interval=0.5)


def unregister():
    if bpy.app.timers.is_registered(_restore_state):
        bpy.app.timers.unregister(_restore_state)
    for module in reversed(_MODULES):
        try:
            module.unregister()
        except Exception:
            pass
