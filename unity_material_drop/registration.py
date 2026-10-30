"""Class registration helpers that roll back cleanly on failure."""

from __future__ import annotations

import bpy


def register_classes(classes) -> None:
    registered = []
    try:
        for cls in classes:
            if getattr(cls, "is_registered", False):
                continue
            bpy.utils.register_class(cls)
            registered.append(cls)
    except Exception:
        for cls in reversed(registered):
            try:
                bpy.utils.unregister_class(cls)
            except RuntimeError:
                pass
        raise


def unregister_classes(classes) -> None:
    for cls in reversed(list(classes)):
        if not getattr(cls, "is_registered", False):
            continue
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
