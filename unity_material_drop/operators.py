"""Everything the panel can do that is not a drop.

Indexing gets the same worker-thread treatment as the drop: it is the one slow
operation in the add-on, and Blender must stay usable while it runs.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator

from . import properties, registration, session
from ._core import converter, fast_index
from .localization import LocalizedDescription, property_name, text as lt


def _rules(preferences):
    return session.rules(bpy.path.abspath(preferences.profiles_path or "").strip())


class UMD_OT_index_project(LocalizedDescription, Operator):
    """Read every texture and shader GUID of a Unity project into the cache"""

    bl_idname = "unity_material_drop.index_project"
    bl_label = "Index Unity project"
    bl_description = (
        "Read the GUID of every texture and shader in a Unity project once, so later drops "
        "resolve instantly. Only folders that changed are re-read"
    )
    tooltip_ru = (
        "Один раз прочитать GUID всех текстур и шейдеров проекта Unity, чтобы дальнейшие "
        "перетаскивания разрешались мгновенно. Перечитываются только изменившиеся папки"
    )
    bl_options = {"REGISTER"}

    directory: StringProperty(subtype="DIR_PATH", options={"SKIP_SAVE"})
    full: BoolProperty(
        name="Read everything again",
        description="Ignore folder timestamps and re-read every .meta file",
        default=False,
        options={"SKIP_SAVE"},
    )

    _timer = None
    _job = None
    _assets_root = None

    def invoke(self, context, event):
        state = properties.get_state(context)
        known = (self.directory or "").strip() or (state.assets_root if state else "")
        if known and fast_index.assets_root_of(known) is not None:
            self.directory = known
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        preferences = properties.get_preferences(context)
        state = properties.get_state(context)
        if preferences is None:
            return {"CANCELLED"}
        if session.is_busy():
            self.report(
                {"WARNING"}, lt("Indexing is already running", "Индексация уже выполняется")
            )
            return {"CANCELLED"}

        assets_root = fast_index.assets_root_of(bpy.path.abspath(self.directory or ""))
        if assets_root is None:
            self.report(
                {"ERROR"},
                lt(
                    "That folder is not inside a Unity project (no Assets and ProjectSettings)",
                    "Эта папка не внутри проекта Unity (нет Assets и ProjectSettings)",
                ),
            )
            return {"CANCELLED"}

        self._assets_root = assets_root
        cache = properties.cache_directory(preferences)
        job = fast_index.IndexJob(
            fast_index.scan_roots(
                assets_root,
                include_packages=preferences.index_packages,
                include_package_cache=preferences.index_package_cache,
            ),
            assets_root=assets_root,
            previous=None if self.full else session.index_for(assets_root, cache),
            workers=preferences.index_workers,
            full=self.full,
        )
        job.start()
        session.set_job(job)
        self._job = job

        if state is not None:
            state.busy = True
            state.progress = 0.0
            state.progress_label = ""
            state.assets_root = str(assets_root)
            state.project = str(assets_root.parent)
        properties.set_status(
            state, "Indexing the Unity project", "Индексируется проект Unity", "FILE_REFRESH"
        )

        if bpy.app.background:
            job.join()
            return self._collect(context)

        self._timer = context.window_manager.event_timer_add(0.15, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC" and event.value == "PRESS":
            if self._job is not None:
                self._job.cancel()
            self._release(context)
            self.report({"INFO"}, lt("Indexing cancelled", "Индексация отменена"))
            return {"CANCELLED"}
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
        return self._collect(context)

    def _collect(self, context):
        preferences = properties.get_preferences(context)
        state = properties.get_state(context)
        job = self._job
        self._release(context)
        if job is None or preferences is None:
            return {"CANCELLED"}
        if job.error:
            self.report({"ERROR"}, job.error)
            properties.set_status(state, job.error, job.error, "ERROR")
            return {"CANCELLED"}
        if job.result is None:
            return {"CANCELLED"}

        session.store(job.result, properties.cache_directory(preferences))
        count = len(job.result.entries)
        if state is not None:
            state.index_count = count
            state.index_built_at = job.result.built_at
        properties.set_status(
            state,
            f"Index ready: {count} assets",
            f"Индекс готов: ассетов {count}",
            "CHECKMARK",
        )
        self.report(
            {"INFO"},
            lt(f"Indexed {count} assets", f"Проиндексировано ассетов: {count}"),
        )
        return {"FINISHED"}

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


class UMD_OT_clear_index(LocalizedDescription, Operator):
    """Forget the cached GUID index of the current project"""

    bl_idname = "unity_material_drop.clear_index"
    bl_label = "Clear index"
    bl_description = "Delete the cached GUID index; the next drop rebuilds what it needs"
    tooltip_ru = "Удалить кэш индекса GUID; следующее перетаскивание соберёт нужное заново"
    bl_options = {"REGISTER"}

    def execute(self, context):
        preferences = properties.get_preferences(context)
        state = properties.get_state(context)
        if preferences is None or state is None or not state.assets_root:
            self.report({"WARNING"}, lt("No indexed project yet", "Проект ещё не индексировался"))
            return {"CANCELLED"}
        cache = properties.cache_directory(preferences)
        session.forget(state.assets_root)
        try:
            fast_index.remove_index(cache, state.assets_root)
        except OSError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        state.index_count = 0
        state.index_built_at = ""
        properties.set_status(state, "Index cleared", "Индекс очищен", "TRASH")
        return {"FINISHED"}


class UMD_OT_drop_from_clipboard(LocalizedDescription, Operator):
    """Build the material whose path is in the clipboard"""

    bl_idname = "unity_material_drop.from_clipboard"
    bl_label = "From clipboard"
    bl_description = (
        "Take a path to a .mat file from the clipboard and build the material. "
        "Use it when the file cannot be dragged, for example straight out of Unity"
    )
    tooltip_ru = (
        "Взять путь к файлу .mat из буфера обмена и собрать материал. "
        "Пригодится, когда файл нельзя перетащить — например, прямо из Unity"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        raw = (context.window_manager.clipboard or "").strip().strip('"')
        path = Path(raw) if raw else None
        if path is None or path.suffix.casefold() != ".mat" or not path.is_file():
            self.report(
                {"WARNING"},
                lt(
                    "The clipboard does not hold a path to a .mat file",
                    "В буфере обмена нет пути к файлу .mat",
                ),
            )
            return {"CANCELLED"}
        return bpy.ops.unity_material_drop.drop_material(filepath=str(path))


class UMD_OT_reload_material(LocalizedDescription, Operator):
    """Rebuild the active material from its Unity source"""

    bl_idname = "unity_material_drop.reload_material"
    bl_label = "Reload from Unity"
    bl_description = "Read the .mat file again and rebuild this material's node tree"
    tooltip_ru = "Перечитать файл .mat и пересобрать дерево узлов этого материала"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        material = getattr(context, "material", None) or (
            context.active_object.active_material if context.active_object else None
        )
        return material is not None and bool(material.get("umd_source"))

    def execute(self, context):
        preferences = properties.get_preferences(context)
        state = properties.get_state(context)
        material = getattr(context, "material", None) or (
            context.active_object.active_material if context.active_object else None
        )
        if preferences is None or material is None:
            return {"CANCELLED"}

        source = Path(str(material.get("umd_source", "")))
        if not source.is_file():
            self.report(
                {"ERROR"},
                lt(f"Source is missing: {source}", f"Исходный файл не найден: {source}"),
            )
            return {"CANCELLED"}

        assets_root = fast_index.assets_root_of(source)
        cache = properties.cache_directory(preferences)
        index = session.index_for(assets_root, cache) if assets_root else None
        conversion, found = converter.resolve_and_convert(
            source,
            index=index,
            assets_root=assets_root,
            rules=_rules(preferences),
            trust_all_tints=preferences.trust_all_tints,
            nearby_seconds=preferences.nearby_seconds,
        )
        if found and assets_root is not None:
            if index is None:
                index = fast_index.ProjectIndex(assets_root=Path(assets_root))
            index.merge(found)
            session.store(index, cache)

        from . import __version__
        from . import node_builder, builder

        builder.normalize_conversion_identity(conversion)
        node_builder.build_material(
            material,
            conversion.plan,
            metadata=converter.metadata(conversion, builder_version=__version__),
                document=conversion.document,
        )
        if getattr(conversion.plan, "vertex_paint", None):
            from .vertex_paint import ensure_color_attribute
            ensure_color_attribute(context.active_object)
        properties.set_status(
            state,
            f"{material.name}: reloaded from Unity",
            f"{material.name}: перечитан из Unity",
            "FILE_REFRESH",
        )
        self.report(
            {"INFO"},
            lt(
                f"{material.name}: {conversion.texture_count} maps",
                f"{material.name}: карт {conversion.texture_count}",
            ),
        )
        return {"FINISHED"}


class UMD_OT_confirm_update(LocalizedDescription, Operator):
    """Ask before rebuilding a material that Unity has changed"""

    bl_idname = "unity_material_drop.confirm_update"
    bl_label = "Update from Unity"
    bl_description = (
        "The dropped material was already in this file and its .mat has changed since. "
        "Rebuild its node tree from Unity, or keep the one in this file"
    )
    tooltip_ru = (
        "Перетащенный материал уже был в файле, и его .mat с тех пор изменился. "
        "Пересобрать дерево узлов из Unity или оставить то, что в файле"
    )
    bl_options = {"REGISTER", "UNDO"}

    remember: BoolProperty(
        name="Remember this answer",
        description="Save the choice as the default and stop asking",
        default=False,
        options={"SKIP_SAVE"},
    )
    remember_ru: BoolProperty(
        name="Запомнить ответ",
        description="Сохранить выбор по умолчанию и больше не спрашивать",
        get=lambda self: self.remember,
        set=lambda self, value: setattr(self, "remember", value),
        options={"SKIP_SAVE"},
    )

    def invoke(self, context, event):
        if not session.pending():
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        pending = session.pending() or {}
        names = [item.name for item in pending.get("conversions", ())]

        column = layout.column(align=True)
        column.label(
            text=lt(
                "These materials changed in Unity since they were built:",
                "Эти материалы изменились в Unity после сборки:",
            ),
            icon="FILE_REFRESH",
        )
        for name in names[:6]:
            column.label(text=f"    {name}")
        if len(names) > 6:
            column.label(text=f"    ... {len(names) - 6}")

        column = layout.column(align=True)
        column.label(
            text=lt(
                "They are already assigned. Rebuild their node trees?",
                "Они уже назначены. Пересобрать их дерево узлов?",
            )
        )
        column.label(
            text=lt(
                "Cancel keeps what is in this file, edits included.",
                "Отмена оставит то, что в файле, вместе с правками.",
            ),
            icon="INFO",
        )
        layout.prop(self, property_name("remember"))

    def execute(self, context):
        from . import __version__, builder

        preferences = properties.get_preferences(context)
        state = properties.get_state(context)
        pending = session.pending() or {}
        session.set_pending(None)
        conversions = pending.get("conversions", ())
        if preferences is None or not conversions:
            return {"CANCELLED"}

        if self.remember:
            preferences.update_mode = properties.UPDATE_ALWAYS

        rebuilt = 0
        for conversion in conversions:
            material = builder.find_existing(conversion)
            if material is None or material.library is not None:
                continue
            from . import node_builder

            from . import builder
            builder.normalize_conversion_identity(conversion)
            node_builder.build_material(
                material,
                conversion.plan,
                metadata=converter.metadata(conversion, builder_version=__version__),
                document=conversion.document,
            )
            rebuilt += 1

        properties.set_status(
            state,
            f"Updated {rebuilt} materials from Unity",
            f"Обновлено материалов из Unity: {rebuilt}",
            "FILE_REFRESH",
        )
        self.report(
            {"INFO"},
            lt(f"Updated {rebuilt} materials", f"Обновлено материалов: {rebuilt}"),
        )
        return {"FINISHED"}

    def cancel(self, context):
        # Declining is a real answer, so it can be remembered too. Nothing else
        # to undo: the drop already assigned the material as it stood.
        preferences = properties.get_preferences(context)
        if preferences is not None and self.remember:
            preferences.update_mode = properties.UPDATE_NEVER
        session.set_pending(None)


class UMD_OT_merge_duplicates(LocalizedDescription, Operator):
    """Collapse materials that stand for the same Unity material into one"""

    bl_idname = "unity_material_drop.merge_duplicates"
    bl_label = "Merge duplicates"
    bl_description = (
        "Find materials in this file that came from the same Unity .mat, point every "
        "object at one of them and delete the rest"
    )
    tooltip_ru = (
        "Найти в файле материалы, пришедшие из одного и того же .mat Unity, перевести "
        "все объекты на один из них, а остальные удалить"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import builder

        state = properties.get_state(context)
        removed, groups = builder.merge_duplicates()
        if not removed:
            properties.set_status(
                state, "No duplicate Unity materials", "Дубликатов материалов Unity нет", "CHECKMARK"
            )
            self.report(
                {"INFO"},
                lt("No duplicate Unity materials", "Дубликатов материалов Unity нет"),
            )
            return {"FINISHED"}

        properties.set_status(
            state,
            f"Merged {removed} duplicates into {groups} materials",
            f"Объединено дубликатов: {removed} → материалов: {groups}",
            "CHECKMARK",
        )
        self.report(
            {"INFO"},
            lt(
                f"Removed {removed} duplicate materials",
                f"Удалено дубликатов материалов: {removed}",
            ),
        )
        return {"FINISHED"}


class UMD_OT_show_source(LocalizedDescription, Operator):
    """Open the folder of the Unity material this was built from"""

    bl_idname = "unity_material_drop.show_source"
    bl_label = "Show in Explorer"
    bl_description = "Open the Unity folder that holds the .mat this material came from"
    tooltip_ru = "Открыть папку Unity с файлом .mat, из которого собран материал"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        material = getattr(context, "material", None) or (
            context.active_object.active_material if context.active_object else None
        )
        return material is not None and bool(material.get("umd_source"))

    def execute(self, context):
        material = getattr(context, "material", None) or (
            context.active_object.active_material if context.active_object else None
        )
        source = Path(str(material.get("umd_source", "")))
        if not source.exists():
            self.report({"ERROR"}, lt("Source is missing", "Исходный файл не найден"))
            return {"CANCELLED"}
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(str(source))])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(source)])
            else:
                subprocess.Popen(["xdg-open", str(source.parent)])
        except OSError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class UMD_OT_reload_all(LocalizedDescription, Operator):
    """Rebuild every Unity material in this file"""

    bl_idname = "unity_material_drop.reload_all"
    bl_label = "Reload all materials"
    bl_description = "Read every .mat again and rebuild the materials this add-on built"
    tooltip_ru = "Перечитать все .mat и пересобрать материалы, созданные этим аддоном"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        preferences = properties.get_preferences(context)
        state = properties.get_state(context)
        if preferences is None:
            return {"CANCELLED"}

        from . import __version__
        from . import node_builder

        cache = properties.cache_directory(preferences)
        rules = _rules(preferences)
        rebuilt = 0
        missing = 0
        for material in list(bpy.data.materials):
            source = str(material.get("umd_source", ""))
            if material.library is not None or not source:
                continue
            path = Path(source)
            if not path.is_file():
                missing += 1
                continue
            assets_root = fast_index.assets_root_of(path)
            index = session.index_for(assets_root, cache) if assets_root else None
            conversion, _found = converter.resolve_and_convert(
                path,
                index=index,
                assets_root=assets_root,
                rules=rules,
                trust_all_tints=preferences.trust_all_tints,
                nearby_seconds=0.0,
            )
            from . import builder
            builder.normalize_conversion_identity(conversion)
            node_builder.build_material(
                material,
                conversion.plan,
                metadata=converter.metadata(conversion, builder_version=__version__),
                document=conversion.document,
            )
            rebuilt += 1

        properties.set_status(
            state,
            f"Rebuilt {rebuilt} materials, {missing} sources missing",
            f"Пересобрано материалов: {rebuilt}, исходников не найдено: {missing}",
            "FILE_REFRESH" if rebuilt else "ERROR",
        )
        self.report(
            {"INFO"},
            lt(f"Rebuilt {rebuilt} materials", f"Пересобрано материалов: {rebuilt}"),
        )
        return {"FINISHED"}


def _redraw(context) -> None:
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type in {"VIEW_3D", "PROPERTIES", "NODE_EDITOR"}:
            area.tag_redraw()


_CLASSES = (
    UMD_OT_index_project,
    UMD_OT_confirm_update,
    UMD_OT_clear_index,
    UMD_OT_drop_from_clipboard,
    UMD_OT_reload_material,
    UMD_OT_reload_all,
    UMD_OT_merge_duplicates,
    UMD_OT_show_source,
)


def register():
    registration.register_classes(_CLASSES)


def unregister():
    registration.unregister_classes(_CLASSES)
