"""Phase B for 3ds Max, tested without 3ds Max.

`recipe.py` decides what the Physical Material becomes; only carrying it out
needs pymxs. These tests cover the deciding half.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import fixtures  # noqa: F401  (adds the project root to sys.path)

from unity_material_max import recipe as max_recipe
from unity_pipeline_core import extract, material_json, profiles


class RecipeTests(unittest.TestCase):
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

    def recipe(self, name: str) -> max_recipe.MaterialRecipe:
        document = material_json.read_document(
            self.library / "materials" / "Materials" / f"{name}.json"
        )
        return max_recipe.recipe_from_document(document)

    def test_base_colour_map_lands_in_the_physical_slot(self):
        recipe = self.recipe("WithStale")
        bitmap = recipe.maps[max_recipe.BASE_COLOR_MAP]
        self.assertTrue(bitmap.path.endswith("albedo.png"))
        self.assertEqual(bitmap.gamma, max_recipe.GAMMA_SRGB)

    def test_unity_tiling_reaches_the_bitmap(self):
        bitmap = self.recipe("WithStale").maps[max_recipe.BASE_COLOR_MAP]
        self.assertEqual(bitmap.tiling, (2.0, 3.0))
        self.assertEqual(bitmap.offset, (0.5, 0.25))

    def test_non_colour_data_is_loaded_linearly(self):
        # A normal map read with sRGB gamma is silently wrong, not obviously so.
        bitmap = self.recipe("WithStale").maps[max_recipe.BUMP_MAP]
        self.assertEqual(bitmap.gamma, max_recipe.GAMMA_LINEAR)
        self.assertTrue(bitmap.is_normal)

    def test_bump_amount_is_always_stated(self):
        # Max starts bump_map_amt at 0.3; leaving it there flattens every normal.
        self.assertEqual(self.recipe("WithStale").bump_amount, 1.0)

    def test_scalars_carry_over(self):
        recipe = self.recipe("ScalarsOnly")
        self.assertAlmostEqual(recipe.roughness, 1.0 - 0.8)
        self.assertEqual(recipe.metalness, 1.0)
        self.assertEqual(recipe.base_color, (0.2, 0.4, 0.6))

    def test_what_cannot_be_built_is_written_down(self):
        recipe = self.recipe("WithStale")
        self.assertTrue(recipe.notes)
        self.assertTrue(any("unmapped" in note for note in recipe.notes))

    def test_provenance_travels_with_the_recipe(self):
        recipe = self.recipe("WithStale")
        self.assertTrue(recipe.unity_path.endswith("WithStale.mat"))
        self.assertEqual(recipe.shader_name, "Custom/Test Bump Spec")

    def test_a_role_never_takes_two_maps(self):
        plan = profiles.build_plan(
            material_json.read_document(
                self.library / "materials" / "Materials" / "WithStale.json"
            )
        )
        slots = [texture.role for texture in plan.textures]
        self.assertEqual(len(slots), len(set(slots)))


if __name__ == "__main__":
    unittest.main()
