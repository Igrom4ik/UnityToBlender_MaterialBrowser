"""Read the shader interface out of a `.shadergraph` file.

The format is a stream of JSON objects. Only property objects are inspected;
the graph itself is never interpreted. Reference names matter here: when an
author leaves `m_OverrideReferenceName` empty, Unity stores the generated
default name in the material, so that is what must be matched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .interface import (
    COLOR,
    CONFIDENCE_FULL,
    CONFIDENCE_PARTIAL,
    FLOAT,
    HDR,
    HIDDEN,
    INT,
    RANGE,
    ShaderInterface,
    ShaderProperty,
    TEXTURE,
    TOGGLE,
    VECTOR,
    WORKFLOW_METALLIC,
    WORKFLOW_SPECULAR,
    WORKFLOW_UNKNOWN,
    WORKFLOW_UNLIT,
)


BACKEND = "shadergraph"
EXTENSIONS = {".shadergraph"}

_TEXTURE_TYPES = {
    "Texture2DShaderProperty",
    "Texture2DArrayShaderProperty",
    "Texture3DShaderProperty",
    "CubemapShaderProperty",
    "VirtualTextureShaderProperty",
}
_VECTOR_TYPES = {
    "Vector2ShaderProperty",
    "Vector3ShaderProperty",
    "Vector4ShaderProperty",
}


def _iter_objects(text: str):
    """Decode the concatenated JSON objects that make up a graph file."""
    decoder = json.JSONDecoder()
    index = 0
    length = len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            return
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError:
            return
        if isinstance(value, dict):
            yield value
        index = end


def _short_type(raw: str) -> str:
    return raw.rsplit(".", 1)[-1] if raw else ""


def _reference_name(item: dict) -> str:
    override = (item.get("m_OverrideReferenceName") or "").strip()
    if override:
        return override
    default = (item.get("m_DefaultReferenceName") or "").strip()
    if default:
        return default
    return (item.get("m_Name") or "").strip()


def _color_default(value) -> tuple[float, float, float, float]:
    if isinstance(value, dict):
        return (
            float(value.get("r", 0.0)),
            float(value.get("g", 0.0)),
            float(value.get("b", 0.0)),
            float(value.get("a", 1.0)),
        )
    return (0.0, 0.0, 0.0, 1.0)


def _vector_default(value) -> tuple[float, float, float, float]:
    if isinstance(value, dict):
        return (
            float(value.get("x", 0.0)),
            float(value.get("y", 0.0)),
            float(value.get("z", 0.0)),
            float(value.get("w", 0.0)),
        )
    return (0.0, 0.0, 0.0, 0.0)


def _property_from_object(item: dict) -> ShaderProperty | None:
    short_type = _short_type(item.get("m_Type", ""))
    if not short_type.endswith("ShaderProperty"):
        return None
    name = _reference_name(item)
    if not name:
        return None

    display = (item.get("m_Name") or name).strip()
    attributes: list[str] = []
    if item.get("m_Hidden"):
        attributes.append(HIDDEN)

    property_type = FLOAT
    default: object = item.get("m_Value")
    bounds = None

    if short_type in _TEXTURE_TYPES:
        property_type = TEXTURE
        default = ""
    elif short_type == "ColorShaderProperty":
        property_type = COLOR
        default = _color_default(item.get("m_Value"))
        if item.get("m_ColorMode") == 1:
            attributes.append(HDR)
    elif short_type in _VECTOR_TYPES:
        property_type = VECTOR
        default = _vector_default(item.get("m_Value"))
    elif short_type == "BooleanShaderProperty":
        property_type = FLOAT
        attributes.append(TOGGLE)
        default = 1.0 if item.get("m_Value") else 0.0
    elif short_type == "Vector1ShaderProperty":
        float_type = item.get("m_FloatType", 0)
        raw_value = item.get("m_Value", 0.0)
        if float_type == 1:
            property_type = RANGE
            range_values = item.get("m_RangeValues") or {}
            bounds = (
                float(range_values.get("x", 0.0)),
                float(range_values.get("y", 1.0)),
            )
            default = float(raw_value or 0.0)
        elif float_type == 2:
            property_type = INT
            default = int(raw_value or 0)
        else:
            default = float(raw_value or 0.0)
    else:
        default = item.get("m_Value")

    return ShaderProperty(
        name=name,
        display=display,
        type=property_type,
        default=default,
        attributes=tuple(attributes),
        range=bounds,
    )


def _detect_workflow(objects: list[dict]) -> str:
    types = {_short_type(item.get("m_Type", "")) for item in objects}
    if any("UnlitSubTarget" in name for name in types):
        return WORKFLOW_UNLIT
    for item in objects:
        if "LitSubTarget" in _short_type(item.get("m_Type", "")):
            # Universal Lit stores 1 for specular setup, 0 for metallic.
            return WORKFLOW_SPECULAR if item.get("m_WorkflowMode") == 1 else WORKFLOW_METALLIC
    if any("LitSubTarget" in name for name in types):
        return WORKFLOW_METALLIC
    return WORKFLOW_UNKNOWN


def parse_text(text: str, source_path: Path | None = None) -> ShaderInterface:
    objects = list(_iter_objects(text))
    graph = next(
        (item for item in objects if _short_type(item.get("m_Type", "")) == "GraphData"),
        None,
    )

    by_id = {item.get("m_ObjectId"): item for item in objects if item.get("m_ObjectId")}
    ordered: list[dict] = []
    if graph is not None:
        for reference in graph.get("m_Properties", []):
            item = by_id.get(reference.get("m_Id"))
            if item is not None:
                ordered.append(item)
    if not ordered:
        ordered = [
            item
            for item in objects
            if _short_type(item.get("m_Type", "")).endswith("ShaderProperty")
        ]

    properties = tuple(
        item
        for item in (_property_from_object(entry) for entry in ordered)
        if item is not None
    )

    notes: list[str] = []
    confidence = CONFIDENCE_FULL
    if graph is None:
        notes.append("GraphData object was not found")
        confidence = CONFIDENCE_PARTIAL
    elif graph.get("m_SGVersion", 0) > 3:
        notes.append(f"Unknown Shader Graph version {graph.get('m_SGVersion')}")
        confidence = CONFIDENCE_PARTIAL
    if not properties:
        notes.append("No shader properties were found")
        confidence = CONFIDENCE_PARTIAL

    name = source_path.stem if source_path else ""
    return ShaderInterface(
        name=name,
        backend=BACKEND,
        workflow=_detect_workflow(objects),
        confidence=confidence,
        properties=properties,
        source_path=source_path,
        source_hash=hashlib.sha1(text.encode("utf-8", "replace")).hexdigest(),
        notes=tuple(notes),
    )


def parse(path: str | Path) -> ShaderInterface:
    source = Path(path)
    text = source.read_text(encoding="utf-8", errors="replace")
    return parse_text(text, source)
