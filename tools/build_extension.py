"""Build a deterministic Blender extension ZIP using only the standard library.

The pure Python core lives outside the add-on so it can be tested without
Blender; this script vendors a copy into `unity_material_browser/core` right
before packaging, so the shipped add-on is self contained.
"""

from __future__ import annotations

import argparse
import filecmp
from pathlib import Path
import shutil
import sys
import tomllib
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = PROJECT_ROOT / "unity_pipeline_core"
ADDON_DIR = PROJECT_ROOT / "unity_material_browser"
VENDOR_DIR = ADDON_DIR / "core"
MANIFEST_PATH = ADDON_DIR / "blender_manifest.toml"

EXCLUDED_PARTS = {"__pycache__", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def included_files(source_dir: Path):
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_dir)
        if EXCLUDED_PARTS.intersection(relative.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        yield path, relative


def vendor_core() -> int:
    """Copy the core package into the add-on, replacing any previous copy."""
    if not CORE_DIR.is_dir():
        raise FileNotFoundError(f"Core package is missing: {CORE_DIR}")
    if VENDOR_DIR.exists():
        shutil.rmtree(VENDOR_DIR)
    VENDOR_DIR.mkdir(parents=True)

    copied = 0
    for path, relative in included_files(CORE_DIR):
        destination = VENDOR_DIR / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        copied += 1
    return copied


def verify_vendored_copy() -> None:
    """The vendored copy must match the source byte for byte."""
    for path, relative in included_files(CORE_DIR):
        mirrored = VENDOR_DIR / relative
        if not mirrored.is_file() or not filecmp.cmp(path, mirrored, shallow=False):
            raise RuntimeError(f"Vendored core differs from the source: {relative}")


def build(output_dir: Path, expected_version: str | None = None) -> Path:
    if not MANIFEST_PATH.is_file():
        raise FileNotFoundError(f"Missing extension manifest: {MANIFEST_PATH}")

    with MANIFEST_PATH.open("rb") as manifest_file:
        manifest = tomllib.load(manifest_file)

    extension_id = manifest.get("id")
    version = manifest.get("version")
    if not extension_id or not version:
        raise ValueError("Manifest must define both id and version")
    if expected_version is not None and version != expected_version:
        raise ValueError(
            f"Release version mismatch: manifest is {version}, expected {expected_version}"
        )

    vendor_core()
    verify_vendored_copy()

    required = {"blender_manifest.toml", "__init__.py", "core/__init__.py"}
    actual = {relative.as_posix() for _path, relative in included_files(ADDON_DIR)}
    missing = required - actual
    if missing:
        raise FileNotFoundError(f"Missing required extension files: {sorted(missing)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{extension_id}-{version}.zip"

    with zipfile.ZipFile(
        output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path, relative in included_files(ADDON_DIR):
            info = zipfile.ZipInfo(relative.as_posix(), ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)

    with zipfile.ZipFile(output_path, "r") as archive:
        bad_file = archive.testzip()
        if bad_file is not None:
            raise RuntimeError(f"Corrupt member in generated ZIP: {bad_file}")
        names = set(archive.namelist())
        if not required.issubset(names):
            raise RuntimeError("Generated ZIP is missing the manifest, entry point or core")

    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "dist")
    parser.add_argument("--expected-version")
    args = parser.parse_args()

    output_path = build(args.output_dir.resolve(), args.expected_version)
    print(output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
