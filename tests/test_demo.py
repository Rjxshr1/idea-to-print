"""The offline demo runs the real tools end to end on a synthetic figurine."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tested_demo", ROOT / "skills/idea-to-print/scripts/demo.py")
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)
HAS_GEOMETRY = all(importlib.util.find_spec(name) for name in ("numpy", "trimesh", "scipy", "rtree", "manifold3d"))


@pytest.mark.skipif(not HAS_GEOMETRY, reason="Install requirements-modeling.txt")
def test_demo_passes_all_geometry_checks_without_slicer(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(demo, "find_bambu", lambda: None)
    out = tmp_path / "demo"
    assert demo.main(["--out", str(out), "--slice"]) == 0
    report = json.loads((out / "job/work/geometry.json").read_text())
    assert report["overall"] == "PASS"
    assert all(check["status"] == "PASS" for check in report["checks"].values() if check["category"] == "geometry")
    job = json.loads((out / "job/job.json").read_text())
    assert job["workflow_v2"]["schema_version"] == 2
    printed = capsys.readouterr().out
    assert "Bambu Studio not found" in printed
    assert "printability_gate.py inspect" in printed  # Commands are shown so they can be rerun by hand.


def test_demo_refuses_an_existing_directory(tmp_path):
    (tmp_path / "demo").mkdir()
    with pytest.raises(SystemExit):
        demo.main(["--out", str(tmp_path / "demo")])


def test_printer_presets_cover_machine_process_filament_and_bed():
    for machine, process, filament, bed in demo.PRINTERS.values():
        assert machine and process and filament and len(bed) == 3
