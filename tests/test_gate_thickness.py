"""Synthetic regressions for minimum wall/detail/connection thickness; no hardware or network."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/3d-print-workflow/scripts/printability_gate.py"
SPEC = importlib.util.spec_from_file_location("printability_gate_thickness", SCRIPT)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
HAS_GEOMETRY = all(importlib.util.find_spec(name) for name in ("numpy", "trimesh", "scipy", "rtree", "manifold3d"))
pytestmark = pytest.mark.skipif(not HAS_GEOMETRY, reason="Install requirements-modeling.txt")
KEYS = ["minimum_wall", "minimum_detail", "minimum_connection"]

if HAS_GEOMETRY:
    import numpy as np
    import trimesh


def profile(rules=None, regions=None, **thickness):
    result = {"schema_version": 1, "id": "synthetic-thickness", "units": "mm",
              "required_checks": ["input_integrity", *KEYS], "build_volume_mm": [400, 400, 400],
              "rules": {"wall_min_mm": 1.2, "detail_min_mm": 0.8, "connection_min_mm": 1.5} if rules is None else rules}
    if regions:
        result["regions"] = regions
    if thickness:
        result["thickness"] = thickness
    return result


def on_bed(mesh):
    mesh.apply_translation([0, 0, -mesh.bounds[0][2]])
    return mesh


def box(extents, center=(0, 0, 0)):
    result = trimesh.creation.box(extents=extents)
    result.apply_translation(center)
    return result


def channel(wall):
    """A 30 mm U-channel: single closed shell with inside corners and walls of the given thickness."""
    outer = box((30, 20, 20), (0, 0, 10))
    inner = box((30 - 2 * wall, 40, 20), (0, 0, 10 + wall))
    return trimesh.boolean.difference([outer, inner], engine="manifold")


@pytest.fixture
def inspect(tmp_path):
    def run(mesh, config=None):
        path = tmp_path / "model.stl"
        mesh.export(path)
        return gate.inspect(path, config or profile())
    return run


def statuses(report):
    return {key: report["checks"][key]["status"] for key in KEYS}


def test_slab_thickness_is_measured_and_rules_are_separate(inspect):
    report = inspect(on_bed(box((20, 20, 1.0))))
    assert statuses(report) == {"minimum_wall": "FAIL", "minimum_detail": "PASS", "minimum_connection": "FAIL"}
    wall = report["checks"]["minimum_wall"]["measured"]
    assert wall["min_measured_thickness_mm"] == pytest.approx(1.0, abs=1e-4)
    assert wall["largest_thin_patch_mm2"] == pytest.approx(400, rel=1e-6)  # Top and bottom are separate patches.
    assert wall["thin_patches"] == 2


def test_thick_solid_passes_every_rule(inspect):
    assert statuses(inspect(on_bed(box((20, 20, 5))))) == dict.fromkeys(KEYS, "PASS")


def test_inside_corners_are_not_thin(inspect):
    assert statuses(inspect(channel(3.0))) == dict.fromkeys(KEYS, "PASS")
    thin = inspect(channel(1.0))
    assert statuses(thin) == {"minimum_wall": "FAIL", "minimum_detail": "PASS", "minimum_connection": "FAIL"}
    assert thin["checks"]["minimum_wall"]["measured"]["min_measured_thickness_mm"] == pytest.approx(1.0, abs=1e-4)


def test_pointed_tip_needs_a_patch_tolerance(inspect):
    cone = on_bed(trimesh.creation.cone(10, 40, sections=64))
    strict = inspect(cone, profile(rules={"wall_min_mm": 1.2}))["checks"]["minimum_wall"]
    assert strict["status"] == "FAIL"
    # The thin zone of a 14 degree half-angle cone at 1.2 mm is only a few square millimetres near the apex.
    assert 1 < strict["measured"]["largest_thin_patch_mm2"] < 10
    tolerant = inspect(cone, profile(rules={"wall_min_mm": 1.2}, max_thin_patch_area_mm2=20))["checks"]["minimum_wall"]
    assert tolerant["status"] == "PASS"
    assert tolerant["measured"]["thin_samples"] == strict["measured"]["thin_samples"]


def test_region_rule_overrides_global_in_both_directions(inspect):
    slab = on_bed(box((20, 20, 0.6)))
    everywhere = [[-30, -30, -1], [30, 30, 2]]
    relaxed = inspect(slab, profile(rules={"wall_min_mm": 1.2},
                                    regions=[{"id": "relief", "bounds_mm": everywhere, "rules": {"wall_min_mm": 0.5}}]))
    assert relaxed["checks"]["minimum_wall"]["status"] == "PASS"
    stricter = inspect(slab, profile(rules={"wall_min_mm": 0.5},
                                     regions=[{"id": "root", "bounds_mm": everywhere, "rules": {"wall_min_mm": 1.2}}]))
    assert stricter["checks"]["minimum_wall"]["status"] == "FAIL"
    assert stricter["checks"]["minimum_wall"]["measured"]["regions_with_thin_samples"] == ["root"]


def test_connection_rule_only_in_a_region(inspect):
    # A thick block with a thin fin; the connection rule covers only the block.
    part = trimesh.boolean.union([box((20, 20, 10), (0, 0, 5)), box((1.0, 10, 10), (0, 0, 15))], engine="manifold")
    regions = [{"id": "block", "bounds_mm": [[-11, -11, -1], [11, 11, 9.9]], "rules": {"connection_min_mm": 1.5}}]
    report = inspect(part, profile(rules={"wall_min_mm": 0.8, "detail_min_mm": 0.6}, regions=regions))
    connection = report["checks"]["minimum_connection"]
    assert connection["status"] == "PASS"
    assert "have no connection_min_mm rule" in connection["limitations"][0]
    whole = inspect(part, profile(rules={"wall_min_mm": 0.8, "detail_min_mm": 0.6, "connection_min_mm": 1.5}))
    assert whole["checks"]["minimum_connection"]["status"] == "FAIL"


def test_unconfigured_rule_stays_unknown(inspect):
    report = inspect(on_bed(box((20, 20, 5))), profile(rules={"wall_min_mm": 1.2}))
    assert statuses(report) == {"minimum_wall": "PASS", "minimum_detail": "UNKNOWN", "minimum_connection": "UNKNOWN"}
    nothing = inspect(on_bed(box((20, 20, 5))), profile(rules={}))
    assert statuses(nothing) == dict.fromkeys(KEYS, "UNKNOWN")


def test_preconditions_and_budget_are_explicit_unknowns(inspect):
    second = box((10, 10, 10), (5, 0, 3))
    overlapping = on_bed(trimesh.util.concatenate((box((10, 10, 10)), second)))
    report = inspect(overlapping)
    assert statuses(report) == dict.fromkeys(KEYS, "UNKNOWN")
    assert "self_intersection" in report["checks"]["minimum_wall"]["limitations"][0]
    budget = inspect(on_bed(box((20, 20, 5))), profile(max_samples=10))
    assert statuses(budget) == dict.fromkeys(KEYS, "UNKNOWN")
    assert "budget" in budget["checks"]["minimum_wall"]["limitations"][0]


def test_samples_cover_the_surface_at_the_stated_spacing():
    mesh = trimesh.creation.cone(10, 40, sections=16)
    (points, faces, areas), count = gate.surface_samples(mesh, 0.5, 10_000_000, np)
    assert count == len(points)
    assert areas.sum() == pytest.approx(mesh.area, rel=1e-9)
    # Each sample's own piece is within the spacing, so random surface points are within it too.
    probe, probe_faces = trimesh.sample.sample_surface(mesh, 2000, seed=1)
    for face in np.unique(probe_faces)[:40]:
        own = points[faces == face]
        near = np.linalg.norm(probe[probe_faces == face][:, None] - own[None], axis=2).min(axis=1)
        assert near.max() <= 0.5


def test_invalid_thickness_configuration_is_rejected():
    for thickness in ({"sample_spacing_mm": 0}, {"max_thin_patch_area_mm2": -1}, {"max_samples": 0}, {"approved": True}):
        with pytest.raises(ValueError):
            gate.validate_profile(dict(profile(), thickness=thickness))
