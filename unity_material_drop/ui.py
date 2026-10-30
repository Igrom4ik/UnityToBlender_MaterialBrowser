"""Panels.

The add-on is meant to be used without ever opening a panel -- you drag a file
onto an object and that is the whole workflow. So the panel exists for the three
things a drop cannot show by itself: how the project index is doing, what the
last drop actually built, and where a material came from.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.types import Menu, Panel, UIList

from . import operators, properties, registration, session
from .localization import language, property_name, text as lt


CATEGORY = "Unity Drop"


class UMD_UL_report(UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_prop):
        row = layout.row(align=True)
        row.label(
            text=item.text_ru if language() == "RU" and item.text_ru else item.text,
            icon=item.icon or "DOT",
        )


def _material_of(context):
    material = getattr(context, "material", None)
    if material is not None:
        return material
    active = context.active_object
    return active.active_material if active is not None else None


def _index_row(layout, state) -> None:
    box = layout.box()
    column = box.column(align=True)
    if state.assets_root:
        column.label(text=Path(state.assets_root).parent.name, icon="FILE_FOLDER")
    else:
        column.label(
            text=lt("No project yet", "Проект ещё не открывался"), icon="FILE_FOLDER"
        )
    if state.index_count:
        column.label(
            text=lt(
                f"Index: {state.index_count} assets",
                f"Индекс: ассетов {state.index_count}",
            ),
            icon="OUTLINER_OB_GROUP_INSTANCE",
        )
        if state.index_built_at:
            column.label(text=state.index_built_at.replace("T", " ").replace("+00:00", " UTC"))
    else:
        column.label(
            text=lt("Index: not built", "Индекс: не построен"), icon="OUTLINER_OB_GROUP_INSTANCE"
        )

    row = box.row(align=True)
    row.enabled = not state.busy
    row.operator(
        operators.UMD_OT_index_project.bl_idname,
        text=lt("Index project", "Индексировать проект"),
        icon="FILE_REFRESH",
    )
    row.operator(
        operators.UMD_OT_clear_index.bl_idname, text="", icon="TRASH"
    )


class _Base(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    @classmethod
    def poll(cls, context):
        return properties.get_state(context) is not None


class UMD_PT_main(_Base):
    bl_idname = "UMD_PT_main"
    bl_label = "Unity Material Drop"

    def draw(self, context):
        layout = self.layout
        state = properties.get_state(context)
        preferences = properties.get_preferences(context)

        column = layout.column(align=True)
        column.label(
            text=lt(
                "Drag a .mat onto an object",
                "Перетащите .mat на объект",
            ),
            icon="MOUSE_LMB_DRAG",
        )
        column.label(
            text=lt(
                "from Explorer, not from Unity",
                "из проводника, не из окна Unity",
            ),
            icon="BLANK1",
        )

        if state.busy:
            box = layout.box()
            box.label(
                text=lt("Indexing the project", "Индексируется проект"), icon="FILE_REFRESH"
            )
            box.progress(factor=state.progress, text=state.progress_label or " ")
            box.label(text=lt("Esc cancels", "Esc — отмена"), icon="EVENT_ESC")
        elif state.status:
            box = layout.box()
            box.label(
                text=state.status_ru if language() == "RU" and state.status_ru else state.status,
                icon=state.status_icon or "INFO",
            )

        _index_row(layout, state)

        column = layout.column(align=True)
        column.operator(
            operators.UMD_OT_drop_from_clipboard.bl_idname,
            text=lt("Build from clipboard path", "Собрать по пути из буфера"),
            icon="PASTEDOWN",
        )
        column.operator(
            operators.UMD_OT_reload_all.bl_idname,
            text=lt("Reload all materials", "Перечитать все материалы"),
            icon="FILE_REFRESH",
        )
        column.operator(
            operators.UMD_OT_merge_duplicates.bl_idname,
            text=lt("Merge duplicates", "Объединить дубликаты"),
            icon="AUTOMERGE_ON",
        )
        if preferences is not None:
            layout.prop(preferences, property_name("assign_mode"))


class UMD_PT_report(_Base):
    bl_idname = "UMD_PT_report"
    bl_parent_id = "UMD_PT_main"
    bl_label = "Report"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text=lt("Report", "Отчёт"))

    def draw(self, context):
        layout = self.layout
        state = properties.get_state(context)
        if not len(state.report):
            layout.label(text=lt("Nothing dropped yet", "Пока ничего не перетаскивали"))
            return
        layout.template_list(
            "UMD_UL_report", "", state, "report", state, "report_index", rows=4
        )


class UMD_PT_settings(_Base):
    bl_idname = "UMD_PT_settings"
    bl_parent_id = "UMD_PT_main"
    bl_label = "Settings"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text=lt("Settings", "Настройки"))

    def draw(self, context):
        preferences = properties.get_preferences(context)
        if preferences is None:
            return
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        for name in (
            "assign_mode",
            "assign_selected",
            "edit_mode_faces",
            "reuse_existing",
            "update_mode",
            "trust_all_tints",
            "auto_index",
        ):
            layout.prop(preferences, property_name(name))
        layout.prop(preferences, property_name("ui_language"))


class UMD_PT_material(Panel):
    bl_idname = "UMD_PT_material"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"
    bl_label = "Unity source"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        material = _material_of(context)
        return material is not None and bool(material.get("umd_source"))

    def draw_header(self, context):
        self.layout.label(text="", icon="IMPORT")

    def draw(self, context):
        layout = self.layout
        material = _material_of(context)
        if material is None:
            return

        column = layout.column(align=True)
        column.label(text=str(material.get("umd_unity_path", "")) or str(material.get("umd_source", "")))
        shader = str(material.get("umd_shader_name", ""))
        if shader:
            column.label(text=shader, icon="NODE_MATERIAL")
        profile = str(material.get("umd_profile", ""))
        workflow = str(material.get("umd_workflow", ""))
        if profile or workflow:
            column.label(text=f"{profile} / {workflow}", icon="PRESET")

        unresolved = str(material.get("umd_unresolved", ""))
        if unresolved:
            column.label(
                text=lt(f"Not found: {unresolved}", f"Не найдено: {unresolved}"), icon="ERROR"
            )
        unmapped = str(material.get("umd_unmapped", ""))
        if unmapped:
            column.label(
                text=lt(f"Unmapped: {unmapped}", f"Не сопоставлено: {unmapped}"), icon="INFO"
            )

        row = layout.row(align=True)
        row.operator(
            operators.UMD_OT_reload_material.bl_idname,
            text=lt("Reload from Unity", "Перечитать из Unity"),
            icon="FILE_REFRESH",
        )
        row.operator(operators.UMD_OT_show_source.bl_idname, text="", icon="FILE_FOLDER")


def draw_preferences(layout, preferences, _context) -> None:
    layout.use_property_split = True
    layout.use_property_decorate = False

    column = layout.column()
    column.prop(preferences, property_name("ui_language"))

    box = layout.box()
    box.label(text=lt("Drop", "Перетаскивание"), icon="MOUSE_LMB_DRAG")
    for name in (
        "assign_mode",
        "assign_selected",
        "edit_mode_faces",
        "reuse_existing",
        "update_mode",
        "trust_all_tints",
        "profiles_path",
    ):
        box.prop(preferences, property_name(name))

    box = layout.box()
    box.label(text=lt("GUID index", "Индекс GUID"), icon="OUTLINER_OB_GROUP_INSTANCE")
    for name in (
        "auto_index",
        "nearby_seconds",
        "index_packages",
        "index_package_cache",
        "index_workers",
        "cache_path",
    ):
        box.prop(preferences, property_name(name))
    box.label(
        text=lt(
            "The index is a cache of the Unity project, never of the .blend",
            "Индекс — кэш проекта Unity, а не файла .blend",
        ),
        icon="INFO",
    )


class UMD_PT_material_values(Panel):
    """Every Unity value the material carries, mapped or not.

    Principled has nowhere to put a Z-fight offset or a damage toggle, but the
    material still has one, and looking it up in Unity to find out is exactly
    the interruption this add-on exists to remove.
    """

    bl_idname = "UMD_PT_material_values"
    bl_parent_id = "UMD_PT_material"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"
    bl_label = "Unity parameters"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text=lt("Unity parameters", "Параметры Unity"))

    def draw(self, context):
        import json

        layout = self.layout
        material = _material_of(context)
        raw = str(material.get("umd_unity_values", "")) if material else ""
        try:
            values = json.loads(raw) if raw else {}
        except ValueError:
            values = {}
        if not values:
            layout.label(text=lt("Nothing recorded", "Ничего не записано"))
            return

        column = layout.column(align=True)
        for name, (value, origin) in values.items():
            if isinstance(value, list):
                shown = ", ".join(f"{channel:.3g}" for channel in value)
            elif isinstance(value, float):
                shown = f"{value:.4g}"
            else:
                shown = str(value)
            row = column.row(align=True)
            row.active = origin == "material"
            row.label(text=name)
            row.label(text=shown)
        layout.label(
            text=lt(
                "Dimmed rows are shader defaults",
                "Тусклые строки — значения по умолчанию из шейдера",
            ),
            icon="INFO",
        )


_CLASSES = (
    UMD_UL_report,
    UMD_PT_main,
    UMD_PT_report,
    UMD_PT_settings,
    UMD_PT_material,
    UMD_PT_material_values,
)


def register():
    registration.register_classes(_CLASSES)


def unregister():
    registration.unregister_classes(_CLASSES)
