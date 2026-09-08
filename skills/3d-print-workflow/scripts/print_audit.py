#!/usr/bin/env python3
"""Offline checks for millimetre STL meshes, proportional fit and Bambu slices.
No printer writes. Mesh topology checks do not establish self-intersection,
minimum wall thickness, stability, or practical support removal.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile


def fit(dimensions, volume, clearance):
    if any(len(values) != 3 for values in (dimensions, volume, clearance)):
        raise ValueError("Dimensions, build volume and clearance require exactly three axes")
    if any(not math.isfinite(x) or x <= 0 for x in dimensions + volume):
        raise ValueError("Dimensions and build volume must be finite and positive")
    if any(not math.isfinite(x) or x < 0 for x in clearance):
        raise ValueError("Clearance must be finite and nonnegative")
    available = [v - c for v, c in zip(volume, clearance)]
    if min(available) <= 0:
        raise ValueError("Clearance consumes build volume")
    factors = [v / d for v, d in zip(available, dimensions)]
    factor = min(factors)
    return {"scale_factor": factor, "scale_percent": factor * 100,
            "dimensions_mm": [x * factor for x in dimensions],
            "limiting_axis": "XYZ"[factors.index(factor)],
            "clearance_total_per_axis_mm": clearance,
            "scope": "Object bounds only, fixed orientation; supports, brim, purge areas and exclusions still require slicing"}


def stl_triangles(path):
    data = Path(path).read_bytes()
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if len(data) == 84 + count * 50:
            return [[tuple(row[j:j+3]) for j in (3, 6, 9)]
                    for row in struct.iter_unpack("<12fH", data[84:])]
    text = data.decode("ascii")
    vertices = [tuple(map(float, m)) for m in re.findall(
        r"\bvertex\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)", text)]
    if not vertices or len(vertices) % 3:
        raise ValueError("Invalid or empty STL")
    return [vertices[i:i+3] for i in range(0, len(vertices), 3)]


def mesh_report(path):
    triangles = stl_triangles(path)
    if not triangles:
        raise ValueError("Empty mesh")
    vertex_ids, edges, faces = {}, defaultdict(list), []
    mins, maxs = [math.inf]*3, [-math.inf]*3
    signed_volume, area, degenerate = 0., 0., 0
    for face_id, tri in enumerate(triangles):
        ids = []
        for p in tri:
            if any(not math.isfinite(x) for x in p):
                raise ValueError("Nonfinite coordinate")
            key = tuple(round(x, 6) for x in p)
            ids.append(vertex_ids.setdefault(key, len(vertex_ids)))
            for axis in range(3):
                mins[axis] = min(mins[axis], p[axis])
                maxs[axis] = max(maxs[axis], p[axis])
        a, b, c = tri
        u, v = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
        cross = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
        size = math.sqrt(sum(x*x for x in cross)) / 2
        area += size
        if size < 1e-10 or len(set(ids)) < 3:
            degenerate += 1
        signed_volume += sum(a[i]*cross[i] for i in range(3))/6
        faces.append(tuple(sorted(ids)))
        for start, end in zip(ids, ids[1:]+ids[:1]):
            edges[tuple(sorted((start, end)))].append((face_id, start < end))
    parents = list(range(len(triangles)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    for occurrences in edges.values():
        for other, _ in occurrences[1:]:
            parents[root(other)] = root(occurrences[0][0])
    boundary = sum(len(v) == 1 for v in edges.values())
    nonmanifold = sum(len(v) > 2 for v in edges.values())
    winding = sum(len(v) == 2 and v[0][1] == v[1][1] for v in edges.values())
    duplicate = sum(n-1 for n in Counter(faces).values() if n > 1)
    return {"file": str(Path(path).resolve()), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "units_assumed": "mm (STL carries no units)", "triangles": len(triangles),
            "vertices_welded_at_1e_6_mm": len(vertex_ids), "bounds_mm": [mins, maxs],
            "dimensions_mm": [maxs[i]-mins[i] for i in range(3)],
            "edge_connected_components": len({root(i) for i in range(len(triangles))}),
            "boundary_edges": boundary, "nonmanifold_edges": nonmanifold,
            "inconsistent_winding_edges": winding, "degenerate_triangles": degenerate,
            "duplicate_triangles": duplicate, "signed_volume_mm3": signed_volume, "surface_area_mm2": area,
            "basic_topology_pass": not any((boundary, nonmanifold, winding, degenerate, duplicate)) and signed_volume > 0,
            "not_checked": ["self intersections", "vertex manifoldness", "minimum thickness", "nested shell orientation", "stability", "supports and slicing"]}


def slice_report(path, plate=1):
    with zipfile.ZipFile(path) as z:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
        root = ET.fromstring(z.read("Metadata/slice_info.config"))
        selected = None
        for candidate in root.findall("plate"):
            values = {x.attrib["key"]: x.attrib["value"] for x in candidate.findall("metadata")}
            if values.get("index") == str(plate):
                selected, metadata = candidate, values
                break
        if selected is None:
            raise ValueError(f"Plate {plate} is absent")
        name = f"Metadata/plate_{plate}.gcode"
        gcode = z.read(name)
        actual = hashlib.md5(gcode).hexdigest()
        declared = z.read(name + ".md5").decode().strip().lower()
        commands = [line.split(";")[0].strip() for line in gcode.decode().splitlines()
                    if re.match(r"^M(?:140|190)\s", line)]
        ranges = [x.attrib for x in selected.findall("layer_filament_lists/layer_filament_list")]
        return {"file": str(Path(path).resolve()), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                "plate": plate, "metadata": metadata, "filaments": [x.attrib for x in selected.findall("filament")],
                "warnings": [x.attrib for x in selected.findall("warning")],
                "objects": [x.attrib for x in selected.findall("object")], "layer_ranges": ranges,
                "md5_actual": actual, "md5_declared": declared, "md5_match": actual == declared,
                "bed_commands": commands,
                "settings": {k: cfg.get(k) for k in ["printer_model", "printer_settings_id", "nozzle_diameter",
                    "curr_bed_type", "filament_type", "filament_colour", "textured_plate_temp",
                    "textured_plate_temp_initial_layer", "temperature_vitrification", "layer_height",
                    "wall_loops", "sparse_infill_density", "enable_support", "support_type", "support_top_z_distance"]},
                "scope": "One selected plate. Warnings preserved. No physical preflight or automatic print approval."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("fit")
    p.add_argument("--dimensions", nargs=3, type=float, required=True)
    p.add_argument("--volume", nargs=3, type=float, required=True)
    p.add_argument("--clearance", nargs=3, type=float, default=[0, 0, 0], help="Total reserve per axis, not per side")
    p.add_argument("--out")
    p = sub.add_parser("mesh")
    p.add_argument("file")
    p.add_argument("--out")
    p = sub.add_parser("slice")
    p.add_argument("file")
    p.add_argument("--plate", type=int, default=1)
    p.add_argument("--out")
    args = parser.parse_args()
    try:
        if args.command == "fit":
            result = fit(args.dimensions, args.volume, args.clearance)
            code = 0
        elif args.command == "mesh":
            result = mesh_report(args.file)
            code = 0 if result["basic_topology_pass"] else 2
        else:
            result = slice_report(args.file, args.plate)
            code = 0 if result["md5_match"] and result["metadata"].get("outside") == "false" else 2
        output = json.dumps(result, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(output + "\n", encoding="utf-8")
        print(output)
        return code
    except (ValueError, OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
