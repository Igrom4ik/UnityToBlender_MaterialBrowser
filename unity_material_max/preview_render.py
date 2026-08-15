"""Render a material on a sphere, the way a browser icon looks.

The texture tile says which material this is; a rendered ball says what it will
look like. There is no MAXScript call that renders a material on its own --
`renderMap` takes a texmap, not a material -- so the sphere, the light and the
camera are made, rendered and deleted again.

Everything this touches is put back: the temporary objects go, the renderer and
the selection are restored, and none of it enters the undo stack. A preview
pass that leaves a sphere in the artist's scene is worse than no preview.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class RenderResult:
    written: int = 0
    reason: str = ""
    renderer: str = ""


# Physical Material is a PBR material, and the renderers differ in how much of
# it they understand. Quicksilver draws cutout and transparency and is built for
# exactly this -- fast preview frames. Scanline is the last resort: it always
# exists, and it renders an alpha-cut material as a solid ball.
RENDERERS = ("Quicksilver_Hardware_Renderer", "ART_Renderer", "Default_Scanline_Renderer")


PREFIX = "__ump_preview_"


def purge(runtime=None) -> int:
    """Delete every leftover preview object; returns how many there were.

    The pass deletes its own sphere, camera and lights, but "deletes them at
    the end" only holds if there is an end: a crash, a closed window or a
    cancelled render can all leave them behind, and they turn up in somebody
    else's scene explorer with no explanation. So the pass also starts by
    clearing whatever an earlier one left, and the window clears them when it
    closes.
    """
    if runtime is None:
        import pymxs  # noqa: PLC0415  (inside Max only)

        runtime = pymxs.runtime

    # Collected first: deleting while walking the scene skips objects.
    doomed = [node for node in runtime.objects if str(node.name).startswith(PREFIX)]
    removed = 0
    for node in doomed:
        try:
            runtime.delete(node)
            removed += 1
        except Exception:  # noqa: BLE001  (already gone is fine)
            pass
    return removed


def _render_to_file(runtime, camera, size: int, destination: Path) -> str:
    """Render the current sphere and write the PNG; returns "" or the reason.

    `render()` hands back the rendered bitmap, which is the one part of the
    call that every release agrees on; the file is written from that rather
    than through an output-file argument whose spelling has moved around.

    The reason travels back rather than being swallowed: a preview pass that
    reports "0 rendered" and no cause is a dead end for whoever has to fix it.
    """
    frame = None
    try:
        frame = runtime.render(
            camera=camera, outputSize=runtime.Point2(size, size), vfb=False
        )
        if frame is None:
            return "render() returned nothing"
        frame.filename = str(destination)
        runtime.save(frame)
    except Exception as error:  # noqa: BLE001  (one material, not the whole pass)
        return f"{type(error).__name__}: {error}"
    finally:
        if frame is not None:
            try:
                runtime.close(frame)
            except Exception:  # noqa: BLE001
                pass
    return "" if destination.is_file() else "the file was not written"


def _pick_renderer(runtime) -> str:
    """Switch to the best renderer this 3ds Max has, and say which it was."""
    for name in RENDERERS:
        factory = getattr(runtime, name, None)
        if factory is None:
            continue
        try:
            runtime.renderers.current = factory()
        except Exception:  # noqa: BLE001  (not installed, or refused)
            continue
        return name
    return ""


def _backdrop(runtime):
    """A checkered wall behind the sphere.

    Transparency is only visible against something. On the black of an empty
    scene an alpha-cut material renders as a black ball, which is precisely how
    "transparency does not work" looks.
    """
    plane = runtime.Plane(
        length=9.0, width=9.0, name=f"{PREFIX}backdrop", pos=runtime.Point3(0, 3.2, 0)
    )
    try:
        runtime.rotate(plane, runtime.angleaxis(90, runtime.Point3(1, 0, 0)))
    except Exception:  # noqa: BLE001  (flat on the floor is still a backdrop)
        pass

    checker = runtime.Checker(
        color1=runtime.color(150, 150, 150), color2=runtime.color(95, 95, 95)
    )
    try:
        checker.coords.U_Tiling = 5.0
        checker.coords.V_Tiling = 5.0
    except Exception:  # noqa: BLE001
        pass

    material = runtime.PhysicalMaterial(name=f"{PREFIX}backdrop_material", roughness=0.95)
    material.base_color_map = checker
    material.base_color_map_on = True
    plane.material = material
    return plane


def _make_scene(runtime, size: int):
    """A sphere, two lights and a camera looking at it."""
    sphere = runtime.Sphere(
        radius=1.0,
        segs=48,
        smooth=True,
        # Without mapping coordinates the sphere renders untextured, which is
        # exactly the flat grey the preview is meant to replace.
        mapcoords=True,
        name=f"{PREFIX}sphere",
        pos=runtime.Point3(0, 0, 0),
    )
    target = runtime.Targetobject(pos=runtime.Point3(0, 0, 0), name=f"{PREFIX}target")
    camera = runtime.Targetcamera(
        pos=runtime.Point3(0, -4.2, 1.4), target=target, fov=28.0, name=f"{PREFIX}camera"
    )
    key = runtime.Omnilight(
        pos=runtime.Point3(-3.5, -4.0, 4.0), multiplier=1.2, name=f"{PREFIX}key"
    )
    fill = runtime.Omnilight(
        pos=runtime.Point3(4.0, -3.0, 1.0), multiplier=0.45, name=f"{PREFIX}fill"
    )
    backdrop = _backdrop(runtime)
    return sphere, camera, [sphere, target, camera, key, fill, backdrop]


def render_previews(jobs, size: int = 256, progress=None):
    """Render `(material, destination)` pairs.

    Returns a `RenderResult`: how many were written, why the first failure
    failed, and which renderer drew them.

    `progress(fraction, name)` returning False stops the pass: four hundred
    renders is long enough that stopping has to be possible.
    """
    import pymxs  # noqa: PLC0415  (inside Max only)

    runtime = pymxs.runtime
    jobs = list(jobs)
    result = RenderResult()
    if not jobs:
        return result

    written = 0
    reason = ""
    created = []
    previous_renderer = None
    previous_selection = list(runtime.selection)

    with pymxs.undo(False):
        try:
            # Anything an earlier pass left behind goes first, so a scene never
            # collects two sets of these.
            purge(runtime)
            sphere, camera, created = _make_scene(runtime, size)

            previous_renderer = runtime.renderers.current
            result.renderer = _pick_renderer(runtime)
            if not result.renderer:
                previous_renderer = None

            for position, (material, destination) in enumerate(jobs, start=1):
                if progress is not None and not progress(position / len(jobs), str(material.name)):
                    break
                destination = Path(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                sphere.material = material
                failure = _render_to_file(runtime, camera, size, destination)
                if failure:
                    reason = reason or f"{material.name}: {failure}"
                else:
                    written += 1
        finally:
            for node in created:
                try:
                    runtime.delete(node)
                except Exception:  # noqa: BLE001
                    pass
            # Belt and braces: if the list above was never filled -- a failure
            # halfway through building the scene -- the objects are still there
            # and still ours.
            try:
                purge(runtime)
            except Exception:  # noqa: BLE001
                pass
            if previous_renderer is not None:
                try:
                    runtime.renderers.current = previous_renderer
                except Exception:  # noqa: BLE001
                    pass
            try:
                runtime.select(previous_selection)
            except Exception:  # noqa: BLE001
                pass

    result.written = written
    result.reason = reason
    return result
