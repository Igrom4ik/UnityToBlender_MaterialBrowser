"""Operators: extract, build, diff and library registration.

Long running work is driven by generators from the core, stepped inside modal
operators with a time budget, so Blender stays responsive and Esc always works.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from . import library_build, properties, registration
from .core import extract, sync
from .localization import LocalizedDescription, text as lt


def _set_status(state, english: str, russian: str, icon: str = "INFO") -> None:
    state.status = english
    state.status_ru = russian
    state.status_icon = icon


def _library_root(preferences) -> Path | None:
    raw = bpy.path.abspath(preferences.library_path).strip()
    return Path(raw) if raw else None


def _refresh_index_state(state, preferences) -> None:
    root = _library_root(preferences)
    index = extract.load_index(root) if root else None
    state.has_index = bool(index)
    if index:
        state.material_count = len(index.get("materials", {}))


def restore_state_from_disk() -> None:
    """Show what is already on disk after Blender restarts.

    Run-time state lives on the window manager and starts empty, so without this
    a finished library looks like nothing was ever scanned.
    """
    import json

    preferences = properties.get_preferences()
    state = properties.get_state()
    if preferences is None or state is None:
        return

    dropped = properties.drop_legacy_project_sources(preferences)
    if dropped:
        _set_status(
            state,
            f"Removed {dropped} whole-project folders: only chosen folders are read",
            f"Удалено источников «весь проект»: {dropped} — читаются только выбранные папки",
            "ERROR",
        )

    root = _library_root(preferences)
    if root is None or not root.is_dir():
        return

    _refresh_index_state(state, preferences)
    try:
        report = json.loads((root / extract.REPORT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return

    _fill_report_rows(state, report)
    build = report.get("build", {})
    state.built_count = build.get("built", 0)
    state.file_count = build.get("files", 0)
    state.catalog_count = build.get("catalogs", 0)
    state.shader_count = len(report.get("shaders", []))
    state.unresolved_textures = report.get("materials_with_unresolved_textures", 0)
    if state.material_count and not dropped:
        _set_status(
            state,
            f"Library on disk: {state.material_count} materials",
            f"Библиотека на диске: материалов {state.material_count}",
            "ASSET_MANAGER",
        )


class _ModalJob:
    """Shared plumbing for generator-driven modal operators."""

    _timer = None
    _generator = None
    _result = None

    def _start(self, context, generator, label_en: str, label_ru: str):
        state = properties.get_state(context)
        if state.running:
            self.report({"WARNING"}, lt("A library job is already running", "Задача библиотеки уже выполняется"))
            return {"CANCELLED"}

        self._generator = generator
        self._result = None
        state.running = True
        state.progress = 0.0
        state.progress_label = lt(label_en, label_ru)

        if bpy.app.background:
            # Background Blender has no modal loop: run the job to completion.
            try:
                while self._step(context, budget=3600.0):
                    pass
            finally:
                state.running = False
                state.progress = 0.0
                state.progress_label = ""
            return self._finish(context)

        context.window_manager.progress_begin(0.0, 1.0)
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _stop(self, context):
        state = properties.get_state(context)
        state.running = False
        state.progress = 0.0
        state.progress_label = ""
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.window_manager.progress_end()
        for area in context.screen.areas:
            area.tag_redraw()

    def _step(self, context, budget: float = 0.12) -> bool:
        state = properties.get_state(context)
        deadline = time.perf_counter() + budget
        while time.perf_counter() < deadline:
            try:
                progress = next(self._generator)
            except StopIteration:
                return False
            state.progress = progress.fraction
            state.progress_label = f"{progress.phase}: {progress.message}"[:90]
            if not bpy.app.background:
                context.window_manager.progress_update(progress.fraction)
            if progress.result is not None:
                self._result = progress.result
                return False
        return True

    def modal(self, context, event):
        if event.type == "ESC":
            self._stop(context)
            state = properties.get_state(context)
            _set_status(state, "Cancelled", "Отменено", "CANCEL")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        try:
            running = self._step(context)
        except Exception as error:
            self._stop(context)
            state = properties.get_state(context)
            _set_status(state, f"Failed: {error}", f"Ошибка: {error}", "ERROR")
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        if running:
            for area in context.screen.areas:
                area.tag_redraw()
            return {"RUNNING_MODAL"}

        self._stop(context)
        return self._finish(context)

    def _finish(self, context):
        return {"FINISHED"}


class UMB_OT_AddSource(LocalizedDescription, Operator):
    bl_idname = "umb.add_source"
    bl_label = "Add Folder"
    bl_description = "Add a Unity folder to read"
    tooltip_ru = "Добавить папку Unity для чтения"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(
        items=(
            (properties.SOURCE_MATERIALS, "Materials", ""),
            (properties.SOURCE_ASSETS, "Assets", ""),
        ),
        default=properties.SOURCE_MATERIALS,
    )

    def execute(self, context):
        preferences = properties.get_preferences(context)
        source = preferences.sources.add()
        source.kind = self.kind
        preferences.active_source = len(preferences.sources) - 1
        return {"FINISHED"}


class UMB_OT_AddFolders(LocalizedDescription, Operator):
    bl_idname = "umb.add_folders"
    bl_label = "Add Folders"
    bl_description = (
        "Pick one or more Unity folders. Ctrl-click selects several at once, and "
        "each subfolder can be added as its own entry"
    )
    tooltip_ru = (
        "Выберите одну или несколько папок Unity. Ctrl-клик выделяет сразу несколько, "
        "а подпапки можно добавить отдельными записями"
    )
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(subtype="DIR_PATH")
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement)
    filter_folder: BoolProperty(default=True, options={"HIDDEN"})
    use_filter_folder = True

    kind: EnumProperty(
        name="Type",
        items=(
            (properties.SOURCE_MATERIALS, "Materials", "Folders searched for .mat files"),
            (properties.SOURCE_ASSETS, "Textures / Shaders", "Folders used to resolve GUIDs"),
        ),
        default=properties.SOURCE_MATERIALS,
    )
    split_subfolders: BoolProperty(
        name="Add each subfolder separately",
        description="Add every immediate subfolder of the picked folder as its own entry, "
        "instead of one entry for the parent",
        default=False,
    )

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "kind", text=lt("Type", "Тип"))
        layout.prop(self, "split_subfolders", text=lt("Each subfolder separately", "Каждую подпапку отдельно"))

    def execute(self, context):
        preferences = properties.get_preferences(context)
        base = Path(bpy.path.abspath(self.directory))

        picked = [base / item.name for item in self.files if item.name]
        picked = [path for path in picked if path.is_dir()]
        if not picked:
            picked = [base]

        if self.split_subfolders:
            expanded: list[Path] = []
            for path in picked:
                children = sorted(child for child in path.iterdir() if child.is_dir())
                expanded.extend(children or [path])
            picked = expanded

        existing = {
            bpy.path.abspath(source.path).rstrip("\\/").casefold()
            for source in preferences.sources
        }
        added = 0
        for path in picked:
            if str(path).rstrip("\\/").casefold() in existing:
                continue
            source = preferences.sources.add()
            source.kind = self.kind
            source.path = str(path)
            added += 1
        preferences.active_source = max(0, len(preferences.sources) - 1)

        self.report(
            {"INFO"},
            lt(f"Added {added} folders", f"Добавлено папок: {added}"),
        )
        return {"FINISHED"}


class UMB_OT_RemoveSource(LocalizedDescription, Operator):
    bl_idname = "umb.remove_source"
    bl_label = "Remove Folder"
    bl_description = "Remove the selected Unity folder"
    tooltip_ru = "Удалить выбранную папку Unity"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1)

    def execute(self, context):
        preferences = properties.get_preferences(context)
        index = self.index if self.index >= 0 else preferences.active_source
        if 0 <= index < len(preferences.sources):
            preferences.sources.remove(index)
            preferences.active_source = max(0, index - 1)
        return {"FINISHED"}


def active_material(context):
    return getattr(getattr(context, "object", None), "active_material", None)


_COPY_SUFFIX = re.compile(r"_edit(_\d+)?$", re.IGNORECASE)


def _default_copy_name(material) -> str:
    """Next free `<unity name>_edit` name.

    A copy of a copy keeps counting instead of stacking suffixes, so the third
    take is `Wall_edit_03`, not `Wall_edit_edit_edit`.
    """
    base = f"{_COPY_SUFFIX.sub('', material.name)}_edit"
    if base not in bpy.data.materials:
        return base
    for number in range(2, 1000):
        candidate = f"{base}_{number:02d}"
        if candidate not in bpy.data.materials:
            return candidate
    return base


def _copy_name_problem(name: str, material) -> tuple[str, str]:
    """Reject a name before anything is created, not after."""
    if not name:
        return ("Enter a name for the copy", "Введите имя копии")
    if name == material.name:
        return (
            "The copy needs a different name: the Unity remapper matches by name",
            "У копии должно быть другое имя: ремаппер Unity сопоставляет по имени",
        )
    if name in bpy.data.materials:
        return (
            f"A material called {name} already exists",
            f"Материал с именем {name} уже есть",
        )
    return ("", "")


class UMB_OT_MakeEditableCopy(LocalizedDescription, Operator):
    bl_idname = "umb.make_editable_copy"
    bl_label = "Create Editable Copy"
    bl_description = (
        "Copy the library material into this file under a new name and assign it to the "
        "selected objects. The library and the Unity project are not touched"
    )
    tooltip_ru = (
        "Скопировать материал библиотеки в этот файл под новым именем и назначить его "
        "выделенным объектам. Библиотека и проект Unity не меняются"
    )
    # No REGISTER: the Adjust Last Operation panel would re-run this against the
    # copy it just made and refuse its own name. Undo still works.
    bl_options = {"UNDO"}

    # SKIP_SAVE so the dialog always opens on a freshly computed name instead of
    # the one used last time.
    new_name: StringProperty(name="New name", default="", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return active_material(context) is not None

    def invoke(self, context, _event):
        material = active_material(context)
        if material is None:
            self.report({"ERROR"}, lt("No active material", "Нет активного материала"))
            return {"CANCELLED"}
        self.new_name = _default_copy_name(material)
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        layout = self.layout
        material = active_material(context)
        column = layout.column(align=True)
        column.label(
            text=lt("Copy of ", "Копия материала ") + (material.name if material else ""),
            icon="MATERIAL",
        )
        column.prop(self, "new_name", text=lt("New name", "Новое имя"))

        if material is None:
            return
        english, russian = _copy_name_problem(self.new_name.strip(), material)
        if english:
            note = layout.row()
            note.alert = True
            note.label(text=lt(english, russian), icon="ERROR")

    def execute(self, context):
        material = active_material(context)
        if material is None:
            self.report({"ERROR"}, lt("No active material", "Нет активного материала"))
            return {"CANCELLED"}

        name = self.new_name.strip()
        english, russian = _copy_name_problem(name, material)
        if english:
            self.report({"ERROR"}, lt(english, russian))
            return {"CANCELLED"}

        copy = material.copy()  # a linked or packed asset copies into this file as local data
        copy.name = name
        if copy.asset_data is not None:
            # Otherwise the copy shows up in the Asset Browser next to the library.
            copy.asset_clear()

        guid = material.get("ump_unity_guid")
        if guid is not None:
            # The copy no longer mirrors Unity: the differ and Scene Scan must
            # stop treating it as a library material (12.2).
            del copy["ump_unity_guid"]
            copy["ump_derived_from"] = guid
            copy["ump_derived_name"] = material.name
            copy["ump_derived_hash"] = material.get("ump_source_hash", "")

        objects = [
            object_
            for object_ in (context.selected_objects or [context.object])
            if object_ is not None and hasattr(object_, "material_slots")
        ]
        assigned = 0
        blocked = 0
        for object_ in objects:
            for slot in object_.material_slots:
                if slot.material is not material:
                    continue
                try:
                    slot.material = copy
                except (AttributeError, RuntimeError):
                    blocked += 1
                    continue
                assigned += 1

        if blocked:
            self.report(
                {"WARNING"},
                lt(
                    f"{copy.name} created; {blocked} slots are on linked data and kept the original",
                    f"{copy.name} создан; {blocked} слотов на связанных данных остались с оригиналом",
                ),
            )
        else:
            self.report(
                {"INFO"},
                lt(
                    f"{copy.name} created and assigned to {assigned} slots",
                    f"{copy.name} создан и назначен в слотов: {assigned}",
                ),
            )
        return {"FINISHED"}


class UMB_OT_OpenAssetBrowser(LocalizedDescription, Operator):
    bl_idname = "umb.open_asset_browser"
    bl_label = "Open Asset Browser"
    bl_description = "Open the Asset Browser in a new window and show the material library"
    tooltip_ru = "Открыть браузер ассетов в новом окне и показать библиотеку материалов"

    def execute(self, context):
        try:
            bpy.ops.wm.window_new()
        except RuntimeError:
            self.report({"ERROR"}, lt("Could not open a window", "Не удалось открыть окно"))
            return {"CANCELLED"}

        window = context.window_manager.windows[-1]
        area = max(window.screen.areas, key=lambda item: item.width * item.height)
        area.ui_type = "ASSETS"
        for space in area.spaces:
            if space.type == "FILE_BROWSER" and getattr(space, "params", None):
                try:
                    space.params.asset_library_reference = "Unity Materials"
                except (AttributeError, TypeError):
                    pass
        return {"FINISHED"}


class UMB_OT_Extract(_ModalJob, LocalizedDescription, Operator):
    bl_idname = "umb.extract"
    bl_label = "Scan Unity"
    bl_description = (
        "Read the Unity folders and write one JSON document per material. "
        "Nothing is written into the Unity project"
    )
    tooltip_ru = (
        "Прочитать папки Unity и записать по одному JSON на материал. "
        "В проект Unity ничего не пишется"
    )

    @classmethod
    def poll(cls, context):
        preferences = properties.get_preferences(context)
        return bool(preferences and preferences.library_path.strip())

    def invoke(self, context, _event):
        preferences = properties.get_preferences(context)
        state = properties.get_state(context)

        roots = properties.material_roots(preferences)
        if not roots:
            self.report({"ERROR"}, lt("Add at least one materials folder", "Добавьте хотя бы одну папку материалов"))
            return {"CANCELLED"}
        library_root = _library_root(preferences)
        if library_root is None:
            self.report({"ERROR"}, lt("Choose a library folder", "Укажите папку библиотеки"))
            return {"CANCELLED"}

        problem = extract.validate_library_root(library_root)
        if problem:
            _set_status(state, problem, problem, "ERROR")
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}

        request = extract.ExtractRequest(
            material_roots=roots,
            asset_roots=properties.asset_roots(preferences),
            library_root=library_root,
        )
        state.shader_rows.clear()
        state.warning_rows.clear()
        return self._start(context, extract.extract_steps(request), "Reading Unity", "Чтение Unity")

    def execute(self, context):
        return self.invoke(context, None)

    def _finish(self, context):
        state = properties.get_state(context)
        result = self._result
        if result is None:
            _set_status(state, "Extraction produced no result", "Извлечение не дало результата", "ERROR")
            return {"CANCELLED"}

        state.material_count = result.material_count
        state.shader_count = result.shader_count
        state.failed_count = result.failed
        state.unresolved_textures = result.unresolved_textures
        state.has_index = True
        state.diff_ready = False
        _fill_report_rows(state, result.report)

        if result.unresolved_textures:
            _set_status(
                state,
                f"Scanned {result.material_count} materials; "
                f"{result.unresolved_textures} have unresolved textures",
                f"Просканировано материалов: {result.material_count}; "
                f"без текстур: {result.unresolved_textures}",
                "ERROR",
            )
        else:
            _set_status(
                state,
                f"Scanned {result.material_count} materials, {result.shader_count} shaders",
                f"Просканировано материалов: {result.material_count}, шейдеров: {result.shader_count}",
                "CHECKMARK",
            )
        self.report({"INFO"}, state.status)
        return {"FINISHED"}


class UMB_OT_Build(_ModalJob, LocalizedDescription, Operator):
    bl_idname = "umb.build"
    bl_label = "Build Library"
    bl_description = "Create Blender asset files from the extracted JSON documents"
    tooltip_ru = "Создать файлы ассетов Blender из извлечённых JSON-документов"

    changed_only: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        state = properties.get_state(context)
        return bool(state and state.has_index and not state.running)

    def invoke(self, context, _event):
        preferences = properties.get_preferences(context)
        library_root = _library_root(preferences)
        if library_root is None:
            self.report({"ERROR"}, lt("Choose a library folder", "Укажите папку библиотеки"))
            return {"CANCELLED"}

        only = None
        if self.changed_only:
            result = sync.diff(library_root, properties.material_roots(preferences))
            only = result.guids_to_build()
            if not only:
                self.report({"INFO"}, lt("Everything is already up to date", "Всё уже актуально"))
                return {"CANCELLED"}

        profiles_path = bpy.path.abspath(preferences.profiles_path).strip() or None
        generator = library_build.build_steps(
            library_root,
            pack_mode=preferences.pack_mode,
            generate_previews=preferences.generate_previews,
            profiles_path=profiles_path,
            only_guids=only,
            trust_all_tints=preferences.apply_all_tints,
        )
        return self._start(context, generator, "Building materials", "Сборка материалов")

    def execute(self, context):
        return self.invoke(context, None)

    def _finish(self, context):
        state = properties.get_state(context)
        result = self._result
        if result is None:
            _set_status(state, "Build produced no result", "Сборка не дала результата", "ERROR")
            return {"CANCELLED"}

        from . import __version__

        state.built_count = result.built
        state.file_count = result.files
        state.catalog_count = result.catalogs
        state.failed_count = result.failed
        state.diff_ready = False
        state.built_with_version = __version__
        state.builder_outdated = False

        state.warning_rows.clear()
        for name, count in sorted(result.unmapped.items(), key=lambda item: -item[1])[:30]:
            row = state.warning_rows.add()
            row.label = name
            row.count = count
            row.status = "UNMAPPED"
        for warning in result.warnings[:30]:
            row = state.warning_rows.add()
            row.label = warning[:90]
            row.status = "ERROR"

        _set_status(
            state,
            f"Built {result.built} materials into {result.files} files",
            f"Собрано материалов: {result.built}, файлов: {result.files}",
            "CHECKMARK",
        )
        self.report({"INFO"}, state.status)
        return {"FINISHED"}


class UMB_OT_CheckUpdates(LocalizedDescription, Operator):
    bl_idname = "umb.check_updates"
    bl_label = "Check for Updates"
    bl_description = "Compare the library with the current state of the Unity folders"
    tooltip_ru = "Сравнить библиотеку с текущим состоянием папок Unity"

    @classmethod
    def poll(cls, context):
        state = properties.get_state(context)
        return bool(state and state.has_index and not state.running)

    def execute(self, context):
        preferences = properties.get_preferences(context)
        state = properties.get_state(context)
        library_root = _library_root(preferences)
        if library_root is None:
            return {"CANCELLED"}

        result = sync.diff(library_root, properties.material_roots(preferences))
        state.diff_new = result.counts.get(sync.NEW, 0)
        state.diff_changed = result.counts.get(sync.CHANGED, 0)
        state.diff_missing = result.counts.get(sync.MISSING, 0)
        state.diff_ready = True

        if result.has_changes:
            _set_status(
                state,
                f"New {state.diff_new}, changed {state.diff_changed}, missing {state.diff_missing}",
                f"Новых {state.diff_new}, изменённых {state.diff_changed}, отсутствуют {state.diff_missing}",
                "FILE_REFRESH",
            )
        else:
            _set_status(state, "Library is up to date", "Библиотека актуальна", "CHECKMARK")
        return {"FINISHED"}


def _apply_import_method(library, method: str) -> bool:
    """Set how a dragged material arrives, instead of taking what Blender gives.

    Blender 5.2 defaults new asset libraries to `PACK`: the material arrives
    linked *and* packed, so its parameters are greyed out, a library rebuild
    never reaches it, and Ctrl+Z after a drop leaves the link in the file. Both
    supported answers are deliberate choices (12.2), so neither is left to the
    Blender default.
    """
    try:
        if library.import_method == method:
            return False
        library.import_method = method
    except (AttributeError, TypeError):
        return False
    return True


class UMB_OT_RegisterAssetLibrary(LocalizedDescription, Operator):
    bl_idname = "umb.register_asset_library"
    bl_label = "Register in Asset Browser"
    bl_description = "Add the library folder to Preferences > File Paths > Asset Libraries"
    tooltip_ru = "Добавить папку библиотеки в Preferences > File Paths > Asset Libraries"

    def execute(self, context):
        preferences = properties.get_preferences(context)
        state = properties.get_state(context)
        library_root = _library_root(preferences)
        if library_root is None or not library_root.is_dir():
            self.report({"ERROR"}, lt("The library folder does not exist yet", "Папка библиотеки ещё не существует"))
            return {"CANCELLED"}

        libraries = context.preferences.filepaths.asset_libraries
        target = os.path.normcase(str(library_root))
        for library in libraries:
            if os.path.normcase(bpy.path.abspath(library.path).rstrip("\\/")) == target.rstrip("\\/"):
                state.library_registered = True
                if _apply_import_method(library, preferences.import_method):
                    self.report(
                        {"INFO"},
                        lt(
                            "Already registered; import method corrected",
                            "Уже зарегистрирована; способ импорта исправлен",
                        ),
                    )
                else:
                    self.report({"INFO"}, lt("Already registered", "Уже зарегистрирована"))
                return {"FINISHED"}

        bpy.ops.preferences.asset_library_add(directory=str(library_root))
        if len(libraries) > 0:
            libraries[-1].name = "Unity Materials"
            _apply_import_method(libraries[-1], preferences.import_method)
        state.library_registered = True
        _set_status(state, "Library registered", "Библиотека зарегистрирована", "CHECKMARK")
        return {"FINISHED"}


class UMB_OT_OpenLibraryFolder(LocalizedDescription, Operator):
    bl_idname = "umb.open_library_folder"
    bl_label = "Open Folder"
    bl_description = "Open the library folder in the system file browser"
    tooltip_ru = "Открыть папку библиотеки в проводнике"

    target: StringProperty(default="LIBRARY")

    def execute(self, context):
        preferences = properties.get_preferences(context)
        library_root = _library_root(preferences)
        if library_root is None or not library_root.exists():
            self.report({"ERROR"}, lt("The folder does not exist", "Папка не существует"))
            return {"CANCELLED"}
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(library_root))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(library_root)])
            else:
                subprocess.Popen(["xdg-open", str(library_root)])
        except OSError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


def _rebuild_one(material, library_root: Path, rules, trust_all_tints: bool) -> str:
    """Rebuild a material that came from the library, in place."""
    from . import library_build, node_builder
    from .core import material_json, profiles

    relative = material.get("ump_json", "")
    document = material_json.read_document(library_root / relative) if relative else None
    if document is None:
        guid = material.get("ump_unity_guid", "")
        index = extract.load_index(library_root) or {"materials": {}}
        entry = index.get("materials", {}).get(guid)
        if entry is None:
            return "no source document"
        document = material_json.read_document(library_root / entry["json"])
        relative = entry["json"]
    if document is None:
        return "source document could not be read"

    plan = profiles.build_plan(document, None, rules, trust_all_tints)
    node_builder.build_material(
        material, plan, metadata=library_build._metadata(document, plan, relative)
    )
    return ""


class UMB_OT_RebuildSceneMaterials(LocalizedDescription, Operator):
    bl_idname = "umb.rebuild_scene_materials"
    bl_label = "Update Materials in This File"
    bl_description = (
        "Rebuild materials already assigned in this file from the library. A dragged-in "
        "copy does not follow library rebuilds; linked materials follow them on their own"
    )
    tooltip_ru = (
        "Пересобрать материалы, уже назначенные в этом файле, из библиотеки. Перетащенная "
        "копия за пересборкой не следует; связанные материалы обновляются сами"
    )
    bl_options = {"REGISTER", "UNDO"}

    only_active: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        preferences = properties.get_preferences(context)
        return bool(preferences and preferences.library_path.strip())

    def execute(self, context):
        from .core import profiles

        preferences = properties.get_preferences(context)
        state = properties.get_state(context)
        library_root = _library_root(preferences)
        if library_root is None or not library_root.is_dir():
            self.report({"ERROR"}, lt("The library folder does not exist", "Папка библиотеки не найдена"))
            return {"CANCELLED"}

        if self.only_active:
            active = getattr(context.object, "active_material", None)
            materials = [active] if active is not None else []
        else:
            materials = [
                material
                for material in bpy.data.materials
                if material.get("ump_unity_guid") is not None
            ]
        if not materials:
            self.report({"WARNING"}, lt("No library materials found", "Материалы библиотеки не найдены"))
            return {"CANCELLED"}

        rules = profiles.load_profiles(bpy.path.abspath(preferences.profiles_path).strip() or None)
        updated = 0
        failed = 0
        linked = 0
        for material in materials:
            if material.get("ump_unity_guid") is None:
                continue
            if material.library is not None:
                # Linked library data cannot be written to. Rebuilding the
                # library itself already updates it; editing needs a copy (12.2).
                linked += 1
                continue
            problem = _rebuild_one(material, library_root, rules, preferences.apply_all_tints)
            if problem:
                failed += 1
            else:
                updated += 1

        _set_status(
            state,
            f"Updated {updated} materials in this file",
            f"Обновлено материалов в файле: {updated}",
            "CHECKMARK" if not failed else "ERROR",
        )
        if linked:
            self.report(
                {"WARNING"},
                lt(
                    f"Updated {updated}; {linked} are linked from the library and update with it. "
                    "Use Create Editable Copy to change one here",
                    f"Обновлено {updated}; связанных с библиотекой: {linked} — они обновляются "
                    "вместе с ней. Чтобы править здесь, сделайте редактируемую копию",
                ),
            )
        else:
            self.report(
                {"INFO"},
                lt(
                    f"Updated {updated}, skipped {failed}",
                    f"Обновлено {updated}, пропущено {failed}",
                ),
            )
        return {"FINISHED"}


class UMB_OT_LoadReport(LocalizedDescription, Operator):
    bl_idname = "umb.load_report"
    bl_label = "Refresh Report"
    bl_description = "Reload the coverage report written by the last run"
    tooltip_ru = "Перечитать отчёт покрытия последнего запуска"

    def execute(self, context):
        import json

        preferences = properties.get_preferences(context)
        state = properties.get_state(context)
        library_root = _library_root(preferences)
        if library_root is None:
            return {"CANCELLED"}
        try:
            report = json.loads(
                (library_root / extract.REPORT_NAME).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            self.report({"WARNING"}, lt("No report was found", "Отчёт не найден"))
            return {"CANCELLED"}

        _fill_report_rows(state, report)
        _refresh_index_state(state, preferences)
        return {"FINISHED"}


def _update_builder_version(state, build: dict) -> None:
    """Flag a library that was produced by an older converter.

    Updating the add-on does not touch already generated .blend files, and the
    diff only watches Unity, so without this the library silently keeps whatever
    node trees the previous version produced.
    """
    from . import __version__

    state.built_with_version = build.get("builder_version", "") if build else ""
    state.builder_outdated = bool(
        state.built_with_version and state.built_with_version != __version__
    )


def _fill_report_rows(state, report: dict) -> None:
    state.shader_rows.clear()
    for entry in report.get("shaders", [])[:60]:
        row = state.shader_rows.add()
        row.label = entry.get("name", "")
        row.count = entry.get("materials", 0)
        row.status = entry.get("confidence", "")
        row.detail = f"{entry.get('backend', '')} · {entry.get('workflow', '')} · {entry.get('properties', 0)}"

    state.warning_rows.clear()
    for entry in report.get("missing_textures", [])[:20]:
        row = state.warning_rows.add()
        row.label = f"Texture missing in Unity: {entry.get('guid', '')[:12]}"
        row.count = entry.get("materials", 0)
        row.status = "ERROR"

    build = report.get("build", {})
    _update_builder_version(state, build)
    if build:
        state.warning_rows.clear()
        for entry in build.get("unmapped", [])[:30]:
            row = state.warning_rows.add()
            row.label = entry.get("property", "")
            row.count = entry.get("count", 0)
            row.status = "UNMAPPED"
        for warning in build.get("warnings", [])[:30]:
            row = state.warning_rows.add()
            row.label = str(warning)[:90]
            row.status = "ERROR"


CLASSES = (
    UMB_OT_AddSource,
    UMB_OT_AddFolders,
    UMB_OT_RemoveSource,
    UMB_OT_MakeEditableCopy,
    UMB_OT_OpenAssetBrowser,
    UMB_OT_Extract,
    UMB_OT_Build,
    UMB_OT_CheckUpdates,
    UMB_OT_RegisterAssetLibrary,
    UMB_OT_OpenLibraryFolder,
    UMB_OT_RebuildSceneMaterials,
    UMB_OT_LoadReport,
)


def register():
    registration.register_classes(CLASSES)


def unregister():
    registration.unregister_classes(CLASSES)
