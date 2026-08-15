"""Write `.mat` libraries from a JSON tree. Runs inside 3ds Max.

One library per Unity catalogue, mirroring the folder structure: that is what
the Material/Map Browser opens, and it keeps a single file from holding two
thousand materials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Callable

from unity_pipeline_core import extract, material_json, profiles

from .material_builder import build_material
from .recipe import recipe_from_document


LIBRARY_DIR = "maxlib"
REPORT_NAME = "_ump_max_report.json"

# Called with how far along the build is and what it is doing; returning False
# stops it. Two thousand materials take minutes, and a run that cannot be
# stopped holds the whole session hostage.
Progress = Callable[[float, str], bool]


@dataclass
class BuildResult:
    built: int = 0
    failed: int = 0
    libraries: int = 0
    cancelled: bool = False
    notes: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _catalog_libraries(index: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for entry in index.get("materials", {}).values():
        grouped.setdefault(entry.get("catalog", ""), []).append(entry)
    return grouped


def library_file_name(catalog: str, name: str = "") -> str:
    """The file name is what the Material/Map Browser calls the group.

    Without a name of its own a library shows up as "Textures_Surface_Bricks"
    between Materials, Maps and Scene Materials, which is not something anyone
    goes looking for. A name puts every catalogue of one project under the same
    prefix, so they sort together and are recognisable at a glance.
    """
    catalog_part = extract.safe_filename(catalog.replace("/", "_")) if catalog else "root"
    # A character Windows refuses becomes an underscore, so a name ending in
    # one would otherwise meet the separator and read as a typo.
    prefix = extract.safe_filename(name.strip()).strip("_") if name.strip() else ""
    return f"{prefix}_{catalog_part}.mat" if prefix else f"{catalog_part}.mat"


def build_library(
    library_root: str | Path,
    only_guids: set[str] | None = None,
    progress: Progress | None = None,
    name: str = "",
) -> BuildResult:
    """Build every catalogue into `<library>/maxlib/<catalogue>.mat`."""
    from pymxs import runtime  # noqa: PLC0415  (only available inside 3ds Max)

    root = Path(library_root)
    index = extract.load_index(root)
    if not index:
        raise FileNotFoundError(f"No {extract.INDEX_NAME} in {root}: run the extract phase first")

    rules = profiles.load_profiles(None)
    result = BuildResult()
    output_root = root / LIBRARY_DIR
    output_root.mkdir(parents=True, exist_ok=True)

    catalogues = sorted(_catalog_libraries(index).items())
    total = max(1, sum(len(entries) for _catalog, entries in catalogues))
    position = 0

    for catalog, entries in catalogues:
        materials = []
        for entry in entries:
            position += 1
            if only_guids is not None and entry.get("guid", "") not in only_guids:
                continue
            if progress is not None and not progress(position / total, entry.get("name", "")):
                result.cancelled = True
                break
            document = material_json.read_document(root / entry["json"])
            if document is None:
                result.failed += 1
                result.warnings.append(f"Unreadable document: {entry.get('json', '')}")
                continue

            recipe = recipe_from_document(document, rules)
            try:
                materials.append(build_material(recipe))
            except Exception as error:  # keep building the rest of the catalogue
                result.failed += 1
                result.warnings.append(f"{recipe.name}: {error}")
                continue

            result.built += 1
            for note in recipe.notes:
                key = note.split(":", 1)[-1].strip()
                result.notes[key] = result.notes.get(key, 0) + 1

        if result.cancelled:
            # Half a catalogue saved as a whole one is a library that lies
            # about itself; the catalogues finished before this one are on
            # disk already and stay there.
            result.built -= len(materials)
            result.warnings.append(f"{catalog}: stopped before this catalogue was written")
            break

        if not materials:
            continue

        library = runtime.materialLibrary()
        for material in materials:
            runtime.append(library, material)

        destination = output_root / library_file_name(catalog, name)
        if runtime.saveTempMaterialLibrary(library, str(destination)):
            result.libraries += 1
        else:
            result.failed += len(materials)
            result.warnings.append(f"{destination.name}: 3ds Max refused to save the library")

    # A report written after a stopped run would describe a library that was
    # never finished, so the previous one is left alone.
    if not result.cancelled:
        _write_report(root, result, str(index.get("generated_at", "")))
    return result


def _write_report(root: Path, result: BuildResult, scanned_at: str = "") -> None:
    payload = {
        "built": result.built,
        "failed": result.failed,
        "libraries": result.libraries,
        # Which scan this library was built from. A rescan that resolves the
        # shaders changes every material, and a .mat built before it looks
        # exactly like one built after.
        "scanned_at": scanned_at,
        "not_reproduced": sorted(
            ({"note": note, "count": count} for note, count in result.notes.items()),
            key=lambda item: -item["count"],
        )[:50],
        "warnings": result.warnings[:50],
    }
    (root / REPORT_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
