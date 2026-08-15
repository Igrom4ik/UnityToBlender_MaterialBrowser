"""The 3ds Max window: four numbered steps, each stating what it needs.

The old entry point was a single `Build .mat` button that opened a folder
picker straight away. A file browser with no context is a question without a
question mark -- the answer here is a window that says, in order, what to
choose, what it found, what it will do and where the result ends up.

Qt only draws; every decision is in `session.py`, which needs neither Qt nor
3ds Max and is covered by the ordinary tests. PySide6 ships with 3ds Max 2025
and later, PySide2 with 2024, so the import is tried in that order and the
version is never assumed.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import time
import traceback

from . import __version__, session as session_module
from .localization import EN, RU, language, set_language, text as lt


TITLE = "Unity Materials"
DONE = "\u2714"
TODO = "\u25cb"
HINT_STYLE = "color: #9a9a9a;"
ALERT_STYLE = "color: #e0705a;"
GOOD_STYLE = "color: #7ac07a;"

_window = None
_dialog_class = None


def enum_value(owner, name: str, scope: str):
    """`QFrame.NoFrame` in PySide2, `QFrame.Shape.NoFrame` in PySide6.

    PySide6 still answers to the flat spelling, but it is deprecated and a
    crash while the window is being built looks like a broken install.
    """
    flat = getattr(owner, name, None)
    if flat is not None:
        return flat
    return getattr(getattr(owner, scope), name)


def qt_modules():
    """QtWidgets and QtCore from whichever PySide this 3ds Max carries."""
    for name in ("PySide6", "PySide2"):
        try:
            core = importlib.import_module(f"{name}.QtCore")
            widgets = importlib.import_module(f"{name}.QtWidgets")
        except ImportError:
            continue
        return widgets, core
    raise ImportError(
        "No PySide: 3ds Max 2025 and later ship PySide6, 2024 ships PySide2. "
        "Build the libraries from batch_build.py instead."
    )


def qt_gui():
    """QtGui, where the icons live, from the same PySide as the rest."""
    for name in ("PySide6", "PySide2"):
        try:
            return importlib.import_module(f"{name}.QtGui")
        except ImportError:
            continue
    raise ImportError("No PySide: neither PySide6 nor PySide2 could be imported")


def max_main_window(widgets):
    """Parent the window to Max, so it does not drop behind the main window."""
    try:
        import qtmax  # noqa: PLC0415  (3ds Max 2021+, absent outside it)

        window = qtmax.GetQMaxMainWindow()
        if window is not None:
            return window
    except Exception:  # noqa: BLE001  (an unparented window is still usable)
        pass
    application = widgets.QApplication.instance()
    return application.activeWindow() if application is not None else None


def show():
    """Open the window, or raise the one already open."""
    global _window

    widgets, _core = qt_modules()
    try:
        if _window is not None:
            _window.refresh()
    except RuntimeError:
        # Qt deleted the C++ side under us; a new window is the answer.
        _window = None
    if _window is None:
        _window = dialog_class()(max_main_window(widgets))
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


def open_in_browser(path) -> bool:
    """Open a .mat as a group in the Material/Map Browser.

    `sme.OpenMtlLib` is the scripted half of the browser's Open Material
    Library and exists from 3ds Max 2021. `loadMaterialLibrary` is not a
    substitute: it fills the current material library, and Autodesk documents
    that an open browser is not refreshed by it. That difference is the whole
    distance between a library of two thousand materials and a browser that
    looks like nothing was ever built.
    """
    from pymxs import runtime  # noqa: PLC0415  (inside Max only)

    editor = getattr(runtime, "sme", None)
    if editor is None:
        # Before 2021 a script can do no better than the current library.
        runtime.loadMaterialLibrary(str(path))
        return False

    if not editor.IsOpen():
        editor.Open()

    opened = False
    try:
        opened = bool(editor.OpenMtlLib(str(path)))
    except Exception:  # noqa: BLE001  (an older signature, or a refusal)
        opened = False
    if not opened:
        # It may already be open from an earlier click, which is not a failure.
        try:
            opened = bool(editor.HasMtlLib(str(path)))
        except Exception:  # noqa: BLE001
            opened = False
    if not opened:
        runtime.loadMaterialLibrary(str(path))
    return opened


def dialog_class():
    """Define the class on first use: its base class lives in PySide."""
    global _dialog_class
    if _dialog_class is not None:
        return _dialog_class

    widgets, core = qt_modules()

    class UnityMaterialsDialog(widgets.QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.session = session_module.Session()
            set_language(self.session.settings.language)

            self._busy = False
            self._cancel = False
            self._last_paint = 0.0

            self._build()
            self.retranslate()
            self.refresh()

        # -- construction ----------------------------------------------------

        def _build(self) -> None:
            self.setMinimumWidth(580)
            self.setMinimumHeight(560)

            outer = widgets.QVBoxLayout(self)

            top = widgets.QHBoxLayout()
            self.summary = widgets.QLabel()
            self.summary.setWordWrap(True)
            top.addWidget(self.summary, 1)
            self.language_button = widgets.QPushButton()
            self.language_button.setFixedWidth(44)
            self.language_button.clicked.connect(self._toggle_language)
            top.addWidget(self.language_button)
            outer.addLayout(top)

            scroll = widgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(enum_value(widgets.QFrame, "NoFrame", "Shape"))
            body = widgets.QWidget()
            self.steps = widgets.QVBoxLayout(body)
            scroll.setWidget(body)
            outer.addWidget(scroll, 1)

            self._build_library_step()
            self._build_scan_step()
            self._build_build_step()
            self._build_use_step()
            self.steps.addStretch(1)

            self.progress = widgets.QProgressBar()
            self.progress.setRange(0, 100)
            self.progress.setVisible(False)
            outer.addWidget(self.progress)

            bottom = widgets.QHBoxLayout()
            self.progress_label = widgets.QLabel()
            self.progress_label.setStyleSheet(HINT_STYLE)
            bottom.addWidget(self.progress_label, 1)
            self.cancel_button = widgets.QPushButton()
            self.cancel_button.setVisible(False)
            self.cancel_button.clicked.connect(self._request_cancel)
            bottom.addWidget(self.cancel_button)
            self.close_button = widgets.QPushButton()
            self.close_button.clicked.connect(self.close)
            bottom.addWidget(self.close_button)
            outer.addLayout(bottom)

        def _step(self, attribute: str):
            """A numbered box with a state line of its own."""
            box = widgets.QGroupBox()
            layout = widgets.QVBoxLayout(box)
            status = widgets.QLabel()
            status.setWordWrap(True)
            status.setTextInteractionFlags(
                enum_value(core.Qt, "TextSelectableByMouse", "TextInteractionFlag")
            )
            layout.addWidget(status)
            setattr(self, f"{attribute}_box", box)
            setattr(self, f"{attribute}_status", status)
            self.steps.addWidget(box)
            return layout

        def _hint(self, layout, attribute: str) -> None:
            label = widgets.QLabel()
            label.setWordWrap(True)
            label.setStyleSheet(HINT_STYLE)
            layout.addWidget(label)
            setattr(self, attribute, label)

        def _build_library_step(self) -> None:
            layout = self._step("library")
            self._hint(layout, "library_hint")

            row = widgets.QHBoxLayout()
            self.library_edit = widgets.QLineEdit()
            self.library_edit.editingFinished.connect(self._library_typed)
            row.addWidget(self.library_edit, 1)
            self.library_button = widgets.QPushButton()
            self.library_button.clicked.connect(self._pick_library)
            row.addWidget(self.library_button)
            self.library_open_button = widgets.QPushButton()
            self.library_open_button.clicked.connect(self._open_library)
            row.addWidget(self.library_open_button)
            layout.addLayout(row)

        def _folder_list(self, layout, attribute: str, add_slot, remove_slot):
            caption = widgets.QLabel()
            caption.setStyleSheet(HINT_STYLE)
            caption.setWordWrap(True)
            layout.addWidget(caption)
            setattr(self, f"{attribute}_caption", caption)

            row = widgets.QHBoxLayout()
            listing = widgets.QListWidget()
            listing.setFixedHeight(74)
            row.addWidget(listing, 1)
            buttons = widgets.QVBoxLayout()
            add = widgets.QPushButton("+")
            add.setFixedWidth(30)
            add.clicked.connect(add_slot)
            remove = widgets.QPushButton("\u2212")
            remove.setFixedWidth(30)
            remove.clicked.connect(remove_slot)
            buttons.addWidget(add)
            buttons.addWidget(remove)
            buttons.addStretch(1)
            row.addLayout(buttons)
            layout.addLayout(row)

            setattr(self, f"{attribute}_list", listing)
            setattr(self, f"{attribute}_add", add)
            setattr(self, f"{attribute}_remove", remove)

        def _build_scan_step(self) -> None:
            layout = self._step("scan")
            self._hint(layout, "scan_hint")

            self._folder_list(
                layout, "material", self._add_material_folder, self._remove_material_folder
            )
            self._folder_list(layout, "asset", self._add_asset_folder, self._remove_asset_folder)

            self.whole_project = widgets.QCheckBox()
            self.whole_project.toggled.connect(self._whole_project_toggled)
            layout.addWidget(self.whole_project)

            self.scan_button = widgets.QPushButton()
            self.scan_button.setMinimumHeight(30)
            self.scan_button.clicked.connect(self._scan)
            layout.addWidget(self.scan_button)

            # An index can be complete and still be built on guesses: this is
            # the line that says so, and it is worth more than the count above.
            self.scan_warning = widgets.QLabel()
            self.scan_warning.setWordWrap(True)
            self.scan_warning.setStyleSheet(ALERT_STYLE)
            self.scan_warning.setVisible(False)
            layout.addWidget(self.scan_warning)

        def _build_build_step(self) -> None:
            layout = self._step("build")
            self._hint(layout, "build_hint")

            # The file name is the name of the group in the Material/Map
            # Browser, and "Textures_Surface_Bricks" among the stock groups is
            # not something anybody goes looking for.
            row = widgets.QHBoxLayout()
            self.name_caption = widgets.QLabel()
            row.addWidget(self.name_caption)
            self.name_edit = widgets.QLineEdit()
            self.name_edit.editingFinished.connect(self._name_typed)
            row.addWidget(self.name_edit, 1)
            layout.addLayout(row)
            self._hint(layout, "name_hint")

            self.build_button = widgets.QPushButton()
            self.build_button.setMinimumHeight(34)
            self.build_button.clicked.connect(self._build_libraries)
            layout.addWidget(self.build_button)

        def _build_use_step(self) -> None:
            layout = self._step("use")
            self._hint(layout, "use_hint")

            # A .mat file does not appear in the browser on its own, and a
            # browser showing only the standard materials looks exactly like a
            # build that produced nothing. So the files are listed here, by
            # name, with the button that opens one.
            self.library_list = widgets.QListWidget()
            self.library_list.setFixedHeight(96)
            self.library_list.itemDoubleClicked.connect(self._load_selected_library)
            layout.addWidget(self.library_list)

            row = widgets.QHBoxLayout()
            self.load_button = widgets.QPushButton()
            self.load_button.setMinimumHeight(30)
            self.load_button.clicked.connect(self._load_selected_library)
            row.addWidget(self.load_button, 1)
            self.load_all_button = widgets.QPushButton()
            self.load_all_button.setMinimumHeight(30)
            self.load_all_button.clicked.connect(self._load_all_libraries)
            row.addWidget(self.load_all_button)
            layout.addLayout(row)

            self._hint(layout, "use_steps")

            self.browser_button = widgets.QPushButton()
            self.browser_button.setMinimumHeight(34)
            self.browser_button.clicked.connect(self._open_browser)
            layout.addWidget(self.browser_button)

            row = widgets.QHBoxLayout()
            self.open_folder_button = widgets.QPushButton()
            self.open_folder_button.clicked.connect(self._open_libraries)
            row.addWidget(self.open_folder_button)
            self.open_editor_button = widgets.QPushButton()
            self.open_editor_button.clicked.connect(self._open_material_editor)
            row.addWidget(self.open_editor_button)
            layout.addLayout(row)

        # -- text ------------------------------------------------------------

        def retranslate(self) -> None:
            self.setWindowTitle(f"{TITLE} {__version__}")
            self.language_button.setText(RU if language() == EN else EN)
            self.close_button.setText(lt("Close", "Закрыть"))
            self.cancel_button.setText(lt("Stop", "Остановить"))

            self.library_box.setTitle(lt("1 \u00b7 Library folder", "1 \u00b7 Папка библиотеки"))
            self.library_hint.setText(
                lt(
                    "Where the library is written and where the scan index lives. "
                    "Pick a folder outside the Unity project, for example D:\\UnityMaterialLib. "
                    "If a library is already there, everything below is filled in from it.",
                    "Куда пишется библиотека и где лежит индекс сканирования. "
                    "Выберите папку вне проекта Unity, например D:\\UnityMaterialLib. "
                    "Если библиотека там уже есть, всё ниже подхватится из неё.",
                )
            )
            self.library_edit.setPlaceholderText(
                lt("D:\\UnityMaterialLib", "D:\\UnityMaterialLib")
            )
            self.library_button.setText(lt("Choose\u2026", "Выбрать\u2026"))
            self.library_open_button.setText(lt("Open", "Открыть"))

            self.scan_box.setTitle(lt("2 \u00b7 Scan Unity", "2 \u00b7 Сканировать Unity"))
            self.scan_hint.setText(
                lt(
                    "Reads the .mat files and writes the JSON tree the build needs. "
                    "Unity is only read. This step needs no 3ds Max at all, so a "
                    "library scanned elsewhere is used as it is.",
                    "Читает файлы .mat и пишет JSON-дерево, из которого потом собираются "
                    "материалы. Unity только читается. 3ds Max для этого шага не нужен: "
                    "библиотека, отсканированная в другом месте, подходит как есть.",
                )
            )
            self.material_caption.setText(
                lt(
                    "Unity folders with .mat files, for example Assets\\Materials",
                    "Папки Unity с файлами .mat, например Assets\\Materials",
                )
            )
            self.asset_caption.setText(
                lt(
                    "Folders with textures and shaders. A shader outside them is not "
                    "read, and the material is then converted from the .mat file alone.",
                    "Папки с текстурами и шейдерами. Шейдер вне них не читается, и "
                    "материал тогда собирается по одному .mat, вслепую.",
                )
            )
            self.whole_project.setText(
                lt(
                    "Look for textures and shaders in the whole Unity project",
                    "Искать текстуры и шейдеры во всём проекте Unity",
                )
            )
            self.scan_button.setText(lt("Scan Unity", "Сканировать Unity"))

            self.build_box.setTitle(lt("3 \u00b7 Build .mat", "3 \u00b7 Собрать .mat"))
            self.build_hint.setText(
                lt(
                    "Builds a Physical Material for every material in the index and "
                    "writes one .mat library per Unity catalogue. Textures stay where "
                    "Unity keeps them; nothing is copied.",
                    "Строит Physical Material для каждого материала из индекса и пишет "
                    "по одной .mat-библиотеке на каталог Unity. Текстуры остаются на "
                    "месте, ничего не копируется.",
                )
            )
            self.name_caption.setText(lt("Library name", "Имя библиотеки"))
            self.name_edit.setPlaceholderText(lt("MadOut2", "MadOut2"))
            self.name_hint.setText(
                lt(
                    "The name goes in front of every file, and the file name is what "
                    "the Material/Map Browser calls the group: MadOut2 gives "
                    "MadOut2_Surface_Bricks. Leave it empty to keep the bare catalogue "
                    "names.",
                    "Имя ставится перед каждым файлом, а имя файла — это и есть "
                    "название группы в Material/Map Browser: MadOut2 даёт "
                    "MadOut2_Surface_Bricks. Пусто — останутся голые имена каталогов.",
                )
            )
            self.build_button.setText(lt("Build .mat", "Собрать .mat"))

            self.use_box.setTitle(lt("4 \u00b7 Use in Max", "4 \u00b7 Использовать в Max"))
            self.use_hint.setText(
                lt(
                    "A .mat library does not appear in the Material/Map Browser by "
                    "itself: it has to be opened. Choose one below and the button "
                    "opens it; the materials then drag onto objects like any others.",
                    "Библиотека .mat сама в Material/Map Browser не появляется — её "
                    "надо открыть. Выберите файл ниже, и кнопка его откроет; дальше "
                    "материалы перетаскиваются на объекты как обычные.",
                )
            )
            self.load_button.setText(
                lt("Open the chosen library", "Открыть выбранную библиотеку")
            )
            self.load_all_button.setText(
                lt(
                    f"Open all {len(self.session.state.library_files)}",
                    f"Открыть все ({len(self.session.state.library_files)})",
                )
            )
            self.use_steps.setText(
                lt(
                    "Preview spheres: right-click inside the browser list and pick the "
                    "icon size. Dragging a material onto an object works from there.\n"
                    "By hand, if the button cannot: the menu at the top left of the "
                    "browser → Open Material Library → the file from the folder below.",
                    "Шарики-превью: правый клик по списку в браузере → размер значков. "
                    "Оттуда же материал перетаскивается на объект.\n"
                    "Вручную, если кнопка не справится: меню в левом верхнем углу "
                    "браузера → Open Material Library → файл из папки ниже.",
                )
            )
            self.browser_button.setText(
                lt("Browse all materials…", "Браузер материалов…")
            )
            self.open_folder_button.setText(lt("Open the .mat folder", "Открыть папку с .mat"))
            self.open_editor_button.setText(
                lt("Open the Material Editor", "Открыть редактор материалов")
            )

        def _problem_text(self, code: str) -> str:
            return {
                session_module.NO_LIBRARY: lt(
                    "Choose the library folder in step 1",
                    "Выберите папку библиотеки в шаге 1",
                ),
                session_module.LIBRARY_INSIDE_UNITY: lt(
                    "This folder is inside a Unity project. Unity is only read, never "
                    "written: choose a folder outside it.",
                    "Эта папка внутри проекта Unity. Unity только читается, запись в него "
                    "не выполняется: выберите папку вне проекта.",
                ),
                session_module.NO_MATERIAL_FOLDERS: lt(
                    "Add at least one Unity folder with .mat files",
                    "Добавьте хотя бы одну папку Unity с файлами .mat",
                ),
                session_module.MATERIAL_FOLDERS_GONE: lt(
                    "None of the chosen folders exist any more",
                    "Ни одной из выбранных папок больше нет",
                ),
                session_module.NO_MAT_FILES: lt(
                    "No .mat files in the chosen folders",
                    "В выбранных папках нет файлов .mat",
                ),
                session_module.NO_INDEX: lt(
                    "Scan Unity first, or pick a library folder that was scanned already",
                    "Сначала выполните сканирование или выберите уже отсканированную папку",
                ),
                session_module.NOTHING_BUILT: lt(
                    "Nothing built yet", "Пока ничего не собрано"
                ),
            }.get(code, code)

        # -- state -----------------------------------------------------------

        def refresh(self) -> None:
            state = self.session.refresh()
            settings = self.session.settings

            if self.library_edit.text() != settings.library_root:
                self.library_edit.setText(settings.library_root)
            self.whole_project.setChecked(settings.search_whole_project)
            self._refresh_folder_lists()

            library_ok = state.problem == session_module.OK and bool(settings.library_root.strip())
            self._set_status(
                self.library_status,
                library_ok,
                lt("Library folder is set", "Папка библиотеки выбрана")
                if library_ok
                else self._problem_text(state.problem),
                alert=not library_ok and state.problem == session_module.LIBRARY_INSIDE_UNITY,
            )

            scan_problem = self.session.scan_problem()
            if state.has_index:
                scanned = state.scanned_at.split("T")[0] if state.scanned_at else ""
                message = lt(
                    f"Index found: {state.material_count} materials, "
                    f"{state.shader_count} shaders, scanned {scanned}",
                    f"Индекс найден: материалов {state.material_count}, "
                    f"шейдеров {state.shader_count}, сканирование {scanned}",
                )
                if state.unresolved_textures:
                    message += lt(
                        f". {state.unresolved_textures} with missing textures",
                        f". С ненайденными текстурами: {state.unresolved_textures}",
                    )
            else:
                message = self._problem_text(scan_problem) if scan_problem else lt(
                    "Ready to scan", "Готово к сканированию"
                )
            self._set_status(self.scan_status, state.has_index, message)
            self.scan_button.setText(
                lt("Scan Unity again", "Сканировать Unity заново")
                if state.has_index
                else lt("Scan Unity", "Сканировать Unity")
            )
            self.scan_button.setEnabled(not self._busy and not scan_problem)

            # The count above can say 384 of 384 while every one of them was
            # converted from the .mat file alone. That is the difference
            # between a library and a library that is wrong, so it is said in
            # its own line, in the colour of a problem.
            blind = state.materials_without_shader
            self.scan_warning.setVisible(bool(blind))
            if blind:
                self.scan_warning.setText(
                    lt(
                        f"{blind} materials were converted without their shader: the "
                        "shader assets are not in the folders above. Add the folder "
                        "holding the .shader files, or tick the box, and scan again.",
                        f"Материалов собрано без шейдера: {blind}. Файлы шейдеров не "
                        "попали в папки выше. Добавьте папку с .shader или включите "
                        "галочку и отсканируйте заново.",
                    )
                )

            build_problem = self.session.build_problem()
            if state.built or state.libraries:
                built = lt(
                    f"Built {state.built} materials into {state.libraries} libraries",
                    f"Собрано материалов: {state.built}, библиотек: {state.libraries}",
                )
                if state.failed:
                    built += lt(f", failed {state.failed}", f", ошибок: {state.failed}")
                if state.build_is_stale:
                    built = lt(
                        f"Built from an older scan: {state.built} materials. "
                        "Unity was scanned again since; build again.",
                        f"Собрано по старому скану: материалов {state.built}. "
                        "После этого Unity сканировали заново — соберите ещё раз.",
                    )
            else:
                built = self._problem_text(build_problem) if build_problem else lt(
                    "Ready to build", "Готово к сборке"
                )
            self._set_status(
                self.build_status,
                bool(state.built) and not state.build_is_stale,
                built,
                alert=state.build_is_stale,
            )
            self.build_button.setEnabled(not self._busy and not build_problem)
            if self.name_edit.text() != settings.library_name:
                self.name_edit.setText(settings.library_name)

            use_problem = self.session.use_problem()
            libraries = self.session.library_dir()
            self._set_status(
                self.use_status,
                not use_problem,
                lt(
                    f"{len(state.library_files)} .mat files in {libraries}",
                    f"Файлов .mat: {len(state.library_files)} в {libraries}",
                )
                if not use_problem
                else self._problem_text(use_problem),
            )
            self._refresh_library_list(state)
            self.open_folder_button.setEnabled(not use_problem)
            # The browser reads the index, not the built files: it is worth
            # opening even before anything has been built.
            self.browser_button.setEnabled(state.has_index)
            self.load_button.setEnabled(not use_problem)
            self.load_all_button.setEnabled(not use_problem)
            self.load_all_button.setText(
                lt(
                    f"Open all {len(state.library_files)}",
                    f"Открыть все ({len(state.library_files)})",
                )
            )
            self.library_open_button.setEnabled(bool(settings.library_root.strip()))

            self.summary.setText(self._summary(state))

        def _summary(self, state) -> str:
            if state.problem or not state.root:
                return lt(
                    "Four steps: choose a folder, scan Unity, build .mat, open it in Max.",
                    "Четыре шага: выбрать папку, отсканировать Unity, собрать .mat, "
                    "открыть в Max.",
                )
            return lt(
                f"Library: {state.root} \u2014 {state.material_count} materials, "
                f"{len(state.library_files)} .mat files",
                f"Библиотека: {state.root} \u2014 материалов {state.material_count}, "
                f"файлов .mat {len(state.library_files)}",
            )

        def _set_status(self, label, done: bool, message: str, alert: bool = False) -> None:
            label.setText(f"{DONE if done else TODO}  {message}")
            label.setStyleSheet(ALERT_STYLE if alert else (GOOD_STYLE if done else ""))

        def _refresh_library_list(self, state) -> None:
            """The built files, under the names the browser will show."""
            current = self.library_list.currentRow()
            self.library_list.clear()
            for path in state.library_files:
                self.library_list.addItem(path.stem)
            if 0 <= current < self.library_list.count():
                self.library_list.setCurrentRow(current)
            elif self.library_list.count():
                self.library_list.setCurrentRow(0)

        def _refresh_folder_lists(self) -> None:
            for listing, paths in (
                (self.material_list, self.session.settings.material_roots),
                (self.asset_list, self.session.settings.asset_roots),
            ):
                current = listing.currentRow()
                listing.clear()
                for path in paths:
                    listing.addItem(self._folder_label(path, listing is self.material_list))
                if 0 <= current < listing.count():
                    listing.setCurrentRow(current)

        def _folder_label(self, path: str, count_materials: bool) -> str:
            facts = session_module.describe_folder(path)
            if not facts.exists:
                return lt(f"{path} \u2014 folder is gone", f"{path} \u2014 папки больше нет")
            if not count_materials:
                return path
            note = f"{facts.counted} .mat"
            if facts.is_whole_assets:
                note += lt(", the whole Assets tree", ", весь Assets целиком")
            return f"{path} \u2014 {note}"

        # -- editing ---------------------------------------------------------

        def _library_typed(self) -> None:
            self.session.settings.library_root = self.library_edit.text().strip()
            self.session.save()
            self.refresh()

        def _name_typed(self) -> None:
            self.session.settings.library_name = self.name_edit.text().strip()
            self.session.save()
            self.refresh()

        def _pick_library(self) -> None:
            chosen = widgets.QFileDialog.getExistingDirectory(
                self,
                lt(
                    "Step 1: folder for the material library, outside the Unity project",
                    "Шаг 1: папка для библиотеки материалов, вне проекта Unity",
                ),
                self.session.settings.library_root or str(Path.home()),
            )
            if not chosen:
                return
            self.session.settings.library_root = str(Path(chosen))
            self.session.save()
            self.refresh()

        def _add_material_folder(self) -> None:
            chosen = widgets.QFileDialog.getExistingDirectory(
                self,
                lt(
                    "Step 2: Unity folder holding .mat files, for example Assets\\Materials",
                    "Шаг 2: папка Unity с файлами .mat, например Assets\\Materials",
                ),
                self._start_folder(self.session.settings.material_roots),
            )
            if chosen:
                self._append(self.session.settings.material_roots, chosen)

        def _add_asset_folder(self) -> None:
            chosen = widgets.QFileDialog.getExistingDirectory(
                self,
                lt(
                    "Folder with textures and shaders, for example Assets\\Textures",
                    "Папка с текстурами и шейдерами, например Assets\\Textures",
                ),
                self._start_folder(
                    self.session.settings.asset_roots or self.session.settings.material_roots
                ),
            )
            if chosen:
                self._append(self.session.settings.asset_roots, chosen)

        def _start_folder(self, paths) -> str:
            for path in reversed(list(paths)):
                if Path(path).is_dir():
                    return str(path)
            return str(Path.home())

        def _append(self, paths: list, chosen: str) -> None:
            path = str(Path(chosen))
            if path not in paths:
                paths.append(path)
            self.session.save()
            self.refresh()

        def _remove_material_folder(self) -> None:
            self._remove(self.material_list, self.session.settings.material_roots)

        def _remove_asset_folder(self) -> None:
            self._remove(self.asset_list, self.session.settings.asset_roots)

        def _remove(self, listing, paths: list) -> None:
            row = listing.currentRow()
            if 0 <= row < len(paths):
                paths.pop(row)
                self.session.save()
                self.refresh()

        def _whole_project_toggled(self, checked: bool) -> None:
            self.session.settings.search_whole_project = bool(checked)
            self.session.save()

        def _toggle_language(self) -> None:
            chosen = RU if language() == EN else EN
            self.session.settings.language = set_language(chosen)
            self.session.save()
            self.retranslate()
            self.refresh()

        # -- the long steps --------------------------------------------------

        def _scan(self) -> None:
            self._run(
                lt("Scanning Unity\u2026", "Сканирование Unity\u2026"),
                lambda: self.session.run_extract(self._progress),
                self._scan_finished,
            )

        def _scan_finished(self, result) -> None:
            self._report(
                lt("Scan finished", "Сканирование завершено"),
                lt(
                    f"{result.material_count} materials and {result.shader_count} shaders "
                    f"written to\n{self.session.settings.library_root}\n\n"
                    f"Materials with textures that were not found: "
                    f"{result.unresolved_textures}",
                    f"Материалов: {result.material_count}, шейдеров: {result.shader_count}.\n"
                    f"Записано в {self.session.settings.library_root}\n\n"
                    f"Материалов с ненайденными текстурами: {result.unresolved_textures}",
                ),
            )

        def _build_libraries(self) -> None:
            self._run(
                lt("Building materials\u2026", "Сборка материалов\u2026"),
                lambda: self.session.run_build(self._progress),
                self._build_finished,
            )

        def _build_finished(self, result) -> None:
            libraries = self.session.library_dir()
            if getattr(result, "cancelled", False):
                self._report(
                    lt("Stopped", "Остановлено"),
                    lt(
                        f"Stopped after {result.libraries} finished libraries in\n{libraries}\n\n"
                        "The catalogue in progress was not written.",
                        f"Остановлено после {result.libraries} готовых библиотек в\n{libraries}\n\n"
                        "Каталог, который собирался в этот момент, не записан.",
                    ),
                )
                return
            self._report(
                lt("Build finished", "Сборка завершена"),
                lt(
                    f"{result.built} materials in {result.libraries} libraries:\n{libraries}\n\n"
                    f"Failed: {result.failed}\n\n"
                    "Open a .mat from the Material/Map Browser (step 4).",
                    f"Материалов: {result.built}, библиотек: {result.libraries}:\n{libraries}\n\n"
                    f"Ошибок: {result.failed}\n\n"
                    "Откройте .mat в Material/Map Browser (шаг 4).",
                ),
            )

        def _run(self, label: str, work, finished) -> None:
            """One place where a long step starts, stops and reports.

            The window is put back in order before anything is said about the
            outcome: a message box on top of a still-running progress bar reads
            as a tool that died halfway.
            """
            if self._busy:
                return

            result = None
            stopped = False
            failure = None

            self._set_busy(True, label)
            try:
                result = work()
            except session_module.Cancelled:
                stopped = True
            except Exception as error:  # noqa: BLE001  (the person needs the reason)
                traceback.print_exc()
                failure = error
            finally:
                self._set_busy(False)
                self.refresh()

            if stopped:
                self._report(
                    lt("Stopped", "Остановлено"),
                    lt("Nothing was written.", "Ничего не записано."),
                )
            elif failure is not None:
                self._report(
                    lt("It did not finish", "Не удалось выполнить"),
                    f"{type(failure).__name__}: {failure}\n\n"
                    + lt(
                        "The full traceback is in the MAXScript Listener.",
                        "Полная трассировка — в MAXScript Listener.",
                    ),
                    alert=True,
                )
            else:
                finished(result)

        def _set_busy(self, busy: bool, label: str = "") -> None:
            self._busy = busy
            self._cancel = False
            self._last_paint = 0.0
            self.progress.setVisible(busy)
            self.progress.setValue(0)
            self.progress_label.setText(label)
            self.cancel_button.setVisible(busy)
            self.cancel_button.setEnabled(busy)
            for button in (
                self.scan_button,
                self.build_button,
                self.library_button,
                self.material_add,
                self.material_remove,
                self.asset_add,
                self.asset_remove,
            ):
                button.setEnabled(not busy)
            widgets.QApplication.processEvents()

        def _request_cancel(self) -> None:
            self._cancel = True
            self.cancel_button.setEnabled(False)
            self.progress_label.setText(lt("Stopping\u2026", "Останавливаюсь\u2026"))

        def _progress(self, fraction: float, message: str) -> bool:
            """Repaint at most twenty times a second, and let Stop through.

            Both long steps run in this thread -- phase B has to, because pymxs
            belongs to it -- so the window is only alive between these calls.
            """
            now = time.monotonic()
            if now - self._last_paint >= 0.05:
                self._last_paint = now
                self.progress.setValue(max(0, min(100, int(fraction * 100))))
                if message:
                    self.progress_label.setText(message[:90])
                widgets.QApplication.processEvents()
            return not self._cancel

        # -- opening things --------------------------------------------------

        def _open_library(self) -> None:
            self._open(Path(self.session.settings.library_root))

        def _open_libraries(self) -> None:
            libraries = self.session.library_dir()
            if libraries is not None:
                self._open(libraries)

        def _open(self, folder: Path) -> None:
            try:
                folder.mkdir(parents=True, exist_ok=True)
                os.startfile(str(folder))  # noqa: S606  (Windows shell, a folder)
            except (OSError, AttributeError, ValueError) as error:
                self._report(lt("Cannot open", "Не удалось открыть"), str(error), alert=True)

        def _load_selected_library(self, *_arguments) -> None:
            """Open the chosen .mat in the Material/Map Browser.

            The message afterwards names the group to look for: a browser that
            gained one row among Materials, Maps and Scene Materials is easy to
            miss, and "nothing happened" is what it looks like.
            """
            files = self.session.state.library_files
            row = self.library_list.currentRow()
            if not (0 <= row < len(files)):
                self._report(
                    lt("Nothing chosen", "Ничего не выбрано"),
                    lt(
                        "Choose a library in the list first.",
                        "Сначала выберите библиотеку в списке.",
                    ),
                )
                return

            path = files[row]
            try:
                opened = open_in_browser(path)
            except Exception as error:  # noqa: BLE001  (outside Max, or refused)
                self._report(
                    lt("Cannot open", "Не удалось открыть"),
                    lt(
                        f"Open it by hand: Material Editor (M) → the menu at the top "
                        f"left of the Material/Map Browser → Open Material Library →\n"
                        f"{path}\n\n{error}",
                        f"Откройте вручную: редактор материалов (M) → меню в левом "
                        f"верхнем углу Material/Map Browser → Open Material Library →\n"
                        f"{path}\n\n{error}",
                    ),
                    alert=True,
                )
                return

            if opened:
                self._report(
                    lt("Library opened", "Библиотека открыта"),
                    lt(
                        f"It is in the Slate Material Editor: the group "
                        f"\"{path.stem}\" in the Material/Map Browser, below "
                        f"Materials, Maps, Scene Materials and Sample Slots.",
                        f"Она в Slate Material Editor: группа «{path.stem}» в "
                        f"Material/Map Browser, ниже Materials, Maps, Scene Materials "
                        f"и Sample Slots.",
                    ),
                )
                return

            # Before 3ds Max 2021 there is no sme.OpenMtlLib, and the current
            # material library does not show up in the browser on its own.
            self._report(
                lt("Open it by hand", "Откройте вручную"),
                lt(
                    f"This release cannot open a library by script. In the Material/Map "
                    f"Browser: the menu at the top left → Open Material Library →\n{path}",
                    f"Эта версия Max не умеет открывать библиотеку скриптом. В "
                    f"Material/Map Browser: меню в левом верхнем углу → Open Material "
                    f"Library →\n{path}",
                ),
                alert=True,
            )

        def _load_all_libraries(self) -> None:
            """Every catalogue at once: one browser, the whole library."""
            files = list(self.session.state.library_files)
            if not files:
                self._report(
                    lt("Nothing built", "Нечего открывать"),
                    lt("Build the .mat files first.", "Сначала соберите файлы .mat."),
                )
                return

            opened, refused = 0, []
            self._set_busy(True, lt("Opening libraries…", "Открываю библиотеки…"))
            try:
                for position, path in enumerate(files, start=1):
                    self._progress(position / len(files), path.stem)
                    if open_in_browser(path):
                        opened += 1
                    else:
                        refused.append(path.stem)
            except Exception as error:  # noqa: BLE001  (outside Max, or refused)
                self._set_busy(False)
                self._report(
                    lt("Cannot open", "Не удалось открыть"),
                    f"{type(error).__name__}: {error}",
                    alert=True,
                )
                return
            finally:
                self._set_busy(False)

            self._report(
                lt("Libraries opened", "Библиотеки открыты"),
                lt(
                    f"{opened} of {len(files)} are in the Material/Map Browser, one "
                    f"group each.\n\nRight-click the list there to choose the icon "
                    f"size; that is where the preview spheres come from.",
                    f"В Material/Map Browser открыто {opened} из {len(files)} — по "
                    f"группе на каждую.\n\nПравый клик по списку там задаёт размер "
                    f"значков: это и есть шарики-превью.",
                )
                + (
                    lt(f"\n\nRefused: {', '.join(refused[:5])}",
                       f"\n\nНе открылись: {', '.join(refused[:5])}")
                    if refused
                    else ""
                ),
                alert=bool(refused),
            )

        def _open_browser(self) -> None:
            """The whole library in one window, with a way into the editor."""
            from . import browser  # noqa: PLC0415  (Qt is already up by now)

            try:
                browser.show(self.session)
            except Exception as error:  # noqa: BLE001
                traceback.print_exc()
                self._report(
                    lt("Cannot open the browser", "Не удалось открыть браузер"),
                    f"{type(error).__name__}: {error}",
                    alert=True,
                )

        def _open_material_editor(self) -> None:
            try:
                from pymxs import runtime  # noqa: PLC0415  (inside Max only)

                runtime.MatEditor.Open()
            except Exception as error:  # noqa: BLE001  (outside Max, or a stripped build)
                self._report(
                    lt("Cannot open", "Не удалось открыть"),
                    lt(
                        f"Open the Material Editor from the toolbar (M).\n\n{error}",
                        f"Откройте редактор материалов с панели (клавиша M).\n\n{error}",
                    ),
                    alert=True,
                )

        def _report(self, title: str, message: str, alert: bool = False) -> None:
            box = widgets.QMessageBox(self)
            box.setWindowTitle(f"{TITLE} \u2014 {title}")
            box.setText(message)
            box.setIcon(
                enum_value(widgets.QMessageBox, "Warning" if alert else "Information", "Icon")
            )
            # PySide2 spells it exec_, PySide6 exec.
            (box.exec_ if hasattr(box, "exec_") else box.exec)()

        # -- lifetime --------------------------------------------------------

        def closeEvent(self, event):  # noqa: N802  (Qt spelling)
            self.session.save()
            super().closeEvent(event)

    _dialog_class = UnityMaterialsDialog
    return _dialog_class
