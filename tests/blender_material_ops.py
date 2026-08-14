"""Checks that only Blender can answer: import method, editable copy, undo.

Run with:
    blender --background --factory-startup --python tests/blender_material_ops.py -- \
        --zip dist/unity_material_browser-0.12.1.zip --library <a built library folder>

The library folder must already contain a built .blend (see blender_smoke.py).
Preferences are never saved: --factory-startup keeps this out of the real
configuration, apart from the extension itself being installed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import bpy


PACKAGE = "bl_ext.user_default.unity_material_browser"


def parse_arguments():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True)
    parser.add_argument("--library", required=True)
    return parser.parse_args(argv)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"  ok: {message}")


def install(zip_path: str):
    bpy.ops.extensions.package_install_files(
        filepath=zip_path, repo="user_default", enable_on_install=True, overwrite=True
    )
    if PACKAGE not in bpy.context.preferences.addons:
        raise SystemExit(f"Extension was not enabled: {PACKAGE}")
    return bpy.context.preferences.addons[PACKAGE].preferences


def check_registration() -> None:
    print("== registration")
    check(
        hasattr(bpy.types, "UMB_MT_material_pie") and hasattr(bpy.types, "UMB_PT_material_popover"),
        "pie menu and popover are registered",
    )
    check(
        any(
            item.idname == "wm.call_menu_pie"
            for keymap in bpy.context.window_manager.keyconfigs.addon.keymaps
            for item in keymap.keymap_items
            if item.properties.get("name") == "UMB_MT_material_pie"
        ),
        "the pie has a key binding",
    )


def check_import_method(preferences, library: Path) -> None:
    print("== import method")
    check(preferences.import_method == "APPEND", "the default is an appended copy")

    preferences.library_path = str(library)
    bpy.ops.umb.register_asset_library()
    entry = next(
        item
        for item in bpy.context.preferences.filepaths.asset_libraries
        if Path(bpy.path.abspath(item.path)) == library
    )
    check(entry.import_method == "APPEND", "registering applies the chosen method")

    preferences.import_method = "LINK"
    check(entry.import_method == "LINK", "switching the setting reaches the library at once")

    # PACK is what Blender 5.2 gives a new library on its own: linked, packed,
    # never updated by a rebuild. It must not survive.
    entry.import_method = "PACK"
    bpy.ops.umb.register_asset_library()
    check(entry.import_method == "LINK", "re-registering repairs a PACK library")


def check_editable_copy(library: Path) -> None:
    print("== editable copy")
    blend = next(library.rglob("*.blend"))
    with bpy.data.libraries.load(str(blend), link=True) as (source, target):
        target.materials = source.materials[:1]
    linked = target.materials[0]
    check(linked.library is not None, f"a dragged-in link behaves like this: {linked.name}")

    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.object
    cube.data.materials.append(linked)
    bpy.context.view_layer.objects.active = cube

    check("FINISHED" in bpy.ops.umb.make_editable_copy(new_name="Copy_For_Editing"), "operator ran")
    copy = bpy.data.materials.get("Copy_For_Editing")
    check(copy is not None and copy.library is None, "the copy is local and editable")
    check(cube.material_slots[0].material is copy, "the copy replaced the linked material")
    check(copy.get("ump_unity_guid") is None, "the copy stops claiming to mirror Unity")
    check(copy.get("ump_derived_from") == linked.get("ump_unity_guid"), "origin guid recorded")
    check(copy.get("ump_derived_name") == linked.name, "origin name recorded")
    check(copy.asset_data is None, "the copy is not published as an asset")
    for node in copy.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image:
            path = Path(bpy.path.abspath(node.image.filepath))
            check(path.is_file(), f"texture still points into Unity: {path.name}")

    before = len(bpy.data.materials)
    try:
        bpy.ops.umb.make_editable_copy(new_name="Copy_For_Editing")
        refused = False
    except RuntimeError:
        refused = True
    check(refused, "a name that is already taken is refused")
    check(len(bpy.data.materials) == before, "the refused call created nothing")

    from bl_ext.user_default.unity_material_browser import operators as umb_operators

    # The operator must stay out of the Adjust Last Operation panel: re-running
    # it there would hit the copy it just made and refuse its own name.
    options = umb_operators.UMB_OT_MakeEditableCopy.bl_options
    check("REGISTER" not in options, "the operator does not offer a redo panel")
    check("UNDO" in options, "the copy is undoable")

    # A copy of a copy keeps counting rather than stacking _edit suffixes.
    copy.name = "Wall_edit_01"
    check(
        umb_operators._default_copy_name(copy) == "Wall_edit",
        f"a copy of a copy is named sensibly: {umb_operators._default_copy_name(copy)}",
    )


def check_undo(library: Path) -> None:
    """Why Append is the default: only an appended drop undoes cleanly.

    The linked case is Blender's own behaviour and it varies with whether the
    linked data is still used, so it is reported rather than asserted. The
    promise the add-on makes is about the default.
    """
    print("== undo after a drop")
    blend = next(library.rglob("*.blend"))

    for link in (True, False):
        for object_ in list(bpy.data.objects):
            bpy.data.objects.remove(object_)
        for material in list(bpy.data.materials):
            bpy.data.materials.remove(material)
        for lib in list(bpy.data.libraries):
            bpy.data.libraries.remove(lib)

        bpy.ops.mesh.primitive_cube_add()
        bpy.context.object.name = "Probe"
        bpy.ops.ed.undo_push(message="before drop")

        with bpy.data.libraries.load(str(blend), link=link) as (source, target):
            target.materials = source.materials[:1]
        name = target.materials[0].name
        bpy.data.objects["Probe"].data.materials.append(bpy.data.materials[name])
        bpy.ops.ed.undo_push(message="dropped")
        bpy.ops.ed.undo()

        left_over = name in bpy.data.materials or len(bpy.data.libraries) > 0
        if link:
            print(f"  note: after undo a linked drop leaves data behind: {left_over}")
        else:
            check(not left_over, "an appended drop is undone completely")


def main() -> None:
    arguments = parse_arguments()
    library = Path(arguments.library)
    if not any(library.rglob("*.blend")):
        raise SystemExit(
            f"No built .blend in {library}: run tests/blender_smoke.py on it first"
        )
    preferences = install(arguments.zip)

    check_registration()
    check_import_method(preferences, library)
    check_editable_copy(library)
    check_undo(library)

    print("MATERIAL OPS TEST PASSED")


if __name__ == "__main__":
    main()
