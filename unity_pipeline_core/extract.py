"""Phase A: turn a Unity project folder into a JSON tree of materials.

Runs without Blender. Every path comes from the caller -- the add-on UI decides
which folders are scanned and where the library lives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Iterator

from . import material_json, unity_library
from .shader_parser import PARSER_VERSION, ShaderResolver


INDEX_NAME = "_ump_index.json"
REPORT_NAME = "_ump_report.json"
MATERIALS_DIR = "materials"
SHADERS_DIR = "shaders"

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class ExtractRequest:
    """Every folder that will be read is listed here and nowhere else.

    There is deliberately no "whole project" root: GUIDs resolve against the
    chosen material and asset folders only, and a reference that lands outside
    them stays unresolved and is reported (invariant 5).
    """

    material_roots: tuple[Path, ...]
    asset_roots: tuple[Path, ...] = ()
    library_root: Path = Path()


def unity_project_of(path: str | Path) -> Path | None:
    """Return the Assets folder of the Unity project containing `path`."""
    current = Path(path).expanduser().resolve()
    for candidate in (current, *current.parents):
        if candidate.name.casefold() == "assets" and (
            candidate.parent / "ProjectSettings"
        ).is_dir():
            return candidate
        if (candidate / "Assets").is_dir() and (candidate / "ProjectSettings").is_dir():
            return candidate / "Assets"
    return None


def validate_library_root(library_root: str | Path) -> str:
    """Refuse a library location that would write into a Unity project.

    The add-on only ever reads Unity; putting generated .blend files, previews
    and JSON inside `Assets/` would break that promise and make Unity import
    thousands of files it never asked for.
    """
    root = Path(library_root)
    if not str(root).strip():
        return "Choose a folder for the Blender library"
    assets = unity_project_of(root)
    if assets is not None:
        return (
            "This folder is inside a Unity project. Choose a folder outside "
            f"{assets.parent}"
        )
    return ""


@dataclass
class ExtractProgress:
    fraction: float
    phase: str
    message: str = ""
    result: "ExtractResult | None" = None


@dataclass
class ExtractResult:
    material_count: int = 0
    shader_count: int = 0
    written: int = 0
    skipped: int = 0
    failed: int = 0
    unresolved_textures: int = 0
    unresolved_shaders: int = 0
    index_path: Path | None = None
    report_path: Path | None = None
    report: dict = field(default_factory=dict)


def safe_filename(name: str) -> str:
    cleaned = _INVALID_FILENAME.sub("_", name).strip().rstrip(". ")
    return cleaned or "material"


def catalog_for(material_path: Path, root: Path) -> str:
    """Mirror the Unity folder structure, prefixed with the source folder name."""
    try:
        relative = material_path.parent.relative_to(root)
    except ValueError:
        return root.name
    parts = [part for part in relative.parts if part not in (".", "")]
    return "/".join([root.name, *parts]) if parts else root.name


def _collect_guid_index(roots: tuple[Path, ...]) -> tuple[dict[str, Path], dict[str, str]]:
    """Return guid -> asset path and normalised asset path -> guid."""
    by_guid: dict[str, Path] = {}
    by_path: dict[str, str] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for guid, path in unity_library.build_guid_index(root).items():
            by_guid.setdefault(guid, path)
            by_path.setdefault(os.path.normcase(str(path)), guid)
    return by_guid, by_path


def _material_paths(roots: tuple[Path, ...]) -> list[tuple[Path, Path]]:
    found: list[tuple[Path, Path]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.mat")):
            found.append((path, root))
    return found


def _shader_paths(guid_index: dict[str, Path]) -> dict[str, Path]:
    return {
        guid: path
        for guid, path in guid_index.items()
        if path.suffix.casefold() in {".shader", ".shadergraph"}
    }


def extract_steps(request: ExtractRequest) -> Iterator[ExtractProgress]:
    library_root = Path(request.library_root)
    material_roots = tuple(Path(root) for root in request.material_roots)
    asset_roots = tuple(Path(root) for root in request.asset_roots)

    if not material_roots:
        raise ValueError("At least one Unity material folder is required")
    if not library_root:
        raise ValueError("A library folder is required")

    error = validate_library_root(library_root)
    if error:
        raise ValueError(error)

    yield ExtractProgress(0.14, "INDEX", "Indexing selected folders")
    guid_index, guid_by_path = _collect_guid_index(material_roots + asset_roots)

    resolver = ShaderResolver(_shader_paths(guid_index))
    materials = _material_paths(material_roots)
    total = max(1, len(materials))

    result = ExtractResult()
    used_names: dict[str, str] = {}
    index_entries: dict[str, dict] = {}
    shader_usage: dict[str, int] = {}
    stale_counter: dict[str, int] = {}
    missing_textures: dict[str, int] = {}
    assets_root = unity_library.resolve_roots(material_roots[0])[1]

    for position, (material_path, root) in enumerate(materials, start=1):
        fraction = 0.05 + 0.85 * position / total
        yield ExtractProgress(fraction, "MATERIALS", material_path.name)

        try:
            material = unity_library.parse_material(material_path, guid_index)
        except OSError:
            result.failed += 1
            continue

        guid = guid_by_path.get(os.path.normcase(str(material_path)), "")
        reference = material.shader
        interface = (
            resolver.resolve(reference.guid, reference.file_id, reference.asset_path)
            if reference is not None
            else resolver.resolve("", 0, None)
        )

        catalog = catalog_for(material_path, root)
        asset_name = safe_filename(material.name)
        key = f"{catalog}/{asset_name}".casefold()
        if key in used_names and used_names[key] != guid:
            asset_name = f"{asset_name}__{(guid or 'nogui')[:6]}"
            key = f"{catalog}/{asset_name}".casefold()
        used_names[key] = guid

        document = material_json.build_document(
            material, interface, assets_root=assets_root, catalog=catalog
        )
        document["source"]["guid"] = guid
        document["material"]["asset_name"] = asset_name

        relative_json = Path(MATERIALS_DIR) / Path(*catalog.split("/")) / f"{asset_name}.json"
        destination = library_root / relative_json
        try:
            material_json.write_document(destination, document)
        except OSError:
            result.failed += 1
            continue

        result.written += 1
        result.material_count += 1
        used_textures = document["effective"]["textures"]
        unresolved = [
            entry
            for name, entry in document["material"]["textures"].items()
            if not entry.get("resolved") and name in used_textures
        ]
        if unresolved:
            result.unresolved_textures += 1
            for entry in unresolved:
                texture_guid = entry.get("guid", "")
                missing_textures[texture_guid] = missing_textures.get(texture_guid, 0) + 1
        shader_usage[interface.name] = shader_usage.get(interface.name, 0) + 1
        for name in document["effective"]["stale"]:
            stale_counter[name] = stale_counter.get(name, 0) + 1

        index_entries[guid or str(material_path)] = {
            "name": material.name,
            "asset_name": asset_name,
            "catalog": catalog,
            "json": relative_json.as_posix(),
            "blend": relative_json.with_suffix(".blend").as_posix(),
            "unity_path": document["source"]["path"],
            "absolute_path": str(material_path),
            "source_hash": document["source"]["hash"],
            "texture_deps": document["source"]["texture_deps"],
            "texture_paths": {
                texture.guid: str(texture.asset_path)
                for texture in material.textures
                if texture.asset_path
            },
            "shader": interface.name,
            "shader_path": str(interface.source_path) if interface.source_path else "",
            "shader_stamp": material_json.file_stamp(interface.source_path)
            if interface.source_path
            else "",
            "status": document["conversion"]["status"],
        }

    yield ExtractProgress(0.92, "SHADERS", "Writing shader interfaces")
    parsed_shaders = resolver.parsed
    for interface in parsed_shaders.values():
        if not interface.is_resolved:
            continue
        name = safe_filename(interface.name.replace("/", "_"))
        path = library_root / SHADERS_DIR / f"{name}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(interface.to_data(), ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            result.shader_count += 1
        except OSError:
            pass

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "materials": result.material_count,
        "failed": result.failed,
        "shaders": sorted(
            (
                {
                    "name": interface.name,
                    "backend": interface.backend,
                    "confidence": interface.confidence,
                    "workflow": interface.workflow,
                    "properties": len(interface.properties),
                    "materials": shader_usage.get(interface.name, 0),
                    "notes": list(interface.notes),
                }
                for interface in parsed_shaders.values()
            ),
            key=lambda item: -item["materials"],
        ),
        "stale_dropped": sorted(
            ({"property": name, "count": count} for name, count in stale_counter.items()),
            key=lambda item: -item["count"],
        )[:50],
        "materials_with_unresolved_textures": result.unresolved_textures,
        "missing_textures": sorted(
            ({"guid": guid, "materials": count} for guid, count in missing_textures.items()),
            key=lambda item: -item["materials"],
        )[:50],
    }

    index = {
        "schema": 1,
        "generated_at": report["generated_at"],
        "parser_version": PARSER_VERSION,
        "material_roots": [str(root) for root in material_roots],
        "asset_roots": [str(root) for root in asset_roots],
        "materials": index_entries,
    }

    library_root.mkdir(parents=True, exist_ok=True)
    index_path = library_root / INDEX_NAME
    report_path = library_root / REPORT_NAME
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    _write_gitignore(library_root)

    result.index_path = index_path
    result.report_path = report_path
    result.report = report
    yield ExtractProgress(1.0, "COMPLETE", "Extraction finished", result)


def _write_gitignore(library_root: Path) -> None:
    """The library is a local derived artefact and must never reach git."""
    path = library_root / ".gitignore"
    if path.exists():
        return
    try:
        path.write_text("*\n", encoding="utf-8")
    except OSError:
        pass


def extract(request: ExtractRequest) -> ExtractResult:
    result = None
    for progress in extract_steps(request):
        if progress.result is not None:
            result = progress.result
    if result is None:
        raise RuntimeError("Extraction produced no result")
    return result


def load_index(library_root: str | Path) -> dict | None:
    try:
        return json.loads((Path(library_root) / INDEX_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
