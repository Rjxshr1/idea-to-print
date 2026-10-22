"""Synthetic regressions for base contact, stability and self-intersection; no hardware or network."""
import importlib.util
import math
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/3d-print-workflow/scripts/printability_gate.py"
SPEC = importlib.util.spec_from_file_location("printability_gate_new_checks", SCRIPT)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
HAS_GEOMETRY = all(importlib.util.find_spec(name) for name in ("numpy", "trimesh", "scipy", "rtree"))
pytestmark = pytest.mark.skipif(not HAS_GEOMETRY, reason="Install requirements-modeling.txt")
CHECKS = ["input_integrity", "self_intersection", "base_contact", "stability"]

if HAS_GEOMETRY:
    import numpy as np
    import trimesh


def profile(**base):
    result = {"schema_version": 1, "id": "synthetic-new-checks", "units": "mm",
              "required_checks": CHECKS.copy(), "build_volume_mm": [400, 400, 400]}
    if base:
        result["base"] = base
    return result


def on_bed(mesh):
    mesh.apply_translation([0, 0, -mesh.bounds[0][2]])
    return mesh


def box(extents=(10, 12, 14)):
    return on_bed(trimesh.creation.box(extents=extents))


@pytest.fixture
def inspect(tmp_path):
    def run(mesh, config=None):
        path = tmp_path / "model.stl"
        mesh.export(path)
        before = gate.file_hash(path)
        report = gate.inspect(path, config or profile())
        assert gate.file_hash(path) == before
        return report
    return run


# Triangle-pair cases: (faces as vertex ids, coordinates) -> expected intersection.
def pair(vertices, first, second):
    vertices = np.asarray(vertices, dtype=float)
    faces = np.asarray([first, second])
    return bool(gate.triangle_pairs_intersect(vertices[faces], faces, np.array([0]), np.array([1]), 1e-9, np)[0])


def test_unrelated_triangles_pierce_or_stay_apart():
    v = [(0, 0, 0), (4, 0, 0), (0, 4, 0), (1, 1, -1), (1, 1, 1), (3, 3, 0.5),
         (10, 10, -1), (10, 10, 1), (12, 12, 0)]
    assert pair(v, (0, 1, 2), (3, 4, 5)) is True
    assert pair(v, (0, 1, 2), (6, 7, 8)) is False


def test_touching_unrelated_faces_count_as_intersection():
    v = [(0, 0, 0), (4, 0, 0), (0, 4, 0), (1, 1, 0), (1, 1, 3), (2, 1, 3)]
    assert pair(v, (0, 1, 2), (3, 4, 5)) is True  # Vertex 3 rests on the first face without being welded.


def test_shared_vertex_contact_only_is_not_intersection():
    v = [(0, 0, 0), (4, 0, 0), (0, 4, 0), (0, 0, 4), (-4, 0, 0)]
    assert pair(v, (0, 1, 2), (0, 3, 4)) is False


def test_shared_vertex_piercing_beyond_vertex_is_intersection():
    v = [(0, 0, 0), (4, 0, 0), (0, 4, 0), (1, 1, 1), (1, 1, -1)]
    assert pair(v, (0, 1, 2), (0, 3, 4)) is True


def test_shared_edge_depends_on_fold():
    v = [(0, 0, 0), (4, 0, 0), (0, 4, 0), (2, 3, 0), (0, -4, 0), (0, 2, 3)]
    assert pair(v, (0, 1, 2), (0, 1, 5)) is False   # Distinct planes meet only along the edge.
    assert pair(v, (0, 1, 2), (0, 1, 4)) is False   # Flat continuation on the other side.
    assert pair(v, (0, 1, 2), (0, 1, 3)) is True    # Folded back onto itself.


def test_coplanar_shared_vertex_sectors():
    v = [(0, 0, 0), (4, 0, 0), (4, 1, 0), (-4, 0, 0), (-4, 1, 0), (4, 2, 0), (1, 4, 0)]
    assert pair(v, (0, 1, 2), (0, 3, 4)) is False   # Opposite sectors.
    assert pair(v, (0, 1, 5), (0, 2, 6)) is True    # Overlapping sectors.


def test_coplanar_unrelated_overlap_and_separation():
    v = [(0, 0, 0), (4, 0, 0), (0, 4, 0), (1, 1, 0), (5, 1, 0), (1, 5, 0), (10, 0, 0), (12, 0, 0), (10, 2, 0)]
    assert pair(v, (0, 1, 2), (3, 4, 5)) is True
    assert pair(v, (0, 1, 2), (6, 7, 8)) is False


def test_duplicate_vertex_set_is_overlap():
    v = [(0, 0, 0), (4, 0, 0), (0, 4, 0)]
    assert pair(v, (0, 1, 2), (0, 2, 1)) is True


def test_clean_meshes_pass_self_intersection(inspect):
    subdivided = box()
    for _ in range(3):
        subdivided = subdivided.subdivide()  # Many coplanar shared-vertex and shared-edge pairs.
    for mesh in (box(), subdivided, on_bed(trimesh.creation.icosphere(3, 10)),
                 on_bed(trimesh.creation.torus(major_radius=10, minor_radius=3))):
        check = inspect(mesh)["checks"]["self_intersection"]
        assert check["status"] == "PASS", check
        assert check["measured"]["intersecting_pairs"] == 0
        assert check["measured"]["candidate_pairs"] > 0


def test_overlapping_shells_and_spindle_torus_fail(inspect):
    second = trimesh.creation.box(extents=(10, 10, 10))
    second.apply_translation([5, 0, 3])
    overlapping = on_bed(trimesh.util.concatenate((trimesh.creation.box(extents=(10, 10, 10)), second)))
    spindle = on_bed(trimesh.creation.torus(major_radius=3, minor_radius=5))  # Tube passes through the axis.
    for mesh in (overlapping, spindle):
        check = inspect(mesh)["checks"]["self_intersection"]
        assert check["status"] == "FAIL"
        assert check["measured"]["intersecting_pairs"] > 0
        assert check["measured"]["sample_face_pairs"]


def test_self_intersection_budget_and_backend_are_explicit_unknowns(inspect, monkeypatch):
    config = profile()
    config["intersection_search"] = {"max_candidate_pairs": 5}
    check = inspect(box(), config)["checks"]["self_intersection"]
    assert check["status"] == "UNKNOWN" and "budget" in check["limitations"][0]
    real = importlib.util.find_spec
    monkeypatch.setattr(gate.importlib.util, "find_spec", lambda name, *a: None if name == "rtree" else real(name, *a))
    check = inspect(box())["checks"]["self_intersection"]
    assert check["status"] == "UNKNOWN" and "rtree" in check["limitations"][0]


def test_base_contact_area_against_part_threshold(inspect):
    measured = inspect(box(), profile(min_contact_area_mm2=100))["checks"]["base_contact"]
    assert measured["status"] == "PASS"
    assert measured["measured"]["projected_contact_area_mm2"] == pytest.approx(120)
    assert measured["measured"]["footprint_hull_area_mm2"] == pytest.approx(120)
    assert inspect(box(), profile(min_contact_area_mm2=150))["checks"]["base_contact"]["status"] == "FAIL"
    diagnostic = inspect(box())["checks"]["base_contact"]
    assert diagnostic["status"] == "UNKNOWN"
    assert diagnostic["measured"]["projected_contact_area_mm2"] == pytest.approx(120)


def test_point_contact_has_no_area_and_cannot_stand(inspect):
    cone = trimesh.creation.cone(10, 30)
    cone.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
    report = inspect(on_bed(cone), profile(min_contact_area_mm2=1, min_stability_margin_mm=1))
    assert report["checks"]["base_contact"]["status"] == "FAIL"
    assert report["checks"]["base_contact"]["measured"]["projected_contact_area_mm2"] == 0
    assert report["checks"]["stability"]["status"] == "FAIL"
    assert "point or a line" in report["checks"]["stability"]["limitations"][0]


def test_plane_tolerance_controls_near_flat_contact(inspect):
    sphere = on_bed(trimesh.creation.icosphere(4, 20))
    strict = inspect(sphere, profile(plane_tolerance_mm=1e-6))["checks"]["base_contact"]["measured"]
    loose = inspect(sphere, profile(plane_tolerance_mm=0.5))["checks"]["base_contact"]["measured"]
    assert loose["projected_contact_area_mm2"] > strict["projected_contact_area_mm2"]


def test_stability_margin_and_tip_angle(inspect):
    # 10 x 10 x 100 column: margin 5 mm, centre of mass 50 mm high, tip angle atan(5/50).
    column = box((10, 10, 100))
    expected_tip = math.degrees(math.atan2(5, 50))
    check = inspect(column, profile(min_stability_margin_mm=4, min_tip_angle_deg=5))["checks"]["stability"]
    assert check["status"] == "PASS", check
    assert check["measured"]["min_margin_mm"] == pytest.approx(5)
    assert check["measured"]["min_tip_angle_deg"] == pytest.approx(expected_tip)
    assert inspect(column, profile(min_tip_angle_deg=8))["checks"]["stability"]["status"] == "FAIL"
    assert inspect(column, profile(min_stability_margin_mm=6))["checks"]["stability"]["status"] == "FAIL"
    diagnostic = inspect(column)["checks"]["stability"]
    assert diagnostic["status"] == "UNKNOWN"
    assert diagnostic["measured"]["min_margin_mm"] == pytest.approx(5)


def test_centre_of_mass_outside_footprint_fails_without_threshold(inspect):
    tower = trimesh.creation.box(extents=(6, 6, 60))
    tower.apply_transform(trimesh.transformations.shear_matrix(math.radians(25), [1, 0, 0], [0, 0, 0], [0, 0, 1]))
    check = inspect(on_bed(tower))["checks"]["stability"]
    assert check["status"] == "FAIL"
    assert check["measured"]["min_margin_mm"] < 0


def test_stability_uses_worst_of_solid_and_shell_mass_models(inspect):
    # A wide thin plate cantilevered from a small post: the shell and solid centres differ.
    post = trimesh.creation.box(extents=(10, 10, 20))
    post.apply_translation([0, 0, 10])
    plate = trimesh.creation.box(extents=(40, 10, 1))
    plate.apply_translation([15, 0, 20.5])
    part = trimesh.boolean.union([post, plate], engine="manifold")
    check = inspect(part)["checks"]["stability"]
    solid, shell = check["measured"]["solid_centre_of_mass_mm"], check["measured"]["shell_centre_of_mass_mm"]
    assert solid[0] != pytest.approx(shell[0])
    worst = check["measured"]["worst_mass_model"]["solid_fraction"]
    assert worst in (0.0, 1.0)  # Margin is concave, so the minimum lies at a mass-model endpoint.


def test_stability_and_contact_need_verified_orientation(inspect):
    inverted = box()
    inverted.invert()
    report = inspect(inverted, profile(min_contact_area_mm2=1, min_stability_margin_mm=1))
    assert report["checks"]["base_contact"]["status"] == "UNKNOWN"
    assert report["checks"]["stability"]["status"] == "UNKNOWN"


def test_invalid_base_configuration_is_rejected():
    for base in ({"min_contact_area_mm2": -1}, {"plane_tolerance_mm": 2}, {"min_tip_angle_deg": 90},
                 {"approved": True}):
        with pytest.raises(ValueError):
            gate.validate_profile(profile(**base))
    with pytest.raises(ValueError):
        gate.validate_profile(dict(profile(), intersection_search={"max_candidate_pairs": 0}))


def test_full_box_with_thresholds_reaches_pass_for_these_checks(inspect):
    report = inspect(box(), profile(min_contact_area_mm2=50, min_stability_margin_mm=2, min_tip_angle_deg=10))
    assert report["overall"] == "PASS", report["unknown_required_checks"]
    assert {key: report["checks"][key]["status"] for key in CHECKS} == dict.fromkeys(CHECKS, "PASS")
