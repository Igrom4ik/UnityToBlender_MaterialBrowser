"""Read the shader interface out of a ShaderLab `.shader` file.

Covers hand written CG/HLSL shaders and generated ones alike: Amplify Shader
Editor emits an ordinary ShaderLab container, so no dedicated backend is needed.
Only the interface is extracted -- the shader body is never interpreted.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .interface import (
    COLOR,
    CONFIDENCE_FULL,
    CONFIDENCE_PARTIAL,
    FLOAT,
    GAMMA,
    HDR,
    HIDDEN,
    INT,
    MAIN_COLOR,
    MAIN_TEXTURE,
    NORMAL,
    NO_SCALE_OFFSET,
    PER_RENDERER_DATA,
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


BACKEND = "shaderlab"
EXTENSIONS = {".shader"}

_SHADER_NAME = re.compile(r'\bShader\s*"(?P<name>[^"]+)"')
_SURFACE_PRAGMA = re.compile(r"#pragma\s+surface\s+\w+\s+(?P<model>\w+)")
_KEYWORD_PRAGMA = re.compile(
    r"#pragma\s+(?:shader_feature|multi_compile)(?:_local)?(?:_fragment|_vertex)?\s+(?P<body>[^\r\n]+)"
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_ATTRIBUTE_MAP = {
    "hideininspector": HIDDEN,
    "normal": NORMAL,
    "hdr": HDR,
    "noscaleoffset": NO_SCALE_OFFSET,
    "gamma": GAMMA,
    "toggle": TOGGLE,
    "toggleui": TOGGLE,
    "toggleoff": TOGGLE,
    "maintexture": MAIN_TEXTURE,
    "maincolor": MAIN_COLOR,
    "perrendererdata": PER_RENDERER_DATA,
}

_TEXTURE_TYPES = {"2d", "3d", "cube", "cubearray", "2darray", "any"}


def _strip_comments(text: str) -> str:
    """Remove // and /* */ comments while respecting string literals."""
    result: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == '"':
            end = index + 1
            while end < length and text[end] != '"':
                if text[end] == "\\":
                    end += 1
                end += 1
            result.append(text[index : end + 1])
            index = end + 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end < 0 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        result.append(character)
        index += 1
    return "".join(result)


def _balanced_block(text: str, start: int, opening: str, closing: str) -> tuple[str, int]:
    """Return the text inside a balanced pair and the index after it."""
    depth = 0
    index = start
    while index < len(text):
        character = text[index]
        if character == '"':
            index += 1
            while index < len(text) and text[index] != '"':
                index += 1
        elif character == opening:
            depth += 1
            if depth == 1:
                start = index
        elif character == closing:
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
        index += 1
    return "", len(text)


def _properties_block(text: str) -> str:
    match = re.search(r"\bProperties\s*\{", text)
    if match is None:
        return ""
    body, _end = _balanced_block(text, match.end() - 1, "{", "}")
    return body


def _parse_attributes(block: str, index: int) -> tuple[list[str], str, str, int]:
    """Collect leading [Attribute] markers, any keyword and the header text."""
    attributes: list[str] = []
    keyword = ""
    group = ""
    while index < len(block):
        while index < len(block) and block[index].isspace():
            index += 1
        if index >= len(block) or block[index] != "[":
            break
        end = block.find("]", index)
        if end < 0:
            return attributes, keyword, group, len(block)
        raw = block[index + 1 : end].strip()
        index = end + 1

        name = raw.split("(", 1)[0].strip().casefold()
        mapped = _ATTRIBUTE_MAP.get(name)
        if mapped is not None and mapped not in attributes:
            attributes.append(mapped)
        if name in {"toggle", "toggleoff"} and "(" in raw:
            keyword = raw.split("(", 1)[1].rstrip(")").strip()
        if name == "header" and "(" in raw:
            group = raw.split("(", 1)[1].rstrip(")").strip()
    return attributes, keyword, group, index


def _parse_type(raw: str) -> tuple[str, tuple[float, float] | None]:
    value = raw.strip()
    lowered = value.casefold()
    if lowered.startswith("range"):
        numbers = re.findall(r"-?\d+(?:\.\d+)?", value)
        bounds = (float(numbers[0]), float(numbers[1])) if len(numbers) >= 2 else None
        return RANGE, bounds
    if lowered in _TEXTURE_TYPES:
        return TEXTURE, None
    if lowered == "color":
        return COLOR, None
    if lowered == "vector":
        return VECTOR, None
    if lowered in {"int", "integer"}:
        return INT, None
    return FLOAT, None


def _parse_default(raw: str, property_type: str):
    value = raw.strip()
    if property_type == TEXTURE:
        quoted = re.search(r'"([^"]*)"', value)
        return quoted.group(1) if quoted else ""
    if property_type in {COLOR, VECTOR}:
        numbers = re.findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", value)
        channels = [float(number) for number in numbers[:4]]
        while len(channels) < 4:
            channels.append(1.0 if property_type == COLOR and len(channels) == 3 else 0.0)
        return tuple(channels)
    numbers = re.findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", value)
    if not numbers:
        return 0
    number = float(numbers[0])
    return int(number) if property_type == INT else number


def parse_properties(block: str) -> tuple[ShaderProperty, ...]:
    """Walk the Properties body entry by entry, skipping anything malformed."""
    properties: list[ShaderProperty] = []
    index = 0
    length = len(block)

    while index < length:
        attributes, keyword, group, index = _parse_attributes(block, index)
        while index < length and block[index].isspace():
            index += 1
        if index >= length:
            break

        name_match = _IDENTIFIER.match(block, index)
        if name_match is None:
            newline = block.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        name = name_match.group(0)
        index = name_match.end()

        while index < length and block[index].isspace():
            index += 1
        if index >= length or block[index] != "(":
            newline = block.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue

        header, index = _balanced_block(block, index, "(", ")")
        display = ""
        display_match = re.match(r'\s*"([^"]*)"\s*,(?P<rest>.*)', header, re.DOTALL)
        if display_match is None:
            type_text = header.split(",", 1)[-1]
        else:
            display = display_match.group(1)
            type_text = display_match.group("rest")
        property_type, bounds = _parse_type(type_text)

        while index < length and block[index] in " \t\r\n":
            index += 1
        default: object = None
        if index < length and block[index] == "=":
            index += 1
            newline = block.find("\n", index)
            end = length if newline < 0 else newline
            default = _parse_default(block[index:end], property_type)
            index = end
            probe = index
            while probe < length and block[probe].isspace():
                probe += 1
            if probe < length and block[probe] == "{":
                _body, index = _balanced_block(block, probe, "{", "}")

        properties.append(
            ShaderProperty(
                name=name,
                display=display,
                type=property_type,
                default=default,
                attributes=tuple(attributes),
                range=bounds,
                keyword=keyword,
                group=group,
            )
        )
    return tuple(properties)


def _detect_workflow(text: str, shader_name: str) -> str:
    surface = _SURFACE_PRAGMA.search(text)
    if surface is not None:
        model = surface.group("model").casefold()
        if model == "standardspecular":
            return WORKFLOW_SPECULAR
        if model == "standard":
            return WORKFLOW_METALLIC
    lowered = shader_name.casefold()
    if "unlit" in lowered:
        return WORKFLOW_UNLIT
    if "specular" in lowered:
        return WORKFLOW_SPECULAR
    if "/lit" in lowered or lowered.endswith("lit"):
        return WORKFLOW_METALLIC
    return WORKFLOW_UNKNOWN


def _collect_keywords(text: str) -> tuple[str, ...]:
    keywords: list[str] = []
    for match in _KEYWORD_PRAGMA.finditer(text):
        for token in match.group("body").split():
            if token in {"__", "_"} or token.startswith("#"):
                continue
            if token not in keywords:
                keywords.append(token)
    return tuple(keywords)


def parse_text(text: str, source_path: Path | None = None) -> ShaderInterface:
    cleaned = _strip_comments(text)
    name_match = _SHADER_NAME.search(cleaned)
    name = name_match.group("name") if name_match else (source_path.stem if source_path else "")

    block = _properties_block(cleaned)
    properties = parse_properties(block) if block else ()
    notes: list[str] = []
    confidence = CONFIDENCE_FULL
    if not name_match:
        notes.append("Shader name declaration was not found")
        confidence = CONFIDENCE_PARTIAL
    if not block:
        notes.append("Properties block was not found")
        confidence = CONFIDENCE_PARTIAL

    return ShaderInterface(
        name=name,
        backend=BACKEND,
        workflow=_detect_workflow(cleaned, name),
        confidence=confidence,
        properties=properties,
        keywords=_collect_keywords(cleaned),
        source_path=source_path,
        source_hash=hashlib.sha1(text.encode("utf-8", "replace")).hexdigest(),
        notes=tuple(notes),
    )


def parse(path: str | Path) -> ShaderInterface:
    source = Path(path)
    text = source.read_text(encoding="utf-8", errors="replace")
    return parse_text(text, source)
