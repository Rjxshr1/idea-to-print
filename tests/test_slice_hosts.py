"""Preset flattening and the Windows/macOS slice launcher; fake slicer only, no printer."""
import importlib.util
import json
import os
from pathlib import Path
import plistlib

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/3d-print-workflow/scripts"


def load(name):
    spec = importlib.util.spec_from_file_location("tested_" + name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pipeline():
    return load("slice_pipeline")


@pytest.fixture
def vendor(tmp_path):
    """A three-level preset tree shaped like Studio's profiles/<vendor> folder."""
    root = tmp_path / "vendor"
    presets = {
        "machine": [{"name": "common", "nozzle_diameter": ["0.4"], "bed": "plain"},
                    {"name": "printer", "inherits": "common", "bed": "textured"},
                    {"name": "printer 0.4", "inherits": "printer", "printable_height": "180"}],
        "process": [{"name": "process", "layer_height": "0.2"}],
        "filament": [{"name": "pla base", "filament_density": ["1.24"]},
                     {"name": "pla", "inherits": "pla base", "filament_type": ["PLA"]}],
    }
    for kind, items in presets.items():
        (root / kind).mkdir(parents=True)
        for item in items:
            (root / kind / (item["name"] + ".json")).write_text(json.dumps(item))
    return root


def test_inheritance_chain_is_flattened_child_first(pipeline, vendor):
    flat, chain = pipeline.resolve_profile("printer 0.4", "machine", [vendor])
    assert [link["name"] for link in chain] == ["printer 0.4", "printer", "common"]
    assert flat == {"name": "printer 0.4", "nozzle_diameter": ["0.4"], "bed": "textured", "printable_height": "180"}
    assert all(len(link["sha256"]) == 64 for link in chain)


def test_user_preset_file_resolves_against_vendor_folder(pipeline, vendor, tmp_path):
    user = tmp_path / "my pla.json"
    user.write_text(json.dumps({"name": "my pla", "inherits": "pla", "nozzle_temperature": ["215"]}))
    flat, chain = pipeline.resolve_profile(str(user), "filament", [vendor])
    assert flat["filament_density"] == ["1.24"] and flat["nozzle_temperature"] == ["215"]
    assert [link["name"] for link in chain] == ["my pla", "pla", "pla base"]


def test_missing_parent_and_loops_are_refused(pipeline, vendor):
    (vendor / "process" / "orphan.json").write_text(json.dumps({"name": "orphan", "inherits": "gone"}))
    with pytest.raises(ValueError, match="parent preset not found"):
        pipeline.resolve_profile("orphan", "process", [vendor])
    (vendor / "process" / "a.json").write_text(json.dumps({"name": "a", "inherits": "b"}))
    (vendor / "process" / "b.json").write_text(json.dumps({"name": "b", "inherits": "a"}))
    with pytest.raises(ValueError, match="loop"):
        pipeline.resolve_profile("a", "process", [vendor])


def test_cli_writes_standalone_profiles_with_provenance(pipeline, vendor, tmp_path):
    out = tmp_path / "profiles"
    assert pipeline.main(["resolve-profiles", "--out", str(out), "--search", str(vendor),
                          "--machine", "printer 0.4", "--process", "process", "--filament", "pla"]) == 0
    record = json.loads((out / "profile-resolution.json").read_text())
    for kind in ("machine", "process", "filament"):
        data = json.loads((out / (kind + ".json")).read_text())
        assert "inherits" not in data
        assert record["profiles"][kind]["resolved"]["sha256"] == pipeline.sha(out / (kind + ".json"))
    assert pipeline.main(["resolve-profiles", "--out", str(out), "--search", str(vendor),
                          "--machine", "printer 0.4", "--process", "process", "--filament", "pla"]) == 2


def test_prepare_refuses_unresolved_presets_before_creating_a_run(pipeline, vendor, tmp_path, write_stl, tetrahedron):
    mesh = write_stl(tmp_path / "input.stl", tetrahedron)
    target = tmp_path / "slice"
    with pytest.raises(ValueError, match="resolve-profiles"):
        pipeline.prepare(mesh, target, vendor / "machine/printer 0.4.json", vendor / "process/process.json",
                         vendor / "filament/pla.json", "slicer", "2.8.2.61", [180, 180, 180])
    assert not target.exists()


def fake_app(tmp_path, version="02.08.02.61"):
    app = tmp_path / "BambuStudio.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    with (app / "Contents/Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleShortVersionString": version}, stream)
    executable = app / "Contents/MacOS/BambuStudio"
    # Records its arguments and writes the files a successful native slice leaves behind.
    executable.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > args.txt\n"
                          "printf '{\"return_code\": 0}' > result.json\n")
    executable.chmod(0o755)
    return executable


def test_macos_bundle_version_is_read_from_info_plist(tmp_path):
    launcher = load("run_bambu_slice")
    assert launcher.bundle_version(fake_app(tmp_path)) == "02.08.02.61"
    loose = tmp_path / "loose-binary"
    loose.write_text("")
    with pytest.raises(ValueError, match=".app bundle"):
        launcher.bundle_version(loose)


@pytest.mark.skipif(os.name == "nt", reason="Uses a POSIX shell script as the fake slicer")
def test_macos_launch_runs_once_with_exact_arguments(pipeline, tmp_path, write_stl, tetrahedron, monkeypatch):
    launcher = load("run_bambu_slice")
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    executable = fake_app(tmp_path)
    profiles = []
    for name in ("machine", "process", "filament"):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps({"name": name}))
        profiles.append(path)
    run = tmp_path / "slice"
    pipeline.prepare(write_stl(tmp_path / "input.stl", tetrahedron), run, *profiles,
                     str(executable), "02.08.02.61", [180, 180, 180])
    result = launcher.run(run / "launch.json", timeout=60)
    assert result["exit_code"] == 0 and result["printer_actions"] == "none"
    launch = json.loads((run / "launch.json").read_text())
    assert (run / "args.txt").read_text().splitlines() == launch["arguments"]
    with pytest.raises(ValueError, match="already attempted"):
        launcher.run(run / "launch.json", timeout=60)


@pytest.mark.skipif(os.name == "nt", reason="Uses a POSIX shell script as the fake slicer")
def test_macos_launch_refuses_a_different_installed_version(pipeline, tmp_path, write_stl, tetrahedron, monkeypatch):
    launcher = load("run_bambu_slice")
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    executable = fake_app(tmp_path, version="02.09.00.01")
    profiles = []
    for name in ("machine", "process", "filament"):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps({"name": name}))
        profiles.append(path)
    run = tmp_path / "slice"
    pipeline.prepare(write_stl(tmp_path / "input.stl", tetrahedron), run, *profiles,
                     str(executable), "02.08.02.61", [180, 180, 180])
    with pytest.raises(ValueError, match="version differs"):
        launcher.run(run / "launch.json", timeout=60)
    assert not (run / "args.txt").exists()


def test_other_hosts_are_refused(monkeypatch, tmp_path):
    launcher = load("run_bambu_slice")
    monkeypatch.setattr(launcher.os, "name", "posix")
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    with pytest.raises(ValueError, match="Windows or macOS"):
        launcher.installed_version(tmp_path / "BambuStudio")
