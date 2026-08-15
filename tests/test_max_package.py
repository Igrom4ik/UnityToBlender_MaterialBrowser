"""The 3ds Max installer package, checked without 3ds Max.

An .mzp that Max cannot read fails at the worst moment -- in front of the
person installing it -- and nothing else in the suite would notice, so the
structural promises are pinned here.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import fixtures  # noqa: F401  (adds the project root to sys.path)

sys.path.insert(0, str(Path(fixtures.PROJECT_ROOT) / "tools"))

import build_max_plugin  # noqa: E402


class MaxPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        cls.written = build_max_plugin.build(Path(cls._temp.name))
        cls.mzp = next(path for path in cls.written if path.suffix == ".mzp")
        cls.zip = next(path for path in cls.written if path.suffix == ".zip")

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def names(self, path: Path) -> set[str]:
        with zipfile.ZipFile(path) as archive:
            return set(archive.namelist())

    def test_both_artefacts_are_written(self):
        self.assertTrue(self.mzp.is_file())
        self.assertTrue(self.zip.is_file())

    def test_the_installer_control_file_sits_at_the_root(self):
        # 3ds Max looks for mzp.run at the root and nowhere else.
        self.assertIn("mzp.run", self.names(self.mzp))

    def test_the_installer_places_both_packages_and_the_macro(self):
        with zipfile.ZipFile(self.mzp) as archive:
            run = archive.read("mzp.run").decode("ascii")
        self.assertIn("treeCopy unity_material_max to $userScripts", run)
        self.assertIn("treeCopy unity_pipeline_core to $userScripts", run)
        self.assertIn("copy UnityMaterialBrowser.mcr to $userMacros", run)
        self.assertIn("copy unity_material_browser_startup.ms to $userScripts", run)
        self.assertIn("copy unity_material_browser_launch.py to $userScripts", run)
        self.assertIn("run install.ms", run)

    def test_the_macro_runs_the_launcher_rather_than_importing_directly(self):
        # 3ds Max keeps one Python interpreter per session: a package imported
        # at the first click stays loaded, and an updated plugin would go
        # unnoticed until a restart. ExecuteFile does not cache.
        with zipfile.ZipFile(self.mzp) as archive:
            macro = archive.read("UnityMaterialBrowser.mcr").decode("ascii")
            launcher = archive.read("unity_material_browser_launch.py").decode("utf-8")
        self.assertIn("python.ExecuteFile launcher", macro)
        self.assertIn("del sys.modules[name]", launcher)
        self.assertIn("dialog.show()", launcher)

    def test_the_menu_uses_the_api_that_exists_in_2025_and_later(self):
        # menuMan was removed in 3ds Max 2025: a menu is registered by answering
        # the #cuiRegisterMenus callback, and 647394 is the macroscript table.
        with zipfile.ZipFile(self.mzp) as archive:
            startup = archive.read("unity_material_browser_startup.ms").decode("ascii")
        self.assertIn("#cuiRegisterMenus", startup)
        self.assertIn("647394", startup)
        self.assertIn("UnityMaterialBrowser_Build`Unity Material Browser", startup)
        self.assertNotIn("menuMan.", startup)

    def test_the_menu_configuration_is_reloaded(self):
        # Startup scripts run after #cuiRegisterMenus has already fired, so
        # registering the callback without reloading leaves the menu bar as it
        # was and the entry never appears.
        with zipfile.ZipFile(self.mzp) as archive:
            startup = archive.read("unity_material_browser_startup.ms").decode("ascii")
        self.assertIn("maxOps.GetICuiMenuMgr()", startup)
        self.assertIn("LoadConfiguration", startup)
        self.assertIn("GetCurrentConfiguration", startup)

    def test_menu_ids_are_guid_strings(self):
        # CreateSubMenu and CreateAction are interface methods: they take GUID
        # strings and reject a number pair with a type error at startup.
        with zipfile.ZipFile(self.mzp) as archive:
            startup = archive.read("unity_material_browser_startup.ms").decode("ascii")
        for line in startup.splitlines():
            if line.startswith("global UMB_MENU_ID") or line.startswith("global UMB_ACTION_ID"):
                value = line.split("=", 1)[1].strip()
                self.assertTrue(value.startswith('"') and value.endswith('"'), line)
                self.assertRegex(value, r'^"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}"$')

    def test_the_macro_opens_the_step_window(self):
        # It used to open a folder picker straight away, which asked for a
        # choice without ever saying what was being chosen.
        with zipfile.ZipFile(self.mzp) as archive:
            macro = archive.read("UnityMaterialBrowser.mcr").decode("ascii")
        self.assertIn("from unity_material_max import dialog", macro)
        self.assertIn("dialog.show()", macro)
        self.assertNotIn("getSavePath", macro)

    def test_the_macro_keeps_the_name_the_menu_is_built_from(self):
        # The startup script points at "UnityMaterialBrowser_Build`Unity
        # Material Browser": renaming either half unhooks the menu entry
        # without a single error message.
        with zipfile.ZipFile(self.mzp) as archive:
            macro = archive.read("UnityMaterialBrowser.mcr").decode("ascii")
        self.assertIn("macroScript UnityMaterialBrowser_Build", macro)
        self.assertIn('category:"Unity Material Browser"', macro)

    def test_no_comment_line_of_the_macro_carries_a_second_marker(self):
        # 3ds Max 2027 refused the whole file with "Syntax error: at -,
        # expected macroScript" over a header line that spelled "--" twice: the
        # macro parser stops reading the line as a comment at the second one.
        # Only .mcr is this fragile -- install.ms has carried the same shape
        # for releases and loads fine -- so the rule is pinned here alone.
        with zipfile.ZipFile(self.mzp) as archive:
            macro = archive.read("UnityMaterialBrowser.mcr").decode("ascii")
        for number, line in enumerate(macro.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("--"):
                self.assertNotIn("--", stripped[2:], f"line {number}: {line}")

    def test_maxscript_files_are_plain_ascii(self):
        # MAXScript reads these as plain text; a stray byte turns the installer
        # into a syntax error.
        with zipfile.ZipFile(self.mzp) as archive:
            for name in (
                "mzp.run",
                "install.ms",
                "UnityMaterialBrowser.mcr",
                "unity_material_browser_startup.ms",
            ):
                archive.read(name).decode("ascii")

    def test_the_payload_is_the_whole_plugin(self):
        for path in (self.mzp, self.zip):
            names = self.names(path)
            self.assertIn("unity_material_max/library_build.py", names)
            # The window is part of the plugin, not an extra someone installs.
            self.assertIn("unity_material_max/dialog.py", names)
            self.assertIn("unity_material_max/session.py", names)
            self.assertIn("unity_material_max/localization.py", names)
            self.assertIn("unity_pipeline_core/extract.py", names)
            self.assertIn("unity_pipeline_core/default_profiles.json", names)
            # Without these the _NMG normal quietly loses its blue channel and
            # metallic never arrives: the fallback is silent by design.
            self.assertIn("unity_material_max/osl/unity_nmg_normal.osl", names)
            self.assertIn("unity_material_max/osl/unity_blue_channel.osl", names)
            self.assertIn("unity_pipeline_core/shader_parser/shaderlab.py", names)

    def test_nothing_compiled_ships(self):
        for path in (self.mzp, self.zip):
            for name in self.names(path):
                self.assertNotIn("__pycache__", name)
                self.assertFalse(name.endswith(".pyc"))

    def test_the_plain_zip_carries_instructions_instead_of_an_installer(self):
        names = self.names(self.zip)
        self.assertIn("INSTALL.txt", names)
        self.assertNotIn("mzp.run", names)


if __name__ == "__main__":
    unittest.main()
