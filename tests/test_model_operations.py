"""Synthetic geometry regression checks: locality, protection, caps and meaning."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills/printable-modeling/scripts"


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ops = module("mesh_operations")
pipeline = module("model_pipeline")


def grid():
    vertices = np.array([(x, y, 1.0 if x == y == 0 else 0.0)
                         for y in range(-2, 3) for x in range(-2, 3)], dtype=float)
    faces = []
    for y in range(4):
        for x in range(4):
            i = y * 5 + x
            faces.extend(((i, i + 1, i + 5), (i + 1, i + 6, i + 5)))
    return vertices, np.array(faces)


def cylinder():
    rings = [np.array([(np.cos(a), np.sin(a), z)
                       for a in np.linspace(0, 2 * np.pi, 12, endpoint=False)])
             for z in (0, 1, 2)]
    vertices = np.vstack(rings)
    faces = []
    for ring in range(2):
        for i in range(12):
            a, b = ring * 12 + i, ring * 12 + (i + 1) % 12
            faces.extend(((a, b, a + 12), (b, b + 12, a + 12)))
    return vertices, np.array(faces)


def soften_spec():
    return {
        "kind": "soften", "stage": "shape",
        "region": {"min_mm": [-1.5, -1.5, -0.5], "max_mm": [1.5, 1.5, 1.5]},
        "feather_mm": 0.3, "protect_regions": [],
        "max_displacement_mm": 0.2,
        "parameters": {"alpha": 0.4, "iterations": 10},
    }


def test_softening_removes_local_spike_without_changing_the_outer_surface():
    vertices, faces = grid()
    saved_vertices, saved_faces = vertices.copy(), faces.copy()
    result, report = ops.apply_operation(vertices, faces, soften_spec())
    assert 0.79 < result[12, 2] < 0.81
    outer = (np.abs(vertices[:, :2]) == 2).any(axis=1)
    assert np.array_equal(result[outer], vertices[outer])
    assert np.max(np.linalg.norm(result - vertices, axis=1)) <= 0.2000000001
    assert np.array_equal(vertices, saved_vertices)
    assert np.array_equal(faces, saved_faces)
    assert report["topology_changed"] is False
    assert report["manufacturing_status"] == "UNKNOWN"


def test_protection_stays_exact_across_many_iterations():
    vertices, faces = grid()
    # Keep one spike protected while the separate second spike can be softened.
    vertices = np.vstack((vertices, vertices + (6, 0, 0)))
    faces = np.vstack((faces, faces + 25))
    spec = soften_spec()
    spec["region"]["max_mm"][0] = 7.5
    spec["protect_regions"] = [{"min_mm": [-0.1, -0.1, 0.7], "max_mm": [0.1, 0.1, 1.1]}]
    result, report = ops.apply_operation(vertices, faces, spec)
    assert np.array_equal(result[12], vertices[12])
    assert report["protected_unchanged"]
    assert report["protected_vertices"] > 1
    assert report["fully_protected_triangles"] > 0
    assert result[37, 2] < vertices[37, 2]
    assert report["changed_vertices"] > 0


def test_inward_feather_is_zero_at_boundary_and_full_interior():
    vertices = np.array([[0, 1, 1], [0.25, 1, 1], [1, 1, 1], [-0.1, 1, 1]])
    weights, _ = ops.region_weights(
        vertices, {"min_mm": [0, 0, 0], "max_mm": [2, 2, 2]}, 1, [])
    assert weights[0] == 0
    assert 0 < weights[1] < 1
    assert weights[2] == 1
    assert weights[3] == 0


def test_root_thickening_enlarges_only_the_selected_root_cross_section():
    vertices, faces = cylinder()
    spec = {
        "kind": "thicken_root", "stage": "fabrication",
        "region": {"min_mm": [-2, -2, -0.1], "max_mm": [2, 2, 0.75]},
        "feather_mm": 0, "protect_regions": [],
        "max_displacement_mm": 0.2,
        "parameters": {"axis": [0, 0, 1], "center_mm": [0, 0, 0], "amount_mm": 0.5},
    }
    result, report = ops.apply_operation(vertices, faces, spec)
    assert np.allclose(np.linalg.norm(result[:12, :2], axis=1), 1.2)
    assert np.array_equal(result[12:], vertices[12:])
    assert np.array_equal(result[:, 2], vertices[:, 2])
    assert report["unselected_unchanged"]


def test_tip_retraction_uses_explicit_plane_and_respects_per_vertex_cap():
    vertices, faces = cylinder()
    spec = {
        "kind": "blunt_tip", "stage": "fabrication",
        "region": {"min_mm": [-2, -2, 1.5], "max_mm": [2, 2, 2.5]},
        "feather_mm": 0, "protect_regions": [],
        "max_displacement_mm": 0.15,
        "parameters": {"axis": [0, 0, 4], "tip_plane_mm": 1.7},
    }
    result, report = ops.apply_operation(vertices, faces, spec)
    assert np.allclose(result[24:, 2], 1.85)
    assert np.array_equal(result[:24], vertices[:24])
    assert np.array_equal(result[:, :2], vertices[:, :2])
    assert any("collapse triangles" in message for message in report["limitations"])


@pytest.mark.parametrize("field", ["region", "protect_regions", "max_displacement_mm", "feather_mm"])
def test_scoped_constraints_cannot_be_omitted(field):
    vertices, faces = grid()
    spec = soften_spec()
    del spec[field]
    with pytest.raises(ops.OperationError):
        ops.apply_operation(vertices, faces, spec)


def test_global_smoothing_and_empty_regions_are_rejected():
    vertices, faces = grid()
    spec = soften_spec()
    spec["region"] = {"min_mm": [-5, -5, -5], "max_mm": [5, 5, 5]}
    with pytest.raises(ops.OperationError, match="Global smoothing"):
        ops.apply_operation(vertices, faces, spec)
    spec["region"] = {"min_mm": [10, 10, 10], "max_mm": [11, 11, 11]}
    with pytest.raises(ops.OperationError, match="no editable"):
        ops.apply_operation(vertices, faces, spec)


def test_shape_cannot_follow_fabrication_and_caps_are_not_silently_cumulative():
    vertices, faces = grid()
    spec = soften_spec()
    second = copy.deepcopy(spec)
    second["stage"] = "fabrication"
    with pytest.raises(ops.OperationError, match="precede"):
        ops.apply_operations(vertices, faces, [second, spec])
    result, report = ops.apply_operations(vertices, faces, [spec, second])
    assert report["measured_total_max_displacement_mm"] > spec["max_displacement_mm"]
    assert "cumulative cap" in report["note"]
    assert result[12, 2] < 0.8


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True])
def test_invalid_displacement_is_rejected(value):
    vertices, faces = grid()
    spec = soften_spec()
    spec["max_displacement_mm"] = value
    with pytest.raises(ops.OperationError):
        ops.apply_operation(vertices, faces, spec)


def config():
    return {"schema_version": 1, "orientation": {"up_axis": "Z", "front_axis": "-Y"},
            "normalize": {"longest_mm": 160}}


def test_pipeline_requires_explicit_axes_and_face_crop():
    result = pipeline.validate_config(config())
    assert result["render"]["views"] == ["front", "side", "left45", "right45", "back", "bottom"]
    invalid = config()
    invalid["orientation"]["front_axis"] = "-Z"
    with pytest.raises(ValueError, match="parallel"):
        pipeline.validate_config(invalid)
    invalid = config()
    invalid["render"] = {"views": ["face"]}
    with pytest.raises(ValueError, match="explicit"):
        pipeline.validate_config(invalid)


def test_config_rejects_path_traversal_cameras_and_unknown_options():
    invalid = config()
    invalid["render"] = {"custom_views": {"../escape": {
        "camera_mm": [0, -300, 100], "target_mm": [0, 0, 100], "span_mm": 50}}}
    with pytest.raises(ValueError, match="filename-safe"):
        pipeline.validate_config(invalid)
    invalid = config()
    invalid["global_smooth"] = True
    with pytest.raises(ValueError, match="Unknown"):
        pipeline.validate_config(invalid)

def test_preserve_mm_is_explicit_and_cannot_be_mixed_or_reorient():
    selected = config()
    selected["normalize"] = {"preserve_mm": True}
    assert pipeline.validate_config(selected)["normalize"] == {"preserve_mm": True}
    for normalization in ({"preserve_mm": False}, {"preserve_mm": True, "longest_mm": 160}):
        selected = config()
        selected["normalize"] = normalization
        with pytest.raises(ValueError):
            pipeline.validate_config(selected)
    selected = config()
    selected["normalize"] = {"preserve_mm": True}
    selected["orientation"]["front_axis"] = "Y"
    with pytest.raises(ValueError, match="canonical"):
        pipeline.validate_config(selected)


def test_preserve_mm_keeps_existing_vertices_without_centering(monkeypatch):
    selected = config()
    selected["normalize"] = {"preserve_mm": True}
    points = np.array([[50, 90, -3], [53, 97, -3], [55, 97, 20]], dtype=float)
    monkeypatch.setattr(pipeline, "vertex_array", lambda obj: points.copy())
    monkeypatch.setattr(pipeline, "replace_vertices", lambda *args: pytest.fail("Preserve mode mutated vertices"))
    report = pipeline.orient_and_normalize(object(), selected)
    assert report["uniform_scale_factor"] == 1
    assert report["translation_mm"] == [0, 0, 0]
    assert report["imported_world_bounds"] == [[50, 90, -3], [55, 97, 20]]


def test_quarter_cameras_fit_all_cube_corners_and_face_crop_stays_explicit():
    render = pipeline.validate_config(config())["render"]
    render["views"].append("face")
    render["face"] = {"center_mm": [0, -3, 8], "span_mm": 4}
    specs = pipeline.camera_specs(render, [[-5, -5, 0], [5, 5, 10]])
    assert specs["left45"]["span_mm"] >= 10 * np.sqrt(2) * 1.199999
    assert specs["right45"]["span_mm"] == specs["left45"]["span_mm"]
    assert specs["front"]["span_mm"] == 12
    assert specs["bottom"]["span_mm"] == 12
    assert specs["face"]["span_mm"] == 4
    assert specs["face"]["target_mm"] == [0, -3, 8]


def test_denoise_must_be_an_explicit_boolean():
    selected = config()
    selected["render"] = {"denoise": "yes"}
    with pytest.raises(ValueError, match="boolean"):
        pipeline.validate_config(selected)


class FakeCycles:
    def __init__(self, denoisers, reject_assignment=False):
        from types import SimpleNamespace
        self.bl_rna = SimpleNamespace(properties={"denoiser": SimpleNamespace(
            enum_items=[SimpleNamespace(identifier=name) for name in denoisers])})
        self.use_denoising = True
        self.reject_assignment = reject_assignment
        self.assignment_count = 0
        self._denoiser = None

    @property
    def denoiser(self):
        return self._denoiser

    @denoiser.setter
    def denoiser(self, value):
        self.assignment_count += 1
        if self.reject_assignment:
            raise TypeError('enum "OPENIMAGEDENOISE" not found in ()')
        self._denoiser = value


def test_missing_denoiser_enum_disables_without_attempting_invalid_assignment():
    from types import SimpleNamespace
    cycles = FakeCycles([])
    layer = SimpleNamespace(cycles=SimpleNamespace(use_denoising=True))
    report = pipeline.configure_denoising(SimpleNamespace(cycles=cycles), [layer], True)
    assert report["enabled"] is False
    assert report["available_denoisers"] == []
    assert "unsupported" in report["fallback"]
    assert "raw CPU render" in report["fallback"]
    assert cycles.assignment_count == 0
    assert cycles.use_denoising is False
    assert layer.cycles.use_denoising is False


def test_runtime_rejecting_advertised_denoiser_is_explicit_nonfatal_fallback():
    from types import SimpleNamespace
    cycles = FakeCycles(["OPENIMAGEDENOISE"], reject_assignment=True)
    report = pipeline.configure_denoising(SimpleNamespace(cycles=cycles), [], True)
    assert report["enabled"] is False
    assert "TypeError" in report["fallback"]
    assert "Runtime rejected" in report["fallback"]
    assert cycles.use_denoising is False


def test_supported_cpu_denoiser_is_selected_but_gpu_only_is_not():
    from types import SimpleNamespace
    cpu = FakeCycles(["OPENIMAGEDENOISE", "OPTIX"])
    report = pipeline.configure_denoising(SimpleNamespace(cycles=cpu), [], True)
    assert report["enabled"] is True
    assert report["method"] == "OPENIMAGEDENOISE"
    assert report["fallback"] is None
    gpu = FakeCycles(["OPTIX"])
    report = pipeline.configure_denoising(SimpleNamespace(cycles=gpu), [], True)
    assert report["enabled"] is False
    assert gpu.assignment_count == 0


def test_protected_surface_crossing_box_with_no_inside_vertices_is_not_deformed():
    # Review regression: the XY point (0,.3) lies on triangle 0 at z=0, while
    # every triangle vertex is outside the protected box. Moving vertex 0 used
    # to raise the protected surface despite protected_unchanged=True.
    vertices = np.array([[2, 0, 0], [-2, 0, 0], [0, 2, 0], [2, 0, 2]], dtype=float)
    faces = np.array([[0, 1, 2], [0, 3, 1]])
    spec = {
        "kind": "soften", "stage": "shape",
        "region": {"min_mm": [1.9, -.1, -.1], "max_mm": [2.1, .1, .1]},
        "feather_mm": 0, "protect_regions": [
            {"min_mm": [-.2, .2, -.2], "max_mm": [.2, .6, .2]}],
        "max_displacement_mm": 1,
        "parameters": {"alpha": .5, "iterations": 1},
    }
    with pytest.raises(ops.OperationError, match="no editable"):
        ops.apply_operation(vertices, faces, spec)
    weights, protected = ops.region_weights(
        vertices, spec["region"], 0, spec["protect_regions"], faces=faces)
    assert protected[:3].all()
    assert weights[0] == 0


def test_crossing_protected_triangle_is_exact_while_another_region_changes():
    vertices = np.array([[2, 0, 0], [-2, 0, 0], [0, 2, 0], [2, 0, 2],
                         [5, 0, 1], [6, 0, 0], [5, 1, 0]], dtype=float)
    faces = np.array([[0, 1, 2], [0, 3, 1], [4, 5, 6]])
    spec = {
        "kind": "soften", "stage": "shape",
        "region": {"min_mm": [1.9, -.1, -.1], "max_mm": [5.1, .1, 1.1]},
        "feather_mm": 0, "protect_regions": [
            {"min_mm": [-.2, .2, -.2], "max_mm": [.2, .6, .2]}],
        "max_displacement_mm": 1,
        "parameters": {"alpha": .5, "iterations": 1},
    }
    result, report = ops.apply_operation(vertices, faces, spec)
    assert np.array_equal(result[faces[0]], vertices[faces[0]])
    barycentric = np.array([.425, .425, .15])
    assert np.array_equal(barycentric @ result[faces[0]], np.array([0., .3, 0.]))
    assert not np.array_equal(result[4], vertices[4])
    assert report["protected_vertices"] == 3
    assert report["fully_protected_triangles"] >= 1
    assert "Conservative" in report["protection_coverage"]
    assert report["protected_unchanged"] is True


def test_negative_world_transform_preserves_outward_signed_volume():
    class Mesh:
        def __init__(self):
            self.vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
            self.faces = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
            self.flip_calls = 0

        def transform(self, matrix):
            self.vertices = (np.c_[self.vertices, np.ones(len(self.vertices))] @ np.asarray(matrix).T)[:, :3]

        def flip_normals(self):
            self.faces = self.faces[:, ::-1]
            self.flip_calls += 1

        def update(self):
            pass

        def signed_volume(self):
            triangle = self.vertices[self.faces]
            return np.einsum("ij,ij->i", triangle[:, 0],
                             np.cross(triangle[:, 1], triangle[:, 2])).sum() / 6

    for x_scale, flips in ((-2, 1), (2, 0)):
        mesh = Mesh()
        matrix = np.diag([x_scale, 3, 4, 1]).astype(float)
        matrix[:3, 3] = [20, -10, 5]
        report = pipeline.apply_mesh_world_transform(mesh, matrix)
        assert mesh.flip_calls == flips
        assert mesh.signed_volume() == pytest.approx(4.0)
        assert report["winding_reversed"] is bool(flips)
    mesh = Mesh()
    with pytest.raises(ValueError, match="Singular"):
        pipeline.apply_mesh_world_transform(mesh, np.diag([0, 1, 1, 1]))
    assert mesh.signed_volume() == pytest.approx(1 / 6)


def resume_fixture(directory, complete=False):
    import json
    selected = config()
    selected["render"] = {"views": ["front", "side"]}
    # Keep a real operation spec in the config to catch any accidental re-execution.
    selected["operations"] = [soften_spec()]
    config_input = directory / "pipeline-config.input.json"
    pipeline.write_json(config_input, selected)
    resolved = pipeline.validate_config(copy.deepcopy(selected))
    config_resolved = directory / "pipeline-config.resolved.json"
    pipeline.write_json(config_resolved, resolved)
    for name in ("model.stl", "model.glb", "model.blend"):
        (directory / name).write_bytes(("synthetic hash-check fixture " + name).encode())
    bounds = [[0, 0, 0], [10, 10, 10]]
    specifications = pipeline.camera_specs(resolved["render"], bounds)
    report = {
        "schema_version": 1, "status": "COMPLETED" if complete else "EXPORTED",
        "stl_sha256": pipeline.digest(directory / "model.stl"),
        "config": pipeline.artifact(config_input), "resolved_config": pipeline.artifact(config_resolved),
        "exports": {name: pipeline.artifact(directory / name)
                    for name in ("model.stl", "model.glb", "model.blend")},
        "bounds_mm": bounds, "camera_specifications": specifications,
    }
    pipeline.write_json(directory / "model-report.json", report)
    previews = directory / "previews"
    previews.mkdir()
    views = {}
    for name in (["front", "side"] if complete else ["front"]):
        path = previews / (name + ".png")
        path.write_bytes(("synthetic rendered bytes " + name).encode())
        views[name] = {**specifications[name], "render_path": str(path),
                       "sha256": pipeline.digest(path), "stl_sha256": report["stl_sha256"], "proxy": False}
    render_report = {"schema_version": 1, "status": "COMPLETED" if complete else "RUNNING",
                     "actual_mesh": True, "stl_sha256": report["stl_sha256"], "views": views}
    pipeline.write_json(directory / "render-report.json", render_report)
    return report


def test_resume_plans_missing_views_without_mutating_or_reapplying_operations(tmp_path):
    resume_fixture(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    state = pipeline.inspect_render_resume(tmp_path)
    assert state["pending"] == ["side"]
    assert list(state["completed"]) == ["front"]
    assert state["config"]["operations"] == [soften_spec()]
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


@pytest.mark.parametrize("name, expected_error", [
    ("model.stl", "Export hash"), ("pipeline-config.input.json", "configuration hash"),
    ("pipeline-config.resolved.json", "configuration hash"), ("previews/front.png", "render hash"),
])
def test_resume_refuses_changed_geometry_config_or_recorded_images(tmp_path, name, expected_error):
    resume_fixture(tmp_path)
    (tmp_path / name).write_bytes(b"changed fixture")
    with pytest.raises(ValueError, match=expected_error):
        pipeline.inspect_render_resume(tmp_path)
    assert (tmp_path / name).read_bytes() == b"changed fixture"


def test_resume_refuses_supplied_config_changes_and_tracks_unrecorded_images(tmp_path):
    resume_fixture(tmp_path)
    alternate = tmp_path / "alternate.json"
    alternate.write_text('{"different": true}')
    with pytest.raises(ValueError, match="Supplied config"):
        pipeline.inspect_render_resume(tmp_path, alternate)
    orphan = tmp_path / "previews/side.png"
    orphan.write_bytes(b"interrupted render bytes")
    state = pipeline.inspect_render_resume(tmp_path)
    assert state["unrecorded_images"] == [orphan]
    assert orphan.read_bytes() == b"interrupted render bytes"


def test_resume_completed_bundle_is_a_noop_without_loading_geometry(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import sys
    resume_fixture(tmp_path, complete=True)
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace())
    monkeypatch.setattr(pipeline, "import_source", lambda *args: pytest.fail("A completed resume must not reimport"))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    report = pipeline.resume_render(tmp_path)
    assert report["status"] == "COMPLETED"
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
