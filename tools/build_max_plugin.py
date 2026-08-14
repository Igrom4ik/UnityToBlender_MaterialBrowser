"""Package the 3ds Max plugin: both packages, side by side, in one ZIP.

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

EXCLUDED_PARTS = {"__pycache__", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

INSTALL_NOTE = """\
Unity Material Browser for 3ds Max {version}

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


def build(output_dir: Path, expected_version: str | None = None) -> Path:
    current = version()
    if expected_version is not None and current != expected_version:
        raise ValueError(f"Release version mismatch: package is {current}, expected {expected_version}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"unity_material_max-{current}.zip"

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for package in PACKAGES:
            directory = PROJECT_ROOT / package
            if not directory.is_dir():
                raise FileNotFoundError(f"Missing package: {directory}")
            for path, relative in included_files(directory):
                info = zipfile.ZipInfo(relative.as_posix(), ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes(), compresslevel=9)

        info = zipfile.ZipInfo("INSTALL.txt", ZIP_TIMESTAMP)
        info.external_attr = 0o100644 << 16
        archive.writestr(info, INSTALL_NOTE.format(version=current).encode("utf-8"))

    with zipfile.ZipFile(output_path, "r") as archive:
        bad_file = archive.testzip()
        if bad_file is not None:
            raise RuntimeError(f"Corrupt member in generated ZIP: {bad_file}")
        required = {
            "unity_material_max/__init__.py",
            "unity_material_max/batch_build.py",
            "unity_pipeline_core/__init__.py",
            "unity_pipeline_core/default_profiles.json",
        }
        missing = required - set(archive.namelist())
        if missing:
            raise RuntimeError(f"Generated ZIP is missing: {sorted(missing)}")

    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "dist")
    parser.add_argument("--expected-version")
    arguments = parser.parse_args()

    print(build(arguments.output_dir.resolve(), arguments.expected_version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
