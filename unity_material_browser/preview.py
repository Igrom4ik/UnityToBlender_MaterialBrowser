"""Render asset thumbnails for generated materials.

Blender's own preview generation is asynchronous and does not run in background
mode, so previews are rendered explicitly in a throwaway scene. The result is
saved next to the material as a PNG and pushed into the datablock preview, so a
rebuild can reuse the image instead of rendering again.
"""

from __future__ import annotations

from pathlib import Path

import bpy


SCENE_NAME = "UMB Preview Scene"
OBJECT_NAME = "UMB Preview Object"
CAMERA_NAME = "UMB Preview Camera"
LIGHT_NAME = "UMB Preview Light"
WORLD_NAME = "UMB Preview World"

SHAPE_SPHERE = "SPHERE"
SHAPE_CUBE = "CUBE"
SHAPE_PLANE = "PLANE"


def _create_mesh(shape: str):
    mesh = bpy.data.meshes.new(OBJECT_NAME)
    import bmesh

    builder = bmesh.new()
    if shape == SHAPE_CUBE:
        bmesh.ops.create_cube(builder, size=1.6)
    elif shape == SHAPE_PLANE:
        bmesh.ops.create_grid(builder, x_segments=1, y_segments=1, size=1.0)
    else:
        bmesh.ops.create_uvsphere(builder, u_segments=48, v_segments=32, radius=1.0)
    bmesh.ops.recalc_face_normals(builder, faces=builder.faces)
    builder.to_mesh(mesh)
    builder.free()

    # A UV layer is required, otherwise every texture samples a single texel.
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    for polygon in mesh.polygons:
        polygon.use_smooth = shape == SHAPE_SPHERE
    return mesh


def ensure_scene(shape: str = SHAPE_SPHERE, size: int = 128):
    """Build (or reuse) the isolated scene used for thumbnails."""
    scene = bpy.data.scenes.get(SCENE_NAME)
    if scene is None:
        scene = bpy.data.scenes.new(SCENE_NAME)

    scene.render.engine = "BLENDER_EEVEE_NEXT" if _has_eevee_next() else "BLENDER_EEVEE"
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = 16

    world = bpy.data.worlds.get(WORLD_NAME) or bpy.data.worlds.new(WORLD_NAME)
    if world.node_tree is None:
        world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs[0].default_value = (0.05, 0.05, 0.05, 1.0)
        background.inputs[1].default_value = 1.0
    scene.world = world

    object_ = bpy.data.objects.get(OBJECT_NAME)
    if object_ is None or object_.data is None or object_.get("umb_shape") != shape:
        if object_ is not None:
            bpy.data.objects.remove(object_)
        object_ = bpy.data.objects.new(OBJECT_NAME, _create_mesh(shape))
        object_["umb_shape"] = shape
    if object_.name not in scene.collection.objects:
        scene.collection.objects.link(object_)
    object_.data.materials.clear()
    object_.data.materials.append(None)

    camera = bpy.data.objects.get(CAMERA_NAME)
    if camera is None:
        camera_data = bpy.data.cameras.new(CAMERA_NAME)
        camera_data.lens = 60
        camera = bpy.data.objects.new(CAMERA_NAME, camera_data)
    camera.location = (0.0, -3.2, 1.6)
    camera.rotation_euler = (1.1, 0.0, 0.0)
    if camera.name not in scene.collection.objects:
        scene.collection.objects.link(camera)
    scene.camera = camera

    light = bpy.data.objects.get(LIGHT_NAME)
    if light is None:
        light_data = bpy.data.lights.new(LIGHT_NAME, type="AREA")
        light_data.energy = 400.0
        light_data.size = 5.0
        light = bpy.data.objects.new(LIGHT_NAME, light_data)
    light.location = (2.5, -3.0, 4.0)
    light.rotation_euler = (0.6, 0.2, 0.6)
    if light.name not in scene.collection.objects:
        scene.collection.objects.link(light)

    return scene, object_


def _has_eevee_next() -> bool:
    try:
        return "BLENDER_EEVEE_NEXT" in {
            item.identifier
            for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
        }
    except (KeyError, AttributeError):
        return False


def render_to_file(material, filepath: str | Path, *, shape: str = SHAPE_SPHERE, size: int = 128) -> bool:
    """Render one material thumbnail. Returns False when rendering is unavailable."""
    destination = Path(filepath)
    destination.parent.mkdir(parents=True, exist_ok=True)

    scene, object_ = ensure_scene(shape, size)
    object_.data.materials[0] = material
    scene.render.filepath = str(destination)

    try:
        with bpy.context.temp_override(scene=scene):
            bpy.ops.render.render(write_still=True)
    except (RuntimeError, TypeError):
        return False
    finally:
        object_.data.materials[0] = None
    return destination.is_file()


def apply_from_file(material, filepath: str | Path) -> bool:
    """Copy a rendered PNG into the datablock preview shown by the browser."""
    path = Path(filepath)
    if not path.is_file():
        return False
    try:
        image = bpy.data.images.load(str(path), check_existing=False)
    except RuntimeError:
        return False

    try:
        width, height = image.size
        if width <= 0 or height <= 0:
            return False
        pixels = list(image.pixels)
        material.preview_ensure()
        preview = material.preview
        if preview is None:
            return False
        preview.image_size = (width, height)
        preview.image_pixels_float.foreach_set(pixels)
        return True
    except (AttributeError, RuntimeError, ValueError):
        return False
    finally:
        bpy.data.images.remove(image)


def generate(material, filepath: str | Path, *, shape: str = SHAPE_SPHERE, size: int = 128) -> bool:
    path = Path(filepath)
    if path.is_file() and apply_from_file(material, path):
        return True
    if not render_to_file(material, path, shape=shape, size=size):
        return False
    return apply_from_file(material, path)


def cleanup() -> None:
    """Remove the preview scene so it never reaches a saved file."""
    for collection, name in (
        (bpy.data.objects, OBJECT_NAME),
        (bpy.data.objects, CAMERA_NAME),
        (bpy.data.objects, LIGHT_NAME),
        (bpy.data.scenes, SCENE_NAME),
        (bpy.data.worlds, WORLD_NAME),
    ):
        item = collection.get(name)
        if item is not None:
            try:
                collection.remove(item)
            except (RuntimeError, ReferenceError):
                pass
    for mesh in list(bpy.data.meshes):
        if mesh.name.startswith(OBJECT_NAME) and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
