"""Format-independent description of a Unity shader interface.

Backends (ShaderLab, Shader Graph, built-in catalog) all produce the same
`ShaderInterface`, so the converter never learns about shader file formats.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


SCHEMA = 2

TEXTURE = "TEXTURE"
COLOR = "COLOR"
VECTOR = "VECTOR"
FLOAT = "FLOAT"
RANGE = "RANGE"
INT = "INT"

NORMAL = "NORMAL"
HDR = "HDR"
NO_SCALE_OFFSET = "NO_SCALE_OFFSET"
HIDDEN = "HIDDEN"
TOGGLE = "TOGGLE"
GAMMA = "GAMMA"
MAIN_TEXTURE = "MAIN_TEXTURE"
MAIN_COLOR = "MAIN_COLOR"
PER_RENDERER_DATA = "PER_RENDERER_DATA"

WORKFLOW_METALLIC = "metallic"
WORKFLOW_SPECULAR = "specular"
WORKFLOW_UNLIT = "unlit"
WORKFLOW_UNKNOWN = "unknown"

CONFIDENCE_FULL = "full"
CONFIDENCE_PARTIAL = "partial"
CONFIDENCE_NONE = "none"


@dataclass(frozen=True)
class ShaderProperty:
    name: str
    display: str = ""
    type: str = FLOAT
    default: object = None
    attributes: tuple[str, ...] = ()
    range: tuple[float, float] | None = None
    keyword: str = ""
    group: str = ""
    """Inspector heading the property sits under, from `[Header(...)]`.

    Amplify writes `[Header(Albedo)] _Color`, which is the only statement the
    generated shader makes about what a colour property is for.
    """

    @property
    def is_texture(self) -> bool:
        return self.type == TEXTURE

    @property
    def is_hidden(self) -> bool:
        return HIDDEN in self.attributes

    def has(self, attribute: str) -> bool:
        return attribute in self.attributes

    def to_data(self) -> dict:
        return {
            "name": self.name,
            "display": self.display,
            "type": self.type,
            "default": list(self.default) if isinstance(self.default, tuple) else self.default,
            "attributes": list(self.attributes),
            "range": list(self.range) if self.range else None,
            "keyword": self.keyword,
            "group": self.group,
        }

    @classmethod
    def from_data(cls, data: dict) -> "ShaderProperty":
        default = data.get("default")
        if isinstance(default, list):
            default = tuple(default)
        range_value = data.get("range")
        return cls(
            name=data["name"],
            display=data.get("display", ""),
            type=data.get("type", FLOAT),
            default=default,
            attributes=tuple(data.get("attributes", ())),
            range=tuple(range_value) if range_value else None,
            keyword=data.get("keyword", ""),
            group=data.get("group", ""),
        )


@dataclass(frozen=True)
class ShaderInterface:
    name: str
    backend: str
    workflow: str = WORKFLOW_UNKNOWN
    confidence: str = CONFIDENCE_NONE
    properties: tuple[ShaderProperty, ...] = ()
    keywords: tuple[str, ...] = ()
    source_path: Path | None = None
    source_hash: str = ""
    parser_version: int = SCHEMA
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def by_name(self) -> dict[str, ShaderProperty]:
        return {item.name: item for item in self.properties}

    @property
    def is_resolved(self) -> bool:
        return self.confidence != CONFIDENCE_NONE

    def to_data(self) -> dict:
        return {
            "schema": SCHEMA,
            "name": self.name,
            "backend": self.backend,
            "workflow": self.workflow,
            "confidence": self.confidence,
            "properties": [item.to_data() for item in self.properties],
            "keywords": list(self.keywords),
            "source_path": str(self.source_path) if self.source_path else None,
            "source_hash": self.source_hash,
            "parser_version": self.parser_version,
            "notes": list(self.notes),
        }

    @classmethod
    def from_data(cls, data: dict) -> "ShaderInterface":
        return cls(
            name=data.get("name", ""),
            backend=data.get("backend", "unknown"),
            workflow=data.get("workflow", WORKFLOW_UNKNOWN),
            confidence=data.get("confidence", CONFIDENCE_NONE),
            properties=tuple(
                ShaderProperty.from_data(item) for item in data.get("properties", [])
            ),
            keywords=tuple(data.get("keywords", ())),
            source_path=Path(data["source_path"]) if data.get("source_path") else None,
            source_hash=data.get("source_hash", ""),
            parser_version=data.get("parser_version", SCHEMA),
            notes=tuple(data.get("notes", ())),
        )


def unresolved_interface(name: str, note: str = "") -> ShaderInterface:
    """Interface used when no backend could read the shader."""
    return ShaderInterface(
        name=name,
        backend="material_only",
        workflow=WORKFLOW_UNKNOWN,
        confidence=CONFIDENCE_NONE,
        notes=(note,) if note else (),
    )
