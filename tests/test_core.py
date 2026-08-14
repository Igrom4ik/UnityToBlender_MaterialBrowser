"""Tests for the pure Python core. No Blender required."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import fixtures  # noqa: F401  (adds the project root to sys.path)

from unity_pipeline_core import extract, material_json, profiles, sync, texture_meta, unity_library
from unity_pipeline_core.shader_parser import ShaderResolver, builtin, shaderlab
from unity_pipeline_core.shader_parser.interface import HIDDEN, NORMAL, TEXTURE


class ShaderLabTests(unittest.TestCase):
    def setUp(self):
        self.interface = shaderlab.parse_text(fixtures.CUSTOM_SHADER)

    def test_reads_name_and_workflow(self):
        self.assertEqual(self.interface.name, "Custom/Test Bump Spec")
        self.assertEqual(self.interface.workflow, "specular")
        self.assertEqual(self.interface.confidence, "full")

    def test_reads_defaults_and_types(self):
        by_name = self.interface.by_name
        self.assertEqual(by_name["_MainTex"].type, TEXTURE)
        self.assertAlmostEqual(by_name["_Specular"].default, 0.3)
        self.assertEqual(by_name["_Specular"].range, (0.0, 1.0))
        self.assertEqual(by_name["_MulColor"].default, (0.5, 0.5, 0.5, 0.0))

    def test_marks_hidden_properties(self):
        by_name = self.interface.by_name
        self.assertIn(HIDDEN, by_name["_texcoord"].attributes)
        self.assertIn(HIDDEN, by_name["__dirty"].attributes)

    def test_reads_normal_attribute(self):
        interface = shaderlab.parse_text(fixtures.STANDARD_LIKE_SHADER)
        self.assertIn(NORMAL, interface.by_name["_NormalTex"].attributes)
        self.assertEqual(interface.workflow, "metallic")

    def test_collects_shader_features(self):
        self.assertIn("_GEOMETRYZOFFSET_ON", self.interface.keywords)

    def test_reads_the_header_a_property_sits_under(self):
        text = (
            'Shader "X/Y" {\nProperties {\n'
            '[Header(Albedo)][Space]_Color("Color", Color) = (1,1,1,1)\n'
            '_Rough("R", Float) = 0\n}\n}'
        )
        interface = shaderlab.parse_text(text)
        self.assertEqual(interface.by_name["_Color"].group, "Albedo")
        # The header belongs to the property it precedes, not to the rest.
        self.assertEqual(interface.by_name["_Rough"].group, "")

    def test_comments_do_not_break_parsing(self):
        text = 'Shader "X/Y" {\nProperties {\n// _Ignored("no", Float) = 1\n_Real("yes", Float) = 2\n}\n}'
        interface = shaderlab.parse_text(text)
        self.assertEqual([item.name for item in interface.properties], ["_Real"])


class BuiltinCatalogTests(unittest.TestCase):
    def test_standard_is_resolved_by_file_id(self):
        interface = builtin.parse_reference(builtin.DEFAULT_RESOURCES_GUID, 46)
        self.assertEqual(interface.name, "Standard")
        self.assertEqual(interface.workflow, "metallic")
        self.assertIn("_MetallicGlossMap", interface.by_name)

    def test_unknown_file_id_is_reported_not_guessed(self):
        interface = builtin.parse_reference(builtin.DEFAULT_RESOURCES_GUID, 999999)
        self.assertFalse(interface.is_resolved)
        self.assertEqual(interface.backend, "material_only")


class TextureMetaTests(unittest.TestCase):
    def test_normal_map_ignores_srgb_flag(self):
        settings = texture_meta.settings_from_text(
            "TextureImporter:\n  sRGBTexture: 1\n  textureType: 1\n"
        )
        self.assertTrue(settings.is_normal_map)
        self.assertEqual(settings.colorspace, "Non-Color")

    def test_linear_texture_is_non_color(self):
        settings = texture_meta.settings_from_text(
            "TextureImporter:\n  sRGBTexture: 0\n  textureType: 0\n"
        )
        self.assertEqual(settings.colorspace, "Non-Color")

    def test_name_fallback_is_marked_unresolved(self):
        settings = texture_meta.settings_from_name("T_Wall_N.png")
        self.assertEqual(settings.source, "name")
        self.assertFalse(settings.resolved)
        self.assertEqual(settings.colorspace, "Non-Color")


class ProjectTestCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.paths = fixtures.build_project(self.root)
        self.library = self.root / "library"
        self.request = extract.ExtractRequest(
            material_roots=(self.paths["materials"],),
            asset_roots=(self.paths["shaders"], self.paths["textures"]),
            library_root=self.library,
        )

    def tearDown(self):
        self._temp.cleanup()

    def document(self, name: str) -> dict:
        return material_json.read_document(self.library / "materials" / "Materials" / f"{name}.json")


class MaterialParsingTests(ProjectTestCase):
    def test_reads_scale_offset_and_scalars(self):
        guid_index = unity_library.build_guid_index(self.paths["assets"])
        material = unity_library.parse_material(
            self.paths["materials"] / "WithStale.mat", guid_index
        )
        texture = next(item for item in material.textures if item.property_name == "_MainTex")
        self.assertEqual(texture.scale, (2.0, 3.0))
        self.assertEqual(texture.offset, (0.5, 0.25))
        self.assertAlmostEqual(material.floats["_Gloss"], 0.75)
        self.assertEqual(material.colors["_Color"], (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(material.invalid_keywords, ("_EMISSION",))

    def test_empty_slots_are_separated_from_assigned_textures(self):
        guid_index = unity_library.build_guid_index(self.paths["assets"])
        material = unity_library.parse_material(
            self.paths["materials"] / "WithStale.mat", guid_index
        )
        assigned = {item.property_name for item in material.textures}
        self.assertEqual(assigned, {"_MainTex", "_BumpMap", "_DiffuseR"})
        self.assertIn("_EmissionMap", material.empty_texture_slots)


class EffectiveLayerTests(ProjectTestCase):
    def test_leftover_properties_are_stale(self):
        extract.extract(self.request)
        document = self.document("WithStale")
        # _DiffuseR is assigned in the material but absent from the shader.
        self.assertIn("_DiffuseR", document["effective"]["stale"])
        self.assertNotIn("_DiffuseR", document["effective"]["textures"])
        self.assertIn("_MainTex", document["effective"]["textures"])

    def test_hidden_properties_are_separated(self):
        extract.extract(self.request)
        document = self.document("WithStale")
        self.assertIn("_texcoord", document["effective"]["hidden"])
        self.assertNotIn("_texcoord", document["effective"]["floats"])

    def test_shader_defaults_fill_missing_values(self):
        extract.extract(self.request)
        floats = self.document("WithStale")["effective"]["floats"]
        self.assertEqual(floats["_Gloss"]["origin"], "material")
        self.assertEqual(floats["_SpecularAll"]["origin"], "shader_default")

    def test_metallic_from_another_shader_is_dropped(self):
        extract.extract(self.request)
        document = self.document("WithStale")
        # The custom shader has no _Metallic, so the stored 0.9 must not apply.
        self.assertIn("_Metallic", document["effective"]["stale"])
        plan = profiles.build_plan(document)
        self.assertEqual(plan.metallic, 0.0)


class PlanTests(ProjectTestCase):
    def test_detail_map_does_not_take_the_base_colour_slot(self):
        extract.extract(self.request)
        plan = profiles.build_plan(self.document("Mixed"))
        base = plan.texture_for(profiles.BASE_COLOR)
        self.assertIsNotNone(base)
        self.assertEqual(base.property_name, "_MainTex")
        self.assertIn("_DetailAlbedoMap", plan.unmapped)

    def test_normal_attribute_wins_over_name(self):
        extract.extract(self.request)
        plan = profiles.build_plan(self.document("Mixed"))
        normal = plan.texture_for(profiles.NORMAL_MAP)
        self.assertIsNotNone(normal)
        self.assertEqual(normal.property_name, "_NormalTex")
        self.assertEqual(normal.colorspace, "Non-Color")

    def test_material_without_textures_uses_scalars(self):
        extract.extract(self.request)
        plan = profiles.build_plan(self.document("ScalarsOnly"))
        self.assertEqual(plan.textures, [])
        self.assertEqual(plan.metallic, 1.0)
        self.assertAlmostEqual(plan.roughness, 1.0 - 0.8)
        self.assertEqual(plan.base_color[:3], (0.2, 0.4, 0.6))

    def test_unproven_tint_builds_a_node_but_stays_off(self):
        extract.extract(self.request)
        plan = profiles.build_plan(self.document("WithStale"))
        # The value stays visible in the tree, but must not repaint the albedo.
        self.assertEqual(plan.tint_property, "_Color")
        self.assertEqual(plan.tint_color[:3], (1.0, 0.0, 0.0))
        self.assertEqual(plan.tint_factor, 0.0)
        self.assertIn("_Color", plan.unmapped)

    def test_albedo_header_proves_the_tint(self):
        # Amplify never writes [MainColor]; it puts the colour under Header(Albedo)
        # next to the albedo map, which says the same thing.
        shader = self.paths["shaders"] / "Test Bump Spec.shader"
        shader.write_text(
            fixtures.CUSTOM_SHADER.replace(
                '_Color("Color", Color)', '[Header(Albedo)][Space]_Color("Color", Color)'
            ),
            encoding="utf-8",
        )
        extract.extract(self.request)

        document = self.document("WithStale")
        # The evidence has to survive into the document: phase B has no interface.
        self.assertEqual(document["effective"]["colors"]["_Color"]["group"], "Albedo")

        plan = profiles.build_plan(document)
        self.assertEqual(plan.tint_factor, 1.0)
        self.assertNotIn("_Color", plan.unmapped)

    def test_shipped_profiles_are_loaded_without_a_user_file(self):
        shipped = profiles.load_profiles(None)
        self.assertTrue(shipped)
        self.assertTrue(
            any(item.matches("", "Custom/Environment/PBR_blend_materials") for item in shipped)
        )

    def test_a_user_profile_is_matched_before_the_shipped_ones(self):
        path = self.root / "custom.json"
        path.write_text(
            json.dumps(
                {
                    "profiles": [
                        {
                            "id": "mine",
                            "match": {"shader_name": ["Custom/Environment/PBR_blend_materials"]},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        loaded = profiles.load_profiles(path)
        matching = [
            item for item in loaded if item.matches("", "Custom/Environment/PBR_blend_materials")
        ]
        self.assertEqual(matching[0].identifier, "mine")

    def test_a_profile_can_read_tiling_from_shader_floats(self):
        document = {
            "shader": {"name": "Custom/Environment/PBR_blend_materials", "backend": "shaderlab"},
            "material": {
                "textures": {
                    "_BaseColor1": {
                        "absolute_path": str(self.paths["textures"] / "albedo.png"),
                        "resolved": True,
                        "meta": {},
                    }
                }
            },
            "effective": {
                "textures": {"_BaseColor1": {"origin": "material"}},
                "floats": {
                    "_Tile1x": {"value": 4.0, "origin": "material"},
                    "_Tile1y": {"value": 2.0, "origin": "material"},
                },
                "colors": {},
                "ints": {},
                "stale": [],
                "hidden": [],
            },
        }
        plan = profiles.build_plan(document, None, profiles.load_profiles(None))
        base = plan.texture_for(profiles.BASE_COLOR)
        self.assertIsNotNone(base)
        self.assertEqual(base.scale, (4.0, 2.0))

    def test_tint_can_be_forced_on(self):
        extract.extract(self.request)
        plan = profiles.build_plan(self.document("WithStale"), trust_all_tints=True)
        self.assertEqual(plan.tint_factor, 1.0)
        self.assertNotIn("_Color", plan.unmapped)

    def test_tint_without_a_base_colour_map_paints_directly(self):
        extract.extract(self.request)
        plan = profiles.build_plan(self.document("ScalarsOnly"))
        self.assertEqual(plan.base_color[:3], (0.2, 0.4, 0.6))
        self.assertEqual(plan.tint_factor, 0.0)

    def test_texture_tiling_reaches_the_plan(self):
        extract.extract(self.request)
        plan = profiles.build_plan(self.document("WithStale"))
        base = plan.texture_for(profiles.BASE_COLOR)
        self.assertEqual(base.scale, (2.0, 3.0))
        self.assertEqual(base.offset, (0.5, 0.25))


class SmoothnessTests(ProjectTestCase):
    def test_zero_smoothness_becomes_fully_rough(self):
        # Unity stores smoothness; a value of 0 must not come out as a mirror.
        path = self.paths["materials"] / "ScalarsOnly.mat"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "    - _Glossiness: 0.8", "    - _GlossMapScale: 1\n    - _Glossiness: 0"
            ),
            encoding="utf-8",
        )
        extract.extract(self.request)
        plan = profiles.build_plan(self.document("ScalarsOnly"))
        self.assertEqual(plan.roughness, 1.0)

    def test_gloss_map_scale_is_not_mistaken_for_smoothness(self):
        extract.extract(self.request)
        document = self.document("ScalarsOnly")
        document["effective"]["floats"]["_GlossMapScale"] = {"value": 1.0, "origin": "material"}
        document["effective"]["floats"]["_Glossiness"] = {"value": 0.25, "origin": "material"}
        plan = profiles.build_plan(document)
        self.assertAlmostEqual(plan.roughness, 0.75)
        self.assertAlmostEqual(plan.smoothness_scale, 1.0)

    def test_smoothness_channel_switch_is_read(self):
        extract.extract(self.request)
        document = self.document("Mixed")
        document["effective"]["floats"]["_SmoothnessTextureChannel"] = {
            "value": 1.0,
            "origin": "material",
        }
        plan = profiles.build_plan(document)
        self.assertTrue(plan.smoothness_from_albedo_alpha)


class PackedTextureTests(unittest.TestCase):
    def test_postfix_is_read_from_the_file_name(self):
        self.assertEqual(
            profiles.packing_from_filename("D:/T_Wall_NMG.tif"),
            (profiles.NORMAL_MAP, profiles.PACK_NMG),
        )
        self.assertEqual(
            profiles.packing_from_filename("D:/T_Wall_BCA.png"),
            (profiles.BASE_COLOR, profiles.PACK_BCA),
        )
        self.assertEqual(
            profiles.packing_from_filename("D:/T_Wall_BC.png"),
            (profiles.BASE_COLOR, profiles.PACK_NONE),
        )
        self.assertEqual(
            profiles.packing_from_filename("D:/T_Wall_N.png"),
            (profiles.NORMAL_MAP, profiles.PACK_NONE),
        )

    def test_longer_postfix_wins_over_shorter(self):
        # `_BCA` must not be read as `_BC`.
        _role, packing = profiles.packing_from_filename("D:/T_Wall_BCA.tif")
        self.assertEqual(packing, profiles.PACK_BCA)

    def test_blender_duplicate_suffix_is_ignored(self):
        self.assertEqual(
            profiles.packing_from_filename("D:/T_Wall_NMG.001.tif"),
            (profiles.NORMAL_MAP, profiles.PACK_NMG),
        )

    def test_unknown_postfix_is_not_forced(self):
        self.assertEqual(
            profiles.packing_from_filename("D:/T_Wall_Diffuse.tif"),
            ("", profiles.PACK_NONE),
        )


class PackedPlanTests(ProjectTestCase):
    def test_packed_map_in_a_bump_slot_is_recognised(self):
        # A custom shader may feed an _NMG map through a slot named _BumpMap.
        textures = self.paths["textures"]
        (textures / "wall_NMG.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (textures / "wall_NMG.png.meta").write_text(
            "fileFormatVersion: 2\nguid: 99999999999999999999999999999999\n"
            "TextureImporter:\n  sRGBTexture: 0\n  textureType: 0\n",
            encoding="utf-8",
        )
        material = self.paths["materials"] / "WithStale.mat"
        material.write_text(
            material.read_text(encoding="utf-8").replace(
                "dddddddddddddddddddddddddddddddd", "99999999999999999999999999999999"
            ),
            encoding="utf-8",
        )

        extract.extract(self.request)
        plan = profiles.build_plan(self.document("WithStale"))
        normal = plan.texture_for(profiles.NORMAL_MAP)
        self.assertIsNotNone(normal)
        self.assertEqual(normal.packing, profiles.PACK_NMG)
        self.assertEqual(normal.colorspace, "Non-Color")


class ExtractionTests(ProjectTestCase):
    def test_writes_documents_index_and_gitignore(self):
        result = extract.extract(self.request)
        self.assertEqual(result.material_count, 3)
        self.assertEqual(result.failed, 0)
        self.assertTrue((self.library / extract.INDEX_NAME).is_file())
        self.assertTrue((self.library / ".gitignore").is_file())
        self.assertTrue((self.library / "shaders").is_dir())

    def test_every_material_reaches_the_index(self):
        # Materials sharing a missing texture must not overwrite each other.
        result = extract.extract(self.request)
        index = extract.load_index(self.library)
        self.assertEqual(len(index["materials"]), result.material_count)
        documents = list((self.library / "materials").rglob("*.json"))
        self.assertEqual(len(documents), result.material_count)

    def test_materials_with_the_same_missing_texture_stay_separate(self):
        # Point two materials at the same non-existent texture GUID.
        missing = "ffffffffffffffffffffffffffffffff"
        for name in ("Mixed", "ScalarsOnly"):
            path = self.paths["materials"] / f"{name}.mat"
            text = path.read_text(encoding="utf-8").replace(
                "    m_Ints: []",
                "    - _GhostMap:\n"
                f"        m_Texture: {{fileID: 2800000, guid: {missing}, type: 3}}\n"
                "        m_Scale: {x: 1, y: 1}\n"
                "        m_Offset: {x: 0, y: 0}\n"
                "    m_Ints: []",
            )
            path.write_text(text, encoding="utf-8")

        result = extract.extract(self.request)
        index = extract.load_index(self.library)
        self.assertEqual(len(index["materials"]), result.material_count)
        # The slot is not in either shader, so it is stale, not missing.
        self.assertEqual(result.unresolved_textures, 0)
        for name in ("Mixed", "ScalarsOnly"):
            self.assertIn("_GhostMap", self.document(name)["effective"]["stale"])

    def test_never_writes_into_the_unity_project(self):
        before = {path for path in self.paths["assets"].rglob("*")}
        extract.extract(self.request)
        after = {path for path in self.paths["assets"].rglob("*")}
        self.assertEqual(before, after)

    def test_catalog_mirrors_unity_folders(self):
        extract.extract(self.request)
        self.assertEqual(self.document("Mixed")["material"]["catalog"], "Materials")


class LibraryLocationTests(ProjectTestCase):
    def test_library_inside_unity_is_refused(self):
        inside = self.paths["assets"] / "GeneratedLibrary"
        message = extract.validate_library_root(inside)
        self.assertIn("Unity project", message)

    def test_library_outside_unity_is_accepted(self):
        self.assertEqual(extract.validate_library_root(self.library), "")

    def test_extraction_refuses_to_write_into_unity(self):
        request = extract.ExtractRequest(
            material_roots=(self.paths["materials"],),
            library_root=self.paths["assets"] / "Library",
        )
        with self.assertRaises(ValueError):
            extract.extract(request)

    def test_project_root_is_found_from_a_subfolder(self):
        found = extract.unity_project_of(self.paths["materials"])
        self.assertEqual(found, self.paths["assets"])


class SelectedFoldersOnlyTests(ProjectTestCase):
    """Invariant 5: nothing outside the chosen folders is ever read."""

    def test_textures_outside_the_selected_folders_stay_unresolved(self):
        # Only the material folder is listed, and the maps live next door in
        # Textures/. Walking up to the project to find them is exactly what the
        # invariant forbids, so they must be reported instead.
        request = extract.ExtractRequest(
            material_roots=(self.paths["materials"],),
            library_root=self.library,
        )
        result = extract.extract(request)
        self.assertTrue(result.unresolved_textures)
        document = self.document("Mixed")
        self.assertFalse(document["material"]["textures"]["_MainTex"]["resolved"])
        self.assertTrue(result.report["missing_textures"])

    def test_adding_the_texture_folder_resolves_them(self):
        request = extract.ExtractRequest(
            material_roots=(self.paths["materials"],),
            asset_roots=(self.paths["textures"],),
            library_root=self.library,
        )
        result = extract.extract(request)
        self.assertEqual(result.unresolved_textures, 0)
        document = self.document("Mixed")
        self.assertTrue(document["material"]["textures"]["_MainTex"]["resolved"])


class DiffTests(ProjectTestCase):
    def test_reports_new_changed_and_missing(self):
        extract.extract(self.request)
        roots = (self.paths["materials"],)

        result = sync.diff(self.library, roots)
        self.assertEqual(result.counts.get(sync.CHANGED, 0), 0)
        self.assertEqual(result.counts.get(sync.NEW, 0), 0)

        (self.paths["materials"] / "Mixed.mat").write_text(
            (self.paths["materials"] / "Mixed.mat").read_text(encoding="utf-8")
            + "\n# touched\n",
            encoding="utf-8",
        )
        (self.paths["materials"] / "Extra.mat").write_text("m_Name: Extra\n", encoding="utf-8")
        (self.paths["materials"] / "ScalarsOnly.mat").unlink()

        result = sync.diff(self.library, roots)
        self.assertEqual(result.counts.get(sync.CHANGED, 0), 1)
        self.assertEqual(result.counts.get(sync.NEW, 0), 1)
        self.assertEqual(result.counts.get(sync.MISSING, 0), 1)
        self.assertTrue(result.has_changes)

    def test_an_edited_shader_marks_its_materials_changed(self):
        # Nothing in the .mat moves, but the shader decides defaults, stale
        # properties and whether a tint is proven.
        extract.extract(self.request)
        shader = self.paths["shaders"] / "Test Bump Spec.shader"
        shader.write_text(
            shader.read_text(encoding="utf-8").replace(
                "Properties\n    {", "Properties\n    {\n        _Added(\"Added\", Float) = 1"
            ),
            encoding="utf-8",
        )

        result = sync.diff(self.library, (self.paths["materials"],))
        changed = [entry for entry in result.entries if entry.status == sync.CHANGED]
        self.assertTrue(changed)
        self.assertIn("Shader changed", changed[0].reason)

    def test_a_new_parser_version_invalidates_every_document(self):
        extract.extract(self.request)
        index_path = self.library / extract.INDEX_NAME
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["parser_version"] = index["parser_version"] - 1
        index_path.write_text(json.dumps(index), encoding="utf-8")

        result = sync.diff(self.library, (self.paths["materials"],))
        self.assertEqual(
            result.counts.get(sync.CHANGED, 0), len(list(self.paths["materials"].glob("*.mat")))
        )
        self.assertIn("parser", result.entries[0].reason.casefold())


class ResolverTests(ProjectTestCase):
    def test_each_shader_is_parsed_once(self):
        resolver = ShaderResolver({self.paths["custom_shader_guid"]: self.paths["shaders"] / "Test Bump Spec.shader"})
        first = resolver.resolve(self.paths["custom_shader_guid"], 4800000)
        second = resolver.resolve(self.paths["custom_shader_guid"], 4800000)
        self.assertIs(first, second)
        self.assertEqual(len(resolver.parsed), 1)

    def test_unknown_shader_degrades_to_material_only(self):
        resolver = ShaderResolver({})
        interface = resolver.resolve("f" * 32, 4800000)
        self.assertEqual(interface.backend, "material_only")
        self.assertFalse(interface.is_resolved)


if __name__ == "__main__":
    unittest.main()
