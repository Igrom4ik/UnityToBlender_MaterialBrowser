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
        self.assertIn("run install.ms", run)

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
            self.assertIn("unity_pipeline_core/extract.py", names)
            self.assertIn("unity_pipeline_core/default_profiles.json", names)
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
