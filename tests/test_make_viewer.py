"""Turntable/AR page generation; synthetic meshes, no network."""
import base64
import importlib.util
import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tested_viewer", ROOT / "skills/printable-modeling/scripts/make_viewer.py")
viewer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(viewer)
HAS_TRIMESH = importlib.util.find_spec("trimesh") is not None
pytestmark = pytest.mark.skipif(not HAS_TRIMESH, reason="Install requirements-modeling.txt")


@pytest.fixture
def stl(tmp_path):
    import trimesh
    mesh = trimesh.creation.box(extents=(40, 30, 20))
    mesh.apply_translation([100, 50, 10])  # Off-centre, standing on z=0.
    path = tmp_path / "part.stl"
    mesh.export(path)
    return path


def embedded_glb(page):
    match = re.search(r'src="data:model/gltf-binary;base64,([^"]+)"', page)
    assert match
    return base64.b64decode(match.group(1))


def test_page_embeds_a_real_size_y_up_glb(stl, tmp_path):
    import trimesh
    out = tmp_path / "part.html"
    result = viewer.make(stl, out, title="Bracket <v1>")
    page = out.read_text(encoding="utf-8")
    assert result["embedded"] and result["size_mm"] == [40.0, 30.0, 20.0] and result["volume_cm3"] == 24.0
    data = embedded_glb(page)
    assert data[:4] == b"glTF"
    scene = trimesh.load(trimesh.util.wrap_as_stream(data), file_type="glb", force="mesh")
    # Metres, centred on the floor, height along +Y.
    assert scene.extents == pytest.approx([0.04, 0.02, 0.03], abs=1e-6)
    assert scene.bounds[0][1] == pytest.approx(0, abs=1e-9)
    assert scene.bounds.mean(axis=0)[[0, 2]] == pytest.approx([0, 0], abs=1e-9)
    assert viewer.MODEL_VIEWER in page and 'ar-scale="fixed"' in page and "quick-look" in page
    assert "Bracket &lt;v1&gt;" in page and "<v1>" not in page
    assert "40 × 30 × 20 mm" in page


def test_external_glb_and_no_overwrite(stl, tmp_path):
    out = tmp_path / "part.html"
    result = viewer.make(stl, out, external=True)
    assert not result["embedded"] and Path(result["glb"]).read_bytes()[:4] == b"glTF"
    assert 'src="part.glb"' in out.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="overwrite"):
        viewer.make(stl, out)


def test_bad_inputs_are_refused(stl, tmp_path):
    with pytest.raises(ValueError, match=".html"):
        viewer.make(stl, tmp_path / "part.htm")
    with pytest.raises(ValueError, match="#RRGGBB"):
        viewer.make(stl, tmp_path / "part.html", color="white")


def test_cli_reports_errors_as_json(tmp_path, capsys):
    assert viewer.main(["--model", str(tmp_path / "missing.stl"), "--output", str(tmp_path / "x.html")]) == 2
    assert "error" in json.loads(capsys.readouterr().err)
