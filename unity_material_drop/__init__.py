"""Unity Material Drop.

Drag a Unity `.mat` from the file browser onto an object and Blender builds the
node tree from the real Unity data -- shader interface, texture import settings,
channel packing -- and assigns it, in one gesture.

Its sibling add-on `unity_material_browser` builds a whole asset library up
front; this one converts exactly the material you are pointing at, right now,
and never writes anything into the Unity project.

Nothing is imported at module level on purpose: the pure Python half
(`fast_index`, `converter`, `session`) has to be importable by the tests without
Blender, and `register()` is the only thing that needs bpy.
"""

from __future__ import annotations

from importlib import import_module


__version__ = "0.5.3"

_MODULES = ("properties", "operators", "drop", "tiling", "vertex_paint", "ui")


def _module(name):
    return import_module(f".{name}", __package__)


def register():
    registered = []
    try:
        for name in _MODULES:
            module = _module(name)
            module.register()
            registered.append(module)
    except Exception:
        for module in reversed(registered):
            try:
                module.unregister()
            except Exception:
                pass
        raise


def unregister():
    for name in reversed(_MODULES):
        try:
            _module(name).unregister()
        except Exception:
            pass
    try:
        _module("session").forget_all()
    except Exception:
        pass
