"""Unity property upload semantics, shared by all Blender shader builders.

Serialized material values remain untouched. Only a transient shader document
contains GPU values. Type and HDR/Gamma flags come from the shader, never names.
"""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import re

VERSION = 1


@lru_cache(maxsize=32)
def _read_settings(path, stamp):
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return "Unknown"
    match = re.search(r"^\s*m_ActiveColorSpace:\s*([01])\s*$", text, re.MULTILINE)
    return ("Linear" if match.group(1) == "1" else "Gamma") if match else "Unknown"


def project_settings(source_path):
    if source_path:
        for parent in Path(source_path).resolve().parents:
            path = parent / "ProjectSettings" / "ProjectSettings.asset"
            try:
                stat = path.stat()
            except OSError:
                continue
            stamp = f"{stat.st_mtime_ns}:{stat.st_size}"
            return {"space": _read_settings(str(path), stamp), "settings_path": str(path), "stamp": stamp}
    return {"space": "Unknown", "settings_path": "", "stamp": ""}


def srgb_to_linear(value):
    value = float(value)
    if value <= .04045:
        return value / 12.92
    if value < 1.0:
        return ((value + .055) / 1.055) ** 2.4
    # Unity extends the transfer curve above 1 with power 2.2.
    return value ** 2.2


def property_value(value, property_type, attributes, space):
    """Match Unity's GPU upload; alpha/W is never gamma transformed."""
    flags = set(attributes)
    convert = (property_type == "COLOR" and "HDR" not in flags) or "GAMMA" in flags
    if space != "Linear" or not convert:
        return value
    if isinstance(value, (tuple, list)):
        return [srgb_to_linear(v) if i < 3 else v for i, v in enumerate(value)]
    return srgb_to_linear(value)


def shader_document(document, interface=None):
    if not document or document.get("_shader_values_version") == VERSION:
        return document
    result = dict(document)
    result["effective"] = effective = deepcopy(document.get("effective", {}))
    state = document.get("source", {}).get("color_space")
    if not state:
        state = project_settings(document.get("source", {}).get("absolute_path", ""))
    space = state.get("space", "Unknown")
    # Old sidecars can still be rebuilt while their original shader is present.
    if interface is None and any("property_type" not in v for v in effective.get("colors", {}).values()):
        source = document.get("shader", {}).get("path")
        if source:
            path = Path(source)
            if not path.is_absolute():
                settings = state.get("settings_path")
                if settings:
                    path = Path(settings).parent.parent / path
            if path.is_file():
                from .shader_parser import parse_file
                interface = parse_file(path)
    known = interface.by_name if interface else {}
    for category in ("colors", "floats"):
        for name, entry in effective.get(category, {}).items():
            prop = known.get(name)
            kind = prop.type if prop else entry.get("property_type", "")
            flags = prop.attributes if prop else entry.get("attributes", ())
            entry["value"] = property_value(entry.get("value"), kind, flags, space)
    result["_shader_values_version"] = VERSION
    return result
