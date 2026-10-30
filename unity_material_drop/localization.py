"""Per-add-on English / Russian switch.

Blender's own translation system is global; this add-on keeps its language a
local preference so it can be used in a project where the rest of Blender stays
in another language.
"""

from __future__ import annotations

import bpy


def preferences():
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except (AttributeError, KeyError):
        return None


def language() -> str:
    prefs = preferences()
    return getattr(prefs, "ui_language", "EN") if prefs else "EN"


def text(english: str, russian: str) -> str:
    return russian if language() == "RU" else english


def property_name(base: str) -> str:
    """Return the localized alias of a property for layout.prop()."""
    return f"{base}_ru" if language() == "RU" else base


class LocalizedDescription:
    """Mixin that swaps an operator tooltip to Russian when requested."""

    tooltip_ru = ""

    @classmethod
    def description(cls, _context, _properties):
        if cls.tooltip_ru and language() == "RU":
            return cls.tooltip_ru
        return cls.bl_description
