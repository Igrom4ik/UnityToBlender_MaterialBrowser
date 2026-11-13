"""Drag a Unity `.mat` into Blender and get a finished material on the object.

The whole point of this add-on is the moment of the drop, so the operator is
built around not stalling it. It converts with whatever is already indexed,
looks around the material for anything missing, and only when that fails does it
index the project -- on a worker thread, passing events through, so the viewport
keeps responding and Esc still works. The object under the cursor is captured
the instant the file lands, not when the scan finishes, so a slow first drop
still ends up on the right object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import traceback

import bpy
from bpy.props import CollectionProperty, StringProperty
from bpy.types import FileHandler, Operator

from . import builder, properties, session
from ._core import converter, fast_index
from .localization import LocalizedDescription, text as lt


MATERIAL_EXTENSION = ".mat"


@dataclass
class _Built:
    conversion: converter.Conversion
    material_name: str = ""
    action: str = builder.CREATED
    assigned: list = field(default_factory=list)
    faces: int = 0
    slot_only: bool = False
    unloaded: list = field(default_factory=list)


def _drop_paths(operator) -> list[Path]:
    directory = (operator.directory or "").strip()
    names = [item.name for item in operator.files if item.name] if operator.files else []
    if directory and names:
        paths = [Path(directory) / name for name in names]
    elif operator.filepath:
        paths = [Path(operator.filepath)]
    else:
        paths = []
    return [
        path
        for path in paths
        if path.suffix.casefold() == MATERIAL_EXTENSION and path.is_file()
    ]


_ACTION_TEXT = {
    builder.CREATED: ("created", "создан"),
    builder.REBUILT: ("rebuilt, Unity changed", "пересобран, Unity изменился"),
    builder.REUSED: ("reused, already in this file", "переиспользован, уже был в файле"),
    builder.KEPT: (
        "kept as it is, Unity has changed",
        "оставлен как есть, в Unity изменился",
    ),
}


def _report_rows(results: list[_Built], project: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for built in results:
        conversion = built.conversion
        icon = "CHECKMARK"
        if conversion.unresolved_textures or built.unloaded or not conversion.shader_resolved:
            icon = "ERROR"
        targets = ", ".join(built.assigned)
        if built.faces:
            where_en = f" -> {targets}, {built.faces} faces"
            where_ru = f" → {targets}, полигонов {built.faces}"
        elif targets:
            where_en, where_ru = f" -> {targets}", f" → {targets}"
        else:
            where_en, where_ru = " (not assigned)", " (не назначен)"
        rows.append(
            (
                f"{built.material_name}: {conversion.texture_count} maps{where_en}",
                f"{built.material_name}: карт {conversion.texture_count}{where_ru}",
                icon,
            )
        )
        action_en, action_ru = _ACTION_TEXT.get(built.action, ("", ""))
        if action_en:
            rows.append(
                (
                    f"   {action_en}",
                    f"   {action_ru}",
                    "DUPLICATE" if built.action == builder.REUSED else "DOT",
                )
            )
        if built.slot_only:
            rows.append(
                (
                    "   Edit Mode: no faces selected, added as a slot",
                    "   Режим редактирования: полигоны не выделены, добавлен слотом",
                    "INFO",
                )
            )
        if not conversion.shader_resolved:
            rows.append(
                (
                    f"   shader not found: {conversion.shader_name}",
                    f"   шейдер не найден: {conversion.shader_name}",
                    "ERROR",
                )
            )
        if conversion.unresolved_textures:
            names = ", ".join(conversion.unresolved_textures)
            rows.append(
                (
                    f"   textures not found by GUID: {names}",
                    f"   текстуры не найдены по GUID: {names}",
                    "ERROR",
                )
            )
        if built.unloaded:
            names = ", ".join(built.unloaded)
            rows.append(
                (
                    f"   Blender cannot read: {names}",
                    f"   Blender не читает файл: {names}",
                    "ERROR",
                )
            )
        if conversion.unmapped:
            names = ", ".join(conversion.unmapped)
            rows.append(
                (
                    f"   unmapped properties: {names}",
                    f"   не сопоставлены свойства: {names}",
                    "INFO",
                )
            )
    if project:
        rows.append((f"Project: {project}", f"Проект: {project}", "FILE_FOLDER"))
    return rows


class UMD_OT_drop_material(LocalizedDescription, Operator):
    """Build a Blender material from a Unity .mat and assign it"""

    bl_idname = "unity_material_drop.drop_material"
    bl_label = "Unity Material"
    bl_description = "Build a Blender material from a Unity .mat file and assign it to the object under the cursor"
    tooltip_ru = "Собрать материал Blender из файла Unity .mat и назначить его объекту под курсором"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"SKIP_SAVE"})
    files: CollectionProperty(
        type=bpy.types.OperatorFileListElement, options={"SKIP_SAVE"}
    )
    filter_glob: StringProperty(default="*.mat", options={"HIDDEN", "SKIP_SAVE"})

    _timer = None
    _job = None
    _paths: list = []
    _target_name = ""
    _fallback_name = ""
    _slot = 0
    _use_cursor = False
    _indexed = False
    _assets_root = None

    def invoke(self, context, event):
        self._paths = _drop_paths(self)
        if not self._paths:
            # Reached from the search menu or File > Import rather than a drop:
            # ask for the file instead of failing on an empty path.
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        area = getattr(context, "area", None)
        self._use_cursor = area is not None and area.type == "VIEW_3D"
        target, slot = (None, 0)
        if self._use_cursor:
            target, slot = builder.object_under_cursor(context, event)
        elif context.active_object is not None:
            target, slot = context.active_object, context.active_object.active_material_index
        self._target_name = target.name if target is not None else ""
        self._slot = slot
        # Captured now, not after a slow first index: by then the selection may
        # well have moved on.
        active = context.active_object
        self._fallback_name = active.name if active is not None else ""
        self._indexed = False
        return self._run(context)

    def execute(self, context):
        # Called without a drop: the panel and the clipboard import land here.
        self._paths = _drop_paths(self)
        if not self._paths:
            self.report(
                {"WARNING"}, lt("Choose a Unity .mat file", "Выберите файл Unity .mat")
            )
            return {"CANCELLED"}
        self._use_cursor = False
        active = context.active_object
        self._target_name = active.name if active is not None else ""
        self._fallback_name = self._target_name
        self._slot = active.active_material_index if active is not None else 0
        self._indexed = False
        return self._run(context)

    # -- pipeline ---------------------------------------------------------

    def _run(self, context):
        preferences = properties.get_preferences(context)
        state = properties.get_state(context)
        if preferences is None:
            self.report({"ERROR"}, "Add-on preferences are unavailable")
            return {"CANCELLED"}

        self._assets_root = fast_index.assets_root_of(self._paths[0])
        if self._assets_root is None:
            self.report(
                {"WARNING"},
                lt(
                    "This .mat is not inside a Unity project; textures cannot be resolved",
                    "Этот .mat лежит вне проекта Unity — текстуры разрешить не по чему",
                ),
            )

        conversions, discovered = self._convert(preferences)
        self._store_discovered(preferences, discovered)

        if preferences.auto_index and self._assets_root and not self._indexed:
            if self._worth_indexing(conversions):
                return self._start_index(context, preferences, state, conversions)
        return self._finish(context, conversions)

    def _worth_indexing(self, conversions) -> bool:
        """True when a fresh index could still find what is missing."""
        missing = [guid for item in conversions for guid in item.missing_guids]
        return bool(missing) and bool(session.worth_indexing(self._assets_root, missing))

    def _convert(self, preferences):
        cache = properties.cache_directory(preferences)
        index = (
            session.index_for(self._assets_root, cache)
            if self._assets_root is not None
            else None
        )
        rules = session.rules(bpy.path.abspath(preferences.profiles_path or "").strip())

        conversions = []
        discovered: dict[str, str] = {}
        for path in self._paths:
            try:
                conversion, found = converter.resolve_and_convert(
                    path,
                    index=index,
                    assets_root=self._assets_root,
                    rules=rules,
                    trust_all_tints=preferences.trust_all_tints,
                    nearby_seconds=preferences.nearby_seconds,
                )
            except Exception as error:
                # One unreadable material must not take the rest of the drop
                # down with it, and a drop that dies silently is worse still.
                traceback.print_exc()
                self.report({"ERROR"}, f"{path.name}: {type(error).__name__}: {error}")
                continue
            builder.normalize_conversion_identity(conversion)
            conversions.append(conversion)
            discovered.update(found)
        return conversions, discovered

    def _store_discovered(self, preferences, discovered: dict[str, str]) -> None:
        """Fold a nearby-search result into the cached index so it is free next time."""
        if not discovered or self._assets_root is None:
            return
        cache = properties.cache_directory(preferences)
        index = session.index_for(self._assets_root, cache)
        if index is None:
            index = fast_index.ProjectIndex(assets_root=Path(self._assets_root))
        index.merge(discovered)
        session.store(index, cache)

    # -- background indexing ----------------------------------------------

    def _start_index(self, context, preferences, state, conversions):
        if session.is_busy():
            self.report(
                {"WARNING"},
                lt("The project is already being indexed", "Проект уже индексируется"),
            )
            return self._finish(context, conversions)

        cache = properties.cache_directory(preferences)
        job = fast_index.IndexJob(
            fast_index.scan_roots(
                self._assets_root,
                include_packages=preferences.index_packages,
                include_package_cache=preferences.index_package_cache,
            ),
            assets_root=self._assets_root,
            previous=session.index_for(self._assets_root, cache),
            workers=preferences.index_workers,
        )
        job.start()
        session.set_job(job)
        self._job = job
        self._indexed = True

        properties.set_status(
            state,
            "Indexing the Unity project",
            "Индексируется проект Unity",
            "FILE_REFRESH",
        )
        if state is not None:
            state.busy = True
            state.progress = 0.0
            state.progress_label = ""
            state.assets_root = str(self._assets_root)

        if bpy.app.background:
            job.join()
            return self._collect_index(context)

        window_manager = context.window_manager
        self._timer = window_manager.event_timer_add(0.15, window=context.window)
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC" and event.value == "PRESS":
            if self._job is not None:
                self._job.cancel()
            self.report({"INFO"}, lt("Indexing cancelled", "Индексация отменена"))
            # The file was already dropped; abandoning the scan should still
            # leave the material behind, with the gaps named in the report.
            return self._collect_index(context)

        if event.type != "TIMER" or self._job is None:
            return {"PASS_THROUGH"}

        state = properties.get_state(context)
        progress = self._job.progress
        if state is not None:
            state.progress = progress.fraction
            state.progress_label = progress.message
        _redraw(context)

        if not self._job.finished:
            return {"PASS_THROUGH"}
        return self._collect_index(context)

    def _collect_index(self, context):
        preferences = properties.get_preferences(context)
        job = self._job
        self._release(context)
        if job is None:
            return {"CANCELLED"}

        if job.error:
            self.report({"ERROR"}, job.error)
        elif job.result is not None and preferences is not None:
            session.store(job.result, properties.cache_directory(preferences))

        if preferences is None:
            return {"CANCELLED"}
        conversions, discovered = self._convert(preferences)
        self._store_discovered(preferences, discovered)
        # Whatever a completed pass could not find is not in the project at all;
        # remembering that keeps the next drop of the same material instant. A
        # cancelled or failed scan proves nothing, so it records nothing.
        if self._assets_root is not None and job.result is not None and not job.cancelled:
            session.mark_unresolvable(
                self._assets_root,
                [guid for item in conversions for guid in item.missing_guids],
            )
        result = self._finish(context, conversions)
        # A modal operator has already left the undo step behind it, so the
        # materials it just created need a push of their own to be undoable.
        if not bpy.app.background and result == {"FINISHED"}:
            bpy.ops.ed.undo_push(message=lt("Drop Unity material", "Перетаскивание материала Unity"))
        return result

    def _release(self, context) -> None:
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        self._job = None
        session.set_job(None)
        state = properties.get_state(context)
        if state is not None:
            state.busy = False
            state.progress = 0.0
            state.progress_label = ""
        _redraw(context)

    def cancel(self, context):
        if self._job is not None:
            self._job.cancel()
        self._release(context)

    # -- building ---------------------------------------------------------

    @staticmethod
    def _edit_mode_meshes(context, preferences, mode: str) -> list:
        """Meshes open in Edit Mode, when faces are what the drop should hit."""
        if not preferences.edit_mode_faces or mode == properties.ASSIGN_NONE:
            return []
        if context.mode != "EDIT_MESH":
            return []
        in_mode = list(getattr(context, "objects_in_mode", None) or [])
        if not in_mode and context.active_object is not None:
            # `objects_in_mode` is a screen-level member and is missing without
            # a window, which is exactly the case in background Blender.
            in_mode = [context.active_object]
        return [
            target
            for target in in_mode
            if target.type == "MESH" and target.mode == "EDIT"
        ]

    def _finish(self, context, conversions):
        preferences = properties.get_preferences(context)
        state = properties.get_state(context)
        if preferences is None or not conversions:
            return {"CANCELLED"}

        target = bpy.data.objects.get(self._target_name) if self._target_name else None
        if target is None and self._fallback_name:
            # Released next to the object rather than exactly on it: the point
            # of the drop was still to get this material onto what is selected,
            # so fall back to the active object instead of doing nothing.
            target = bpy.data.objects.get(self._fallback_name)
            if target is not None:
                self._slot = target.active_material_index

        targets = [target] if target is not None else []
        if preferences.assign_selected:
            targets.extend(context.selected_objects)

        mode = preferences.assign_mode
        editing = self._edit_mode_meshes(context, preferences, mode)
        # The material is assigned as it already is, and only then is the
        # question asked. Cancelling the dialog then costs nothing: the drop has
        # already done what was asked of it, and a hand-edited node tree is
        # still standing.
        rebuild = preferences.update_mode == properties.UPDATE_ALWAYS
        outdated = [
            item for item in conversions if builder.pending_update(item, preferences)
        ]

        results: list[_Built] = []
        failures = []
        for position, conversion in enumerate(conversions):
            try:
                material, action = build_material(conversion, preferences, rebuild=rebuild)
            except Exception as error:
                failures.append(f"{conversion.name}: {error}")
                continue
            position = len(results)  # The first successful material owns the target.
            faces = 0
            if editing:
                # In Edit Mode the selection is the statement of intent, so the
                # cursor and the slot mode step aside. Only the first material
                # takes the faces; the rest would just overwrite it, so they
                # arrive as slots and say so in the report.
                if position:
                    assigned = builder.add_slot_only(material, editing)
                else:
                    assigned, faces = builder.assign_to_selected_faces(material, editing)
                    if not assigned:
                        assigned = builder.add_slot_only(material, editing)
            else:
                # Several materials dropped at once must not all land on the
                # same target: after the first, keep adding slots instead of
                # overwriting what the previous one just did.
                slot_mode = (
                    properties.ASSIGN_APPEND
                    if position
                    and mode in (properties.ASSIGN_SLOT, properties.ASSIGN_REPLACE_ALL)
                    else mode
                )
                assigned = builder.assign_to_many(
                    material, targets, mode=slot_mode, slot=self._slot
                )
            if getattr(conversion.plan, "vertex_paint", None):
                from .vertex_paint import ensure_color_attribute
                for obj in assigned:
                    ensure_color_attribute(obj)
            results.append(
                _Built(
                    conversion=conversion,
                    material_name=material.name,
                    action=action,
                    assigned=[item.name for item in assigned],
                    faces=faces,
                    slot_only=bool(editing) and not faces,
                    unloaded=builder.unloaded_textures(material, conversion.plan),
                )
            )

        _write_state(state, preferences, results, self._assets_root, context)
        _redraw(context)
        if failures:
            self.report({"WARNING"} if results else {"ERROR"}, "; ".join(failures))
        if not results:
            return {"CANCELLED"}
        succeeded = {id(item.conversion) for item in results}
        outdated = [item for item in outdated if id(item) in succeeded]

        if outdated and preferences.update_mode == properties.UPDATE_ASK:
            session.set_pending(
                {
                    "conversions": outdated,
                    "names": [item.material_name for item in results],
                    "assets_root": self._assets_root,
                }
            )
            if not bpy.app.background:
                bpy.ops.unity_material_drop.confirm_update("INVOKE_DEFAULT")

        first = results[0]
        if not targets and mode != properties.ASSIGN_NONE:
            self.report(
                {"INFO"},
                lt(
                    f"{first.material_name}: created, no object to assign it to",
                    f"{first.material_name}: создан, назначать не на что",
                ),
            )
        elif first.faces:
            self.report(
                {"INFO"},
                lt(
                    f"{first.material_name}: {first.conversion.texture_count} maps "
                    f"on {first.faces} faces",
                    f"{first.material_name}: карт {first.conversion.texture_count}, "
                    f"полигонов {first.faces}",
                ),
            )
        else:
            self.report(
                {"INFO"},
                lt(
                    f"{first.material_name}: {first.conversion.texture_count} maps",
                    f"{first.material_name}: карт {first.conversion.texture_count}",
                ),
            )
        return {"FINISHED"}


def build_material(conversion: converter.Conversion, preferences, *, rebuild: bool = True):
    from . import __version__

    return builder.build(conversion, preferences, version=__version__, rebuild=rebuild)


def _write_state(state, preferences, results, assets_root, context) -> None:
    if state is None:
        return
    project = str(Path(assets_root).parent) if assets_root else ""
    state.project = project
    state.assets_root = str(assets_root) if assets_root else ""
    state.material_name = results[0].material_name if results else ""

    index = session.cached_index(assets_root) if assets_root else None
    state.index_count = len(index.entries) if index is not None else 0
    state.index_built_at = index.built_at if index is not None else ""

    properties.set_report(
        state, _report_rows(results, project), limit=preferences.report_limit
    )
    broken = any(
        item.conversion.unresolved_textures
        or item.unloaded
        or not item.conversion.shader_resolved
        for item in results
    )
    if not results:
        properties.set_status(state, "Material build failed", "Не удалось собрать материал", "ERROR")
    elif broken:
        properties.set_status(
            state,
            "Material built with gaps -- see the report",
            "Материал собран с пробелами — смотрите отчёт",
            "ERROR",
        )
    else:
        properties.set_status(
            state,
            f"{results[0].material_name}: built from Unity",
            f"{results[0].material_name}: собран из Unity",
            "CHECKMARK",
        )


def _redraw(context) -> None:
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type in {"VIEW_3D", "PROPERTIES", "NODE_EDITOR"}:
            area.tag_redraw()


class UMD_FH_unity_material(FileHandler):
    bl_idname = "UMD_FH_unity_material"
    bl_label = "Unity material"
    bl_import_operator = UMD_OT_drop_material.bl_idname
    bl_file_extensions = MATERIAL_EXTENSION

    @classmethod
    def poll_drop(cls, context):
        area = getattr(context, "area", None)
        if area is None:
            return False
        if area.type in {"VIEW_3D", "PROPERTIES", "OUTLINER"}:
            return True
        if area.type == "NODE_EDITOR":
            return getattr(context.space_data, "tree_type", "") == "ShaderNodeTree"
        return False


def _menu_import(self, _context):
    self.layout.operator(
        UMD_OT_drop_material.bl_idname,
        text=lt("Unity Material (.mat)", "Материал Unity (.mat)"),
    )


_CLASSES = (UMD_OT_drop_material, UMD_FH_unity_material)


def register():
    from . import registration

    registration.register_classes(_CLASSES)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)


def unregister():
    from . import registration

    try:
        bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    except (AttributeError, ValueError):
        pass
    registration.unregister_classes(_CLASSES)
