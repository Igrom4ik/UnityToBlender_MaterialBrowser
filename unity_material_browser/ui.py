"""Sidebar UI.

Everything lives in one always-visible column of panels, in the order the work
happens: folders, library, scan, build, open. Each step states what it needs and
what it produced, so it is never a guess whether a scan already ran.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.types import Menu, Panel, UIList

from . import properties, registration
from .core import extract
from .localization import property_name as pn, text as lt
from .node_builder import NODE_PREFIX


CATEGORY = "Unity Materials"

_CONFIDENCE_ICONS = {"full": "CHECKMARK", "partial": "ERROR", "none": "CANCEL"}
_KIND_ICONS = {
    properties.SOURCE_MATERIALS: "MATERIAL",
    properties.SOURCE_ASSETS: "TEXTURE",
}


def _folder_name(path: str) -> str:
    cleaned = path.strip().rstrip("\\/")
    return cleaned.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] if cleaned else ""


class UMB_UL_Sources(UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_prop, index):
        row = layout.row(align=True)
        row.prop(item, pn("enabled"), text="")
        row.label(text="", icon=_KIND_ICONS.get(item.kind, "DOT"))
        body = row.row()
        body.active = item.enabled
        body.label(text=_folder_name(item.path) or lt("Choose a folder…", "Выберите папку…"))
        row.operator("umb.remove_source", text="", icon="X", emboss=False).index = index


class UMB_UL_Shaders(UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_prop, _index):
        row = layout.row(align=True)
        row.label(text="", icon=_CONFIDENCE_ICONS.get(item.status, "DOT"))
        row.label(text=item.label)
        counter = row.row()
        counter.alignment = "RIGHT"
        counter.label(text=str(item.count))


class UMB_UL_Warnings(UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_prop, _index):
        row = layout.row(align=True)
        row.label(text="", icon="ERROR" if item.status == "ERROR" else "QUESTION")
        row.label(text=item.label)
        if item.count:
            counter = row.row()
            counter.alignment = "RIGHT"
            counter.label(text=str(item.count))


def _done_icon(done: bool) -> str:
    return "CHECKMARK" if done else "RADIOBUT_OFF"


class _Base(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    @staticmethod
    def context_data(context):
        return properties.get_preferences(context), properties.get_state(context)


class UMB_PT_Main(_Base):
    bl_label = "Unity Material Browser"

    def draw(self, context):
        layout = self.layout
        preferences, state = self.context_data(context)
        if preferences is None or state is None:
            layout.label(text="Add-on preferences are unavailable", icon="ERROR")
            return

        if state.running:
            box = layout.box()
            box.label(text=state.progress_label or lt("Working…", "Выполняется…"), icon="TIME")
            box.prop(state, "progress", text="", slider=True)
            box.label(text=lt("Esc cancels", "Esc — отмена"))
            return

        box = layout.box()
        box.label(text=lt(state.status, state.status_ru), icon=state.status_icon)

        summary = layout.row(align=True)
        summary.alignment = "CENTER"
        summary.label(text=str(state.material_count), icon="MATERIAL")
        summary.label(text=str(state.shader_count), icon="NODE_MATERIAL")
        summary.label(text=str(state.built_count), icon="ASSET_MANAGER")


class UMB_PT_Sources(_Base):
    bl_parent_id = "UMB_PT_Main"
    bl_label = "1 · Unity folders"

    def draw_header(self, context):
        preferences, _state = self.context_data(context)
        ready = bool(preferences and properties.material_roots(preferences))
        self.layout.label(text="", icon=_done_icon(ready))

    def draw(self, context):
        layout = self.layout
        preferences, state = self.context_data(context)
        if preferences is None:
            return

        row = layout.row()
        row.template_list(
            "UMB_UL_Sources", "", preferences, "sources", preferences, "active_source", rows=4
        )
        column = row.column(align=True)
        column.operator("umb.add_source", text="", icon="ADD").kind = properties.SOURCE_MATERIALS
        column.operator("umb.remove_source", text="", icon="REMOVE").index = -1

        pick = layout.column(align=True)
        pick.scale_y = 1.2
        operator = pick.operator(
            "umb.add_folders",
            text=lt("Add material folders…", "Добавить папки материалов…"),
            icon="FILEBROWSER",
        )
        operator.kind = properties.SOURCE_MATERIALS
        operator = pick.operator(
            "umb.add_folders",
            text=lt("Add texture / shader folders…", "Добавить папки текстур и шейдеров…"),
            icon="TEXTURE",
        )
        operator.kind = properties.SOURCE_ASSETS
        hint = layout.column(align=True)
        hint.scale_y = 0.85
        hint.label(
            text=lt("Ctrl-click picks several folders", "Ctrl-клик выделяет несколько папок"),
            icon="INFO",
        )
        hint.label(
            text=lt("Subfolders are scanned automatically", "Подпапки сканируются автоматически")
        )
        if any(
            (Path(bpy.path.abspath(source.path)) / "Assets").is_dir()
            for source in preferences.sources
            if source.path.strip()
        ):
            # Picking the project root is a legitimate choice, but it means the
            # whole Assets tree, which is worth saying out loud (invariant 5).
            hint.label(
                text=lt(
                    "A project root means the whole Assets tree",
                    "Корень проекта — это весь Assets целиком",
                ),
                icon="ERROR",
            )

        if 0 <= preferences.active_source < len(preferences.sources):
            source = preferences.sources[preferences.active_source]
            detail = layout.column(align=True)
            detail.prop(source, pn("kind"), text="")
            detail.prop(source, pn("path"), text="")

        if not properties.material_roots(preferences):
            note = layout.box()
            note.label(
                text=lt("Add a folder with .mat files", "Добавьте папку с файлами .mat"),
                icon="ERROR",
            )
            note.label(
                text=lt("Type: Materials", "Тип: Материалы"),
            )

        if state is not None and state.unresolved_textures:
            # Only the chosen folders are read, so an unresolved texture means
            # its folder is simply not in the list yet.
            hint = layout.box()
            hint.label(
                text=lt("Some textures were not found", "Часть текстур не найдена"),
                icon="QUESTION",
            )
            column = hint.column(align=True)
            column.scale_y = 0.85
            column.label(
                text=lt(
                    "Add the folder they live in above,",
                    "Добавьте выше папку, где они лежат,",
                )
            )
            column.label(
                text=lt('type "Textures / Shaders"', "тип «Текстуры и шейдеры»"),
            )


class UMB_PT_Library(_Base):
    bl_parent_id = "UMB_PT_Main"
    bl_label = "2 · Library folder"

    def draw_header(self, context):
        preferences, _state = self.context_data(context)
        ready = bool(
            preferences
            and preferences.library_path.strip()
            and not extract.validate_library_root(bpy.path.abspath(preferences.library_path))
        )
        self.layout.label(text="", icon=_done_icon(ready))

    def draw(self, context):
        layout = self.layout
        preferences, _state = self.context_data(context)
        if preferences is None:
            return

        layout.label(
            text=lt("Where Blender saves the library", "Куда Blender сохранит библиотеку"),
        )
        layout.prop(preferences, pn("library_path"), text="")

        path = bpy.path.abspath(preferences.library_path).strip()
        if not path:
            box = layout.box()
            box.label(
                text=lt("This is an output folder", "Это папка для результата"), icon="INFO"
            )
            column = box.column(align=True)
            column.scale_y = 0.85
            column.label(text=lt("Not a Unity folder.", "Не папка Unity."))
            column.label(text=lt("Example: D:\\UnityMaterialLibrary", "Например: D:\\UnityMaterialLibrary"))
            return

        problem = extract.validate_library_root(path)
        if problem:
            box = layout.box()
            box.alert = True
            box.label(text=lt("Wrong folder", "Неверная папка"), icon="ERROR")
            column = box.column(align=True)
            column.scale_y = 0.85
            column.label(text=lt("It is inside the Unity project.", "Она внутри проекта Unity."))
            column.label(text=lt("Unity is only read, never written.", "Unity только читается."))
            column.label(text=lt("Pick a folder outside it.", "Выберите папку вне проекта."))
            return

        row = layout.row(align=True)
        row.operator("umb.open_library_folder", text=lt("Open", "Открыть"), icon="FILE_FOLDER")


class UMB_PT_Scan(_Base):
    bl_parent_id = "UMB_PT_Main"
    bl_label = "3 · Scan and build"

    def draw_header(self, context):
        _preferences, state = self.context_data(context)
        self.layout.label(text="", icon=_done_icon(bool(state and state.built_count)))

    def draw(self, context):
        layout = self.layout
        preferences, state = self.context_data(context)
        if preferences is None or state is None:
            return

        ready = bool(properties.material_roots(preferences)) and not extract.validate_library_root(
            bpy.path.abspath(preferences.library_path)
        )

        scan = layout.column(align=True)
        scan.scale_y = 1.5
        scan.enabled = ready and not state.running
        scan.operator("umb.extract", text=lt("Scan Unity", "Сканировать Unity"), icon="VIEWZOOM")

        if state.has_index:
            info = layout.box()
            info.label(
                text=lt(
                    f"Found {state.material_count} materials, {state.shader_count} shaders",
                    f"Найдено материалов: {state.material_count}, шейдеров: {state.shader_count}",
                ),
                icon="CHECKMARK",
            )
            if state.unresolved_textures:
                warning = info.column(align=True)
                warning.alert = True
                warning.label(
                    text=lt(
                        f"{state.unresolved_textures} with missing textures",
                        f"С ненайденными текстурами: {state.unresolved_textures}",
                    ),
                    icon="ERROR",
                )
                warning.scale_y = 0.9
                warning.label(
                    text=lt(
                        "Add their folder in step 1",
                        "Добавьте их папку в шаге 1",
                    )
                )
                warning.label(text=lt("See the Report panel", "Подробности в разделе «Отчёт»"))
        else:
            layout.label(
                text=lt("Not scanned yet", "Ещё не сканировалось"), icon="RADIOBUT_OFF"
            )

        if state.builder_outdated:
            # Updating the add-on does not rewrite existing .blend files.
            box = layout.box()
            box.alert = True
            box.label(
                text=lt("Library built by an older version", "Библиотека собрана старой версией"),
                icon="ERROR",
            )
            column = box.column(align=True)
            column.scale_y = 0.85
            column.label(
                text=lt(
                    f"Built with {state.built_with_version}, add-on is newer",
                    f"Собрано версией {state.built_with_version}, аддон новее",
                )
            )
            column.label(text=lt("Build again to apply the fixes", "Соберите заново, чтобы применить"))

        build = layout.column(align=True)
        build.scale_y = 1.5
        build.enabled = state.has_index and not state.running
        build.operator(
            "umb.build", text=lt("Build library", "Собрать библиотеку"), icon="ASSET_MANAGER"
        ).changed_only = False

        if state.built_count:
            grid = layout.box().grid_flow(columns=2, align=True)
            grid.label(text=lt("Assets", "Ассеты"), icon="ASSET_MANAGER")
            grid.label(text=str(state.built_count))
            grid.label(text=lt("Files", "Файлы"), icon="FILE_BLEND")
            grid.label(text=str(state.file_count))
            grid.label(text=lt("Catalogs", "Каталоги"), icon="OUTLINER")
            grid.label(text=str(state.catalog_count))
            if state.failed_count:
                row = grid.row()
                row.alert = True
                row.label(text=lt("Failed", "Ошибки"), icon="ERROR")
                grid.label(text=str(state.failed_count))


class UMB_PT_Browse(_Base):
    bl_parent_id = "UMB_PT_Main"
    bl_label = "4 · Use the library"

    def draw_header(self, context):
        _preferences, state = self.context_data(context)
        self.layout.label(text="", icon=_done_icon(bool(state and state.library_registered)))

    def draw(self, context):
        layout = self.layout
        _preferences, state = self.context_data(context)

        column = layout.column(align=True)
        column.scale_y = 1.3
        column.operator(
            "umb.register_asset_library",
            text=lt("Register library", "Зарегистрировать библиотеку"),
            icon="FILE_REFRESH",
        )
        column.operator(
            "umb.open_asset_browser",
            text=lt("Open Asset Browser", "Открыть браузер ассетов"),
            icon="ASSET_MANAGER",
        )

        steps = layout.box()
        steps.scale_y = 0.9
        steps.label(text=lt("Then in the Asset Browser:", "Дальше в браузере ассетов:"), icon="INFO")
        column = steps.column(align=True)
        column.scale_y = 0.85
        column.label(text=lt("1. Pick the Unity Materials library", "1. Выберите библиотеку Unity Materials"))
        column.label(text=lt("2. Choose a catalog on the left", "2. Слева выберите каталог"))
        column.label(text=lt("3. Drag a material onto an object", "3. Перетащите материал на объект"))


def _node(material, prefix: str):
    return next(
        (node for node in material.node_tree.nodes if node.name.startswith(prefix)), None
    )


def _nodes(material, prefix: str):
    return [node for node in material.node_tree.nodes if node.name.startswith(prefix)]


def draw_material_parameters(layout, context) -> None:
    """Parameters of the active material, shared by the sidebar and the pie."""
    material = getattr(context.object, "active_material", None)

    if material is not None and material.library is not None:
        note = layout.box()
        note.label(text=lt("Linked from the library", "Связан с библиотекой"), icon="LINKED")
        row = note.column(align=True)
        row.scale_y = 0.85
        row.label(text=lt("Values below are read-only", "Значения ниже только для чтения"))
        row.label(text=lt("Undo keeps the link in the file", "Undo оставляет связь в файле"))
        note.operator(
            "umb.make_editable_copy",
            text=lt("Create Editable Copy", "Создать редактируемую копию"),
            icon="DUPLICATE",
        )

    if material is None:
        layout.label(text=lt("No active material", "Нет активного материала"), icon="INFO")
        return
    if material.get("ump_unity_guid") is None and material.get("ump_derived_from") is None:
        layout.label(text=material.name, icon="MATERIAL")
        layout.label(text=lt("Not from the Unity library", "Не из библиотеки Unity"), icon="INFO")
        return
    if material.node_tree is None:
        return

    header = layout.box()
    header.label(text=material.name, icon="MATERIAL")
    column = header.column(align=True)
    column.scale_y = 0.85
    column.label(text=str(material.get("ump_shader_name", "")), icon="NODE_MATERIAL")
    column.label(text=str(material.get("ump_unity_path", "")))
    if material.get("ump_derived_from") is not None:
        column.label(
            text=lt("Editable copy of ", "Редактируемая копия ")
            + str(material.get("ump_derived_name", "")),
            icon="DUPLICATE",
        )

    principled = _node(material, f"{NODE_PREFIX} Principled")
    if principled is not None:
        box = layout.box()
        box.label(text=lt("Surface", "Поверхность"), icon="SHADING_RENDERED")
        for socket_name, english, russian in (
            ("Base Color", "Base Color", "Базовый цвет"),
            ("Metallic", "Metallic", "Металличность"),
            ("Roughness", "Roughness", "Шероховатость"),
            ("Alpha", "Alpha", "Прозрачность"),
        ):
            socket = principled.inputs.get(socket_name)
            if socket is None:
                continue
            row = box.row()
            if socket.links:
                # Driven by a texture: show what feeds it instead of a dead field.
                row.label(text=lt(english, russian))
                sub = row.row()
                sub.alignment = "RIGHT"
                sub.label(text=socket.links[0].from_node.label or socket.links[0].from_node.name[4:])
            else:
                row.prop(socket, "default_value", text=lt(english, russian))

    tint = _node(material, f"{NODE_PREFIX} Tint")
    if tint is not None:
        box = layout.box()
        box.label(text=lt("Colour tint", "Цветовой тинт"), icon="COLOR")
        box.prop(tint.inputs["Factor"], "default_value", text=lt("Amount", "Сила"), slider=True)
        box.prop(tint.inputs[7], "default_value", text=lt("Colour", "Цвет"))
        if not tint.inputs["Factor"].default_value:
            note = box.column(align=True)
            note.scale_y = 0.85
            note.label(
                text=lt(
                    "Unity did not prove this tint", "Unity не подтвердил этот тинт"
                ),
                icon="INFO",
            )

    normal_map = _node(material, f"{NODE_PREFIX} Normal Map")
    if normal_map is not None:
        box = layout.box()
        box.label(text=lt("Normal", "Нормаль"), icon="NORMALS_FACE")
        box.prop(
            normal_map.inputs["Strength"],
            "default_value",
            text=lt("Strength", "Сила"),
            slider=True,
        )

    mappings = _nodes(material, f"{NODE_PREFIX} Mapping")
    box = layout.box()
    box.label(text=lt("Tiling", "Тайлинг"), icon="UV")
    if mappings:
        for mapping in mappings:
            column = box.column(align=True)
            column.prop(mapping.inputs["Scale"], "default_value", text=lt("Scale", "Масштаб"))
            column.prop(mapping.inputs["Location"], "default_value", text=lt("Offset", "Смещение"))
    else:
        box.label(text=lt("Unity tiling is 1:1", "В Unity тайлинг 1:1"), icon="INFO")

    occlusion = _node(material, f"{NODE_PREFIX} Occlusion Mix")
    if occlusion is not None:
        box = layout.box()
        box.prop(
            occlusion.inputs["Factor"],
            "default_value",
            text=lt("Occlusion", "Окклюзия"),
            slider=True,
        )

    row = layout.row(align=True)
    row.scale_y = 1.2
    row.operator(
        "umb.rebuild_scene_materials",
        text=lt("Reset from Unity", "Сбросить из Unity"),
        icon="FILE_REFRESH",
    ).only_active = True


class UMB_PT_Material(_Base):
    bl_parent_id = "UMB_PT_Main"
    bl_label = "Active material"

    def draw(self, context):
        draw_material_parameters(self.layout, context)


class UMB_PT_MaterialPopover(Panel):
    """Same parameters as a popover, so the pie can open them in place."""

    bl_idname = "UMB_PT_material_popover"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_label = "Unity Material"
    bl_ui_units_x = 15

    def draw(self, context):
        draw_material_parameters(self.layout, context)


class UMB_MT_MaterialPie(Menu):
    bl_idname = "UMB_MT_material_pie"
    bl_label = "Unity Material"

    def draw(self, context):
        pie = self.layout.menu_pie()
        # West / East first: those are the two halves of the same question --
        # look at the material, or make it yours.
        pie.popover(
            panel=UMB_PT_MaterialPopover.bl_idname,
            text=lt("Material parameters", "Параметры материала"),
            icon="MATERIAL",
        )
        pie.operator(
            "umb.make_editable_copy",
            text=lt("Create Editable Copy", "Создать редактируемую копию"),
            icon="DUPLICATE",
        )
        pie.operator(
            "umb.rebuild_scene_materials",
            text=lt("Reset from Unity", "Сбросить из Unity"),
            icon="FILE_REFRESH",
        ).only_active = True
        pie.operator(
            "umb.open_asset_browser",
            text=lt("Open Asset Browser", "Открыть Asset Browser"),
            icon="ASSET_MANAGER",
        )


def _material_context_menu(self, context):
    material = getattr(context, "material", None) or getattr(
        getattr(context, "object", None), "active_material", None
    )
    if material is None:
        return
    self.layout.separator()
    self.layout.operator(
        "umb.make_editable_copy",
        text=lt("Create Editable Copy", "Создать редактируемую копию"),
        icon="DUPLICATE",
    )


class UMB_PT_Updates(_Base):
    bl_parent_id = "UMB_PT_Main"
    bl_label = "Updates from Unity"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        _preferences, state = self.context_data(context)
        if state is None:
            return

        row = layout.row()
        row.scale_y = 1.2
        row.enabled = state.has_index and not state.running
        row.operator(
            "umb.check_updates", text=lt("Check for updates", "Проверить обновления"), icon="FILE_REFRESH"
        )

        if not state.diff_ready:
            layout.label(text=lt("Not checked yet", "Проверка не выполнялась"), icon="RADIOBUT_OFF")
            return

        grid = layout.box().grid_flow(columns=2, align=True)
        grid.label(text=lt("New", "Новые"), icon="ADD")
        grid.label(text=str(state.diff_new))
        grid.label(text=lt("Changed", "Изменённые"), icon="GREASEPENCIL")
        grid.label(text=str(state.diff_changed))
        grid.label(text=lt("Missing in Unity", "Нет в Unity"), icon="TRASH")
        grid.label(text=str(state.diff_missing))

        if state.diff_new or state.diff_changed:
            column = layout.column(align=True)
            column.scale_y = 1.3
            column.enabled = not state.running
            column.operator(
                "umb.build",
                text=lt("Rebuild changed only", "Пересобрать изменённые"),
                icon="TRACKING_REFINE_FORWARDS",
            ).changed_only = True
        if state.diff_missing:
            note = layout.column(align=True)
            note.scale_y = 0.85
            note.label(
                text=lt("Missing materials are kept.", "Отсутствующие материалы сохраняются."),
                icon="INFO",
            )

        scene = layout.box()
        scene.label(text=lt("Materials in this file", "Материалы в этом файле"), icon="FILE_BLEND")
        column = scene.column(align=True)
        column.scale_y = 0.85
        column.label(
            text=lt(
                "Dragging an asset in makes a copy.",
                "Перетаскивание ассета создаёт копию.",
            )
        )
        column.label(
            text=lt("Rebuilding the library leaves it as is.", "Пересборка библиотеки её не меняет.")
        )
        row = scene.row()
        row.scale_y = 1.2
        row.operator(
            "umb.rebuild_scene_materials",
            text=lt("Update materials in this file", "Обновить материалы в файле"),
            icon="FILE_REFRESH",
        ).only_active = False


class UMB_PT_Report(_Base):
    bl_parent_id = "UMB_PT_Main"
    bl_label = "Report"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        _preferences, state = self.context_data(context)
        if state is None:
            return

        layout.operator("umb.load_report", text=lt("Refresh", "Обновить"), icon="FILE_REFRESH")

        box = layout.box()
        box.label(text=lt("Shader coverage", "Покрытие шейдеров"), icon="NODE_MATERIAL")
        if len(state.shader_rows):
            box.template_list(
                "UMB_UL_Shaders", "", state, "shader_rows", state, "active_shader_row", rows=6
            )
            if 0 <= state.active_shader_row < len(state.shader_rows):
                box.label(text=state.shader_rows[state.active_shader_row].detail, icon="DOT")
        else:
            box.label(text=lt("Scan first", "Сначала выполните сканирование"), icon="INFO")

        warnings = layout.box()
        warnings.label(text=lt("Unmapped and warnings", "Непокрытое и предупреждения"), icon="ERROR")
        if len(state.warning_rows):
            warnings.template_list(
                "UMB_UL_Warnings", "", state, "warning_rows", state, "active_warning_row", rows=5
            )
        else:
            warnings.label(text=lt("Nothing to report", "Замечаний нет"), icon="CHECKMARK")


class UMB_PT_Settings(_Base):
    bl_parent_id = "UMB_PT_Main"
    bl_label = "Settings"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        preferences, _state = self.context_data(context)
        if preferences is None:
            return

        layout.prop(preferences, pn("ui_language"), text="")
        layout.prop(preferences, pn("import_method"), text="")
        layout.prop(preferences, pn("pack_mode"), text="")
        layout.prop(preferences, pn("generate_previews"))
        layout.prop(preferences, pn("apply_all_tints"))
        layout.prop(preferences, pn("profiles_path"), text="")

        facts = layout.box()
        column = facts.column(align=True)
        column.scale_y = 0.85
        for english, russian in (
            ("Unity is only read, never written.", "Unity только читается, запись не выполняется."),
            ("Textures stay in place; paths only.", "Текстуры остаются на месте; только пути."),
            ("The library is local, not for git.", "Библиотека локальная, не для git."),
        ):
            column.label(text=lt(english, russian), icon="DOT")


def draw_preferences(preferences, context):
    """Status only.

    Every setting is edited in the sidebar, where the work happens; repeating the
    same fields here would give two places to change one value.
    """
    layout = preferences.layout

    box = layout.box()
    box.label(
        text=lt(
            "Everything is set up in 3D View > N panel > Unity Materials",
            "Всё настраивается в 3D-виде: N-панель > Unity Materials",
        ),
        icon="INFO",
    )

    path = bpy.path.abspath(preferences.library_path).strip()
    column = layout.column(align=True)
    if not path:
        column.label(
            text=lt("Library folder is not set yet", "Папка библиотеки ещё не указана"),
            icon="RADIOBUT_OFF",
        )
        return

    problem = extract.validate_library_root(path)
    row = column.row()
    row.alert = bool(problem)
    row.label(
        text=lt("Library folder", "Папка библиотеки") + f": {path}",
        icon="ERROR" if problem else "CHECKMARK",
    )
    if problem:
        note = column.row()
        note.alert = True
        note.label(
            text=lt(
                "It is inside a Unity project; change it in the sidebar",
                "Она внутри проекта Unity; измените её в боковой панели",
            )
        )
        return

    state = properties.get_state(context)
    if state is not None and state.material_count:
        column.label(
            text=lt(
                f"{state.material_count} materials, {state.built_count} assets built",
                f"Материалов: {state.material_count}, собрано ассетов: {state.built_count}",
            ),
            icon="ASSET_MANAGER",
        )


CLASSES = (
    UMB_UL_Sources,
    UMB_UL_Shaders,
    UMB_UL_Warnings,
    UMB_PT_Main,
    UMB_PT_Sources,
    UMB_PT_Library,
    UMB_PT_Scan,
    UMB_PT_Browse,
    UMB_PT_Material,
    UMB_PT_MaterialPopover,
    UMB_MT_MaterialPie,
    UMB_PT_Updates,
    UMB_PT_Report,
    UMB_PT_Settings,
)

_keymaps: list = []


def _register_keymap() -> None:
    """Ctrl+Shift+M in the 3D View opens the material pie.

    Registered in the add-on key config, so the user can rebind or delete it in
    Preferences > Keymap without the add-on fighting back.
    """
    configuration = bpy.context.window_manager.keyconfigs.addon
    if configuration is None:
        return
    keymap = configuration.keymaps.new(name="3D View", space_type="VIEW_3D")
    item = keymap.keymap_items.new("wm.call_menu_pie", "M", "PRESS", ctrl=True, shift=True)
    item.properties.name = UMB_MT_MaterialPie.bl_idname
    _keymaps.append((keymap, item))


def _unregister_keymap() -> None:
    for keymap, item in _keymaps:
        try:
            keymap.keymap_items.remove(item)
        except (RuntimeError, ReferenceError):
            pass
    _keymaps.clear()


def register():
    registration.register_classes(CLASSES)
    menu = getattr(bpy.types, "MATERIAL_MT_context_menu", None)
    if menu is not None:
        menu.append(_material_context_menu)
    _register_keymap()


def unregister():
    _unregister_keymap()
    menu = getattr(bpy.types, "MATERIAL_MT_context_menu", None)
    if menu is not None:
        try:
            menu.remove(_material_context_menu)
        except ValueError:
            pass
    registration.unregister_classes(CLASSES)
