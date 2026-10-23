#!/usr/bin/env python3
"""Offline end-to-end demo with a synthetic cat figurine. No network, no account, no printer.

Runs the same command-line tools an agent uses, printing each command so it can be
repeated by hand: image intake, the job ledger, the full 16-check geometry gate and
the basic mesh audit. With --slice and a local Bambu Studio (Windows or macOS), it
also flattens presets and slices offline. Nothing is ever sent to a printer.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys

SKILLS = Path(__file__).resolve().parents[2]
SCRIPTS = {
    "prepare_image": SKILLS / "idea-to-print/scripts/prepare_image.py",
    "workflow": SKILLS / "idea-to-print/scripts/workflow_v2.py",
    "gate": SKILLS / "3d-print-workflow/scripts/printability_gate.py",
    "audit": SKILLS / "3d-print-workflow/scripts/print_audit.py",
    "slice": SKILLS / "3d-print-workflow/scripts/slice_pipeline.py",
    "launch": SKILLS / "3d-print-workflow/scripts/run_bambu_slice.py",
}
# Bambu system preset names checked against Bambu Studio 02.08.02.61; bed is X, Y, Z in mm.
PRINTERS = {
    "a1mini": ("Bambu Lab A1 mini 0.4 nozzle", "0.20mm Standard @BBL A1M", "Bambu PLA Basic @BBL A1M", (180, 180, 180)),
    "a1": ("Bambu Lab A1 0.4 nozzle", "0.20mm Standard @BBL A1", "Bambu PLA Basic @BBL A1", (256, 256, 256)),
    "p1s": ("Bambu Lab P1S 0.4 nozzle", "0.20mm Standard @BBL X1C", "Bambu PLA Basic @BBL X1C", (256, 256, 256)),
    "x1c": ("Bambu Lab X1 Carbon 0.4 nozzle", "0.20mm Standard @BBL X1C", "Bambu PLA Basic @BBL X1C", (256, 256, 256)),
}
# Thresholds for this synthetic figurine only; a real job sets them from its own part.
DEMO_PROFILE = {
    "schema_version": 1, "id": "demo-cat-figurine", "units": "mm",
    "description": "Thresholds chosen for the synthetic demo cat; not defaults for real parts.",
    "build_volume_mm": [180, 180, 180], "reserve_total_mm": [10, 10, 5],
    "rules": {"wall_min_mm": 1.2, "detail_min_mm": 0.8, "connection_min_mm": 1.5},
    "thickness": {"max_thin_patch_area_mm2": 6},
    "base": {"min_contact_area_mm2": 500, "min_stability_margin_mm": 8, "min_tip_angle_deg": 15},
}


def step(title):
    print(f"\n== {title}", flush=True)


def run(name, *args, check=True):
    command = [sys.executable, str(SCRIPTS[name]), *map(str, args)]
    print("$ " + " ".join(shlex.quote(part) for part in command), flush=True)
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode not in (0, 3):
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"Step failed with exit code {result.returncode}")
    return result


def reference_image(path):
    """A flat drawing of the figurine, standing in for a user's photo or concept image."""
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (768, 768), "white")
    draw = ImageDraw.Draw(image)
    ink = (60, 60, 60)
    draw.rectangle((184, 650, 584, 690), fill=(200, 200, 200))
    draw.ellipse((254, 380, 514, 660), fill=ink)
    draw.ellipse((284, 190, 484, 390), fill=ink)
    draw.polygon([(300, 250), (320, 120), (370, 210)], fill=ink)
    draw.polygon([(468, 250), (448, 120), (398, 210)], fill=ink)
    draw.line([(510, 620), (600, 520), (590, 430)], fill=ink, width=26)
    image.save(path)


def figurine(path):
    """A seated cat on a round base, built as one watertight solid with manifold3d."""
    import manifold3d
    import trimesh
    solid = manifold3d.Manifold

    def ellipsoid(radius, centre, scale=(1, 1, 1)):
        return solid.sphere(radius, 64).scale(scale).translate(centre)

    def ear(side):
        return solid.cylinder(9, 4.5, 0.6, 32).rotate((-12, 18 * side, 0)).translate((5.5 * side, -6, 44))

    parts = [solid.cylinder(4, 26, 26, 96), ellipsoid(14, (0, 0, 18), (1.0, 0.8, 1.1)),
             ellipsoid(10, (0, -6, 38)), ear(-1), ear(1),
             solid.cylinder(26, 3.2, 2.4, 32).rotate((60, 0, 0)).translate((0, 8, 6))]
    cat = parts[0]
    for part in parts[1:]:
        cat = cat + part
    mesh = cat.to_mesh()
    trimesh.Trimesh(mesh.vert_properties[:, :3], mesh.tri_verts, process=False).export(path)


def find_bambu():
    """(executable, version, vendor preset folder) of a local Bambu Studio, or None."""
    if sys.platform == "darwin":
        app = Path("/Applications/BambuStudio.app")
        if app.is_dir():
            with (app / "Contents/Info.plist").open("rb") as stream:
                version = plistlib.load(stream).get("CFBundleShortVersionString")
            return app / "Contents/MacOS/BambuStudio", version, app / "Contents/Resources/profiles/BBL"
    elif os.name == "nt":
        root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Bambu Studio"
        executable = root / "bambu-studio.exe"
        if executable.is_file():
            sys.path.insert(0, str(SCRIPTS["launch"].parent))
            from run_bambu_slice import file_version
            return executable, file_version(executable), root / "resources/profiles/BBL"
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="idea-to-print-demo", help="New directory for the demo job (default: %(default)s)")
    parser.add_argument("--slice", action="store_true", help="Also slice offline with a local Bambu Studio, if installed")
    parser.add_argument("--printer", choices=sorted(PRINTERS), default="a1mini", help="Printer presets for --slice")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    if out.exists():
        parser.error(f"{out} already exists; choose a new --out")
    out.mkdir(parents=True)
    job = out / "job"

    step("1. Reference image -> new job (a photo or concept image would go here)")
    reference = out / "reference.png"
    reference_image(reference)
    run("prepare_image", "--image", reference, "--job", job, "--target-mm", 52, "--brief", "Seated cat on a round base")
    run("workflow", "init", "--job", job)
    print("next:", json.loads(run("workflow", "next", "--job", job).stdout))

    step("2. Model (the agent would generate this with Hunyuan3D and refine it in Blender)")
    mesh = job / "outputs" / "demo-cat.stl"
    mesh.parent.mkdir(parents=True, exist_ok=True)
    figurine(mesh)
    print(f"wrote {mesh}")

    step("3. Geometry gate: all 16 checks")
    profile = job / "work" / "printability-profile.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(json.dumps(DEMO_PROFILE, indent=2) + "\n", encoding="utf-8")
    report_path = job / "work" / "geometry.json"
    run("gate", "inspect", "--mesh", mesh, "--profile", profile, "--out", report_path, check=False)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for key, check in report["checks"].items():
        if check["category"] == "geometry":
            print(f"  {check['status']:<7} {key}")
    print(f"overall: {report['overall']}")

    step("4. Basic mesh audit")
    audit = json.loads(run("audit", "mesh", mesh).stdout)
    print("dimensions_mm:", [round(x, 1) for x in audit["dimensions_mm"]],
          "| volume_cm3:", round(audit["signed_volume_mm3"] / 1000, 1))

    sliced = None
    if args.slice:
        step(f"5. Offline slice ({args.printer}); never sent to a printer")
        found = find_bambu()
        if not found:
            print("Bambu Studio not found in the default location; skipped.")
        else:
            executable, version, vendor = found
            machine, process, filament, bed = PRINTERS[args.printer]
            profiles = job / "work" / "profiles"
            run("slice", "resolve-profiles", "--out", profiles, "--search", vendor,
                "--machine", machine, "--process", process, "--filament", filament)
            slice_run = job / "work" / "slice"
            run("slice", "prepare", "--mesh", mesh, "--out", slice_run, "--machine", profiles / "machine.json",
                "--process", profiles / "process.json", "--filament", profiles / "filament.json",
                "--slicer", executable, "--slicer-version", version, "--bed", *bed)
            run("launch", "--launch", slice_run / "launch.json")
            sliced = json.loads(run("slice", "collect", "--run", slice_run).stdout)
            metadata = sliced["audit"]["metadata"]
            print(f"slice {sliced['status']}: {metadata.get('weight')} g, "
                  f"{int(float(metadata.get('prediction') or 0)) // 60} min -> {sliced['package']['path']}")

    step("Done")
    print(f"Everything is in {out}")
    print("Real jobs use the same commands with their own image, model and thresholds;")
    print("see skills/idea-to-print/references/workflow-v2.md.")
    return 0 if report["overall"] == "PASS" and (not args.slice or sliced is None or sliced["status"] == "PASS") else 3


if __name__ == "__main__":
    raise SystemExit(main())
