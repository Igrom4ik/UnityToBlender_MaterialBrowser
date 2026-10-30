"""Validate a shared vendored core without relying on its install path."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path


_IGNORED_PARTS = {"__pycache__"}
_IGNORED_SUFFIXES = {".pyc", ".pyo"}


def _distribution_fingerprint(root: Path) -> tuple[tuple[str, int, str], ...]:
    """Return a content fingerprint for every meaningful file in a package."""
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _IGNORED_PARTS.intersection(relative.parts):
            continue
        if path.suffix.lower() in _IGNORED_SUFFIXES:
            continue
        payload = path.read_bytes()
        entries.append((relative.as_posix(), len(payload), sha256(payload).hexdigest()))
    return tuple(entries)


def ensure_compatible_core(vendor_root: Path, loaded_core) -> None:
    """Accept another add-on's core only when both distributions are identical."""
    expected_root = (vendor_root / "unity_pipeline_core").resolve()
    loaded_file = Path(getattr(loaded_core, "__file__", "") or "").resolve()
    loaded_root = loaded_file.parent

    if loaded_root == expected_root:
        return

    try:
        compatible = (
            expected_root.is_dir()
            and loaded_root.is_dir()
            and _distribution_fingerprint(expected_root)
            == _distribution_fingerprint(loaded_root)
        )
    except OSError as error:
        raise ImportError(
            "Could not verify the already loaded unity_pipeline_core: "
            f"{error}"
        ) from error

    if compatible:
        return

    raise ImportError(
        f"unity_pipeline_core was loaded from {loaded_file}, but its files "
        f"do not match this add-on's vendored copy in {expected_root}"
    )
