"""Validate the published Windows Blender extension archives."""
from pathlib import Path
import hashlib
import tomllib
import zipfile

root = Path(__file__).resolve().parents[1]
expected = {
    "unity_material_drop-0.5.3-windows-x64.zip": ("unity_material_drop", "0.5.3"),
    "unity_material_browser-0.13.3-windows-x64.zip": ("unity_material_browser", "0.13.3"),
}
checksums = {}
for line in (root / "dist" / "SHA256SUMS").read_text(encoding="utf8").splitlines():
    digest, name = line.split("  ", 1)
    checksums[name] = digest
assert set(checksums) == set(expected)
cores = []
for name, (identifier, version) in expected.items():
    path = root / "dist" / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksums[name]
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        manifest = tomllib.loads(z.read("blender_manifest.toml").decode("utf8"))
        assert manifest["id"] == identifier and manifest["version"] == version
        assert manifest["blender_version_min"] == "5.2.0"
        assert manifest["platforms"] == ["windows-x64"]
        core = sorted(n for n in z.namelist() if n.startswith("_vendor/unity_pipeline_core/"))
        assert core and any(n.endswith(".pyd") for n in core)
        assert not any(n.endswith(".py") for n in core)
        cores.append({n: hashlib.sha256(z.read(n)).hexdigest() for n in core})
        for needed in ("environment_unlit_adapter.py", "packed_shader_adapter.py", "golden_gate_window.py"):
            assert needed in z.namelist()
    print(name, checksums[name])
assert cores[0] == cores[1], "The two add-ons have different core binaries"
print("RELEASE_ARCHIVES_OK")
