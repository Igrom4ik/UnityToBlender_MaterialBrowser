"""Write `.mat` libraries from a JSON tree. Runs inside 3ds Max.

One library per Unity catalogue, mirroring the folder structure: that is what
the Material/Map Browser opens, and it keeps a single file from holding two
thousand materials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from unity_pipeline_core import extract, material_json, profiles

from .material_builder import build_material
from .recipe import recipe_from_document


LIBRARY_DIR = "maxlib"
REPORT_NAME = "_ump_max_report.json"


@dataclass
class BuildResult:
    built: int = 0
    failed: int = 0
    libraries: int = 0
    notes: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _catalog_libraries(index: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for entry in index.get("materials", {}).values():
        grouped.setdefault(entry.get("catalog", ""), []).append(entry)
    return grouped


def build_library(library_root: str | Path, only_guids: set[str] | None = None) -> BuildResult:
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

    for catalog, entries in sorted(_catalog_libraries(index).items()):
        materials = []
        for entry in entries:
            if only_guids is not None and entry.get("guid", "") not in only_guids:
                continue
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

        if not materials:
            continue

        library = runtime.materialLibrary()
        for material in materials:
            runtime.append(library, material)

        destination = output_root / f"{catalog.replace('/', '_') or 'root'}.mat"
        if runtime.saveTempMaterialLibrary(library, str(destination)):
            result.libraries += 1
        else:
            result.failed += len(materials)
            result.warnings.append(f"{destination.name}: 3ds Max refused to save the library")

    _write_report(root, result)
    return result


def _write_report(root: Path, result: BuildResult) -> None:
    payload = {
        "built": result.built,
        "failed": result.failed,
        "libraries": result.libraries,
        "not_reproduced": sorted(
            ({"note": note, "count": count} for note, count in result.notes.items()),
            key=lambda item: -item["count"],
        )[:50],
        "warnings": result.warnings[:50],
    }
    (root / REPORT_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
