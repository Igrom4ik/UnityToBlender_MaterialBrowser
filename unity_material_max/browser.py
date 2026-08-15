"""A browser of the built library: find a material, then edit it in Slate.

The Material/Map Browser that comes with 3ds Max shows one library at a time
and its previews are the size it wants them to be. This window shows the whole
library at once, searches across all catalogues, and hands a chosen material to
the Slate Material Editor as a node -- which is where a material is edited, so
that is where the trail ends.

Previews are the Unity base colour map, cached as PNG beside the library. A
rendered sample sphere would be truer and costs a render each; the albedo is
what makes a material recognisable in a grid of four hundred.
"""

from __future__ import annotations

from pathlib import Path

from . import catalog
from .dialog import ALERT_STYLE, HINT_STYLE, enum_value, max_main_window, qt_gui, qt_modules
from .localization import text as lt


TILE_MIN = 48
TILE_MAX = 256
TILE_DEFAULT = 96

_window = None
_browser_class = None


def show(session):
    """Open the browser for a session's library, or raise the open one."""
    global _window

    widgets, _core = qt_modules()
    try:
        if _window is not None:
            _window.reload(session)
    except RuntimeError:
        _window = None
    if _window is None:
        _window = browser_class()(session, max_main_window(widgets))
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


def load_library(path: Path):
    """The materials of one `.mat`, by name, read once and kept.

    `loadTempMaterialLibrary` reads the file without touching the current
    library, so browsing never disturbs what the artist has open.
    """
    from pymxs import runtime  # noqa: PLC0415  (inside Max only)

    library = runtime.loadTempMaterialLibrary(str(path))
    if library is None:
        return {}

    count = int(getattr(library, "count", 0) or 0)
    # MAXScript counts from one and pymxs from zero, and which one reaches
    # through here is not worth betting a silent empty library on.
    for first in (0, 1):
        materials = {}
        try:
            for index in range(first, count + first):
                material = library[index]
                if material is None:
                    raise IndexError(index)
                materials[str(material.name)] = material
        except Exception:  # noqa: BLE001  (the other base, then)
            continue
        if materials:
            return materials
    return {}


def edit_in_slate(material) -> bool:
    """Put a material into the active Slate view, where it can be edited."""
    from pymxs import runtime  # noqa: PLC0415  (inside Max only)

    editor = getattr(runtime, "sme", None)
    if editor is None:
        return False
    if not editor.IsOpen():
        editor.Open()
    view = editor.GetView(editor.activeView)
    if view is None:
        return False
    view.CreateNode(material, runtime.Point2(0, 0))
    return True


def assign_to_selection(material) -> int:
    """Give the material to every selected object; returns how many got it.

    Wrapped in an undo record on purpose: a change made through pymxs is
    outside the undo system unless it is asked for, and an assignment that
    Ctrl+Z cannot take back is a trap in somebody's scene.
    """
    import pymxs  # noqa: PLC0415  (inside Max only)

    runtime = pymxs.runtime
    nodes = list(runtime.selection)
    if not nodes:
        return 0

    try:
        record = pymxs.undo(True, "Unity material")
    except TypeError:  # older pymxs takes the flag alone
        record = pymxs.undo(True)
    with record:
        for node in nodes:
            node.material = material

    runtime.redrawViews()
    return len(nodes)


def make_thumbnail(source: str, destination: Path, size: int = catalog.THUMBNAIL_SIZE) -> bool:
    """Cache a small PNG of a texture, using Max to read formats Qt cannot.

    85% of this project's textures are `.tif`, which Qt often has no plugin
    for, while Max reads it natively -- and this code only ever runs inside
    Max, so the strong reader is the one that is always there.
    """
    if not source or not Path(source).is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)

    original = small = None
    try:
        from pymxs import runtime  # noqa: PLC0415  (inside Max only)

        original = runtime.openBitMap(source)
        small = runtime.bitmap(size, size, filename=str(destination))
        runtime.copy(original, small)
        runtime.save(small)
    except Exception:  # noqa: BLE001  (an unreadable texture is not a failure)
        return False
    finally:
        try:
            from pymxs import runtime  # noqa: PLC0415

            for handle in (original, small):
                if handle is not None:
                    runtime.close(handle)
        except Exception:  # noqa: BLE001  (nothing left to close)
            pass
    return destination.is_file()


def browser_class():
    global _browser_class
    if _browser_class is not None:
        return _browser_class

    widgets, core = qt_modules()
    gui = qt_gui()

    class LibraryBrowser(widgets.QDialog):
        def __init__(self, session, parent=None):
            super().__init__(parent)
            self.session = session
            self.entries = []
            self.shown = []
            self.libraries = {}
            self._busy = False
            self._cancel = False

            self._build()
            self.reload(session)

        # -- construction ----------------------------------------------------

        def _build(self) -> None:
            self.setWindowTitle(lt("Unity Materials — browser", "Unity Materials — браузер"))
            self.setMinimumSize(720, 520)
            outer = widgets.QVBoxLayout(self)

            top = widgets.QHBoxLayout()
            self.search_edit = widgets.QLineEdit()
            self.search_edit.setPlaceholderText(lt("Search…", "Поиск…"))
            self.search_edit.textChanged.connect(self._refilter)
            top.addWidget(self.search_edit, 2)

            self.catalog_box = widgets.QComboBox()
            self.catalog_box.currentIndexChanged.connect(self._refilter)
            top.addWidget(self.catalog_box, 1)
            outer.addLayout(top)

            self.grid = widgets.QListWidget()
            self.grid.setViewMode(enum_value(widgets.QListView, "IconMode", "ViewMode"))
            self.grid.setResizeMode(enum_value(widgets.QListView, "Adjust", "ResizeMode"))
            self.grid.setMovement(enum_value(widgets.QListView, "Static", "Movement"))
            self.grid.setSpacing(6)
            self.grid.setUniformItemSizes(True)
            self.grid.setWordWrap(True)
            self.grid.itemDoubleClicked.connect(self._edit_selected)
            outer.addWidget(self.grid, 1)

            size_row = widgets.QHBoxLayout()
            size_row.addWidget(widgets.QLabel(lt("Tile size", "Размер плиток")))
            self.size_slider = widgets.QSlider(
                enum_value(core.Qt, "Horizontal", "Orientation")
            )
            self.size_slider.setRange(TILE_MIN, TILE_MAX)
            self.size_slider.setValue(TILE_DEFAULT)
            self.size_slider.valueChanged.connect(self._resize_tiles)
            size_row.addWidget(self.size_slider, 1)
            self.preview_button = widgets.QPushButton(
                lt("Rebuild previews", "Пересобрать превью")
            )
            self.preview_button.clicked.connect(self._build_previews)
            size_row.addWidget(self.preview_button)
            self.ball_button = widgets.QPushButton(
                lt("Render sample spheres", "Отрисовать шары")
            )
            self.ball_button.setToolTip(
                lt(
                    "Renders every material on a sphere. Slower, and the truest "
                    "preview there is.",
                    "Рендерит каждый материал на шаре. Дольше, но это самое честное "
                    "превью.",
                )
            )
            self.ball_button.clicked.connect(self._render_balls)
            size_row.addWidget(self.ball_button)
            outer.addLayout(size_row)

            self.status = widgets.QLabel()
            self.status.setStyleSheet(HINT_STYLE)
            self.status.setWordWrap(True)
            outer.addWidget(self.status)

            self.progress = widgets.QProgressBar()
            self.progress.setVisible(False)
            outer.addWidget(self.progress)

            buttons = widgets.QHBoxLayout()
            self.edit_button = widgets.QPushButton(
                lt("Edit in Slate", "Редактировать в Slate")
            )
            self.edit_button.setMinimumHeight(30)
            self.edit_button.clicked.connect(self._edit_selected)
            buttons.addWidget(self.edit_button, 1)
            self.assign_button = widgets.QPushButton(
                lt("Assign to selection", "Назначить на выделенное")
            )
            self.assign_button.setMinimumHeight(30)
            self.assign_button.clicked.connect(self._assign_selected)
            buttons.addWidget(self.assign_button, 1)
            self.close_button = widgets.QPushButton(lt("Close", "Закрыть"))
            self.close_button.clicked.connect(self.close)
            buttons.addWidget(self.close_button)
            outer.addLayout(buttons)

        # -- contents --------------------------------------------------------

        def reload(self, session=None) -> None:
            if session is not None:
                self.session = session
            root = self.session.settings.library_root

            # A pass that was cut short by a crash leaves its sphere and lights
            # in the scene; opening the window is a good moment to notice.
            try:
                from . import preview_render  # noqa: PLC0415  (needs Max)

                preview_render.purge()
            except Exception:  # noqa: BLE001  (outside Max there is nothing to clear)
                pass
            self.entries = catalog.load_entries(root, self.session.settings.library_name)
            self.libraries = {}

            self.catalog_box.blockSignals(True)
            self.catalog_box.clear()
            self.catalog_box.addItem(lt("All catalogues", "Все каталоги"), "")
            for name in catalog.catalogs(self.entries):
                self.catalog_box.addItem(name, name)
            self.catalog_box.blockSignals(False)
            self._refilter()

            # Fill the grid without being asked: the window opens, paints, and
            # then draws itself in. Waiting for a button press looks like a
            # gallery that failed to load.
            #
            # A flat tile does not count as done here. It used to, and a cache
            # full of tiles from an earlier pass then kept the spheres from
            # ever being rendered.
            if self._missing(root, self.shown):
                core.QTimer.singleShot(50, self._auto_previews)

        def _missing(self, root, entries) -> bool:
            """True while anything on screen still has no sphere of its own."""
            if self._libraries_built():
                return any(not catalog.ball_path(root, entry).is_file() for entry in entries)
            return any(catalog.preview_for(root, entry) is None for entry in entries)

        def _libraries_built(self) -> bool:
            libraries = Path(self.session.settings.library_root) / "maxlib"
            return libraries.is_dir() and any(libraries.glob("*.mat"))

        def _refilter(self, *_arguments) -> None:
            chosen = self.catalog_box.currentData() or ""
            self.shown = catalog.search(self.entries, self.search_edit.text(), chosen)

            self.grid.clear()
            root = self.session.settings.library_root
            cached = 0
            for entry in self.shown:
                item = widgets.QListWidgetItem(entry.name)
                item.setToolTip(f"{entry.catalog}\n{entry.shader}\n{entry.unity_path}")
                preview = catalog.preview_for(root, entry)
                if preview is not None:
                    item.setIcon(gui.QIcon(str(preview)))
                    cached += 1
                self.grid.addItem(item)
            self._resize_tiles(self.size_slider.value())

            missing = len(self.shown) - cached
            self.status.setText(
                lt(
                    f"{len(self.shown)} of {len(self.entries)} materials"
                    + (f", {missing} without a preview yet" if missing else ""),
                    f"Материалов: {len(self.shown)} из {len(self.entries)}"
                    + (f", без превью: {missing}" if missing else ""),
                )
            )

        def _resize_tiles(self, size: int) -> None:
            self.grid.setIconSize(core.QSize(size, size))
            self.grid.setGridSize(core.QSize(size + 24, size + 40))

        # -- actions ---------------------------------------------------------

        def _selected(self):
            row = self.grid.currentRow()
            return self.shown[row] if 0 <= row < len(self.shown) else None

        def _material(self, entry, quiet: bool = False):
            """The Max material behind an entry, from its .mat library.

            `quiet` is for the passes over hundreds of entries, where one
            message box per missing library would be its own kind of failure.
            """
            path = catalog.library_path(self.session.settings.library_root, entry)
            if not path.is_file():
                if quiet:
                    return None
                self._report(
                    lt("Not built", "Не собрано"),
                    lt(
                        f"This catalogue has no library yet:\n{path}\n\nBuild the .mat "
                        "files first.",
                        f"У этого каталога ещё нет библиотеки:\n{path}\n\nСначала "
                        "соберите файлы .mat.",
                    ),
                )
                return None

            key = str(path)
            if key not in self.libraries:
                try:
                    self.libraries[key] = load_library(path)
                except Exception as error:  # noqa: BLE001  (outside Max, or refused)
                    self.libraries[key] = {}
                    if quiet:
                        return None
                    self._report(
                        lt("Cannot read the library", "Не удалось прочитать библиотеку"),
                        f"{path}\n\n{type(error).__name__}: {error}",
                        alert=True,
                    )
                    return None

            material = self.libraries[key].get(entry.name)
            if material is None and not quiet:
                self._report(
                    lt("Not in the library", "Нет в библиотеке"),
                    lt(
                        f"\"{entry.name}\" is in the index but not in {path.name}. "
                        "Build the .mat files again.",
                        f"«{entry.name}» есть в индексе, но нет в {path.name}. "
                        "Соберите файлы .mat заново.",
                    ),
                    alert=True,
                )
            return material

        def _edit_selected(self, *_arguments) -> None:
            entry = self._selected()
            if entry is None:
                return
            material = self._material(entry)
            if material is None:
                return
            try:
                opened = edit_in_slate(material)
            except Exception as error:  # noqa: BLE001
                self._report(lt("Cannot open", "Не удалось открыть"), str(error), alert=True)
                return
            if not opened:
                self._report(
                    lt("Cannot open", "Не удалось открыть"),
                    lt(
                        "The Slate Material Editor did not take the material.",
                        "Slate Material Editor не принял материал.",
                    ),
                    alert=True,
                )
                return
            self.status.setText(
                lt(
                    f"\"{entry.name}\" is in the Slate view — edits there stay in the "
                    "scene, the .mat on disk is untouched.",
                    f"«{entry.name}» в виде Slate — правки остаются в сцене, файл .mat "
                    "на диске не меняется.",
                )
            )

        def _assign_selected(self) -> None:
            entry = self._selected()
            if entry is None:
                return
            material = self._material(entry)
            if material is None:
                return
            try:
                count = assign_to_selection(material)
            except Exception as error:  # noqa: BLE001
                self._report(lt("Cannot assign", "Не удалось назначить"), str(error), alert=True)
                return
            self.status.setText(
                lt(
                    f"\"{entry.name}\" assigned to {count} objects"
                    if count
                    else "Nothing is selected in the scene",
                    f"«{entry.name}» назначен объектам: {count}"
                    if count
                    else "В сцене ничего не выделено",
                )
            )

        def _auto_previews(self) -> None:
            """Spheres if the libraries are built, flat tiles if they are not.

            A sphere is what a material looks like; a texture tile is only what
            it is made of. So the render is the default and the tile is what
            happens when there is nothing to render yet.
            """
            if self._libraries_built():
                self._render_balls()
            else:
                self._build_previews()

        def _build_previews(self, missing_only: bool = False) -> None:
            """Cache a tile for everything on screen that has no preview yet.

            A gallery that opens empty and waits to be told to draw itself
            reads as broken, so this also runs on its own when the window is
            opened. While it runs, the same button stops it.
            """
            if self._busy:
                self._cancel = True
                return
            root = self.session.settings.library_root
            todo = [
                entry
                for entry in self.shown
                if not (missing_only and catalog.preview_for(root, entry) is not None)
                and not catalog.thumbnail_path(root, entry).is_file()
            ]
            if not todo:
                self.status.setText(lt("Every preview is cached", "Все превью уже готовы"))
                return

            self._busy = True
            self._cancel = False
            self.progress.setVisible(True)
            self.progress.setRange(0, len(todo))
            self.preview_button.setText(lt("Stop", "Остановить"))
            made = 0
            for position, entry in enumerate(todo, start=1):
                self.progress.setValue(position)
                widgets.QApplication.processEvents()
                if self._cancel:
                    break
                destination = catalog.thumbnail_path(root, entry)
                source = catalog.preview_source(root, entry)
                if make_thumbnail(source, destination):
                    made += 1
                elif self._colour_tile(catalog.preview_color(root, entry), destination):
                    # No texture behind it, or Max could not read one: the
                    # material's own colour still tells it apart.
                    made += 1
            self.progress.setVisible(False)
            self.preview_button.setText(lt("Rebuild previews", "Пересобрать превью"))
            self._busy = False

            self._refilter()
            self.status.setText(
                lt(
                    f"{made} previews cached in {catalog.THUMBNAIL_DIR}",
                    f"Готово превью: {made}, кэш в {catalog.THUMBNAIL_DIR}",
                )
            )

        def _colour_tile(self, colour, destination: Path) -> bool:
            """A flat swatch of the material's own colour."""
            try:
                red, green, blue = (
                    max(0, min(255, int(round(channel * 255)))) for channel in colour[:3]
                )
                pixmap = gui.QPixmap(catalog.THUMBNAIL_SIZE, catalog.THUMBNAIL_SIZE)
                pixmap.fill(gui.QColor(red, green, blue))
                destination.parent.mkdir(parents=True, exist_ok=True)
                return bool(pixmap.save(str(destination), "PNG"))
            except Exception:  # noqa: BLE001  (a tile is not worth a failure)
                return False

        def _render_balls(self) -> None:
            """The truest preview: every material rendered on a sphere."""
            if self._busy:
                self._cancel = True
                return
            root = self.session.settings.library_root
            entries = list(self.shown)
            if not entries:
                return

            jobs = []
            for entry in entries:
                if catalog.ball_path(root, entry).is_file():
                    continue  # rendered once is enough
                material = self._material(entry, quiet=True)
                if material is not None:
                    jobs.append((material, catalog.ball_path(root, entry)))
            if not jobs:
                if all(catalog.ball_path(root, entry).is_file() for entry in entries):
                    self.status.setText(
                        lt("Every sphere is rendered", "Все шары уже отрисованы")
                    )
                else:
                    # The libraries hold none of these materials, so there is
                    # nothing to put on a sphere: the texture tile it is.
                    self._build_previews()
                return

            self._busy = True
            self._cancel = False
            self.progress.setVisible(True)
            self.progress.setRange(0, 100)
            self.ball_button.setText(lt("Stop", "Остановить"))

            def report(fraction, name):
                self.progress.setValue(int(fraction * 100))
                self.status.setText(lt(f"Rendering {name}…", f"Рендерю {name}…"))
                widgets.QApplication.processEvents()
                return not self._cancel

            reason = ""
            renderer = ""
            try:
                from . import preview_render  # noqa: PLC0415  (needs Max)

                outcome = preview_render.render_previews(
                    jobs, catalog.THUMBNAIL_SIZE, report
                )
                made, reason, renderer = outcome.written, outcome.reason, outcome.renderer
            except Exception as error:  # noqa: BLE001  (outside Max, or a refusal)
                made = 0
                reason = f"{type(error).__name__}: {error}"
            finally:
                self.progress.setVisible(False)
                self.ball_button.setText(lt("Render sample spheres", "Отрисовать шары"))
                self._busy = False

            self._refilter()
            drawn_by = f" ({renderer})" if renderer else ""
            self.status.setText(
                lt(
                    f"{made} spheres rendered of {len(jobs)}{drawn_by}",
                    f"Отрисовано шаров: {made} из {len(jobs)}{drawn_by}",
                )
            )
            if not made and reason:
                # Nothing rendered and a reason to hand over: say it once,
                # rather than leaving a grid of flat tiles and no explanation.
                self._report(
                    lt("The spheres did not render", "Шары не отрисовались"),
                    lt(
                        f"{reason}\n\nThe flat tiles stay in place. Sample spheres need "
                        "a renderer that can draw a Physical Material.",
                        f"{reason}\n\nПлитки остаются на месте. Для шаров нужен "
                        "рендерер, умеющий рисовать Physical Material.",
                    ),
                    alert=True,
                )

        def closeEvent(self, event):  # noqa: N802  (Qt spelling)
            """Stop a running pass and leave no preview objects in the scene."""
            self._cancel = True
            try:
                from . import preview_render  # noqa: PLC0415  (needs Max)

                preview_render.purge()
            except Exception:  # noqa: BLE001  (outside Max there is nothing to clear)
                pass
            super().closeEvent(event)

        def _report(self, title: str, message: str, alert: bool = False) -> None:
            box = widgets.QMessageBox(self)
            box.setWindowTitle(title)
            box.setText(message)
            box.setIcon(
                enum_value(widgets.QMessageBox, "Warning" if alert else "Information", "Icon")
            )
            (box.exec_ if hasattr(box, "exec_") else box.exec)()
            if alert:
                self.status.setStyleSheet(ALERT_STYLE)

    _browser_class = LibraryBrowser
    return _browser_class
