"""Entry point for 3dsmaxbatch: build the .mat libraries with no interface.

    3dsmaxbatch.exe unity_material_max\\batch_build.py -mxsString "library:D:\\UnityMaterialLib"

3ds Max Batch passes strings in through `-mxsString`, so the library path is
read from the MAXScript globals rather than from argv.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _library_path() -> Path:
    """Read `library` from the batch string, or fall back to argv."""
    try:
        from pymxs import runtime  # noqa: PLC0415

        value = getattr(runtime, "library", None)
        if value:
            return Path(str(value))
    except ImportError:
        pass

    for argument in sys.argv[1:]:
        if not argument.startswith("-"):
            return Path(argument)
    raise SystemExit("Pass the library folder: -mxsString \"library:<path>\"")


def main() -> int:
    root = _library_path()
    print(f"Unity Material Browser: building .mat libraries in {root}")

    # The vendored core sits next to this package when the plugin is installed;
    # in a source checkout the repository root is already on sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from unity_material_max.library_build import build_library

    try:
        result = build_library(root)
    except Exception:
        traceback.print_exc()
        return 1

    print(
        f"built {result.built} materials into {result.libraries} libraries, "
        f"failed {result.failed}"
    )
    for warning in result.warnings[:10]:
        print(f"  warning: {warning}")
    return 0 if not result.failed else 1


if __name__ == "__main__":
    sys.exit(main())
