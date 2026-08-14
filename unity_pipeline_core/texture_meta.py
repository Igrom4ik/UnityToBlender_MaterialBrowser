"""Read Unity texture import settings from the `.meta` file next to an asset.

This is the only correct source of colour space and map type. Guessing from a
file name (`_N`, `_NMG`) is kept as a last resort, because a project is free to
name maps however it likes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


TYPE_DEFAULT = "default"
TYPE_NORMAL = "normal"
TYPE_EDITOR_GUI = "editor_gui"
TYPE_SPRITE = "sprite"
TYPE_CURSOR = "cursor"
TYPE_COOKIE = "cookie"
TYPE_LIGHTMAP = "lightmap"
TYPE_DIRECTIONAL_LIGHTMAP = "directional_lightmap"
TYPE_SHADOWMASK = "shadowmask"
TYPE_SINGLE_CHANNEL = "single_channel"

_TEXTURE_TYPES = {
    0: TYPE_DEFAULT,
    1: TYPE_NORMAL,
    2: TYPE_EDITOR_GUI,
    3: TYPE_SPRITE,
    4: TYPE_CURSOR,
    5: TYPE_COOKIE,
    6: TYPE_LIGHTMAP,
    7: TYPE_DIRECTIONAL_LIGHTMAP,
    8: TYPE_SHADOWMASK,
    10: TYPE_SINGLE_CHANNEL,
}
_WRAP_MODES = {-1: "repeat", 0: "repeat", 1: "clamp", 2: "mirror", 3: "mirror_once"}

_NORMAL_NAME_HINTS = ("_n", "_nrm", "_norm", "_normal", "_nmg", "_bump")
_NON_COLOR_NAME_HINTS = ("_m", "_mask", "_ao", "_orm", "_mra", "_rough", "_metal", "_height")


@dataclass(frozen=True)
class TextureImportSettings:
    srgb: bool = True
    texture_type: str = TYPE_DEFAULT
    alpha_is_transparency: bool = False
    wrap_mode: str = "repeat"
    aniso: int = 1
    resolved: bool = False
    source: str = "default"

    @property
    def is_normal_map(self) -> bool:
        return self.texture_type == TYPE_NORMAL

    @property
    def colorspace(self) -> str:
        """Blender colour space name; normal maps ignore the sRGB flag."""
        if self.is_normal_map or not self.srgb:
            return "Non-Color"
        return "sRGB"

    def to_data(self) -> dict:
        return {
            "srgb": self.srgb,
            "texture_type": self.texture_type,
            "alpha_is_transparency": self.alpha_is_transparency,
            "wrap_mode": self.wrap_mode,
            "aniso": self.aniso,
            "colorspace": self.colorspace,
            "resolved": self.resolved,
            "source": self.source,
        }

    @classmethod
    def from_data(cls, data: dict) -> "TextureImportSettings":
        return cls(
            srgb=data.get("srgb", True),
            texture_type=data.get("texture_type", TYPE_DEFAULT),
            alpha_is_transparency=data.get("alpha_is_transparency", False),
            wrap_mode=data.get("wrap_mode", "repeat"),
            aniso=data.get("aniso", 1),
            resolved=data.get("resolved", False),
            source=data.get("source", "default"),
        )


def _int_field(text: str, key: str) -> int | None:
    match = re.search(rf"^\s*{re.escape(key)}:\s*(-?\d+)\s*$", text, re.MULTILINE)
    return int(match.group(1)) if match else None


def settings_from_text(text: str) -> TextureImportSettings:
    srgb = _int_field(text, "sRGBTexture")
    texture_type = _int_field(text, "textureType")
    alpha = _int_field(text, "alphaIsTransparency")
    wrap = _int_field(text, "wrapMode")
    aniso = _int_field(text, "aniso")

    return TextureImportSettings(
        srgb=True if srgb is None else bool(srgb),
        texture_type=_TEXTURE_TYPES.get(texture_type or 0, TYPE_DEFAULT),
        alpha_is_transparency=bool(alpha),
        wrap_mode=_WRAP_MODES.get(wrap if wrap is not None else -1, "repeat"),
        aniso=1 if aniso is None else aniso,
        resolved=True,
        source="meta",
    )


def settings_from_name(filename: str) -> TextureImportSettings:
    """Last resort when no .meta file exists: read the name suffix."""
    stem = Path(filename).stem.casefold()
    if any(stem.endswith(hint) for hint in _NORMAL_NAME_HINTS):
        return TextureImportSettings(
            srgb=False, texture_type=TYPE_NORMAL, resolved=False, source="name"
        )
    if any(stem.endswith(hint) for hint in _NON_COLOR_NAME_HINTS):
        return TextureImportSettings(srgb=False, resolved=False, source="name")
    return TextureImportSettings(resolved=False, source="name")


def read_settings(asset_path: str | Path) -> TextureImportSettings:
    path = Path(asset_path)
    meta_path = path.with_name(path.name + ".meta")
    try:
        text = meta_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return settings_from_name(path.name)
    if "TextureImporter:" not in text:
        return settings_from_name(path.name)
    return settings_from_text(text)
