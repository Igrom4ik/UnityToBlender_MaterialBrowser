"""The browser's list, tested without Qt and without 3ds Max.

`browser.py` draws a grid and talks to Max; what is in the grid, what a search
box leaves on screen and which `.mat` a material lives in are decided here.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import fixtures  # noqa: F401  (adds the project root to sys.path)

from unity_material_max import catalog
from unity_pipeline_core import extract


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.paths = fixtures.build_project(self.root)
        self.library = self.root / "library"
        extract.extract(
            extract.ExtractRequest(
                material_roots=(self.paths["materials"],),
                asset_roots=(self.paths["shaders"], self.paths["textures"]),
                library_root=self.library,
            )
        )

    def tearDown(self):
        self._temp.cleanup()

    def test_every_material_in_the_index_is_listed(self):
        entries = catalog.load_entries(self.library)
        self.assertEqual(len(entries), 3)
        self.assertEqual(
            [entry.name for entry in entries], ["Mixed", "ScalarsOnly", "WithStale"]
        )
        self.assertTrue(all(entry.catalog for entry in entries))

    def test_a_library_that_was_never_scanned_is_an_empty_list(self):
        self.assertEqual(catalog.load_entries(self.root / "nowhere"), [])

    def test_each_material_knows_which_mat_holds_it(self):
        # The .mat name is also the browser group, and the library name is part
        # of it: a material found here has to be findable there.
        entry = catalog.load_entries(self.library, "MadOut2")[0]
        self.assertTrue(entry.library.startswith("MadOut2_"))
        self.assertTrue(entry.library.endswith(".mat"))
        self.assertEqual(
            catalog.library_path(self.library, entry).parent.name, "maxlib"
        )

    def test_search_takes_words_in_any_order(self):
        entries = catalog.load_entries(self.library)
        self.assertEqual(len(catalog.search(entries, "stale")), 1)
        self.assertEqual(len(catalog.search(entries, "STALE with")), 1)
        self.assertEqual(len(catalog.search(entries, "nothing here")), 0)
        self.assertEqual(len(catalog.search(entries, "")), 3)

    def test_search_reaches_the_shader_name_as_well(self):
        entries = catalog.load_entries(self.library)
        found = catalog.search(entries, "Test Standard")
        self.assertTrue(found)
        self.assertTrue(all("Standard" in entry.shader for entry in found))

    def test_a_catalogue_filter_narrows_the_list(self):
        entries = catalog.load_entries(self.library)
        first = catalog.catalogs(entries)[0]
        self.assertTrue(all(entry.catalog == first for entry in catalog.search(entries, "", first)))
        self.assertEqual(catalog.search(entries, "", "Nowhere"), [])

    def test_previews_are_cached_outside_unity(self):
        # Unity is only ever read; a cache inside Assets is a cache Unity
        # imports and the artist has to explain.
        entry = catalog.load_entries(self.library)[0]
        path = catalog.thumbnail_path(self.library, entry)
        self.assertEqual(path.suffix, ".png")
        self.assertTrue(str(path).startswith(str(self.library)))
        self.assertIsNone(extract.unity_project_of(path.parent))

    def test_a_preview_is_made_from_the_base_colour_map(self):
        entry = next(
            entry for entry in catalog.load_entries(self.library) if entry.name == "WithStale"
        )
        self.assertTrue(catalog.preview_source(self.library, entry).endswith("albedo.png"))

    def test_a_rendered_sphere_wins_over_a_texture_tile(self):
        # Both are previews of the same material; the rendered one is what the
        # material actually looks like.
        entry = catalog.load_entries(self.library)[0]
        tile = catalog.thumbnail_path(self.library, entry)
        ball = catalog.ball_path(self.library, entry)
        self.assertNotEqual(tile, ball)
        self.assertIsNone(catalog.preview_for(self.library, entry))

        tile.parent.mkdir(parents=True, exist_ok=True)
        tile.write_bytes(b"tile")
        self.assertEqual(catalog.preview_for(self.library, entry), tile)
        ball.write_bytes(b"ball")
        self.assertEqual(catalog.preview_for(self.library, entry), ball)

    def test_a_material_of_pure_scalars_still_has_a_colour(self):
        entry = next(
            entry for entry in catalog.load_entries(self.library) if entry.name == "ScalarsOnly"
        )
        colour = catalog.preview_color(self.library, entry)
        self.assertEqual(len(colour), 3)
        self.assertEqual(tuple(round(channel, 2) for channel in colour), (0.2, 0.4, 0.6))

    def test_a_material_without_a_base_map_has_no_preview_source(self):
        entry = next(
            entry for entry in catalog.load_entries(self.library) if entry.name == "ScalarsOnly"
        )
        self.assertEqual(catalog.preview_source(self.library, entry), "")


if __name__ == "__main__":
    unittest.main()
