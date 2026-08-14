"""End-to-end check inside Blender: install, extract, build, verify assets.

Run with:
    blender --background --factory-startup --python tests/blender_smoke.py -- \
        --zip dist/unity_material_browser-0.9.0.zip \
        --materials <unity materials folder> --assets <textures folder> \
        --assets <shaders folder> --library <output folder>

Only the folders passed here are read, so the shader folder has to be listed
too: without it every material falls back to the `material_only` backend.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

import bpy


PACKAGE = "bl_ext.user_default.unity_material_browser"


def parse_arguments():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True)
    parser.add_argument("--materials", required=True, action="append")
    parser.add_argument("--assets", action="append", default=[])
    parser.add_argument("--library", required=True)
    parser.add_argument("--previews", action="store_true")
    return parser.parse_args(argv)


def install(zip_path: str) -> None:
    bpy.ops.extensions.package_install_files(
        filepath=zip_path,
        repo="user_default",
        enable_on_install=True,
        overwrite=True,
    )
    if PACKAGE not in bpy.context.preferences.addons:
        raise SystemExit(f"Extension was not enabled: {PACKAGE}")


def configure(arguments) -> None:
    preferences = bpy.context.preferences.addons[PACKAGE].preferences
    preferences.library_path = arguments.library
    preferences.generate_previews = arguments.previews
    preferences.sources.clear()
    for path in arguments.materials:
        source = preferences.sources.add()
        source.kind = "MATERIALS"
        source.path = path
    for path in arguments.assets:
        source = preferences.sources.add()
        source.kind = "ASSETS"
        source.path = path


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"  ok: {message}")


def main() -> None:
    arguments = parse_arguments()
    library = Path(arguments.library)
    if library.exists():
        shutil.rmtree(library)

    print("== installing extension")
    install(arguments.zip)
    configure(arguments)

    print("== extract")
    result = bpy.ops.umb.extract()
    check("FINISHED" in result, "extract operator finished")
    state = bpy.context.window_manager.umb_state
    check(state.material_count > 0, f"materials extracted: {state.material_count}")
    check(state.shader_count > 0, f"shaders parsed: {state.shader_count}")
    check((library / "_ump_index.json").is_file(), "index written")
    check((library / ".gitignore").is_file(), "library is excluded from git")

    print("== build")
    result = bpy.ops.umb.build(changed_only=False)
    check("FINISHED" in result, "build operator finished")
    check(state.built_count > 0, f"assets built: {state.built_count}")
    check((library / "blender_assets.cats.txt").is_file(), "catalog file written")

    blends = sorted(library.rglob("*.blend"))
    check(bool(blends), f"blend files written: {len(blends)}")

    print("== verifying a generated file")
    with bpy.data.libraries.load(str(blends[0]), link=False) as (source, target):
        names = list(source.materials)
        target.materials = names[:3]
    check(bool(names), f"materials inside {blends[0].name}: {len(names)}")

    checked_nodes = False
    for material in bpy.data.materials:
        if material.get("ump_unity_guid") is None and material.get("ump_unity_path") is None:
            continue
        check(material.node_tree is not None, f"{material.name} has a node tree")
        principled = [n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"]
        check(bool(principled), f"{material.name} has a Principled BSDF")
        images = [
            n.image
            for n in material.node_tree.nodes
            if n.type == "TEX_IMAGE" and n.image is not None
        ]
        for image in images:
            path = Path(bpy.path.abspath(image.filepath))
            check(path.is_file(), f"texture path resolves: {path.name}")
            check(
                library not in path.parents,
                f"texture stays outside the library: {path.name}",
            )
        checked_nodes = True
        break
    check(checked_nodes, "at least one built material was inspected")

    print("== diff")
    result = bpy.ops.umb.check_updates()
    check("FINISHED" in result, "check updates finished")
    check(state.diff_changed == 0, f"no spurious changes reported: {state.diff_changed}")
    check(state.diff_new == 0, f"no unknown materials reported: {state.diff_new}")

    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
