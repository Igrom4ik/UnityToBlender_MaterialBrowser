"""Run-time state shared by the drop operator and the panel.

A GUID index is a few megabytes and a project is dropped from many times per
session, so it is parsed from disk once and then kept in memory, keyed by the
project it belongs to. Nothing here is saved into a .blend: the index is a
derived artefact of the Unity project, not of the file being worked on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
import os

from ._core import fast_index, profiles


_INDEXES: dict[str, fast_index.ProjectIndex] = {}
_PROFILES: dict[str, tuple[float, list]] = {}
_UNRESOLVABLE: dict[str, set[str]] = {}
_JOB: fast_index.IndexJob | None = None
_PENDING: dict | None = None


def _key(assets_root: str | Path) -> str:
    return os.path.normcase(str(Path(assets_root)))


def cached_index(assets_root: str | Path) -> fast_index.ProjectIndex | None:
    return _INDEXES.get(_key(assets_root))


def index_for(
    assets_root: str | Path,
    cache_directory: str | Path,
    *,
    load: bool = True,
) -> fast_index.ProjectIndex | None:
    """The index for a project: memory first, then the disk cache."""
    key = _key(assets_root)
    index = _INDEXES.get(key)
    if index is not None or not load:
        return index
    index = fast_index.load_index(cache_directory, assets_root)
    if index is not None:
        _INDEXES[key] = index
    return index


def remember(index: fast_index.ProjectIndex) -> None:
    _INDEXES[_key(index.assets_root)] = index


def store(index: fast_index.ProjectIndex, cache_directory: str | Path) -> Path | None:
    """Keep the index in memory and write it back to the disk cache."""
    remember(index)
    try:
        return fast_index.save_index(cache_directory, index)
    except OSError:
        return None


def forget(assets_root: str | Path) -> None:
    _INDEXES.pop(_key(assets_root), None)
    _UNRESOLVABLE.pop(_key(assets_root), None)


def forget_all() -> None:
    global _PENDING
    _INDEXES.clear()
    _PROFILES.clear()
    _UNRESOLVABLE.clear()
    _PENDING = None


def mark_unresolvable(assets_root: str | Path, guids: Iterable[str]) -> None:
    """Remember GUIDs a full index failed to find.

    Without this, a material that references a texture someone deleted from
    Unity would send every single drop off to re-index the project, forever
    looking for a file that is not there.
    """
    known = _UNRESOLVABLE.setdefault(_key(assets_root), set())
    known.update(guid.casefold() for guid in guids if guid)


def worth_indexing(assets_root: str | Path, guids: Iterable[str]) -> list[str]:
    """The missing GUIDs that a fresh index could still plausibly find."""
    known = _UNRESOLVABLE.get(_key(assets_root), set())
    return sorted({guid.casefold() for guid in guids if guid} - known)


def set_pending(payload: dict | None) -> None:
    """What an update prompt should rebuild if the user says yes.

    Kept here rather than on operator properties because it holds conversions,
    which are Python objects a `bpy.ops` call cannot carry.
    """
    global _PENDING
    _PENDING = payload


def pending() -> dict | None:
    return _PENDING


def job() -> fast_index.IndexJob | None:
    return _JOB


def set_job(value: fast_index.IndexJob | None) -> None:
    global _JOB
    _JOB = value


def is_busy() -> bool:
    return _JOB is not None and not _JOB.finished


def rules(profiles_path: str | Path | None) -> list:
    """Conversion profiles, re-read only when the user's file changes."""
    path = str(profiles_path or "")
    stamp = 0.0
    if path:
        try:
            stamp = Path(path).stat().st_mtime
        except OSError:
            stamp = 0.0
    cached = _PROFILES.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    loaded = profiles.load_profiles(path or None)
    _PROFILES[path] = (stamp, loaded)
    return loaded
