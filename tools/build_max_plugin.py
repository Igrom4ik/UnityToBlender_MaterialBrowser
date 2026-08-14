"""Package the 3ds Max plugin, twice.

`unity_material_max-<version>.zip` is for copying by hand, and
`unity_material_max-<version>.mzp` is the same payload plus an installer:
dropping an .mzp into a 3ds Max viewport unpacks it, copies both packages into
the user scripts folder and registers the macro.

3ds Max has no extension format that owns a single folder, so the core is not
vendored into the plugin the way the Blender build does it -- both packages go
into the archive as they are, and `from unity_pipeline_core import ...` keeps
working in a checkout and in an install alike.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("unity_material_max", "unity_pipeline_core")
INIT_PATH = PROJECT_ROOT / "unity_material_max" / "__init__.py"
INSTALLER_DIR = Path(__file__).resolve().parent / "max_installer"
# mzp.run must sit at the root of the package: that is where 3ds Max looks.
INSTALLER_FILES = ("mzp.run", "install.ms", "UnityMaterialBrowser.mcr")

EXCLUDED_PARTS = {"__pycache__", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

INSTALL_NOTE = """\
Unity Material Browser for 3ds Max {version}

The .mzp next to this archive installs itself: drag it into a 3ds Max viewport.
This ZIP is the same payload for installing by hand.

Copy both folders next to each other into a scripts folder 3ds Max reads, for
example:

    %LOCALAPPDATA%\\Autodesk\\3dsMax\\<version>\\ENU\\scripts\\

Build the libraries without opening the interface:

    3dsmaxbatch.exe "<scripts>\\unity_material_max\\batch_build.py" ^
        -mxsString "library:D:\\UnityMaterialLib"

The library folder is the one the extract phase wrote: it must already contain
_ump_index.json. Extracting needs no 3ds Max at all -- it is plain Python.

The result is <library>\\maxlib\\<catalogue>.mat, opened from the
Material/Map Browser. Textures are referenced where Unity keeps them; nothing
is copied.
"""


def version() -> str:
    match = re.search(r'__version__ = "([^"]+)"', INIT_PATH.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"No __version__ in {INIT_PATH}")
    return match.group(1)


def included_files(source_dir: Path):
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_dir.parent)
        if EXCLUDED_PARTS.intersection(relative.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        yield path, relative


def _write_payload(archive: zipfile.ZipFile, current: str, *, installer: bool) -> None:
    for package in PACKAGES:
        directory = PROJECT_ROOT / package
        if not directory.is_dir():
            raise FileNotFoundError(f"Missing package: {directory}")
        for path, relative in included_files(directory):
            info = zipfile.ZipInfo(relative.as_posix(), ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)

    if installer:
        for name in INSTALLER_FILES:
            source = INSTALLER_DIR / name
            if not source.is_file():
                raise FileNotFoundError(f"Missing installer file: {source}")
            info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compresslevel=9)
    else:
        info = zipfile.ZipInfo("INSTALL.txt", ZIP_TIMESTAMP)
        info.external_attr = 0o100644 << 16
        archive.writestr(info, INSTALL_NOTE.format(version=current).encode("utf-8"))


def _verify(path: Path, *, installer: bool) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        bad_file = archive.testzip()
        if bad_file is not None:
            raise RuntimeError(f"Corrupt member in {path.name}: {bad_file}")
        required = {
            "unity_material_max/__init__.py",
            "unity_material_max/batch_build.py",
            "unity_pipeline_core/__init__.py",
            "unity_pipeline_core/default_profiles.json",
        }
        if installer:
            # 3ds Max looks for mzp.run at the root and nowhere else.
            required |= set(INSTALLER_FILES)
        missing = required - set(archive.namelist())
        if missing:
            raise RuntimeError(f"{path.name} is missing: {sorted(missing)}")


def build(output_dir: Path, expected_version: str | None = None) -> list[Path]:
    current = version()
    if expected_version is not None and current != expected_version:
        raise ValueError(f"Release version mismatch: package is {current}, expected {expected_version}")

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for suffix, installer in ((".zip", False), (".mzp", True)):
        output_path = output_dir / f"unity_material_max-{current}{suffix}"
        with zipfile.ZipFile(
            output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            _write_payload(archive, current, installer=installer)
        _verify(output_path, installer=installer)
        written.append(output_path)

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "dist")
    parser.add_argument("--expected-version")
    arguments = parser.parse_args()

    for path in build(arguments.output_dir.resolve(), arguments.expected_version):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
