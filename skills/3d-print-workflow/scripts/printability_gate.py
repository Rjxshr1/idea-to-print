#!/usr/bin/env python3
"""Conservative offline STL gate. PASS certifies only required, implemented checks.

No mesh repair, evidence overrides, slicer or printer operations are performed.
STL coordinates must be millimetres. Exit 0=PASS, 2=FAIL, 3=UNKNOWN.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys


CHECK_IDS = (
    "input_integrity", "watertight", "edge_manifold", "vertex_manifold",
    "winding", "degenerate_faces", "duplicate_faces", "components",
    "outward_normals", "build_volume", "self_intersection", "minimum_wall",
    "minimum_detail", "minimum_connection", "base_contact", "stability",
    "appearance_fidelity", "slicer_islands", "support_removal",
)
EXIT_CODES = {"PASS": 0, "FAIL": 2, "UNKNOWN": 3}
EXTERNAL_CATEGORIES = {"appearance_fidelity": "appearance", "slicer_islands": "slice", "support_removal": "slice"}
GEOMETRY_CHECKS = tuple(key for key in CHECK_IDS if key not in EXTERNAL_CATEGORIES)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dependency_modules():
    return importlib.import_module("numpy"), importlib.import_module("trimesh")


def finite_number(value, label, minimum=0, strictly_positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if value < minimum or (strictly_positive and value <= 0):
        raise ValueError(f"{label} is out of range")
    return value


def vector(value, label, positive=False):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label} requires three coordinates")
    return [finite_number(x, label, strictly_positive=positive) for x in value]


def validate_profile(profile):
    if not isinstance(profile, dict) or profile.get("schema_version") != 1:
        raise ValueError("Profile schema_version must be 1")
    allowed = {"schema_version", "id", "description", "units", "required_checks",
               "build_volume_mm", "reserve_total_mm", "max_components",
               "degenerate_area_mm2", "expected_mesh_sha256", "rules", "regions",
               "thickness_sampling", "process", "scope"}
    unexpected = set(profile) - allowed
    if unexpected:
        raise ValueError(f"Unknown profile keys (no result overrides allowed): {sorted(unexpected)}")
    if profile.get("units") != "mm":
        raise ValueError("STL profile must explicitly declare units=mm")
    required = profile.get("required_checks", list(GEOMETRY_CHECKS))
    if not isinstance(required, list) or not required or len(set(required)) != len(required):
        raise ValueError("required_checks must be a nonempty list of unique check IDs")
    if set(required) - set(CHECK_IDS):
        raise ValueError(f"Unknown required checks: {sorted(set(required) - set(CHECK_IDS))}")
    if set(required) & set(EXTERNAL_CATEGORIES):
        raise ValueError("Appearance and slice reviews are separate ledger stages, not required geometry checks")
    if "input_integrity" not in required:
        raise ValueError("input_integrity must be required")
    build = vector(profile["build_volume_mm"], "build_volume_mm", positive=True)
    reserve = vector(profile.get("reserve_total_mm", [0, 0, 0]), "reserve_total_mm")
    if any(r >= b for r, b in zip(reserve, build)):
        raise ValueError("reserve_total_mm consumes the build volume")
    maximum = profile.get("max_components", 1)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise ValueError("max_components must be a positive integer")
    finite_number(profile.get("degenerate_area_mm2", 1e-12), "degenerate_area_mm2")
    expected = profile.get("expected_mesh_sha256")
    if expected is not None and (not isinstance(expected, str) or len(expected) != 64 or
                                 any(c not in "0123456789abcdef" for c in expected)):
        raise ValueError("expected_mesh_sha256 must be a lowercase SHA256")
    rule_names = {"wall_min_mm", "detail_min_mm", "connection_min_mm"}

    def validate_rules(rules, label):
        if not isinstance(rules, dict) or set(rules) - rule_names:
            raise ValueError(f"{label} supports only {sorted(rule_names)}")
        for key, value in rules.items():
            if value is not None:
                finite_number(value, key, strictly_positive=True)

    validate_rules(profile.get("rules", {}), "rules")
    regions = profile.get("regions", [])
    if not isinstance(regions, list):
        raise ValueError("regions must be a list")
    region_ids = []
    for region in regions:
        if not isinstance(region, dict) or set(region) - {"id", "bounds_mm", "rules"}:
            raise ValueError("Each region requires id, bounds_mm and rules")
        if not isinstance(region.get("id"), str) or not region["id"]:
            raise ValueError("Region id must be nonempty")
        region_ids.append(region["id"])
        bounds = region.get("bounds_mm")
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError("Region bounds_mm must contain minimum and maximum XYZ")
        for row in bounds:
            if not isinstance(row, list) or len(row) != 3:
                raise ValueError("Region bounds_mm must be 2x3")
            for value in row:
                finite_number(value, "region bound", minimum=-math.inf)
        if any(a >= b for a, b in zip(*bounds)):
            raise ValueError("Region minimum must be below maximum on every axis")
        validate_rules(region.get("rules", {}), "region rules")
    if len(set(region_ids)) != len(region_ids):
        raise ValueError("Region ids must be unique")
    sampling = profile.get("thickness_sampling", {})
    if not isinstance(sampling, dict) or set(sampling) - {"enabled", "max_samples", "max_candidates"}:
        raise ValueError("Invalid thickness_sampling configuration")
    if not isinstance(sampling.get("enabled", False), bool):
        raise ValueError("thickness_sampling.enabled must be boolean")
    for key, default, limit in (("max_samples", 128, 4096), ("max_candidates", 32, 4096)):
        value = sampling.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= limit:
            raise ValueError(f"thickness_sampling.{key} must be 1..{limit}")
    return profile


def new_report(mesh_path, profile_path, profile):
    required = set(profile.get("required_checks", GEOMETRY_CHECKS))
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mesh": {"path": str(Path(mesh_path).resolve()), "sha256": None, "units": "mm"},
        "profile": {"path": str(Path(profile_path).resolve()) if profile_path else None,
                    "sha256": file_hash(profile_path) if profile_path else None,
                    "id": profile.get("id"), "configuration": profile},
        "scope": "Offline fixed-orientation geometry gate; not print authorization or a manufacture certificate. Appearance and slice review are separate workflow stages.",
        "overall_scope": "geometry",
        "checks": {key: {"status": "UNKNOWN", "required": key in required,
                         "category": EXTERNAL_CATEGORIES.get(key, "geometry"),
                         "method": "not executed", "measured": {}, "thresholds": {},
                         "limitations": ["No verified result is available."]} for key in CHECK_IDS},
    }


def set_check(report, key, status, method, measured=None, thresholds=None, limitations=None):
    report["checks"][key].update(status=status, method=method, measured=measured or {},
                                  thresholds=thresholds or {}, limitations=limitations or [])


def finish(report):
    checks = report["checks"]
    failed = [key for key, item in checks.items() if item["category"] == "geometry" and item["status"] == "FAIL"]
    unknown = [key for key, item in checks.items() if item["required"] and item["status"] == "UNKNOWN"]
    report.update(overall="FAIL" if failed else "UNKNOWN" if unknown else "PASS",
                  failed_checks=failed, unknown_required_checks=unknown,
                  required_checks=[key for key, item in checks.items() if item["required"]],
                  excluded_checks=[key for key, item in checks.items() if not item["required"]])
    report["exit_code"] = EXIT_CODES[report["overall"]]
    return report


def establish_unknowns(report, profile):
    rules = {"global": profile.get("rules", {}), "regions": profile.get("regions", [])}
    for key, reason in {
        "self_intersection": "No exhaustive, validated triangle self-intersection backend is implemented.",
        "minimum_wall": "Surface sampling cannot certify the minimum thickness of every part of a mesh.",
        "minimum_detail": "Semantic detail/relief size requires part identification and resolved toolpath checks.",
        "minimum_connection": "A bounding box or ray thickness does not certify a load-bearing connection.",
        "base_contact": "Exact-plane geometry alone does not establish first-layer contact after slicing.",
        "stability": "Mass, infill, support polygon and physical loading have not been fully validated.",
        "appearance_fidelity": "Photo/design fidelity is an independent visual review; topology cannot certify it.",
        "slicer_islands": "Unsupported starts require a sliced layer/toolpath inspection of this exact model.",
        "support_removal": "Support accessibility, trapped supports and removal damage require separate review.",
    }.items():
        set_check(report, key, "UNKNOWN", "explicit coverage boundary", thresholds=rules if key.startswith("minimum_") else {},
                  limitations=[reason])


def thickness_risks(report, mesh, profile, np):
    """Deterministic area-stratified centroid rays. A clear short ray is a risk, never global PASS."""
    config = profile.get("thickness_sampling", {})
    if not config.get("enabled", False):
        return
    check = report["checks"]["minimum_wall"]
    if any(report["checks"][key]["status"] != "PASS" for key in
           ("watertight", "edge_manifold", "winding", "degenerate_faces", "outward_normals")):
        check["limitations"].append("Sampling skipped: closed, consistent outward geometry is required.")
        return
    cumulative = np.cumsum(mesh.area_faces)
    face_ids = np.unique(np.searchsorted(cumulative, (np.arange(config.get("max_samples", 128)) + .5)
                                        * cumulative[-1] / config.get("max_samples", 128)))
    points = mesh.triangles_center[face_ids]
    minimums = np.full(len(face_ids), profile.get("rules", {}).get("wall_min_mm") or np.nan)
    region_names = [["global"] if np.isfinite(x) else [] for x in minimums]
    for region in profile.get("regions", []):
        threshold = region.get("rules", {}).get("wall_min_mm")
        if threshold is None:
            continue
        bounds = np.asarray(region["bounds_mm"])
        included = np.all((points >= bounds[0]) & (points <= bounds[1]), axis=1)
        minimums[included] = np.fmax(minimums[included], threshold)
        for index in np.flatnonzero(included):
            region_names[index].append(region["id"])
    eligible = np.flatnonzero(np.isfinite(minimums))
    check["method"] = "fixed area-stratified face centroids; inward normal first-hit rays; candidate-only coverage"
    check["measured"] = {"sample_faces": len(face_ids), "samples_with_wall_rule": len(eligible), "candidates": []}
    check["limitations"] += [
        "Unselected faces and in-plane/oblique thin features remain untested; no-risk result is UNKNOWN.",
        "Ray distance is a local opposing-surface measurement, not structural strength or the global medial thickness.",
        "Self-intersections remain separately UNKNOWN; a short ray is a conservative review/repair candidate.",
        "Region selectors use face centroids in unchanged mesh coordinates; overlapping rules use the strictest minimum.",
    ]
    if not len(eligible):
        check["limitations"].append("No eligible sampled face has a wall_min_mm rule.")
        return
    directions = -mesh.face_normals[face_ids[eligible]]
    epsilon = max(float(np.max(mesh.extents)) * 1e-8, 1e-7)
    try:
        # One bounded ray query; backend exceptions explicitly leave coverage UNKNOWN.
        locations, ray_ids, hit_faces = mesh.ray.intersects_location(
            points[eligible] + directions * epsilon, directions, multiple_hits=False)
    except Exception as exc:
        check["limitations"].append(f"Ray backend unavailable/failed: {type(exc).__name__}: {exc}")
        return
    distances = np.linalg.norm(locations - points[eligible][ray_ids], axis=1)
    alignment = np.einsum("ij,ij->i", mesh.face_normals[hit_faces], directions[ray_ids])
    candidates = []
    for hit_index in np.flatnonzero((distances < minimums[eligible][ray_ids] - epsilon * 4)
                                  & (distances > epsilon * 4) & (alignment > .5)):
        sample_index = int(eligible[ray_ids[hit_index]])
        if int(hit_faces[hit_index]) == int(face_ids[sample_index]):
            continue
        candidates.append({"face_id": int(face_ids[sample_index]), "opposing_face_id": int(hit_faces[hit_index]),
                           "point_mm": points[sample_index].tolist(), "ray_distance_mm": float(distances[hit_index]),
                           "required_wall_mm": float(minimums[sample_index]), "regions": region_names[sample_index]})
    candidates.sort(key=lambda item: (item["ray_distance_mm"], item["face_id"]))
    check["measured"].update(rays_with_hit=len(ray_ids), candidate_count=len(candidates),
                             candidates=candidates[:config.get("max_candidates", 32)],
                             numeric_offset_mm=epsilon,
                             minimum_observed_ray_mm=float(distances.min()) if len(distances) else None)
    if candidates:
        check["status"] = "FAIL"


def inspect(mesh_path, profile, profile_path=None):
    validate_profile(profile)
    report = new_report(mesh_path, profile_path, profile)
    establish_unknowns(report, profile)
    path = Path(mesh_path)
    try:
        before = file_hash(path)
        report["mesh"]["sha256"] = before
    except OSError as exc:
        set_check(report, "input_integrity", "UNKNOWN", "read input bytes", limitations=[str(exc)])
        return finish(report)
    expected = profile.get("expected_mesh_sha256")
    if expected and before != expected:
        set_check(report, "input_integrity", "FAIL", "SHA256 compared before inspection",
                  {"actual_sha256": before}, {"expected_sha256": expected}, ["Stale/mismatched artifacts are not inspected under prior evidence."])
        return finish(report)
    if path.suffix.lower() != ".stl":
        set_check(report, "input_integrity", "UNKNOWN", "file-type check", limitations=["Only STL geometry is supported; export the selected transformed object in millimetres."])
        return finish(report)
    try:
        np, trimesh = dependency_modules()
    except ImportError as exc:
        set_check(report, "input_integrity", "UNKNOWN", "dependency import", limitations=[f"Required geometry dependency missing: {exc}"])
        return finish(report)
    report["dependencies"] = {"numpy": np.__version__, "trimesh": trimesh.__version__}
    try:
        raw = trimesh.load_mesh(str(path), file_type="stl", process=False)
        if not isinstance(raw, trimesh.Trimesh) or not len(raw.faces):
            raise ValueError("Expected a nonempty triangular mesh")
        if not np.all(np.isfinite(raw.vertices)):
            raise ValueError("Nonfinite coordinate")
        # Exact identity weld only: preserve all face rows, winding, duplicates and tiny features.
        vertices, inverse = np.unique(raw.vertices, axis=0, return_inverse=True)
        faces = inverse[np.asarray(raw.faces)]
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        del raw
    except Exception as exc:
        set_check(report, "input_integrity", "FAIL", "STL parse without repair", limitations=[f"{type(exc).__name__}: {exc}"])
        return finish(report)
    set_check(report, "input_integrity", "PASS", "STL parse, finite coordinates, exact vertex identity weld; no repair",
              {"sha256": before, "face_count": len(faces), "vertex_count": len(vertices)},
              {"units": "mm", "expected_sha256": expected}, ["STL stores no units; millimetres are an explicit caller/profile assertion."])
    report["mesh"].update(faces=len(faces), vertices=len(vertices), bounds_mm=mesh.bounds.tolist(), dimensions_mm=mesh.extents.tolist())
    areas = mesh.area_faces
    degenerate = np.flatnonzero(areas <= profile.get("degenerate_area_mm2", 1e-12))
    _, duplicates = np.unique(np.sort(faces, axis=1), axis=0, return_counts=True)
    duplicate_count = int(np.maximum(duplicates - 1, 0).sum())
    set_check(report, "degenerate_faces", "FAIL" if len(degenerate) else "PASS", "all triangle cross-product areas",
              {"count": len(degenerate), "candidate_face_ids": degenerate[:64].tolist()}, {"maximum_count": 0, "area_tolerance_mm2": profile.get("degenerate_area_mm2", 1e-12)})
    set_check(report, "duplicate_faces", "FAIL" if duplicate_count else "PASS", "all exact unordered vertex-index triplets",
              {"count": duplicate_count}, {"maximum_count": 0})
    directed = np.stack((faces, np.roll(faces, -1, axis=1)), axis=2).reshape(-1, 2)
    sorted_edges = np.sort(directed, axis=1)
    _, edge_inverse, counts = np.unique(sorted_edges, axis=0, return_inverse=True, return_counts=True)
    boundary, nonmanifold = int((counts == 1).sum()), int((counts > 2).sum())
    order = np.argsort(edge_inverse, kind="stable")
    same = edge_inverse[order[1:]] == edge_inverse[order[:-1]]
    left, right = order[:-1][same], order[1:][same]
    pair_only = counts[edge_inverse[left]] == 2
    first, second = left[pair_only], right[pair_only]
    inconsistent = int(np.count_nonzero(directed[first, 0] == directed[second, 0]))
    set_check(report, "watertight", "PASS" if not boundary and not nonmanifold else "FAIL",
              "every undirected edge has exactly two incident faces", {"boundary_edges": boundary, "nonmanifold_edges": nonmanifold},
              {"boundary_edges": 0, "nonmanifold_edges": 0}, ["Combinatorial closure does not exclude self-intersection."])
    set_check(report, "edge_manifold", "FAIL" if nonmanifold else "PASS", "all edge incidence counts",
              {"edges_with_more_than_two_faces": nonmanifold}, {"maximum": 0}, ["Open boundaries are handled by watertight; vertex links are checked separately."])
    set_check(report, "winding", "FAIL" if inconsistent else "UNKNOWN" if nonmanifold else "PASS",
              "opposite directed edges for every two-face adjacency", {"inconsistent_edges": inconsistent}, {"maximum": 0},
              ["Consistency is distinct from outward orientation."])
    labels = None
    try:
        sparse = importlib.import_module("scipy.sparse")
        graph = importlib.import_module("scipy.sparse.csgraph")
        adjacency = sparse.coo_matrix((np.ones(len(left), dtype=np.uint8), (left // 3, right // 3)), shape=(len(faces), len(faces)))
        component_count, labels = graph.connected_components(adjacency, directed=False)
        face_counts = np.bincount(labels)
        set_check(report, "components", "FAIL" if component_count > profile.get("max_components", 1) else "PASS",
                  "shared-edge face connectivity without filling, joining or deleting parts",
                  {"count": int(component_count), "face_counts": sorted(face_counts.tolist(), reverse=True)[:128],
                   "face_counts_truncated": len(face_counts) > 128}, {"maximum": profile.get("max_components", 1)},
                  ["Every disconnected shell counts, including tiny fragments; a single shell does not exclude sliced unsupported starts."])
        if not boundary and not nonmanifold and not len(degenerate):
            next_first = (first // 3) * 3 + (first % 3 + 1) % 3
            next_second = (second // 3) * 3 + (second % 3 + 1) % 3
            starts_match = directed[first, 0] == directed[second, 0]
            rows = np.concatenate((first, next_first))
            cols = np.concatenate((np.where(starts_match, second, next_second), np.where(starts_match, next_second, second)))
            links = sparse.coo_matrix((np.ones(len(rows), dtype=np.uint8), (rows, cols)), shape=(3 * len(faces), 3 * len(faces)))
            _, link_labels = graph.connected_components(links, directed=False)
            mins = np.full(len(vertices), len(link_labels), dtype=np.int64)
            maxs = np.full(len(vertices), -1, dtype=np.int64)
            np.minimum.at(mins, faces.reshape(-1), link_labels)
            np.maximum.at(maxs, faces.reshape(-1), link_labels)
            bad = np.flatnonzero(mins != maxs)
            set_check(report, "vertex_manifold", "FAIL" if len(bad) else "PASS", "all vertex links must be one cycle in an edge-closed triangle surface",
                      {"nonmanifold_vertices": len(bad), "candidate_vertex_ids": bad[:64].tolist()}, {"maximum": 0},
                      ["Combinatorial link check; geometric intersections at unrelated vertices are checked separately."])
        else:
            set_check(report, "vertex_manifold", "UNKNOWN", "closed-surface link checker preconditions", limitations=["Open/nonmanifold edges or degenerate faces prevent this closed-surface link test."])
    except ImportError as exc:
        for key in ("components", "vertex_manifold"):
            set_check(report, key, "UNKNOWN", "optional scipy sparse graph backend", limitations=[str(exc)])
    if labels is not None and report["checks"]["watertight"]["status"] == "PASS" and not inconsistent:
        signed = np.bincount(labels, weights=np.einsum("ij,ij->i", mesh.triangles[:, 0], np.cross(mesh.triangles[:, 1], mesh.triangles[:, 2])) / 6)
        status = "PASS" if len(signed) == 1 and signed[0] > 0 else "FAIL" if len(signed) == 1 else "UNKNOWN"
        set_check(report, "outward_normals", status, "signed enclosed volume for consistent closed shells",
                  {"signed_shell_volumes_mm3": signed.tolist()}, {"single_outer_shell_volume_positive": True},
                  ["Multiple/nested shells require containment classification; positive signed volume does not exclude self-intersection."])
    build = np.asarray(profile["build_volume_mm"])
    reserve = np.asarray(profile.get("reserve_total_mm", [0, 0, 0]))
    available = build - reserve
    set_check(report, "build_volume", "PASS" if np.all(mesh.extents <= available) else "FAIL",
              "fixed-orientation axis-aligned object dimensions versus build volume minus TOTAL reserve per axis",
              {"dimensions_mm": mesh.extents.tolist(), "remaining_mm": (available - mesh.extents).tolist()},
              {"build_volume_mm": build.tolist(), "reserve_total_mm": reserve.tolist(), "available_mm": available.tolist()},
              ["Does not place or rotate the object; support, brim, purge and bed exclusions require slicer verification."])
    bottom = float(mesh.bounds[0, 2])
    flat = np.all(np.abs(mesh.triangles[:, :, 2] - bottom) <= 1e-6, axis=1) & (mesh.face_normals[:, 2] < -.9)
    report["checks"]["base_contact"]["measured"] = {"minimum_z_mm": bottom, "planar_bottom_area_mm2": float(areas[flat].sum()),
                                                         "planar_bottom_faces": int(flat.sum()), "plane_tolerance_mm": 1e-6}
    report["checks"]["base_contact"]["method"] = "diagnostic downward triangles on lowest plane; not a contact acceptance test"
    thickness_risks(report, mesh, profile, np)
    if file_hash(path) != before:
        set_check(report, "input_integrity", "FAIL", "SHA256 before and after inspection", limitations=["Input changed during inspection; results are invalid."])
    return finish(report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("inspect")
    command.add_argument("--mesh", required=True)
    command.add_argument("--profile", required=True)
    command.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    output = Path(args.out)
    if output.resolve() in (Path(args.mesh).resolve(), Path(args.profile).resolve()):
        print(json.dumps({"overall": "UNKNOWN", "error": "--out must not overwrite the mesh or profile"}))
        return 3
    try:
        profile = json.loads(Path(args.profile).read_text(encoding="utf-8-sig"))
        report = inspect(args.mesh, profile, args.profile)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report = {"schema_version": 1, "overall": "UNKNOWN", "exit_code": 3,
                  "error": f"{type(exc).__name__}: {exc}", "checks": {},
                  "scope": "Invalid/unavailable input or profile; no printability claim."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"overall": report["overall"], "report": str(output.resolve()),
                      "failed_checks": report.get("failed_checks", []),
                      "unknown_required_checks": report.get("unknown_required_checks", [])}, ensure_ascii=False))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
