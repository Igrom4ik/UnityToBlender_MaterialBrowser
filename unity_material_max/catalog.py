"""The library as a list of materials, decided without Qt and without Max.

The browser window draws this and nothing else: which materials exist, which
`.mat` each one lives in, where its preview is cached and what a search box
should leave on screen. All of it comes from the index the scan wrote, so the
list is available before 3ds Max has loaded a single library.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from unity_pipeline_core import extract, material_json

from .library_build import LIBRARY_DIR, library_file_name


THUMBNAIL_DIR = "_ump_thumbs"
THUMBNAIL_SIZE = 256


@dataclass(frozen=True)
class MaterialEntry:
    """One material, as the browser needs to know it."""

    name: str
    unity_name: str
    catalog: str
    shader: str
    guid: str
    document: str
    unity_path: str
    library: str
    """File name of the .mat holding it, the same one the browser group has."""

    @property
    def haystack(self) -> str:
        return f"{self.name}\n{self.unity_name}\n{self.catalog}\n{self.shader}".casefold()


def load_entries(library_root: str | Path, name: str = "") -> list:
    """Every material in the index, in the order a person would expect."""
    index = extract.load_index(library_root)
    if not index:
        return []

    entries = []
    for key, item in index.get("materials", {}).items():
        catalog = str(item.get("catalog", ""))
        entries.append(
            MaterialEntry(
                name=str(item.get("asset_name") or item.get("name") or key),
                unity_name=str(item.get("name", "")),
                catalog=catalog,
                shader=str(item.get("shader", "")),
                guid=str(item.get("guid", key)),
                document=str(item.get("json", "")),
                unity_path=str(item.get("unity_path", "")),
                library=library_file_name(catalog, name),
            )
        )
    entries.sort(key=lambda entry: (entry.catalog.casefold(), entry.name.casefold()))
    return entries


def catalogs(entries) -> list:
    return sorted({entry.catalog for entry in entries if entry.catalog})


def search(entries, text: str = "", catalog: str = "") -> list:
    """Filter by every word typed, in any order, and by catalogue."""
    words = [word for word in text.casefold().split() if word]
    result = []
    for entry in entries:
        if catalog and entry.catalog != catalog:
            continue
        haystack = entry.haystack
        if all(word in haystack for word in words):
            result.append(entry)
    return result


def ball_path(library_root: str | Path, entry: MaterialEntry) -> Path:
    """Where a rendered sample sphere is cached, if one was ever rendered."""
    return thumbnail_path(library_root, entry).with_name(
        f"{thumbnail_path(library_root, entry).stem}_ball.png"
    )


def preview_for(library_root: str | Path, entry: MaterialEntry) -> Path | None:
    """The best preview on disk: a rendered ball beats a texture tile."""
    for path in (ball_path(library_root, entry), thumbnail_path(library_root, entry)):
        if path.is_file():
            return path
    return None


def thumbnail_path(library_root: str | Path, entry: MaterialEntry) -> Path:
    """Where the preview of this material is cached.

    Beside the library rather than beside the textures: Unity is only ever
    read, and a cache that lands in `Assets/` is a cache Unity imports.
    """
    stem = entry.guid or entry.name
    return Path(library_root) / THUMBNAIL_DIR / f"{stem}.png"


def preview_source(library_root: str | Path, entry: MaterialEntry) -> str:
    """The texture a preview is made from: the base colour map, if there is one.

    A rendered sample sphere would be truer, but it costs a render per material
    and needs a scene; the albedo is what makes a material recognisable in a
    grid of four hundred.
    """
    document = material_json.read_document(Path(library_root) / entry.document)
    if document is None:
        return ""

    from .recipe import BASE_COLOR_MAP, recipe_from_document  # noqa: PLC0415  (cycle)

    recipe = recipe_from_document(document)
    bitmap = recipe.maps.get(BASE_COLOR_MAP)
    return bitmap.path if bitmap is not None else ""


def preview_color(library_root: str | Path, entry: MaterialEntry):
    """The material's own colour, for a tile with no texture behind it.

    A material of pure scalars still has to be told apart from the one next to
    it, and a blank square in a grid of four hundred reads as a broken tool.
    """
    document = material_json.read_document(Path(library_root) / entry.document)
    if document is None:
        return (0.5, 0.5, 0.5)

    from .recipe import recipe_from_document  # noqa: PLC0415  (cycle)

    return recipe_from_document(document).base_color


def library_path(library_root: str | Path, entry: MaterialEntry) -> Path:
    return Path(library_root) / LIBRARY_DIR / entry.library
