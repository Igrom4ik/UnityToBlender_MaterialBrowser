"""Add-on data model.

Configuration lives in add-on preferences, not in the scene: one machine has one
library, and it must not depend on which .blend happens to be open. Run-time
state lives on the window manager so it never gets saved into a file.
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, PropertyGroup

from . import registration


SOURCE_MATERIALS = "MATERIALS"
SOURCE_ASSETS = "ASSETS"

# Enum index of the "Whole Unity project" source removed in 0.9.0. Preferences
# written by earlier versions still carry it, and it must not come back to life
# as another type: indexing a whole project is what invariant 5 forbids.
LEGACY_PROJECT_KIND = 2

# A user asset library accepts only LINK, APPEND and PACK -- the "Append (Reuse
# Data)" variant lives in the Asset Browser header, not on the library itself.
IMPORT_APPEND = "APPEND"
IMPORT_LINK = "LINK"


def _apply_import_method_to_library(preferences, context) -> None:
    """Push the chosen method onto the registered library entry right away.

    Blender stores it per asset library, not per add-on, so a setting that only
    took effect on the next registration would look broken.
    """
    root = bpy.path.abspath(preferences.library_path).strip().rstrip("\\/")
    if not root:
        return
    import os

    target = os.path.normcase(root)
    for library in context.preferences.filepaths.asset_libraries:
        if os.path.normcase(bpy.path.abspath(library.path).rstrip("\\/")) != target:
            continue
        try:
            library.import_method = preferences.import_method
        except (AttributeError, TypeError):
            pass


def _alias_get(name):
    return lambda self: getattr(self, name)


def _alias_set(name):
    return lambda self, value: setattr(self, name, value)


def _enum_alias_get(name, identifiers):
    def getter(self):
        try:
            return identifiers.index(getattr(self, name))
        except ValueError:
            return 0

    return getter


def _enum_alias_set(name, identifiers):
    def setter(self, value):
        if 0 <= value < len(identifiers):
            setattr(self, name, identifiers[value])

    return setter


class UMB_PG_Source(PropertyGroup):
    enabled: BoolProperty(
        name="Enabled",
        description="Use this folder when extracting the Unity library",
        default=True,
    )
    path: StringProperty(
        name="Folder",
        description="Unity folder to read. Materials folders are searched for .mat files; "
        "asset folders only resolve GUID references such as textures and shaders",
        subtype="DIR_PATH",
    )
    kind: EnumProperty(
        name="Type",
        description="What this folder contributes to the extraction",
        items=(
            (SOURCE_MATERIALS, "Materials", "Search this folder for .mat files", "MATERIAL", 0),
            (SOURCE_ASSETS, "Textures / Shaders", "Only resolve GUID references from this folder", "TEXTURE", 1),
        ),
        default=SOURCE_MATERIALS,
    )
    enabled_ru: BoolProperty(
        name="Включено",
        description="Использовать эту папку при извлечении библиотеки Unity",
        get=_alias_get("enabled"),
        set=_alias_set("enabled"),
    )
    path_ru: StringProperty(
        name="Папка",
        description="Папка Unity для чтения. В папках материалов ищутся файлы .mat; "
        "папки ассетов нужны только для разрешения ссылок GUID — текстур и шейдеров",
        subtype="DIR_PATH",
        get=_alias_get("path"),
        set=_alias_set("path"),
    )
    kind_ru: EnumProperty(
        name="Тип",
        description="Что даёт эта папка при сканировании",
        items=(
            (SOURCE_MATERIALS, "Материалы", "Искать в этой папке файлы .mat", "MATERIAL", 0),
            (SOURCE_ASSETS, "Текстуры и шейдеры", "Только разрешение ссылок GUID из этой папки", "TEXTURE", 1),
        ),
        get=_enum_alias_get("kind", [SOURCE_MATERIALS, SOURCE_ASSETS]),
        set=_enum_alias_set("kind", [SOURCE_MATERIALS, SOURCE_ASSETS]),
    )


class UMB_PG_ReportRow(PropertyGroup):
    label: StringProperty(default="")
    detail: StringProperty(default="")
    count: IntProperty(default=0)
    status: StringProperty(default="")


class UMB_PG_State(PropertyGroup):
    """Run-time only: progress, statuses and the last report."""

    ui_section: EnumProperty(
        name="Section",
        items=(
            ("LIBRARY", "Library", "Unity folders and library location", "ASSET_MANAGER", 0),
            ("BUILD", "Build", "Extract and build the asset library", "MOD_BUILD", 1),
            ("REPORT", "Report", "Shader coverage and warnings", "INFO", 2),
            ("SETTINGS", "Settings", "Language and build options", "PREFERENCES", 3),
        ),
        default="LIBRARY",
    )
    ui_section_ru: EnumProperty(
        name="Раздел",
        items=(
            ("LIBRARY", "Библиотека", "Папки Unity и расположение библиотеки", "ASSET_MANAGER", 0),
            ("BUILD", "Сборка", "Извлечение и сборка библиотеки ассетов", "MOD_BUILD", 1),
            ("REPORT", "Отчёт", "Покрытие шейдеров и предупреждения", "INFO", 2),
            ("SETTINGS", "Настройки", "Язык и параметры сборки", "PREFERENCES", 3),
        ),
        get=_enum_alias_get("ui_section", ["LIBRARY", "BUILD", "REPORT", "SETTINGS"]),
        set=_enum_alias_set("ui_section", ["LIBRARY", "BUILD", "REPORT", "SETTINGS"]),
    )

    running: BoolProperty(default=False)
    progress: FloatProperty(default=0.0, min=0.0, max=1.0, subtype="FACTOR")
    progress_label: StringProperty(default="")

    status: StringProperty(default="No extraction has been run yet")
    status_ru: StringProperty(default="Извлечение ещё не выполнялось")
    status_icon: StringProperty(default="INFO")

    material_count: IntProperty(default=0)
    shader_count: IntProperty(default=0)
    built_count: IntProperty(default=0)
    file_count: IntProperty(default=0)
    failed_count: IntProperty(default=0)
    catalog_count: IntProperty(default=0)

    unresolved_textures: IntProperty(default=0)
    diff_new: IntProperty(default=0)
    diff_changed: IntProperty(default=0)
    diff_missing: IntProperty(default=0)
    diff_ready: BoolProperty(default=False)

    has_index: BoolProperty(default=False)
    library_registered: BoolProperty(default=False)
    built_with_version: StringProperty(default="")
    builder_outdated: BoolProperty(default=False)

    shader_rows: CollectionProperty(type=UMB_PG_ReportRow)
    active_shader_row: IntProperty(default=0)
    warning_rows: CollectionProperty(type=UMB_PG_ReportRow)
    active_warning_row: IntProperty(default=0)


class UMB_Preferences(AddonPreferences):
    bl_idname = __package__

    ui_language: EnumProperty(
        name="Language / Язык",
        description="Language of this add-on / Язык этого аддона",
        items=(
            ("EN", "English", "Use English labels and tooltips"),
            ("RU", "Русский", "Использовать русские подписи и подсказки"),
        ),
        default="EN",
    )
    library_path: StringProperty(
        name="Library Folder",
        description="Folder that receives the generated asset library. Keep it outside "
        "the Unity project and outside any git repository",
        subtype="DIR_PATH",
    )
    profiles_path: StringProperty(
        name="Shader Profiles",
        description="Optional JSON file with shader conversion rules. Leave empty to use "
        "the built-in heuristics",
        subtype="FILE_PATH",
    )
    pack_mode: EnumProperty(
        name="Packing",
        description="How generated materials are packed into .blend files",
        items=(
            ("per_catalog", "One file per folder", "Fewer files, faster library scanning; "
             "rebuilding one material rewrites its whole folder", "FILE_BLEND", 0),
            ("per_material", "One file per material", "Rebuilding touches a single small file; "
             "produces thousands of files", "DUPLICATE", 1),
        ),
        default="per_catalog",
    )
    import_method: EnumProperty(
        name="Dragged material",
        description="What the Asset Browser puts in the file when a material is dragged in",
        items=(
            (
                IMPORT_APPEND,
                "Append a copy",
                "The dragged material is a local copy: it can be edited and Ctrl+Z undoes the "
                "drop completely. Library rebuilds reach it through Update materials in this "
                "file, and repeated drops add their own image data-blocks",
                "DUPLICATE",
                0,
            ),
            (
                IMPORT_LINK,
                "Link to the library",
                "The dragged material stays linked: a library rebuild reaches every scene at "
                "once, but its values are read-only and undo leaves the link in the file",
                "LINKED",
                1,
            ),
        ),
        default=IMPORT_APPEND,
        update=_apply_import_method_to_library,
    )
    generate_previews: BoolProperty(
        name="Generate previews",
        description="Render asset thumbnails while building. Slower, but the Asset Browser "
        "shows real materials instead of blank tiles",
        default=True,
    )
    apply_all_tints: BoolProperty(
        name="Apply colour tint everywhere",
        description="Multiply the base colour by the material colour even when the shader "
        "does not prove it does so. The tint node is always created either way; this only "
        "decides whether its factor starts at 1",
        default=False,
    )
    sources: CollectionProperty(type=UMB_PG_Source)
    active_source: IntProperty(default=0)

    ui_language_ru: EnumProperty(
        name="Язык",
        description="Язык этого аддона",
        items=(
            ("EN", "English", "Английский интерфейс", 0),
            ("RU", "Русский", "Русский интерфейс", 1),
        ),
        get=_enum_alias_get("ui_language", ["EN", "RU"]),
        set=_enum_alias_set("ui_language", ["EN", "RU"]),
    )
    library_path_ru: StringProperty(
        name="Папка библиотеки",
        description="Папка, в которую пишется библиотека ассетов. Держите её вне проекта "
        "Unity и вне любого git-репозитория",
        subtype="DIR_PATH",
        get=_alias_get("library_path"),
        set=_alias_set("library_path"),
    )
    profiles_path_ru: StringProperty(
        name="Профили шейдеров",
        description="Необязательный JSON с правилами конвертации шейдеров. Пусто — работают "
        "встроенные эвристики",
        subtype="FILE_PATH",
        get=_alias_get("profiles_path"),
        set=_alias_set("profiles_path"),
    )
    pack_mode_ru: EnumProperty(
        name="Упаковка",
        description="Как материалы раскладываются по файлам .blend",
        items=(
            ("per_catalog", "Один файл на папку", "Меньше файлов и быстрее сканирование; "
             "пересборка одного материала переписывает всю папку", "FILE_BLEND", 0),
            ("per_material", "Один файл на материал", "Пересборка затрагивает один небольшой файл; "
             "создаются тысячи файлов", "DUPLICATE", 1),
        ),
        get=_enum_alias_get("pack_mode", ["per_catalog", "per_material"]),
        set=_enum_alias_set("pack_mode", ["per_catalog", "per_material"]),
    )
    import_method_ru: EnumProperty(
        name="Перетащенный материал",
        description="Что Asset Browser кладёт в файл при перетаскивании материала",
        items=(
            (
                IMPORT_APPEND,
                "Копия в файле",
                "Перетащенный материал — локальная копия: его можно править, а Ctrl+Z полностью "
                "отменяет перетаскивание. Пересборка библиотеки доходит до него через "
                "«Обновить материалы в этом файле»; повторные перетаскивания заводят свои "
                "датаблоки изображений",
                "DUPLICATE",
                0,
            ),
            (
                IMPORT_LINK,
                "Связь с библиотекой",
                "Перетащенный материал остаётся связанным: пересборка сразу доходит до всех сцен, "
                "но значения только для чтения, а undo оставляет связь в файле",
                "LINKED",
                1,
            ),
        ),
        get=_enum_alias_get("import_method", [IMPORT_APPEND, IMPORT_LINK]),
        set=_enum_alias_set("import_method", [IMPORT_APPEND, IMPORT_LINK]),
    )
    apply_all_tints_ru: BoolProperty(
        name="Применять цветовой тинт всегда",
        description="Умножать базовый цвет на цвет материала, даже если шейдер этого не "
        "подтверждает. Узел тинта создаётся в любом случае; параметр решает только, будет ли "
        "его фактор равен 1",
        get=_alias_get("apply_all_tints"),
        set=_alias_set("apply_all_tints"),
    )
    generate_previews_ru: BoolProperty(
        name="Создавать превью",
        description="Рендерить иконки ассетов при сборке. Дольше, но в браузере видны "
        "материалы, а не пустые плитки",
        get=_alias_get("generate_previews"),
        set=_alias_set("generate_previews"),
    )

    def draw(self, context):
        from . import ui

        ui.draw_preferences(self, context)


CLASSES = (
    UMB_PG_Source,
    UMB_PG_ReportRow,
    UMB_PG_State,
    UMB_Preferences,
)


def get_preferences(context=None):
    context = context or bpy.context
    try:
        return context.preferences.addons[__package__].preferences
    except (AttributeError, KeyError):
        return None


def get_state(context=None):
    context = context or bpy.context
    return getattr(context.window_manager, "umb_state", None)


def _is_kind(source, kind: str) -> bool:
    """Match a source type, ignoring entries left by the removed project type.

    Preferences are read the moment a panel draws, which can happen before the
    migration timer runs. Without this check Blender would resolve the stale
    value to the first enum item and scan a whole project as a materials folder.
    """
    if source.get("kind") == LEGACY_PROJECT_KIND:
        return False
    return source.kind == kind


def material_roots(preferences) -> tuple:
    from pathlib import Path

    return tuple(
        Path(bpy.path.abspath(source.path))
        for source in preferences.sources
        if source.enabled and _is_kind(source, SOURCE_MATERIALS) and source.path.strip()
    )


def asset_roots(preferences) -> tuple:
    from pathlib import Path

    return tuple(
        Path(bpy.path.abspath(source.path))
        for source in preferences.sources
        if source.enabled and _is_kind(source, SOURCE_ASSETS) and source.path.strip()
    )


def drop_legacy_project_sources(preferences) -> int:
    """Remove "whole project" entries left by preferences from before 0.9.0.

    The stored value is read through `get`, not through `kind`: the enum item is
    gone, so Blender would report the removed type as `MATERIALS` and quietly
    turn a project root into a folder scanned for materials.
    """
    removed = 0
    for index in reversed(range(len(preferences.sources))):
        if preferences.sources[index].get("kind") == LEGACY_PROJECT_KIND:
            preferences.sources.remove(index)
            removed += 1
    if removed:
        preferences.active_source = max(0, min(preferences.active_source, len(preferences.sources) - 1))
    return removed


def register():
    registration.register_classes(CLASSES)
    bpy.types.WindowManager.umb_state = PointerProperty(type=UMB_PG_State)


def unregister():
    if hasattr(bpy.types.WindowManager, "umb_state"):
        del bpy.types.WindowManager.umb_state
    registration.unregister_classes(CLASSES)
