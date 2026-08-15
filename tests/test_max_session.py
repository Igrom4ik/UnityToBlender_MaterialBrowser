"""The step window, tested without Qt and without 3ds Max.

`dialog.py` only draws; every answer it shows -- which step is done, why a
button is off, what a chosen folder actually holds -- comes from `session.py`,
which is what these tests pin down.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import fixtures  # noqa: F401  (adds the project root to sys.path)

from unity_material_max import library_build, localization, session
from unity_pipeline_core import extract


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.path = Path(self._temp.name) / "settings.json"

    def tearDown(self):
        self._temp.cleanup()

    def test_choices_survive_a_restart(self):
        session.Settings(
            library_root="D:\\Lib",
            library_name="MadOut2",
            material_roots=["D:\\Unity\\Assets\\Materials"],
            asset_roots=["D:\\Unity\\Assets\\Textures"],
            search_whole_project=True,
            language="RU",
        ).save(self.path)

        loaded = session.Settings.load(self.path)
        self.assertEqual(loaded.library_root, "D:\\Lib")
        self.assertEqual(loaded.library_name, "MadOut2")
        self.assertEqual(loaded.material_roots, ["D:\\Unity\\Assets\\Materials"])
        self.assertEqual(loaded.asset_roots, ["D:\\Unity\\Assets\\Textures"])
        self.assertTrue(loaded.search_whole_project)
        self.assertEqual(loaded.language, "RU")

    def test_a_missing_or_broken_file_is_an_empty_start(self):
        # The window opens on a machine that never ran it, and after someone
        # edited the file by hand.
        self.assertEqual(session.Settings.load(self.path).library_root, "")
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(session.Settings.load(self.path).material_roots, [])
        self.path.write_text(json.dumps([1, 2]), encoding="utf-8")
        self.assertEqual(session.Settings.load(self.path).material_roots, [])

    def test_the_same_folder_is_not_stored_twice(self):
        session.Settings(material_roots=["D:\\A", "D:\\A", " ", "D:\\B"]).save(self.path)
        self.assertEqual(session.Settings.load(self.path).material_roots, ["D:\\A", "D:\\B"])

    def test_a_profile_that_cannot_be_written_does_not_lose_the_session(self):
        # A settings file the window cannot write is a reason to say nothing,
        # not a reason to fall over on the way in.
        self.path.write_text("{}", encoding="utf-8")
        blocked = self.path / "nested.json"
        self.assertIsNone(session.Settings(library_root="D:\\Lib").save(blocked))


class FolderTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.paths = fixtures.build_project(self.root)

    def tearDown(self):
        self._temp.cleanup()

    def test_a_folder_says_how_many_materials_it_holds(self):
        # This is the answer the file picker never gave: whether the folder
        # that was just chosen is the right one.
        facts = session.describe_folder(self.paths["materials"])
        self.assertTrue(facts.exists)
        self.assertEqual(facts.mat_count, 3)
        self.assertFalse(facts.capped)
        self.assertEqual(facts.counted, "3")

    def test_counting_stops_at_the_limit(self):
        facts = session.describe_folder(self.paths["materials"], limit=2)
        self.assertTrue(facts.capped)
        self.assertEqual(facts.counted, "2+")

    def test_the_whole_assets_tree_is_named_as_such(self):
        # Picking the project root is legitimate and means every material in
        # it, which is worth saying before a scan that takes minutes.
        facts = session.describe_folder(self.paths["assets"])
        self.assertTrue(facts.is_whole_assets)
        self.assertEqual(facts.unity_assets, self.paths["assets"])

    def test_a_folder_that_is_gone_is_not_an_error(self):
        facts = session.describe_folder(self.root / "nowhere")
        self.assertFalse(facts.exists)
        self.assertEqual(facts.mat_count, 0)


class StepTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.paths = fixtures.build_project(self.root)
        self.library = self.root / "library"
        self.settings_path = self.root / "settings.json"

    def tearDown(self):
        self._temp.cleanup()

    def make(self, **overrides) -> session.Session:
        settings = session.Settings(
            library_root=str(overrides.pop("library_root", self.library)),
            material_roots=[str(path) for path in overrides.pop("material_roots", ())],
            asset_roots=[str(path) for path in overrides.pop("asset_roots", ())],
            **overrides,
        )
        return session.Session(settings, self.settings_path)

    def test_every_step_names_what_is_missing(self):
        empty = self.make(library_root="")
        self.assertEqual(empty.scan_problem(), session.NO_LIBRARY)
        self.assertEqual(empty.build_problem(), session.NO_LIBRARY)

        no_folders = self.make()
        self.assertEqual(no_folders.scan_problem(), session.NO_MATERIAL_FOLDERS)
        self.assertEqual(no_folders.build_problem(), session.NO_INDEX)
        self.assertEqual(no_folders.use_problem(), session.NOTHING_BUILT)

        gone = self.make(material_roots=(self.root / "nowhere",))
        self.assertEqual(gone.scan_problem(), session.MATERIAL_FOLDERS_GONE)

        empty_folder = self.root / "empty"
        empty_folder.mkdir()
        self.assertEqual(
            self.make(material_roots=(empty_folder,)).scan_problem(), session.NO_MAT_FILES
        )

        ready = self.make(material_roots=(self.paths["materials"],))
        self.assertEqual(ready.scan_problem(), session.OK)

    def test_a_library_inside_unity_is_refused_by_name(self):
        # Unity is only read, never written: the window has to say so before
        # the first file is created, not after.
        inside = self.make(
            library_root=self.paths["assets"] / "GeneratedLibrary",
            material_roots=(self.paths["materials"],),
        )
        self.assertEqual(inside.scan_problem(), session.LIBRARY_INSIDE_UNITY)
        self.assertEqual(inside.build_problem(), session.LIBRARY_INSIDE_UNITY)

    def test_scanning_fills_in_the_state_the_window_shows(self):
        working = self.make(
            material_roots=(self.paths["materials"],),
            asset_roots=(self.paths["shaders"], self.paths["textures"]),
        )
        result = working.run_extract()

        self.assertEqual(result.material_count, 3)
        state = working.state
        self.assertTrue(state.has_index)
        self.assertEqual(state.material_count, 3)
        self.assertEqual(state.shader_count, 2)
        self.assertTrue(state.scanned_at)
        self.assertEqual(working.build_problem(), session.OK)

    def test_a_library_scanned_elsewhere_is_understood_when_it_is_picked(self):
        # The usual case in Max: the scan already happened, in Blender or from
        # a script, and the person only wants the .mat files.
        extract.extract(
            extract.ExtractRequest(
                material_roots=(self.paths["materials"],),
                library_root=self.library,
            )
        )
        fresh = self.make()
        self.assertTrue(fresh.state.has_index)
        self.assertEqual(fresh.state.material_count, 3)
        self.assertEqual(fresh.build_problem(), session.OK)

    def test_stopping_a_scan_writes_nothing(self):
        working = self.make(material_roots=(self.paths["materials"],))
        with self.assertRaises(session.Cancelled):
            working.run_extract(lambda _fraction, _message: False)
        self.assertFalse((self.library / extract.INDEX_NAME).exists())

    def test_progress_is_reported_from_nothing_to_finished(self):
        working = self.make(material_roots=(self.paths["materials"],))
        seen: list = []
        working.run_extract(lambda fraction, message: seen.append((fraction, message)) is None)
        self.assertTrue(seen)
        self.assertEqual(seen[-1][0], 1.0)
        self.assertTrue(all(0.0 <= fraction <= 1.0 for fraction, _message in seen))

    def test_the_whole_project_switch_is_the_only_way_to_widen_the_search(self):
        # Only listed folders are read (invariant 5), so this stays a choice
        # somebody made, never a silent default.
        narrow = self.make(material_roots=(self.paths["materials"],))
        self.assertEqual(narrow.asset_folders(), ())

        wide = self.make(material_roots=(self.paths["materials"],), search_whole_project=True)
        self.assertEqual(wide.asset_folders(), (self.paths["assets"],))

    def test_the_built_libraries_are_read_back_from_disk(self):
        # What the window reports about a build is the build's own report, so
        # closing and reopening Max does not lose it.
        self.library.mkdir(parents=True, exist_ok=True)
        (self.library / extract.INDEX_NAME).write_text(
            json.dumps({"materials": {}, "generated_at": "2026-08-15T10:00:00+00:00"}),
            encoding="utf-8",
        )
        (self.library / "_ump_max_report.json").write_text(
            json.dumps({"built": 12, "failed": 1, "libraries": 3}), encoding="utf-8"
        )
        maxlib = self.library / "maxlib"
        maxlib.mkdir()
        (maxlib / "Materials.mat").write_bytes(b"")

        state = session.read_state(self.library)
        self.assertEqual((state.built, state.failed, state.libraries), (12, 1, 3))
        self.assertTrue(state.has_libraries)
        self.assertEqual(self.make().use_problem(), session.OK)


class LibraryNameTests(unittest.TestCase):
    """The file name is the name of the group in the Material/Map Browser."""

    def test_without_a_name_the_catalogue_stands_alone(self):
        self.assertEqual(library_build.library_file_name("Textures/Surface/Bricks"),
                         "Textures_Surface_Bricks.mat")
        self.assertEqual(library_build.library_file_name(""), "root.mat")

    def test_a_name_puts_every_catalogue_under_one_prefix(self):
        # Sorted together and recognisable between Materials, Maps, Scene
        # Materials and Sample Slots, which is the whole point of naming it.
        self.assertEqual(
            library_build.library_file_name("Textures/Surface/Bricks", "MadOut2"),
            "MadOut2_Textures_Surface_Bricks.mat",
        )

    def test_a_name_that_windows_would_refuse_is_made_safe(self):
        self.assertEqual(
            library_build.library_file_name("Bricks", 'Mad:Out*2?'),
            "Mad_Out_2_Bricks.mat",
        )
        self.assertEqual(library_build.library_file_name("Bricks", "   "), "Bricks.mat")


class ShaderCoverageTests(unittest.TestCase):
    """A full index can still be a library built on guesses."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.library = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def test_materials_whose_shader_was_never_found_are_counted(self):
        (self.library / extract.INDEX_NAME).write_text(
            json.dumps({"materials": {}, "generated_at": "2026-08-15T16:09:34+00:00"}),
            encoding="utf-8",
        )
        (self.library / extract.REPORT_NAME).write_text(
            json.dumps(
                {
                    "materials": 384,
                    "materials_with_unresolved_textures": 372,
                    "shaders": [
                        {"name": "unresolved:fdeebc9e", "confidence": "none", "materials": 180},
                        {"name": "unresolved:2ff6aa0f", "confidence": "none", "materials": 144},
                        {"name": "Custom/Real", "confidence": "full", "materials": 60},
                    ],
                }
            ),
            encoding="utf-8",
        )

        state = session.read_state(self.library)
        self.assertEqual(state.shader_count, 3)
        self.assertEqual(state.materials_without_shader, 324)
        self.assertEqual(state.unresolved_textures, 372)


class LanguageTests(unittest.TestCase):
    def tearDown(self):
        localization.set_language("")

    def test_the_language_is_a_setting_of_the_tool(self):
        localization.set_language("RU")
        self.assertEqual(localization.text("Build", "Собрать"), "Собрать")
        localization.set_language("EN")
        self.assertEqual(localization.text("Build", "Собрать"), "Build")

    def test_an_unknown_language_follows_the_system(self):
        self.assertIn(localization.set_language("kl"), localization.LANGUAGES)
        self.assertIn(localization.set_language(""), localization.LANGUAGES)


if __name__ == "__main__":
    unittest.main()
