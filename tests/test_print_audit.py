"""Geometry and slice checks use mathematical fixtures, not owner models."""
import math

import pytest


def test_closed_tetrahedron_is_one_outward_wound_solid(tmp_path, load_helper, tetrahedron, write_stl):
    helper = load_helper("audit")
    path = write_stl(tmp_path / "tetrahedron.stl", tetrahedron)
    report = helper.mesh_report(path)
    assert report["basic_topology_pass"] is True
    assert report["triangles"] == 4
    assert report["vertices_welded_at_1e_6_mm"] == 4
    assert report["dimensions_mm"] == [1, 1, 1]
    assert report["edge_connected_components"] == 1
    assert report["signed_volume_mm3"] == pytest.approx(1 / 6)
    assert report["surface_area_mm2"] == pytest.approx(1.5 + math.sqrt(3) / 2)


def test_missing_face_fails_with_three_boundary_edges(tmp_path, load_helper, tetrahedron, write_stl):
    helper = load_helper("audit")
    report = helper.mesh_report(write_stl(tmp_path / "open.stl", tetrahedron[:-1]))
    assert report["basic_topology_pass"] is False
    assert report["boundary_edges"] == 3
    assert report["edge_connected_components"] == 1


def test_reversed_face_and_duplicate_are_detected(tmp_path, load_helper, tetrahedron, write_stl):
    helper = load_helper("audit")
    triangles = [tuple(reversed(tetrahedron[0])), *tetrahedron[1:]]
    report = helper.mesh_report(write_stl(tmp_path / "reversed.stl", triangles))
    assert report["basic_topology_pass"] is False
    assert report["inconsistent_winding_edges"] == 3
    report = helper.mesh_report(write_stl(tmp_path / "duplicate.stl", [*tetrahedron, tetrahedron[0]]))
    assert report["basic_topology_pass"] is False
    assert report["duplicate_triangles"] == 1
    assert report["nonmanifold_edges"] == 3


@pytest.mark.parametrize("coordinate", [math.nan, math.inf, -math.inf])
def test_nonfinite_mesh_coordinates_are_rejected(tmp_path, load_helper, tetrahedron, write_stl, coordinate):
    helper = load_helper("audit")
    triangles = [((coordinate, 0, 0), *tetrahedron[0][1:]), *tetrahedron[1:]]
    with pytest.raises(ValueError):
        helper.mesh_report(write_stl(tmp_path / "invalid.stl", triangles))


@pytest.mark.parametrize("axis", range(3))
def test_proportional_fit_checks_every_axis(load_helper, axis):
    helper = load_helper("audit")
    dimensions = [40.0, 40.0, 40.0]
    dimensions[axis] = 100.0
    result = helper.fit(dimensions, [180.0] * 3, [10.0] * 3)
    assert result["limiting_axis"] == "XYZ"[axis]
    assert result["scale_factor"] == pytest.approx(1.7)
    assert result["dimensions_mm"][axis] == pytest.approx(170.0)
    assert all(size <= 170.0 for size in result["dimensions_mm"])
    assert all(size / original == pytest.approx(1.7) for size, original in zip(result["dimensions_mm"], dimensions))


@pytest.mark.parametrize("field,value", [
    ("dimensions", [0, 1, 1]), ("dimensions", [-1, 1, 1]),
    ("dimensions", [math.nan, 1, 1]), ("volume", [180, math.inf, 180]),
    ("clearance", [0, -1, 0]), ("clearance", [0, math.nan, 0]),
    ("clearance", [180, 0, 0]), ("clearance", [181, 0, 0]),
])
def test_fit_rejects_invalid_bounds(load_helper, field, value):
    helper = load_helper("audit")
    arguments = {"dimensions": [10, 20, 30], "volume": [180, 180, 180], "clearance": [0, 0, 0]}
    arguments[field] = value
    with pytest.raises(ValueError):
        helper.fit(**arguments)


@pytest.mark.parametrize("field", ["dimensions", "volume", "clearance"])
@pytest.mark.parametrize("length", [0, 2, 4])
def test_fit_requires_exactly_three_axes(load_helper, field, length):
    helper = load_helper("audit")
    arguments = {"dimensions": [10, 20, 30], "volume": [180, 180, 180], "clearance": [0, 0, 0]}
    arguments[field] = [1] * length
    with pytest.raises(ValueError):
        helper.fit(**arguments)


def test_slice_report_retains_warning_and_integrity_evidence(tmp_path, load_helper, single_plate_entries, write_package):
    helper = load_helper("audit")
    source = write_package(tmp_path / "synthetic.3mf", single_plate_entries)
    original = source.read_bytes()
    report = helper.slice_report(source)
    assert report["md5_match"] is True
    assert report["warnings"] == [{"msg": "synthetic_temperature_warning", "level": "3", "error_code": "fixture-only"}]
    assert report["bed_commands"] == ["M140 S55", "M190 S55"]
    assert report["settings"]["textured_plate_temp"] == ["55"]
    assert source.read_bytes() == original


def test_corrupt_gcode_md5_does_not_pass(tmp_path, load_helper, single_plate_entries, write_package):
    helper = load_helper("audit")
    single_plate_entries["Metadata/plate_1.gcode"] += b"G1 X2\n"
    source = write_package(tmp_path / "corrupt.3mf", single_plate_entries)
    report = helper.slice_report(source)
    assert report["md5_match"] is False
    assert report["md5_actual"] != report["md5_declared"]
    assert len(report["warnings"]) == 1


def test_absent_plate_is_rejected(tmp_path, load_helper, single_plate_entries, write_package):
    helper = load_helper("audit")
    source = write_package(tmp_path / "synthetic.3mf", single_plate_entries)
    with pytest.raises(ValueError):
        helper.slice_report(source, plate=2)
