"""Compare a built library against the current state of the Unity project.

Nothing is deleted automatically: materials that disappeared from Unity are
reported, and removing them stays an explicit decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

from . import extract, material_json
from .shader_parser import PARSER_VERSION


NEW = "NEW"
CHANGED = "CHANGED"
MISSING = "MISSING"
UNCHANGED = "UNCHANGED"


@dataclass
class DiffEntry:
    status: str
    name: str
    guid: str = ""
    path: str = ""
    reason: str = ""


@dataclass
class DiffResult:
    entries: list[DiffEntry] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return bool(self.counts.get(NEW) or self.counts.get(CHANGED) or self.counts.get(MISSING))

    def guids_to_build(self) -> set[str]:
        return {
            entry.guid
            for entry in self.entries
            if entry.status in (NEW, CHANGED) and entry.guid
        }


def _dependencies_changed(entry: dict) -> str:
    for guid, stamp in (entry.get("texture_deps") or {}).items():
        # The stored stamp is mtime:size, so a replaced texture is visible
        # without hashing multi-megabyte files.
        path = entry.get("texture_paths", {}).get(guid)
        if path and material_json.file_stamp(path) != stamp:
            return f"Texture changed: {Path(path).name}"

    # An edited shader changes what the material means -- defaults, which
    # properties are stale, whether the tint is proven -- without touching the
    # .mat at all (5.4).
    shader_path = entry.get("shader_path", "")
    if shader_path and material_json.file_stamp(shader_path) != entry.get("shader_stamp", ""):
        return f"Shader changed: {Path(shader_path).name}"
    return ""


def diff(library_root: str | Path, material_roots: tuple[Path, ...]) -> DiffResult:
    root = Path(library_root)
    index = extract.load_index(root) or {"materials": {}}
    known = index.get("materials", {})
    # A better parser reads the same files differently, so every document is out
    # of date even though nothing in Unity moved (5.4).
    parser_changed = index.get("parser_version") != PARSER_VERSION

    by_path: dict[str, tuple[str, dict]] = {}
    for guid, entry in known.items():
        absolute = entry.get("absolute_path")
        if absolute:
            by_path[os.path.normcase(absolute)] = (guid, entry)

    result = DiffResult()
    seen: set[str] = set()

    for material_root in material_roots:
        material_root = Path(material_root)
        if not material_root.is_dir():
            continue
        for path in sorted(material_root.rglob("*.mat")):
            key = os.path.normcase(str(path))
            record = by_path.get(key)
            if record is None:
                result.entries.append(DiffEntry(NEW, path.stem, "", str(path), "Not in the library"))
                continue

            guid, entry = record
            seen.add(key)
            if parser_changed:
                result.entries.append(
                    DiffEntry(
                        CHANGED,
                        entry.get("name", path.stem),
                        guid,
                        str(path),
                        "Shader parser was updated",
                    )
                )
                continue

            current_hash = material_json.file_hash(path)
            if current_hash != entry.get("source_hash"):
                result.entries.append(
                    DiffEntry(CHANGED, entry.get("name", path.stem), guid, str(path), "Material changed")
                )
                continue

            reason = _dependencies_changed(entry)
            if reason:
                result.entries.append(
                    DiffEntry(CHANGED, entry.get("name", path.stem), guid, str(path), reason)
                )
            else:
                result.entries.append(
                    DiffEntry(UNCHANGED, entry.get("name", path.stem), guid, str(path))
                )

    for key, (guid, entry) in by_path.items():
        if key not in seen:
            result.entries.append(
                DiffEntry(
                    MISSING,
                    entry.get("name", ""),
                    guid,
                    entry.get("absolute_path", ""),
                    "No longer present in Unity",
                )
            )

    for entry in result.entries:
        result.counts[entry.status] = result.counts.get(entry.status, 0) + 1
    return result
