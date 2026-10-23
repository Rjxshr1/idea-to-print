"""Name plates: real font outlines merged with a figurine into one printable solid."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tested_name_base", ROOT / "skills/printable-modeling/scripts/name_base.py")
plate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plate)
GATE_SPEC = importlib.util.spec_from_file_location("gate_for_name_base", ROOT / "skills/3d-print-workflow/scripts/printability_gate.py")
gate = importlib.util.module_from_spec(GATE_SPEC)
GATE_SPEC.loader.exec_module(gate)
HAS_DEPS = all(importlib.util.find_spec(name) for name in ("trimesh", "manifold3d", "fontTools", "scipy", "rtree"))


def any_font():
    if not HAS_DEPS:
        return None
    try:
        return plate.find_font("Mochi 2026")[0]
    except ValueError:
        return None


FONT = any_font()
pytestmark = pytest.mark.skipif(FONT is None, reason="Needs modeling dependencies and an installed font")


@pytest.fixture
def figurine(tmp_path):
    import trimesh
    body = trimesh.creation.box(extents=(20, 16, 30))
    body.apply_translation([5, 5, 15])  # Off-centre; the plate should still centre it.
    path = tmp_path / "figure.stl"
    body.export(path)
    return path


def test_plate_text_and_figure_form_one_clean_solid(figurine, tmp_path):
    import trimesh
    out = tmp_path / "named.stl"
    result = plate.build(figurine, out, "Mochi", subtitle="2026", font_path=FONT)
    assert result["watertight"] and result["lines"] == ["Mochi", "2026"]
    mesh = trimesh.load(out, force="mesh")
    assert mesh.bounds[0][2] == pytest.approx(0, abs=1e-6)
    assert len(mesh.split(only_watertight=False)) == 1
    width, depth, base = result["plate_mm"]
    assert mesh.extents[0] == pytest.approx(width, abs=0.05) and mesh.extents[1] == pytest.approx(depth, abs=0.05)
    # Letters stand text_depth above the plate inside the suggested lettering region.
    (x0, y0, z0), (x1, y1, z1) = result["suggested_gate_region"]["bounds_mm"]
    inside = (mesh.vertices[:, 0] > x0) & (mesh.vertices[:, 0] < x1) & (mesh.vertices[:, 1] > y0) & (mesh.vertices[:, 1] < y1)
    assert mesh.vertices[inside, 2].max() == pytest.approx(base + result["text_raised_mm"], abs=0.01)
    assert y1 < mesh.bounds[0][1] + depth / 2  # The label is in the front half, facing the viewer.


def test_result_passes_the_geometry_gate(figurine, tmp_path):
    out = tmp_path / "named.stl"
    result = plate.build(figurine, out, "Mochi", font_path=FONT)
    profile = {"schema_version": 1, "units": "mm", "build_volume_mm": [256, 256, 256],
               "required_checks": ["input_integrity", "watertight", "edge_manifold", "vertex_manifold", "winding",
                                   "degenerate_faces", "duplicate_faces", "components", "outward_normals",
                                   "self_intersection", "minimum_detail"],
               "rules": {"detail_min_mm": 0.8}, "thickness": {"max_thin_patch_area_mm2": 2},
               "regions": [result["suggested_gate_region"]]}
    report = gate.inspect(out, profile)
    assert report["overall"] == "PASS", {k: v["status"] for k, v in report["checks"].items() if v["status"] != "PASS"}


def test_refusals(figurine, tmp_path):
    out = tmp_path / "named.stl"
    with pytest.raises(ValueError, match="empty"):
        plate.build(figurine, out, "   ", font_path=FONT)
    with pytest.raises(ValueError, match="base height"):
        plate.build(figurine, out, "Mochi", font_path=FONT, base_height=0.5)
    plate.build(figurine, out, "Mochi", font_path=FONT)
    with pytest.raises(ValueError, match="overwrite"):
        plate.build(figurine, out, "Mochi", font_path=FONT)


def test_missing_glyphs_are_reported_for_an_explicit_font():
    with pytest.raises(ValueError, match="no glyphs"):
        plate.find_font("\U0001F98A", FONT)  # A fox emoji is not in text fonts.
