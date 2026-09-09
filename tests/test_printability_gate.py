"""Deterministic synthetic geometry and stale-artifact regressions; no hardware."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "skills/3d-print-workflow/scripts/printability_gate.py"
SPEC = importlib.util.spec_from_file_location("printability_gate", SCRIPT)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
HAS_GEOMETRY = all(importlib.util.find_spec(name) is not None for name in ("numpy", "trimesh", "scipy"))
BASIC = ["input_integrity", "watertight", "edge_manifold", "vertex_manifold", "winding",
         "degenerate_faces", "duplicate_faces", "components", "outward_normals", "build_volume"]


def basic_profile():
    return {"schema_version": 1, "id": "synthetic-basic-only", "units": "mm",
            "required_checks": BASIC.copy(), "build_volume_mm": [180, 180, 180],
            "reserve_total_mm": [10, 10, 5], "rules": {"wall_min_mm": 1.2}}


class TestProfileAndUnavailableInput(unittest.TestCase):
    def test_rejects_result_override_and_bad_thresholds(self):
        for edit in ({"approved": True}, {"units": "m"}, {"max_components": True},
                     {"rules": {"wall_min_mm": -1}}, {"required_checks": ["made_up"]},
                     {"required_checks": ["input_integrity", "appearance_fidelity"]},
                     {"reserve_total_mm": [180, 0, 0]}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                gate.validate_profile(dict(basic_profile(), **edit))

    def test_missing_dependency_never_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "anything.stl"
            path.write_bytes(b"input exists, importer never runs")
            with mock.patch.object(gate, "dependency_modules", side_effect=ImportError("numpy unavailable")):
                report = gate.inspect(path, basic_profile())
            self.assertEqual(report["overall"], "UNKNOWN")
            self.assertEqual(report["exit_code"], 3)
            self.assertIn("dependency", report["checks"]["input_integrity"]["method"])

    def test_old_hash_fails_before_any_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "changed.stl"
            path.write_bytes(b"new mesh bytes")
            profile = dict(basic_profile(), expected_mesh_sha256="0" * 64)
            with mock.patch.object(gate, "dependency_modules") as importer:
                report = gate.inspect(path, profile)
                importer.assert_not_called()
            self.assertEqual(report["overall"], "FAIL")
            self.assertEqual(report["failed_checks"], ["input_integrity"])

    def test_absent_file_is_unknown_and_cli_writes_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, out = Path(tmp) / "profile.json", Path(tmp) / "report.json"
            config.write_text(json.dumps(basic_profile()))
            result = subprocess.run([sys.executable, str(SCRIPT), "inspect", "--mesh", str(Path(tmp) / "absent.stl"),
                                     "--profile", str(config), "--out", str(out)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 3, result.stderr)
            report = json.loads(out.read_text())
            self.assertEqual(report["overall"], "UNKNOWN")
            self.assertEqual(report["profile"]["sha256"], gate.file_hash(config))

    def test_cli_cannot_overwrite_source_or_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            mesh, config = Path(tmp) / "model.stl", Path(tmp) / "profile.json"
            mesh.write_bytes(b"preserved model bytes")
            config.write_text(json.dumps(basic_profile()))
            for output in (mesh, config):
                before = output.read_bytes()
                result = subprocess.run([sys.executable, str(SCRIPT), "inspect", "--mesh", str(mesh),
                                         "--profile", str(config), "--out", str(output)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 3)
                self.assertEqual(output.read_bytes(), before)


@unittest.skipUnless(HAS_GEOMETRY, "Install requirements-modeling.txt for synthetic geometry regressions")
class TestSyntheticMeshes(unittest.TestCase):
    def setUp(self):
        self.np, self.trimesh = gate.dependency_modules()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "model.stl"

    def box(self, extents=(10, 12, 14)):
        result = self.trimesh.creation.box(extents=extents)
        result.apply_translation([0, 0, extents[2] / 2])
        return result

    def run_mesh(self, mesh, profile=None):
        mesh.export(self.path)
        before = gate.file_hash(self.path)
        result = gate.inspect(self.path, profile or basic_profile())
        self.assertEqual(before, gate.file_hash(self.path), "Inspection mutated source geometry")
        return result

    def test_closed_box_basic_pass_and_domains_are_separate(self):
        report = self.run_mesh(self.box())
        self.assertEqual(report["overall"], "PASS", report)
        self.assertEqual(report["overall_scope"], "geometry")
        self.assertEqual(report["checks"]["vertex_manifold"]["status"], "PASS")
        self.assertEqual(report["checks"]["appearance_fidelity"]["category"], "appearance")
        self.assertFalse(report["checks"]["appearance_fidelity"]["required"])
        self.assertEqual(report["checks"]["support_removal"]["status"], "UNKNOWN")

    def test_full_geometry_never_passes_from_topology_alone(self):
        profile = basic_profile()
        profile.pop("required_checks")
        report = self.run_mesh(self.box(), profile)
        self.assertEqual(report["overall"], "UNKNOWN")
        self.assertIn("self_intersection", report["unknown_required_checks"])
        self.assertIn("minimum_wall", report["unknown_required_checks"])
        self.assertIn("stability", report["unknown_required_checks"])

    def test_open_hole_fails(self):
        box = self.box()
        opened = self.trimesh.Trimesh(box.vertices, box.faces[:-1], process=False)
        report = self.run_mesh(opened)
        self.assertEqual(report["overall"], "FAIL")
        self.assertEqual(report["checks"]["watertight"]["measured"]["boundary_edges"], 3)

    def test_two_solids_are_disconnected_not_automatically_joined(self):
        first, second = self.box(), self.box((2, 2, 2))
        second.apply_translation([30, 0, 0])
        report = self.run_mesh(self.trimesh.util.concatenate((first, second)))
        self.assertEqual(report["checks"]["components"]["status"], "FAIL")
        self.assertEqual(report["checks"]["components"]["measured"]["count"], 2)

    def test_vertex_only_contact_is_not_manifold(self):
        first, second = self.box((2, 2, 2)), self.box((2, 2, 2))
        second.apply_translation([2, 2, 2])
        report = self.run_mesh(self.trimesh.util.concatenate((first, second)))
        self.assertEqual(report["checks"]["edge_manifold"]["status"], "PASS")
        self.assertEqual(report["checks"]["vertex_manifold"]["status"], "FAIL")
        self.assertEqual(report["checks"]["vertex_manifold"]["measured"]["nonmanifold_vertices"], 1)

    def test_reserved_build_space_is_enforced(self):
        report = self.run_mesh(self.box((171, 20, 20)))
        self.assertEqual(report["checks"]["build_volume"]["status"], "FAIL")
        self.assertEqual(report["checks"]["build_volume"]["measured"]["remaining_mm"][0], -1)

    def test_duplicate_and_degenerate_faces_are_preserved_and_rejected(self):
        box = self.box()
        faces = self.np.vstack((box.faces, box.faces[0], [0, 0, 1]))
        report = self.run_mesh(self.trimesh.Trimesh(box.vertices, faces, process=False))
        self.assertEqual(report["checks"]["duplicate_faces"]["measured"]["count"], 1)
        self.assertEqual(report["checks"]["degenerate_faces"]["measured"]["count"], 1)
        self.assertEqual(report["overall"], "FAIL")

    def test_inconsistent_and_inverted_normals(self):
        box = self.box()
        box.faces[0] = box.faces[0][::-1]
        report = self.run_mesh(box)
        self.assertEqual(report["checks"]["winding"]["status"], "FAIL")
        box = self.box()
        box.invert()
        report = self.run_mesh(box)
        self.assertEqual(report["checks"]["winding"]["status"], "PASS")
        self.assertEqual(report["checks"]["outward_normals"]["status"], "FAIL")

    def test_excluding_known_failure_cannot_turn_it_into_pass(self):
        profile = basic_profile()
        profile["required_checks"] = ["input_integrity"]
        report = self.run_mesh(self.box((181, 20, 20)), profile)
        self.assertEqual(report["overall"], "FAIL")

    def test_hash_change_during_inspection_invalidates_result(self):
        self.box().export(self.path)
        before = gate.file_hash(self.path)
        with mock.patch.object(gate, "file_hash", side_effect=[before, "f" * 64]):
            report = gate.inspect(self.path, basic_profile())
        self.assertEqual(report["checks"]["input_integrity"]["status"], "FAIL")

    def test_missing_optional_graph_backend_leaves_topology_unknown(self):
        self.box().export(self.path)
        actual_import = gate.importlib.import_module

        def unavailable(name, *args, **kwargs):
            if name.startswith("scipy.sparse"):
                raise ImportError("scipy sparse unavailable")
            return actual_import(name, *args, **kwargs)

        with mock.patch.object(gate.importlib, "import_module", side_effect=unavailable):
            report = gate.inspect(self.path, basic_profile())
        self.assertEqual(report["overall"], "UNKNOWN")
        self.assertEqual(report["checks"]["components"]["status"], "UNKNOWN")
        self.assertEqual(report["checks"]["vertex_manifold"]["status"], "UNKNOWN")

    @unittest.skipUnless(importlib.util.find_spec("rtree"), "Optional rtree sampling backend")
    def test_thickness_sampling_finds_thin_slab_but_never_proves_thick_slab(self):
        profile = basic_profile()
        profile["required_checks"].append("minimum_wall")
        profile["thickness_sampling"] = {"enabled": True, "max_samples": 64}
        thin = self.run_mesh(self.box((20, 20, .6)), profile)
        self.assertEqual(thin["checks"]["minimum_wall"]["status"], "FAIL", thin)
        self.assertGreater(thin["checks"]["minimum_wall"]["measured"]["candidate_count"], 0)
        thick = self.run_mesh(self.box((20, 20, 5)), profile)
        self.assertEqual(thick["checks"]["minimum_wall"]["status"], "UNKNOWN")
        self.assertEqual(thick["overall"], "UNKNOWN")

    @unittest.skipUnless(importlib.util.find_spec("rtree"), "Optional rtree sampling backend")
    def test_region_wall_rule_changes_risk_without_rewriting_geometry(self):
        profile = basic_profile()
        profile["rules"]["wall_min_mm"] = .5
        profile["thickness_sampling"] = {"enabled": True, "max_samples": 64}
        base = self.run_mesh(self.box((20, 20, .6)), profile)
        self.assertEqual(base["checks"]["minimum_wall"]["status"], "UNKNOWN")
        profile["regions"] = [{"id": "reinforced-area", "bounds_mm": [[-20, -20, -1], [20, 20, 1]], "rules": {"wall_min_mm": 1.2}}]
        reinforced = self.run_mesh(self.box((20, 20, .6)), profile)
        self.assertEqual(reinforced["checks"]["minimum_wall"]["status"], "FAIL")
        self.assertIn("reinforced-area", reinforced["checks"]["minimum_wall"]["measured"]["candidates"][0]["regions"])

    def test_cli_exit_codes_match_saved_reports(self):
        config, out = Path(self.tmp.name) / "profile.json", Path(self.tmp.name) / "report.json"
        for extents, required, code in [((10, 12, 14), BASIC, 0), ((181, 12, 14), BASIC, 2),
                                         ((10, 12, 14), list(gate.GEOMETRY_CHECKS), 3)]:
            self.box(extents).export(self.path)
            profile = basic_profile()
            profile["required_checks"] = required
            config.write_text(json.dumps(profile))
            result = subprocess.run([sys.executable, str(SCRIPT), "inspect", "--mesh", str(self.path),
                                     "--profile", str(config), "--out", str(out)], capture_output=True, text=True)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertEqual(json.loads(out.read_text())["exit_code"], code)


if __name__ == "__main__":
    unittest.main()
