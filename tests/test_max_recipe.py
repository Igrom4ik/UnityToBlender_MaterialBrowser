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


class PackedChannelTests(unittest.TestCase):
    """Channel packing, taken apart exactly as the Blender front-end does."""

    def plan(self, **overrides) -> profiles.ConversionPlan:
        plan = profiles.ConversionPlan(shader_name="Custom/PBR_Packed_Alpha")
        for key, value in overrides.items():
            setattr(plan, key, value)
        return plan

    def texture(self, name: str, role: str, path: str, packing: str, **overrides):
        return profiles.TexturePlan(
            property_name=name,
            role=role,
            image_path=path,
            colorspace="Non-Color" if role == profiles.NORMAL_MAP else "sRGB",
            is_normal=role == profiles.NORMAL_MAP,
            packing=packing,
            **overrides,
        )

    def test_nmg_becomes_a_normal_a_metalness_and_a_roughness(self):
        # Unity packs the normal into RG, metallic into B and gloss into A.
        # Hanging the file on the normal slot alone loses two of the three and
        # points every normal wherever metallic happened to be.
        plan = self.plan(
            textures=[
                self.texture(
                    "_Normals", profiles.NORMAL_MAP, "D:/T_Fence_NMG.tif", profiles.PACK_NMG
                )
            ]
        )
        recipe = max_recipe.recipe_from_plan(plan, "M_Fence")

        bump = recipe.maps[max_recipe.BUMP_MAP]
        self.assertTrue(bump.unpack_z)
        self.assertEqual(bump.gamma, max_recipe.GAMMA_LINEAR)

        metalness = recipe.channels[max_recipe.METALNESS_MAP]
        self.assertEqual(metalness.channel, max_recipe.CHANNEL_BLUE)
        self.assertEqual(metalness.source.path, "D:/T_Fence_NMG.tif")

        roughness = recipe.channels[max_recipe.ROUGHNESS_MAP]
        self.assertEqual(roughness.channel, max_recipe.CHANNEL_INV_ALPHA)

    def test_gloss_scale_is_applied_before_the_inversion(self):
        # Unity multiplies the gloss map by _GlossMapScale and then inverts, so
        # a scaled map does not come out at full gloss.
        plan = self.plan(
            smoothness_scale=0.5,
            textures=[
                self.texture(
                    "_Normals", profiles.NORMAL_MAP, "D:/T_Fence_NMG.tif", profiles.PACK_NMG
                )
            ],
        )
        recipe = max_recipe.recipe_from_plan(plan, "M_Fence")
        self.assertEqual(recipe.channels[max_recipe.ROUGHNESS_MAP].scale, 0.5)

    def test_bca_alpha_is_a_cutout(self):
        plan = self.plan(
            textures=[
                self.texture(
                    "_Albedo", profiles.BASE_COLOR, "D:/T_Fence_BCA.tif", profiles.PACK_BCA
                )
            ]
        )
        recipe = max_recipe.recipe_from_plan(plan, "M_Fence")
        cutout = recipe.channels[max_recipe.CUTOUT_MAP]
        self.assertEqual(cutout.channel, max_recipe.CHANNEL_ALPHA)
        self.assertEqual(cutout.source.path, "D:/T_Fence_BCA.tif")

    def test_albedo_alpha_that_holds_smoothness_is_not_a_cutout(self):
        # The channel switch decides: the same alpha is opacity in one shader
        # and smoothness in the next, and guessing makes a fence transparent.
        plan = self.plan(
            smoothness_from_albedo_alpha=True,
            textures=[
                self.texture(
                    "_Albedo", profiles.BASE_COLOR, "D:/T_Fence_BCA.tif", profiles.PACK_BCA
                )
            ],
        )
        recipe = max_recipe.recipe_from_plan(plan, "M_Fence")
        self.assertNotIn(max_recipe.CUTOUT_MAP, recipe.channels)
        self.assertEqual(
            recipe.channels[max_recipe.ROUGHNESS_MAP].channel, max_recipe.CHANNEL_INV_ALPHA
        )

    def test_a_texture_of_its_own_beats_an_unpacked_channel(self):
        plan = self.plan(
            textures=[
                self.texture(
                    "_Normals", profiles.NORMAL_MAP, "D:/T_Fence_NMG.tif", profiles.PACK_NMG
                ),
                self.texture(
                    "_MetallicGloss",
                    profiles.METALLIC_GLOSS,
                    "D:/T_Fence_MG.tif",
                    profiles.PACK_NONE,
                ),
            ]
        )
        recipe = max_recipe.recipe_from_plan(plan, "M_Fence")
        self.assertEqual(recipe.maps[max_recipe.METALNESS_MAP].path, "D:/T_Fence_MG.tif")
        self.assertNotIn(max_recipe.METALNESS_MAP, recipe.channels)

    def test_what_is_reproduced_is_no_longer_reported_as_missing(self):
        plan = self.plan(
            textures=[
                self.texture(
                    "_Normals", profiles.NORMAL_MAP, "D:/T_Fence_NMG.tif", profiles.PACK_NMG
                )
            ]
        )
        recipe = max_recipe.recipe_from_plan(plan, "M_Fence")
        self.assertFalse([note for note in recipe.notes if "_NMG" in note])


if __name__ == "__main__":
    unittest.main()
