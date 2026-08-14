"""Synthetic Unity project fixtures shared by the tests."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


CUSTOM_SHADER = """\
// Made with Amplify Shader Editor
Shader "Custom/Test Bump Spec"
{
    Properties
    {
        [Toggle]_SpecularAll("SpecularAll", Float) = 0
        _MulColor("MulColor", Color) = (0.5,0.5,0.5,0)
        _MainTex("MainTex", 2D) = "white" {}
        _BumpMap("BumpMap", 2D) = "bump" {}
        _Specular("Specular", Range( 0 , 1)) = 0.3
        _Gloss("Gloss", Range( 0 , 1)) = 0.25
        _Color("Color", Color) = (0,0,0,0)
        [HideInInspector] _texcoord( "", 2D ) = "white" {}
        [HideInInspector] __dirty( "", Int ) = 1
    }
    SubShader
    {
        CGPROGRAM
        #pragma surface surf StandardSpecular keepalpha
        #pragma shader_feature_local _GEOMETRYZOFFSET_ON
        ENDCG
    }
}
"""

STANDARD_LIKE_SHADER = """\
Shader "Custom/Test Standard"
{
    Properties
    {
        _Color("Color", Color) = (1,1,1,1)
        _MainTex("Albedo", 2D) = "white" {}
        [Normal] _NormalTex("Some Normal", 2D) = "bump" {}
        _DetailAlbedoMap("Detail Albedo", 2D) = "grey" {}
        _Metallic("Metallic", Range(0,1)) = 0
        _Glossiness("Smoothness", Range(0,1)) = 0.5
        _Cutoff("Alpha Cutoff", Range(0,1)) = 0.5
    }
    SubShader
    {
        CGPROGRAM
        #pragma surface surf Standard
        ENDCG
    }
}
"""

_META = "fileFormatVersion: 2\nguid: {guid}\n"

_TEXTURE_META = """\
fileFormatVersion: 2
guid: {guid}
TextureImporter:
  serializedVersion: 12
  sRGBTexture: {srgb}
  alphaIsTransparency: 0
  textureType: {texture_type}
  wrapMode: -1
  aniso: 1
"""


def _material(name: str, shader_guid: str, shader_file_id: int, tex_envs: str, floats: str, colors: str) -> str:
    return f"""\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!21 &2100000
Material:
  serializedVersion: 8
  m_Name: {name}
  m_Shader: {{fileID: {shader_file_id}, guid: {shader_guid}, type: 3}}
  m_ValidKeywords: []
  m_InvalidKeywords:
  - _EMISSION
  m_LightmapFlags: 4
  m_DoubleSidedGI: 0
  m_CustomRenderQueue: -1
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
{tex_envs}
    m_Ints: []
    m_Floats:
{floats}
    m_Colors:
{colors}
  m_BuildTextureStacks: []
"""


def _tex_env(name: str, guid: str | None, scale=(1, 1), offset=(0, 0)) -> str:
    texture = "{fileID: 0}" if guid is None else f"{{fileID: 2800000, guid: {guid}, type: 3}}"
    return (
        f"    - {name}:\n"
        f"        m_Texture: {texture}\n"
        f"        m_Scale: {{x: {scale[0]}, y: {scale[1]}}}\n"
        f"        m_Offset: {{x: {offset[0]}, y: {offset[1]}}}\n"
    )


def build_project(root: Path) -> dict:
    """Create a small but realistic Unity tree and return useful paths."""
    assets = root / "UnityProject" / "Assets"
    materials = assets / "Materials"
    shaders = assets / "Shaders"
    textures = assets / "Textures"
    for folder in (materials, shaders, textures, assets.parent / "ProjectSettings"):
        folder.mkdir(parents=True, exist_ok=True)

    custom_guid = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    standard_guid = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    albedo_guid = "cccccccccccccccccccccccccccccccc"
    normal_guid = "dddddddddddddddddddddddddddddddd"
    detail_guid = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"

    (shaders / "Test Bump Spec.shader").write_text(CUSTOM_SHADER, encoding="utf-8")
    (shaders / "Test Bump Spec.shader.meta").write_text(
        _META.format(guid=custom_guid), encoding="utf-8"
    )
    (shaders / "Test Standard.shader").write_text(STANDARD_LIKE_SHADER, encoding="utf-8")
    (shaders / "Test Standard.shader.meta").write_text(
        _META.format(guid=standard_guid), encoding="utf-8"
    )

    for name, guid, srgb, texture_type in (
        ("albedo.png", albedo_guid, 1, 0),
        ("normal.png", normal_guid, 1, 1),
        ("detail.png", detail_guid, 1, 0),
    ):
        (textures / name).write_bytes(b"\x89PNG\r\n\x1a\n")
        (textures / f"{name}.meta").write_text(
            _TEXTURE_META.format(guid=guid, srgb=srgb, texture_type=texture_type),
            encoding="utf-8",
        )

    # Material carrying leftovers from a previous shader.
    stale_material = materials / "WithStale.mat"
    stale_material.write_text(
        _material(
            "WithStale",
            custom_guid,
            4800000,
            _tex_env("_MainTex", albedo_guid, scale=(2, 3), offset=(0.5, 0.25))
            + _tex_env("_BumpMap", normal_guid)
            + _tex_env("_DiffuseR", detail_guid)
            + _tex_env("_EmissionMap", None),
            "    - _Gloss: 0.75\n    - _Specular: 0.4\n    - _Metallic: 0.9\n",
            "    - _Color: {r: 1, g: 0, b: 0, a: 1}\n",
        ),
        encoding="utf-8",
    )
    stale_material.with_suffix(".mat.meta").write_text(
        _META.format(guid="11111111111111111111111111111111"), encoding="utf-8"
    )

    # Material with no textures at all: the look comes from scalars.
    scalar_material = materials / "ScalarsOnly.mat"
    scalar_material.write_text(
        _material(
            "ScalarsOnly",
            standard_guid,
            4800000,
            _tex_env("_MainTex", None) + _tex_env("_NormalTex", None),
            "    - _Metallic: 1\n    - _Glossiness: 0.8\n",
            "    - _Color: {r: 0.2, g: 0.4, b: 0.6, a: 1}\n",
        ),
        encoding="utf-8",
    )
    scalar_material.with_suffix(".mat.meta").write_text(
        _META.format(guid="22222222222222222222222222222222"), encoding="utf-8"
    )

    # Material where a detail map must not steal the base colour slot.
    mixed_material = materials / "Mixed.mat"
    mixed_material.write_text(
        _material(
            "Mixed",
            standard_guid,
            4800000,
            _tex_env("_DetailAlbedoMap", detail_guid)
            + _tex_env("_MainTex", albedo_guid)
            + _tex_env("_NormalTex", normal_guid),
            "    - _Metallic: 0.25\n    - _Glossiness: 0.5\n",
            "    - _Color: {r: 1, g: 1, b: 1, a: 1}\n",
        ),
        encoding="utf-8",
    )
    mixed_material.with_suffix(".mat.meta").write_text(
        _META.format(guid="33333333333333333333333333333333"), encoding="utf-8"
    )

    return {
        "assets": assets,
        "materials": materials,
        "shaders": shaders,
        "textures": textures,
        "custom_shader_guid": custom_guid,
        "standard_shader_guid": standard_guid,
        "albedo_guid": albedo_guid,
        "normal_guid": normal_guid,
    }
