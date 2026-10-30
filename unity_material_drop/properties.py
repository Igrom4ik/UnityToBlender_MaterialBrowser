"""Add-on data model.

Settings live in add-on preferences, not in the scene: they describe this
machine's Unity checkout and must not change with whichever .blend is open.
Run-time state lives on the window manager so it is never saved into a file.
"""

from __future__ import annotations

from pathlib import Path

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


UPDATE_ASK = "ASK"
UPDATE_ALWAYS = "ALWAYS"
UPDATE_NEVER = "NEVER"

ASSIGN_SLOT = "SLOT"
ASSIGN_APPEND = "APPEND"
ASSIGN_REPLACE_ALL = "REPLACE_ALL"
ASSIGN_NONE = "NONE"


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


_ASSIGN_IDENTIFIERS = (ASSIGN_REPLACE_ALL, ASSIGN_SLOT, ASSIGN_APPEND, ASSIGN_NONE)
_UPDATE_IDENTIFIERS = (UPDATE_ASK, UPDATE_ALWAYS, UPDATE_NEVER)
_LANGUAGE_IDENTIFIERS = ("EN", "RU")


def default_cache_directory() -> str:
    """Where the GUID index is kept when the user names no folder."""
    try:
        return bpy.utils.extension_path_user(__package__, path="cache", create=True)
    except (AttributeError, ValueError):
        return bpy.utils.user_resource("DATAFILES", path="unity_material_drop", create=True)


class UMD_PG_ReportRow(PropertyGroup):
    text: StringProperty(name="Text", default="")
    text_ru: StringProperty(name="Текст", default="")
    icon: StringProperty(name="Icon", default="DOT")


class UMD_PG_State(PropertyGroup):
    """Everything the panel shows about the last drop and the running scan."""

    busy: BoolProperty(name="Busy", default=False)
    progress: FloatProperty(name="Progress", default=0.0, min=0.0, max=1.0)
    progress_label: StringProperty(name="Progress", default="")

    status: StringProperty(name="Status", default="")
    status_ru: StringProperty(name="Состояние", default="")
    status_icon: StringProperty(name="Icon", default="INFO")

    project: StringProperty(name="Unity project", default="")
    assets_root: StringProperty(name="Assets", default="")
    index_count: IntProperty(name="Indexed assets", default=0)
    index_built_at: StringProperty(name="Indexed at", default="")

    material_name: StringProperty(name="Material", default="")
    report: CollectionProperty(type=UMD_PG_ReportRow)
    report_index: IntProperty(name="Row", default=0)


class UMD_Preferences(AddonPreferences):
    bl_idname = __package__

    ui_language: EnumProperty(
        name="Language",
        description="Language of this add-on's own interface",
        items=(
            ("EN", "English", "English interface"),
            ("RU", "Русский", "Русский интерфейс"),
        ),
        default="EN",
    )
    cache_path: StringProperty(
        name="Index cache",
        description="Folder for the GUID index of each Unity project. "
        "Leave empty to keep it in Blender's own extension data folder",
        subtype="DIR_PATH",
        default="",
    )
    assign_mode: EnumProperty(
        name="Drop assigns",
        description="What a drop does to the object under the cursor in Object Mode. "
        "In Edit Mode the selected faces are the target instead",
        items=(
            (
                ASSIGN_REPLACE_ALL,
                "Whole object",
                "Give the object this material, replacing every slot it has. "
                "For part of an object, drop in Edit Mode with faces selected",
                "OBJECT_DATA",
                0,
            ),
            (
                ASSIGN_SLOT,
                "Slot under the cursor",
                "Replace only the material of the face the cursor is over, like Blender's "
                "own material drop",
                "MATERIAL",
                1,
            ),
            (
                ASSIGN_APPEND,
                "New slot",
                "Add the material as an extra slot and leave existing slots alone",
                "ADD",
                2,
            ),
            (
                ASSIGN_NONE,
                "Only create",
                "Build the material into the file without assigning it to anything",
                "FILE_NEW",
                3,
            ),
        ),
        default=ASSIGN_REPLACE_ALL,
    )
    assign_selected: BoolProperty(
        name="Also to selected objects",
        description="Apply the same assignment to every selected object, not only "
        "to the one under the cursor",
        default=False,
    )
    edit_mode_faces: BoolProperty(
        name="Edit Mode: selected faces",
        description="Dropping while a mesh is in Edit Mode assigns the material to the "
        "selected faces instead of to the whole object",
        default=True,
    )
    reuse_existing: BoolProperty(
        name="Reuse material",
        description="Dropping the same Unity material again reuses the material already "
        "in this file instead of creating a copy",
        default=True,
    )
    update_mode: EnumProperty(
        name="Material changed in Unity",
        description="What to do when the dropped material is already in this file and its "
        ".mat has changed since it was built",
        items=(
            (
                UPDATE_ASK,
                "Ask",
                "Assign the material as it is, then ask whether to rebuild it from Unity",
                "QUESTION",
                0,
            ),
            (
                UPDATE_ALWAYS,
                "Update",
                "Rebuild the node tree from Unity without asking",
                "FILE_REFRESH",
                1,
            ),
            (
                UPDATE_NEVER,
                "Keep",
                "Never rebuild; the material in this file wins over the one in Unity",
                "LOCKED",
                2,
            ),
        ),
        default=UPDATE_ASK,
    )
    auto_index: BoolProperty(
        name="Index automatically",
        description="When a dropped material references assets that are not in the index yet, "
        "index the project in the background and finish the drop when it is done",
        default=True,
    )
    index_packages: BoolProperty(
        name="Index Packages",
        description="Also index the project's Packages folder, where local packages keep "
        "their shaders and textures",
        default=True,
    )
    index_package_cache: BoolProperty(
        name="Index PackageCache",
        description="Also index Library/PackageCache. Needed for materials that use a shader "
        "from a downloaded package such as URP, and makes indexing noticeably slower",
        default=False,
    )
    index_workers: IntProperty(
        name="Reader threads",
        description="How many .meta files are read at once. Indexing waits on disk, not on "
        "the processor, so more threads help up to a point",
        default=8,
        min=1,
        max=32,
    )
    nearby_seconds: FloatProperty(
        name="Nearby search",
        description="How long a drop may spend looking for missing assets in the folders "
        "around the material before it falls back to indexing the project",
        default=1.5,
        min=0.0,
        max=30.0,
        subtype="TIME_ABSOLUTE",
    )
    trust_all_tints: BoolProperty(
        name="Always apply colour tint",
        description="Multiply the base colour texture by the material colour even when the "
        "shader does not prove that is what Unity does",
        default=False,
    )
    profiles_path: StringProperty(
        name="Shader profiles",
        description="Optional JSON with your own conversion rules. It is matched before the "
        "profiles shipped with the add-on",
        subtype="FILE_PATH",
        default="",
    )
    report_limit: IntProperty(
        name="Report rows",
        description="How many lines the report keeps after a drop",
        default=12,
        min=1,
        max=64,
    )

    ui_language_ru: EnumProperty(
        name="Язык",
        description="Язык интерфейса самого аддона",
        items=(
            ("EN", "English", "Английский интерфейс"),
            ("RU", "Русский", "Русский интерфейс"),
        ),
        get=_enum_alias_get("ui_language", _LANGUAGE_IDENTIFIERS),
        set=_enum_alias_set("ui_language", _LANGUAGE_IDENTIFIERS),
    )
    cache_path_ru: StringProperty(
        name="Кэш индекса",
        description="Папка для индекса GUID каждого проекта Unity. "
        "Пусто — индекс лежит в папке данных расширения Blender",
        subtype="DIR_PATH",
        get=_alias_get("cache_path"),
        set=_alias_set("cache_path"),
    )
    assign_mode_ru: EnumProperty(
        name="Перетаскивание",
        description="Что делает перетаскивание с объектом под курсором в объектном режиме. "
        "В режиме редактирования целью становятся выделенные полигоны",
        items=(
            (ASSIGN_REPLACE_ALL, "Весь объект", "Отдать объекту этот материал, заменив все его слоты. Для части объекта перетаскивайте в режиме редактирования с выделенными полигонами", "OBJECT_DATA", 0),
            (ASSIGN_SLOT, "Слот под курсором", "Заменить материал только той грани, что под курсором — как штатное перетаскивание материала в Blender", "MATERIAL", 1),
            (ASSIGN_APPEND, "Новый слот", "Добавить материал отдельным слотом, существующие не трогать", "ADD", 2),
            (ASSIGN_NONE, "Только создать", "Собрать материал в файле и никуда не назначать", "FILE_NEW", 3),
        ),
        get=_enum_alias_get("assign_mode", _ASSIGN_IDENTIFIERS),
        set=_enum_alias_set("assign_mode", _ASSIGN_IDENTIFIERS),
    )
    assign_selected_ru: BoolProperty(
        name="И на выделенные объекты",
        description="Применить то же назначение ко всем выделенным объектам, а не только "
        "к объекту под курсором",
        get=_alias_get("assign_selected"),
        set=_alias_set("assign_selected"),
    )
    edit_mode_faces_ru: BoolProperty(
        name="Режим редактирования: выделенные полигоны",
        description="Перетаскивание на меш в режиме редактирования назначает материал "
        "выделенным полигонам, а не всему объекту",
        get=_alias_get("edit_mode_faces"),
        set=_alias_set("edit_mode_faces"),
    )
    reuse_existing_ru: BoolProperty(
        name="Переиспользовать материал",
        description="Повторное перетаскивание того же материала Unity берёт материал, "
        "уже существующий в файле, вместо создания копии",
        get=_alias_get("reuse_existing"),
        set=_alias_set("reuse_existing"),
    )
    update_mode_ru: EnumProperty(
        name="Материал изменился в Unity",
        description="Что делать, когда перетащенный материал уже есть в файле, а его .mat "
        "изменился после сборки",
        items=(
            (UPDATE_ASK, "Спрашивать", "Назначить материал как есть, потом спросить, пересобирать ли из Unity", "QUESTION", 0),
            (UPDATE_ALWAYS, "Обновлять", "Пересобирать дерево узлов из Unity без вопросов", "FILE_REFRESH", 1),
            (UPDATE_NEVER, "Оставлять", "Никогда не пересобирать: материал в файле важнее, чем в Unity", "LOCKED", 2),
        ),
        get=_enum_alias_get("update_mode", _UPDATE_IDENTIFIERS),
        set=_enum_alias_set("update_mode", _UPDATE_IDENTIFIERS),
    )
    auto_index_ru: BoolProperty(
        name="Индексировать автоматически",
        description="Если перетащенный материал ссылается на ассеты, которых ещё нет в индексе, "
        "проиндексировать проект в фоне и завершить перетаскивание после этого",
        get=_alias_get("auto_index"),
        set=_alias_set("auto_index"),
    )
    index_packages_ru: BoolProperty(
        name="Индексировать Packages",
        description="Также индексировать папку Packages проекта, где локальные пакеты "
        "хранят свои шейдеры и текстуры",
        get=_alias_get("index_packages"),
        set=_alias_set("index_packages"),
    )
    index_package_cache_ru: BoolProperty(
        name="Индексировать PackageCache",
        description="Также индексировать Library/PackageCache. Нужно материалам с шейдером "
        "из скачанного пакета вроде URP и заметно замедляет индексацию",
        get=_alias_get("index_package_cache"),
        set=_alias_set("index_package_cache"),
    )
    index_workers_ru: IntProperty(
        name="Потоков чтения",
        description="Сколько файлов .meta читается одновременно. Индексация ждёт диск, "
        "а не процессор, поэтому потоки помогают до определённого предела",
        min=1,
        max=32,
        get=_alias_get("index_workers"),
        set=_alias_set("index_workers"),
    )
    nearby_seconds_ru: FloatProperty(
        name="Поиск рядом",
        description="Сколько времени перетаскивание может искать недостающие ассеты в папках "
        "вокруг материала, прежде чем перейти к индексации проекта",
        min=0.0,
        max=30.0,
        subtype="TIME_ABSOLUTE",
        get=_alias_get("nearby_seconds"),
        set=_alias_set("nearby_seconds"),
    )
    trust_all_tints_ru: BoolProperty(
        name="Всегда применять цветовой тинт",
        description="Умножать базовую текстуру на цвет материала даже тогда, когда шейдер "
        "не доказывает, что Unity делает именно так",
        get=_alias_get("trust_all_tints"),
        set=_alias_set("trust_all_tints"),
    )
    profiles_path_ru: StringProperty(
        name="Профили шейдеров",
        description="Необязательный JSON с вашими правилами конвертации. Он матчится раньше "
        "профилей, поставляемых с аддоном",
        subtype="FILE_PATH",
        get=_alias_get("profiles_path"),
        set=_alias_set("profiles_path"),
    )
    report_limit_ru: IntProperty(
        name="Строк отчёта",
        description="Сколько строк отчёта сохраняется после перетаскивания",
        min=1,
        max=64,
        get=_alias_get("report_limit"),
        set=_alias_set("report_limit"),
    )

    def draw(self, context):
        from . import ui

        ui.draw_preferences(self.layout, self, context)


_CLASSES = (UMD_PG_ReportRow, UMD_PG_State, UMD_Preferences)


def get_preferences(context=None):
    context = context or bpy.context
    try:
        return context.preferences.addons[__package__].preferences
    except (AttributeError, KeyError):
        return None


def get_state(context=None):
    context = context or bpy.context
    return getattr(context.window_manager, "unity_material_drop", None)


def cache_directory(preferences) -> Path:
    raw = bpy.path.abspath(getattr(preferences, "cache_path", "") or "").strip()
    return Path(raw) if raw else Path(default_cache_directory())


def set_status(state, english: str, russian: str, icon: str = "INFO") -> None:
    if state is None:
        return
    state.status = english
    state.status_ru = russian
    state.status_icon = icon


def set_report(state, rows, limit: int = 12) -> None:
    """Replace the report with `rows` of (english, russian, icon)."""
    if state is None:
        return
    state.report.clear()
    for english, russian, icon in list(rows)[:limit]:
        row = state.report.add()
        row.text = english
        row.text_ru = russian
        row.icon = icon


def register():
    registration.register_classes(_CLASSES)
    bpy.types.WindowManager.unity_material_drop = PointerProperty(type=UMD_PG_State)


def unregister():
    if hasattr(bpy.types.WindowManager, "unity_material_drop"):
        del bpy.types.WindowManager.unity_material_drop
    registration.unregister_classes(_CLASSES)
