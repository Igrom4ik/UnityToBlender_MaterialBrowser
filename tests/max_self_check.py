"""Prove the pymxs half works, on a machine that actually has 3ds Max.

Everything in `recipe.py` is covered by the ordinary test suite; this checks the
calls that only 3ds Max can answer -- creating a Physical Material, loading a
bitmap with a given gamma, Normal Bump, AppData, and writing a .mat library.

Run it either way:

    3dsmaxbatch.exe tests\\max_self_check.py -mxsString "library:D:\\UnityMaterialLib"

or paste into the Max listener after putting the repository on sys.path. The
library argument is optional: without it the material checks still run and only
the library round-trip is skipped.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from pymxs import runtime as rt


def check(condition: bool, message: str) -> bool:
    print(f"  {'ok  ' if condition else 'FAIL'}: {message}")
    return bool(condition)


def _library_argument() -> Path | None:
    value = getattr(rt, "library", None)
    if value:
        return Path(str(value))
    for argument in sys.argv[1:]:
        if not argument.startswith("-"):
            return Path(argument)
    return None


def check_material() -> bool:
    print("== Physical Material")
    from unity_material_max.material_builder import build_material, read_provenance
    from unity_material_max.recipe import BASE_COLOR_MAP, BUMP_MAP, BitmapRecipe, MaterialRecipe

    # A real file is needed for openBitMap; any bitmap Max ships with will do.
    sample = Path(str(rt.getDir(rt.name("maxroot")))) / "Maps" / "Noise" / "NOISE.JPG"
    recipe = MaterialRecipe(
        name="UMP_SelfCheck",
        base_color=(0.2, 0.4, 0.6),
        metalness=0.25,
        roughness=0.75,
        bump_amount=1.0,
        unity_guid="deadbeef",
        unity_path="Assets/Self/Check.mat",
        shader_name="Custom/SelfCheck",
    )
    if sample.is_file():
        recipe.maps[BASE_COLOR_MAP] = BitmapRecipe(path=str(sample), gamma=2.2, tiling=(2.0, 3.0))
        recipe.maps[BUMP_MAP] = BitmapRecipe(path=str(sample), gamma=1.0, is_normal=True)
    else:
        print(f"  note: no sample bitmap at {sample}; map checks skipped")

    material = build_material(recipe)
    passed = check(rt.classOf(material) == rt.PhysicalMaterial, "PhysicalMaterial is created")
    passed &= check(abs(material.metalness - 0.25) < 1e-6, "metalness is set")
    passed &= check(abs(material.roughness - 0.75) < 1e-6, "roughness is set")

    if recipe.maps:
        passed &= check(material.base_color_map is not None, "base colour map is assigned")
        passed &= check(bool(material.base_color_map_on), "its checkbox is on")
        coordinates = material.base_color_map.coords
        passed &= check(
            abs(coordinates.U_Tiling - 2.0) < 1e-6 and abs(coordinates.V_Tiling - 3.0) < 1e-6,
            "Unity tiling reached the bitmap",
        )
        passed &= check(
            rt.classOf(material.bump_map) == rt.Normal_Bump,
            "the normal map went through Normal Bump",
        )
        passed &= check(
            abs(material.bump_map_amt - 1.0) < 1e-6,
            f"bump amount is stated, not left at Max's 0.3 (got {material.bump_map_amt})",
        )

    record = read_provenance(material)
    passed &= check(record.get("ump_unity_guid") == "deadbeef", "provenance survives in AppData")
    return bool(passed)


def check_library() -> bool:
    print("== material library")
    from unity_material_max.material_builder import build_material
    from unity_material_max.recipe import MaterialRecipe

    library = rt.materialLibrary()
    rt.append(library, build_material(MaterialRecipe(name="UMP_SelfCheck_Lib")))

    destination = Path(tempfile.gettempdir()) / "ump_self_check.mat"
    saved = rt.saveTempMaterialLibrary(library, str(destination))
    passed = check(bool(saved), f"saveTempMaterialLibrary wrote {destination.name}")
    passed &= check(destination.is_file(), "the file is on disk")

    reloaded = rt.loadTempMaterialLibrary(str(destination))
    passed &= check(reloaded is not None and len(reloaded) == 1, "it loads back with one material")
    return bool(passed)


def check_real_library(root: Path) -> bool:
    print(f"== building from {root}")
    from unity_material_max.library_build import build_library

    result = build_library(root)
    passed = check(result.built > 0, f"materials built: {result.built}")
    passed &= check(result.libraries > 0, f".mat libraries written: {result.libraries}")
    passed &= check(result.failed == 0, f"failures: {result.failed}")
    for warning in result.warnings[:5]:
        print(f"    warning: {warning}")
    return bool(passed)


def main() -> int:
    print(f"3ds Max {rt.maxVersion()[7] if len(rt.maxVersion()) > 7 else ''} self check")
    passed = check_material()
    passed &= check_library()

    root = _library_argument()
    if root is not None and root.is_dir():
        passed &= check_real_library(root)
    else:
        print("== building from a real library: skipped (no library path given)")

    print("SELF CHECK PASSED" if passed else "SELF CHECK FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
