"""Resolve Unity GUIDs fast enough for a drag and drop.

A drop has to feel instant, so nothing here reads the whole project unless it
must. Three levels, cheapest first: a GUID index cached on disk, then a scan of
the folders around the dropped material, and only then a full pass over the
project -- once, in a worker thread, with the result written back to the cache.

Only `.meta` files that belong to a texture or a shader are ever opened. In a
real project that is a quarter of all `.meta` files, and the other three
quarters -- scripts, prefabs, scenes -- can never be the target of a material
reference anyway.

Nothing here imports bpy.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Iterable, Iterator


CACHE_SCHEMA = 1
META_SUFFIX = ".meta"

# Only the head of a `.meta` is read: `guid:` sits in the first few lines.
_META_HEAD_BYTES = 256
_GUID_BYTES = re.compile(rb"guid:\s*([0-9a-fA-F]{32})")

TEXTURE_EXTENSIONS = frozenset(
    {
        ".bmp", ".dds", ".exr", ".gif", ".hdr", ".iff", ".jpeg", ".jpg", ".pict",
        ".png", ".psd", ".tga", ".tif", ".tiff", ".webp",
    }
)
SHADER_EXTENSIONS = frozenset({".shader", ".shadergraph", ".shadersubgraph"})
INDEXED_EXTENSIONS = TEXTURE_EXTENSIONS | SHADER_EXTENSIONS


def is_indexable_directory(name: str) -> bool:
    """Unity's own rules for folders it ignores -- and nothing else.

    Deliberately no list of names such as `Temp` or `Build`: those only need
    skipping at the project root, which is never scanned, and material folders
    literally called `Temp` do exist in the wild. Dropping one would silently
    lose the textures sitting next to the material.
    """
    if not name or name.startswith("."):
        return False
    if name.endswith("~"):
        return False
    return name.casefold() != "cvs"


def assets_root_of(path: str | Path) -> Path | None:
    """Return the `Assets` folder of the Unity project holding `path`."""
    current = Path(path).expanduser()
    try:
        current = current.resolve()
    except OSError:
        return None
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "Assets").is_dir() and (candidate / "ProjectSettings").is_dir():
            return candidate / "Assets"
    return None


def project_root_of(path: str | Path) -> Path | None:
    assets = assets_root_of(path)
    return assets.parent if assets is not None else None


def scan_roots(
    assets_root: str | Path,
    *,
    include_packages: bool = True,
    include_package_cache: bool = False,
) -> tuple[Path, ...]:
    """Folders searched for GUIDs, in the order Unity resolves them."""
    assets = Path(assets_root)
    roots = [assets]
    project = assets.parent
    if include_packages and (project / "Packages").is_dir():
        roots.append(project / "Packages")
    if include_package_cache and (project / "Library" / "PackageCache").is_dir():
        roots.append(project / "Library" / "PackageCache")
    return tuple(roots)


def read_guid(meta_path: str) -> tuple[str, str] | None:
    """Return (guid, asset path) for a `.meta`, or None when it carries none."""
    try:
        with open(meta_path, "rb") as handle:
            head = handle.read(_META_HEAD_BYTES)
    except OSError:
        return None
    match = _GUID_BYTES.search(head)
    if match is None:
        return None
    return match.group(1).decode("ascii").casefold(), meta_path[: -len(META_SUFFIX)]


def guid_of_asset(asset_path: str | Path) -> str:
    """GUID of a single asset, read straight from its side-car `.meta`."""
    entry = read_guid(f"{asset_path}{META_SUFFIX}")
    return entry[0] if entry else ""


def _indexable_metas(directory: str, filenames: Iterable[str]) -> list[str]:
    found = []
    for name in filenames:
        if not name.endswith(META_SUFFIX):
            continue
        _stem, extension = os.path.splitext(name[: -len(META_SUFFIX)])
        if extension.casefold() in INDEXED_EXTENSIONS:
            found.append(os.path.join(directory, name))
    return found


def _directory_mtime(directory: str) -> float:
    try:
        return os.stat(directory).st_mtime
    except OSError:
        return 0.0


@dataclass
class ProjectIndex:
    """GUID -> asset path for one Unity project."""

    assets_root: Path
    entries: dict[str, str] = field(default_factory=dict)
    directories: dict[str, float] = field(default_factory=dict)
    built_at: str = ""
    roots: tuple[str, ...] = ()

    def lookup(self, guid: str) -> Path | None:
        path = self.entries.get(guid.casefold())
        if not path:
            return None
        # A cached entry can outlive the file it points at, and a stale hit
        # would become a broken image datablock instead of an honest miss.
        return Path(path) if os.path.isfile(path) else None

    def merge(self, entries: dict[str, str]) -> int:
        added = 0
        for guid, path in entries.items():
            key = guid.casefold()
            if self.entries.get(key) != str(path):
                self.entries[key] = str(path)
                added += 1
        return added

    def to_data(self) -> dict:
        return {
            "schema": CACHE_SCHEMA,
            "assets_root": str(self.assets_root),
            "built_at": self.built_at,
            "roots": list(self.roots),
            "entries": self.entries,
            "directories": self.directories,
        }

    @classmethod
    def from_data(cls, data: dict) -> "ProjectIndex":
        if data.get("schema") != CACHE_SCHEMA:
            raise ValueError("Unsupported GUID index schema")
        return cls(
            assets_root=Path(data["assets_root"]),
            entries=dict(data.get("entries", {})),
            directories=dict(data.get("directories", {})),
            built_at=data.get("built_at", ""),
            roots=tuple(data.get("roots", ())),
        )


def cache_key(assets_root: str | Path) -> str:
    normalized = os.path.normcase(str(Path(assets_root)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def cache_path(cache_directory: str | Path, assets_root: str | Path) -> Path:
    return Path(cache_directory) / f"unity-guids-{cache_key(assets_root)}.json"


def load_index(cache_directory: str | Path, assets_root: str | Path) -> ProjectIndex | None:
    source = cache_path(cache_directory, assets_root)
    if not source.is_file():
        return None
    try:
        index = ProjectIndex.from_data(json.loads(source.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if os.path.normcase(str(index.assets_root)) != os.path.normcase(str(Path(assets_root))):
        return None
    return index


def save_index(cache_directory: str | Path, index: ProjectIndex) -> Path:
    destination = cache_path(cache_directory, index.assets_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(index.to_data(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination


def remove_index(cache_directory: str | Path, assets_root: str | Path) -> bool:
    source = cache_path(cache_directory, assets_root)
    if not source.exists():
        return False
    source.unlink()
    return True


def _ancestors(start: Path, stop: Path) -> Iterator[Path]:
    """The material folder first, then outwards, ending at the scan root."""
    current = start
    seen: set[str] = set()
    while True:
        key = os.path.normcase(str(current))
        if key in seen:
            break
        seen.add(key)
        yield current
        if key == os.path.normcase(str(stop)) or current.parent == current:
            break
        current = current.parent


def resolve_nearby(
    start_directory: str | Path,
    stop_directory: str | Path,
    wanted: Iterable[str],
    *,
    file_budget: int = 6000,
    deadline: float | None = None,
) -> tuple[dict[str, str], set[str]]:
    """Hunt for GUIDs outwards from the material, closest folders first.

    Textures usually sit beside the material that uses them, so this finds them
    without touching the rest of the project. It gives up on a budget instead of
    degenerating into a full scan -- the caller has a slower path for that.

    Returns (found guid -> path, still missing guids).
    """
    remaining = {guid.casefold() for guid in wanted if guid}
    found: dict[str, str] = {}
    if not remaining:
        return found, remaining

    start = Path(start_directory)
    stop = Path(stop_directory)
    scanned: set[str] = set()
    read = 0

    for anchor in _ancestors(start, stop):
        for directory, directories, filenames in os.walk(anchor):
            directories[:] = [name for name in directories if is_indexable_directory(name)]
            key = os.path.normcase(directory)
            if key in scanned:
                continue
            scanned.add(key)
            for meta_path in _indexable_metas(directory, filenames):
                entry = read_guid(meta_path)
                read += 1
                if entry is not None and entry[0] in remaining:
                    found[entry[0]] = entry[1]
                    remaining.discard(entry[0])
                    if not remaining:
                        return found, remaining
            if read >= file_budget:
                return found, remaining
            if deadline is not None and time.monotonic() >= deadline:
                return found, remaining
    return found, remaining


@dataclass
class IndexProgress:
    fraction: float = 0.0
    message: str = ""
    scanned: int = 0
    total: int = 0


class IndexJob:
    """Index a project on a worker thread so Blender keeps drawing.

    The thread only touches the file system and plain dicts -- never bpy -- so
    the main thread stays free to run the modal loop and repaint. Reads go to a
    small pool because the cost is per-file latency, not CPU.
    """

    def __init__(
        self,
        roots: Iterable[str | Path],
        *,
        assets_root: str | Path,
        previous: ProjectIndex | None = None,
        workers: int = 8,
        full: bool = False,
    ):
        self.roots = tuple(Path(root) for root in roots)
        self.assets_root = Path(assets_root)
        self.workers = max(1, min(32, int(workers)))
        self.full = full
        self.result: ProjectIndex | None = None
        self.error: str = ""
        self.added: int = 0
        self._previous = previous
        self._progress = IndexProgress(0.0, "Preparing")
        self._cancelled = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def progress(self) -> IndexProgress:
        return self._progress

    @property
    def started(self) -> bool:
        return self._thread is not None

    @property
    def finished(self) -> bool:
        return self._thread is not None and not self._thread.is_alive()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="unity-material-drop-index", daemon=True
        )
        self._thread.start()

    def cancel(self) -> None:
        self._cancelled.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _collect(self) -> tuple[list[str], dict[str, float]]:
        pending: list[str] = []
        directories: dict[str, float] = {}
        known = {} if self.full or self._previous is None else self._previous.directories
        for root in self.roots:
            if not root.is_dir():
                continue
            for directory, sub_directories, filenames in os.walk(root):
                if self._cancelled.is_set():
                    return pending, directories
                sub_directories[:] = [
                    name for name in sub_directories if is_indexable_directory(name)
                ]
                metas = _indexable_metas(directory, filenames)
                if not metas:
                    continue
                key = os.path.normcase(directory)
                mtime = _directory_mtime(directory)
                directories[key] = mtime
                # A folder keeps its GUIDs unless a file was added or removed,
                # which is exactly what its mtime records. Editing a texture
                # never changes its GUID, so re-reading it would be wasted time.
                if known.get(key) == mtime:
                    continue
                pending.extend(metas)
                self._progress = IndexProgress(
                    0.02, os.path.basename(directory), 0, len(pending)
                )
        return pending, directories

    def _run(self) -> None:
        try:
            started = time.monotonic()
            pending, directories = self._collect()
            if self._cancelled.is_set():
                return

            entries = dict(self._previous.entries) if self._previous is not None else {}
            total = len(pending)
            scanned = 0
            if total:
                with ThreadPoolExecutor(max_workers=self.workers) as pool:
                    for entry in pool.map(read_guid, pending, chunksize=64):
                        scanned += 1
                        if entry is not None:
                            if entries.get(entry[0]) != entry[1]:
                                self.added += 1
                            entries[entry[0]] = entry[1]
                        if scanned % 256:
                            continue
                        if self._cancelled.is_set():
                            pool.shutdown(wait=False, cancel_futures=True)
                            return
                        self._progress = IndexProgress(
                            0.02 + 0.97 * scanned / total,
                            f"{scanned} / {total}",
                            scanned,
                            total,
                        )

            self.result = ProjectIndex(
                assets_root=self.assets_root,
                entries=entries,
                directories=directories,
                built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                roots=tuple(str(root) for root in self.roots),
            )
            self._progress = IndexProgress(
                1.0,
                f"{len(entries)} assets, {time.monotonic() - started:.1f}s",
                scanned,
                total,
            )
        except Exception as error:  # a worker thread must never die silently
            self.error = f"{type(error).__name__}: {error}"


def build_index(
    assets_root: str | Path,
    *,
    include_packages: bool = True,
    include_package_cache: bool = False,
    previous: ProjectIndex | None = None,
    workers: int = 8,
    full: bool = False,
) -> ProjectIndex:
    """Blocking index build, used by tests and by background Blender."""
    job = IndexJob(
        scan_roots(
            assets_root,
            include_packages=include_packages,
            include_package_cache=include_package_cache,
        ),
        assets_root=assets_root,
        previous=previous,
        workers=workers,
        full=full,
    )
    job.start()
    job.join()
    if job.error:
        raise RuntimeError(job.error)
    if job.result is None:
        raise RuntimeError("Indexing produced no result")
    return job.result


class LazyGuidIndex:
    """Mapping adapter that records which GUIDs a material asked for.

    `unity_library.parse_material` only ever calls `.get()`, so a material can be
    parsed once against the index and still report exactly which references
    stayed unresolved -- no second parse, no guessing from the texture list.
    """

    def __init__(
        self,
        index: ProjectIndex | None = None,
        extra: dict[str, str | Path] | None = None,
    ):
        self.index = index
        self.extra = {guid.casefold(): Path(path) for guid, path in (extra or {}).items()}
        self.requested: dict[str, Path | None] = {}

    def add(self, entries: dict[str, str | Path]) -> None:
        for guid, path in entries.items():
            key = guid.casefold()
            self.extra[key] = Path(path)
            self.requested.pop(key, None)

    def get(self, guid: str, default=None):
        key = (guid or "").casefold()
        if key not in self.requested:
            resolved = self.extra.get(key)
            if resolved is None and self.index is not None:
                resolved = self.index.lookup(key)
            self.requested[key] = resolved
        resolved = self.requested[key]
        return default if resolved is None else resolved

    def __getitem__(self, guid: str):
        value = self.get(guid)
        if value is None:
            raise KeyError(guid)
        return value

    def __contains__(self, guid: str) -> bool:
        return self.get(guid) is not None

    @property
    def missing(self) -> list[str]:
        return sorted(guid for guid, path in self.requested.items() if path is None and guid)

    @property
    def resolved(self) -> dict[str, Path]:
        return {guid: path for guid, path in self.requested.items() if path is not None}
