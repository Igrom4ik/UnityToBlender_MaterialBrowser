"""What the step dialog knows, decided without Qt and without 3ds Max.

The dialog only draws. Which step is done, what is missing, what a chosen
folder actually contains -- all of that is decided here, so it is covered by
the ordinary tests instead of by clicking around inside Max.

Nothing is remembered that can be read from disk: the number of materials, the
date of the scan and the built libraries all come from the library folder
itself. The settings file therefore holds choices only, and a library built
elsewhere is understood the moment it is picked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import islice
import json
import os
from pathlib import Path
from typing import Callable

from unity_pipeline_core import extract

from .library_build import LIBRARY_DIR, REPORT_NAME


SETTINGS_NAME = "settings.json"

# Problem codes rather than sentences: the text is chosen by the dialog in the
# language it is drawing, and a code is what a test can assert on.
OK = ""
NO_LIBRARY = "NO_LIBRARY"
LIBRARY_INSIDE_UNITY = "LIBRARY_INSIDE_UNITY"
NO_MATERIAL_FOLDERS = "NO_MATERIAL_FOLDERS"
MATERIAL_FOLDERS_GONE = "MATERIAL_FOLDERS_GONE"
NO_MAT_FILES = "NO_MAT_FILES"
NO_INDEX = "NO_INDEX"
NOTHING_BUILT = "NOTHING_BUILT"

COUNT_LIMIT = 500

Progress = Callable[[float, str], bool]


class Cancelled(Exception):
    """Raised when the person at the keyboard stopped a long step."""


def settings_path() -> Path:
    """Beside the user profile, never inside the plugin folder.

    Reinstalling the plugin overwrites its folder; the answers to "which
    folders" would go with it, and the dialog would forget everything on every
    update.
    """
    override = os.environ.get("UNITY_MATERIAL_MAX_HOME")
    if override:
        return Path(override) / SETTINGS_NAME
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "unity_material_max" / SETTINGS_NAME


@dataclass
class Settings:
    """The choices, and only the choices."""

    library_root: str = ""
    library_name: str = ""
    material_roots: list = field(default_factory=list)
    asset_roots: list = field(default_factory=list)
    search_whole_project: bool = False
    language: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        try:
            data = json.loads(Path(path or settings_path()).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls(
            library_root=str(data.get("library_root") or ""),
            library_name=str(data.get("library_name") or ""),
            material_roots=_clean_paths(data.get("material_roots")),
            asset_roots=_clean_paths(data.get("asset_roots")),
            search_whole_project=bool(data.get("search_whole_project")),
            language=str(data.get("language") or ""),
        )

    def save(self, path: Path | None = None) -> Path | None:
        destination = Path(path or settings_path())
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(
                    {
                        "library_root": self.library_root,
                        "library_name": self.library_name,
                        "material_roots": list(self.material_roots),
                        "asset_roots": list(self.asset_roots),
                        "search_whole_project": self.search_whole_project,
                        "language": self.language,
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
        except OSError:
            # A read-only profile is not a reason to lose the session.
            return None
        return destination


def _clean_paths(value) -> list:
    if not isinstance(value, list):
        return []
    seen: list = []
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


@dataclass
class FolderFacts:
    """What a folder is, said before it is scanned rather than after."""

    path: Path
    exists: bool = False
    mat_count: int = 0
    capped: bool = False
    unity_assets: Path | None = None
    is_whole_assets: bool = False

    @property
    def counted(self) -> str:
        return f"{self.mat_count}+" if self.capped else str(self.mat_count)


def describe_folder(path: str | Path, limit: int = COUNT_LIMIT) -> FolderFacts:
    """Count the `.mat` files a folder holds, stopping at `limit`.

    Picking the project root is legitimate and means the whole `Assets` tree,
    which is worth saying out loud before a scan that takes minutes.
    """
    folder = Path(path)
    facts = FolderFacts(path=folder, exists=folder.is_dir())
    if not facts.exists:
        return facts

    found = list(islice(folder.rglob("*.mat"), limit + 1))
    facts.capped = len(found) > limit
    facts.mat_count = min(len(found), limit)

    assets = extract.unity_project_of(folder)
    facts.unity_assets = assets
    if assets is not None:
        facts.is_whole_assets = os.path.normcase(str(folder)) == os.path.normcase(str(assets))
    return facts


@dataclass
class LibraryState:
    """The library folder, read from disk every time it is asked about."""

    root: Path | None = None
    problem: str = NO_LIBRARY
    exists: bool = False
    has_index: bool = False
    material_count: int = 0
    shader_count: int = 0
    unresolved_textures: int = 0
    materials_without_shader: int = 0
    scanned_at: str = ""
    index_material_roots: list = field(default_factory=list)
    index_asset_roots: list = field(default_factory=list)
    built: int = 0
    failed: int = 0
    libraries: int = 0
    built_from_scan: str = ""
    library_files: list = field(default_factory=list)
    not_reproduced: list = field(default_factory=list)

    @property
    def has_libraries(self) -> bool:
        return bool(self.library_files)

    @property
    def build_is_stale(self) -> bool:
        """The .mat files came from an older scan than the one on disk.

        A rescan that finally resolves the shaders changes every material, and
        a library built before it looks exactly like one built after: same
        files, same count, same green tick.
        """
        if not (self.has_index and self.library_files and self.built_from_scan):
            return False
        return self.built_from_scan != self.scanned_at


def library_problem(library_root: str | Path) -> str:
    if not str(library_root).strip():
        return NO_LIBRARY
    # The core states the rule; here it only has to be a yes or a no.
    return LIBRARY_INSIDE_UNITY if extract.validate_library_root(library_root) else OK


def read_state(library_root: str | Path) -> LibraryState:
    problem = library_problem(library_root)
    if problem:
        return LibraryState(problem=problem)

    root = Path(library_root)
    state = LibraryState(root=root, problem=OK, exists=root.is_dir())

    index = extract.load_index(root)
    if index:
        materials = index.get("materials", {})
        state.has_index = True
        state.material_count = len(materials)
        state.scanned_at = str(index.get("generated_at", ""))
        state.index_material_roots = [str(item) for item in index.get("material_roots", [])]
        state.index_asset_roots = [str(item) for item in index.get("asset_roots", [])]

    report = _read_json(root / extract.REPORT_NAME)
    if report:
        shaders = [item for item in report.get("shaders", []) or [] if isinstance(item, dict)]
        state.shader_count = len(shaders)
        state.unresolved_textures = int(report.get("materials_with_unresolved_textures", 0) or 0)
        # A shader that was never found means the material was converted from
        # the .mat file alone: property names are guessed, and most maps end up
        # nowhere. The count is the difference between a library that looks
        # empty and one that is wrong, so it is not left to the report file.
        state.materials_without_shader = sum(
            int(item.get("materials", 0) or 0)
            for item in shaders
            if item.get("confidence") == "none"
        )

    max_report = _read_json(root / REPORT_NAME)
    if max_report:
        state.built = int(max_report.get("built", 0) or 0)
        state.failed = int(max_report.get("failed", 0) or 0)
        state.libraries = int(max_report.get("libraries", 0) or 0)
        state.built_from_scan = str(max_report.get("scanned_at", "") or "")
        state.not_reproduced = [
            (str(item.get("note", "")), int(item.get("count", 0) or 0))
            for item in max_report.get("not_reproduced", []) or []
            if isinstance(item, dict)
        ]

    libraries = root / LIBRARY_DIR
    if libraries.is_dir():
        state.library_files = sorted(libraries.glob("*.mat"))
    return state


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class Session:
    """The dialog's model: settings, derived state and the two long steps."""

    def __init__(self, settings: Settings | None = None, path: Path | None = None):
        self.path = Path(path) if path is not None else settings_path()
        self.settings = settings if settings is not None else Settings.load(self.path)
        self.state = read_state(self.settings.library_root)

    # -- state ---------------------------------------------------------------

    def refresh(self) -> LibraryState:
        self.state = read_state(self.settings.library_root)
        return self.state

    def save(self) -> Path | None:
        return self.settings.save(self.path)

    def material_folders(self) -> tuple:
        return tuple(Path(item) for item in self.settings.material_roots)

    def asset_folders(self) -> tuple:
        """The chosen texture folders, plus the whole project when asked for.

        Only listed folders are read (invariant 5), so an unresolved texture
        means its folder is simply not in the list. The checkbox is the one
        explicit way to say "look everywhere" without it happening silently.
        """
        chosen = [Path(item) for item in self.settings.asset_roots]
        if not self.settings.search_whole_project:
            return tuple(chosen)

        known = {os.path.normcase(str(item)) for item in chosen}
        for folder in self.material_folders():
            assets = extract.unity_project_of(folder)
            if assets is not None and os.path.normcase(str(assets)) not in known:
                known.add(os.path.normcase(str(assets)))
                chosen.append(assets)
        return tuple(chosen)

    def scan_problem(self) -> str:
        """Why "Scan Unity" cannot run yet, as a code."""
        problem = library_problem(self.settings.library_root)
        if problem:
            return problem
        folders = self.material_folders()
        if not folders:
            return NO_MATERIAL_FOLDERS
        existing = [folder for folder in folders if folder.is_dir()]
        if not existing:
            return MATERIAL_FOLDERS_GONE
        if not any(any(folder.rglob("*.mat")) for folder in existing):
            return NO_MAT_FILES
        return OK

    def build_problem(self) -> str:
        """Why "Build .mat" cannot run yet, as a code."""
        problem = library_problem(self.settings.library_root)
        if problem:
            return problem
        return OK if self.state.has_index else NO_INDEX

    def use_problem(self) -> str:
        if library_problem(self.settings.library_root):
            return NO_LIBRARY
        return OK if self.state.has_libraries else NOTHING_BUILT

    def library_dir(self) -> Path | None:
        root = self.state.root
        return root / LIBRARY_DIR if root is not None else None

    # -- the two long steps --------------------------------------------------

    def run_extract(self, progress: Progress | None = None):
        """Phase A. Pure Python: no part of this needs 3ds Max."""
        problem = self.scan_problem()
        if problem:
            raise ValueError(problem)

        request = extract.ExtractRequest(
            material_roots=tuple(folder for folder in self.material_folders() if folder.is_dir()),
            asset_roots=tuple(folder for folder in self.asset_folders() if folder.is_dir()),
            library_root=Path(self.settings.library_root),
        )

        result = None
        for step in extract.extract_steps(request):
            if progress is not None and not progress(step.fraction, step.message):
                raise Cancelled()
            if step.result is not None:
                result = step.result
        self.refresh()
        return result

    def run_build(self, progress: Progress | None = None):
        """Phase B. This one is inside Max, and only this one."""
        problem = self.build_problem()
        if problem:
            raise ValueError(problem)

        from .library_build import build_library  # noqa: PLC0415  (pymxs lives here)

        # A stopped build still built something, so the result comes back with
        # `cancelled` set instead of as an exception: the catalogues that did
        # finish are on disk and worth reporting.
        result = build_library(
            self.settings.library_root, progress=progress, name=self.settings.library_name
        )
        self.refresh()
        return result
