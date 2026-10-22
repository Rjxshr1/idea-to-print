#!/usr/bin/env python3
"""Conservative offline STL gate. PASS certifies only required, implemented checks.

No mesh repair, evidence overrides, slicer or printer operations are performed.
STL coordinates must be millimetres. Exit 0=PASS, 2=FAIL, 3=UNKNOWN.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
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
# Base thresholds are part-specific; a missing threshold leaves its check UNKNOWN.
BASE_DEFAULTS = {"plane_tolerance_mm": 0.05, "min_contact_area_mm2": None,
                 "min_stability_margin_mm": None, "min_tip_angle_deg": None}
MAX_CANDIDATE_PAIRS = 20_000_000
QUERY_CHUNK = 50_000


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
               "thickness_sampling", "base", "intersection_search", "process", "scope"}
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
    base = profile.get("base", {})
    if not isinstance(base, dict) or set(base) - set(BASE_DEFAULTS):
        raise ValueError(f"base supports only {sorted(BASE_DEFAULTS)}")
    for key, value in base.items():
        if value is not None:
            finite_number(value, f"base.{key}", strictly_positive=True)
    if base.get("plane_tolerance_mm") is not None and base["plane_tolerance_mm"] > 1:
        raise ValueError("base.plane_tolerance_mm must be at most 1 mm")
    if base.get("min_tip_angle_deg") is not None and base["min_tip_angle_deg"] >= 90:
        raise ValueError("base.min_tip_angle_deg must be below 90")
    search = profile.get("intersection_search", {})
    if not isinstance(search, dict) or set(search) - {"max_candidate_pairs"}:
        raise ValueError("intersection_search supports only max_candidate_pairs")
    pairs = search.get("max_candidate_pairs", MAX_CANDIDATE_PAIRS)
    if isinstance(pairs, bool) or not isinstance(pairs, int) or not 1 <= pairs <= 200_000_000:
        raise ValueError("intersection_search.max_candidate_pairs must be 1..200000000")
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
        "self_intersection": "Not executed: requires a parsed mesh without degenerate faces and the rtree backend.",
        "minimum_wall": "Surface sampling cannot certify the minimum thickness of every part of a mesh.",
        "minimum_detail": "Semantic detail/relief size requires part identification and resolved toolpath checks.",
        "minimum_connection": "A bounding box or ray thickness does not certify a load-bearing connection.",
        "base_contact": "Not executed: requires consistent outward normals and base.min_contact_area_mm2.",
        "stability": "Not executed: requires a single closed outward shell, a support footprint and a base stability threshold.",
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


def base_settings(profile):
    configured = {key: value for key, value in profile.get("base", {}).items() if value is not None}
    return {**BASE_DEFAULTS, **configured}


def _orientation_ok(report):
    return all(report["checks"][key]["status"] == "PASS" for key in ("winding", "outward_normals"))


def footprint_hull(points, np):
    """2-D convex hull of support points, or None for a point/line footprint."""
    spatial = importlib.import_module("scipy.spatial")
    unique = np.unique(points, axis=0)
    if len(unique) < 3:
        return None
    try:
        hull = spatial.ConvexHull(unique)
    except getattr(spatial, "QhullError", Exception):
        return None
    return hull if hull.volume > 1e-12 else None


def base_contact(report, mesh, profile, np):
    """Downward faces within plane_tolerance_mm of the lowest point, in the fixed print orientation."""
    settings = base_settings(profile)
    tolerance = settings["plane_tolerance_mm"]
    bottom = float(mesh.bounds[0, 2])
    normals_z = mesh.face_normals[:, 2]
    contact = np.all(mesh.triangles[:, :, 2] <= bottom + tolerance, axis=1) & (normals_z < 0)
    area = float((mesh.area_faces * np.abs(normals_z))[contact].sum())
    support_points = mesh.vertices[mesh.vertices[:, 2] <= bottom + tolerance][:, :2]
    footprint = {"bottom": bottom, "tolerance": tolerance, "points": support_points, "hull": None}
    measured = {"minimum_z_mm": bottom, "contact_faces": int(contact.sum()),
                "projected_contact_area_mm2": area, "support_vertices": int(len(support_points))}
    try:
        footprint["hull"] = footprint_hull(support_points, np)
        measured["footprint_hull_area_mm2"] = float(footprint["hull"].volume) if footprint["hull"] else 0.0
    except ImportError as exc:
        footprint = None
        measured["footprint_hull_area_mm2"] = None
        hull_note = [f"scipy.spatial unavailable for the support footprint: {exc}"]
    else:
        hull_note = []
    threshold = settings["min_contact_area_mm2"]
    method = "projected area of downward faces whose vertices all lie within plane_tolerance_mm of the lowest point"
    limitations = hull_note + [
        "Fixed orientation; the lowest point defines the bed plane. Brim, rafts, supports and slicer first-layer "
        "compensation are not modelled.",
        "Contact area is geometric; adhesion, warping and elephant-foot effects require slice and print review.",
    ]
    thresholds = {"min_contact_area_mm2": threshold, "plane_tolerance_mm": tolerance}
    if not _orientation_ok(report):
        set_check(report, "base_contact", "UNKNOWN", method, measured, thresholds,
                  ["Face orientation is not verified (winding/outward_normals must PASS)."] + limitations)
    elif threshold is None:
        set_check(report, "base_contact", "UNKNOWN", method, measured, thresholds,
                  ["No base.min_contact_area_mm2 is configured for this part; area is diagnostic only."] + limitations)
    else:
        set_check(report, "base_contact", "PASS" if area >= threshold else "FAIL", method, measured, thresholds, limitations)
    return footprint


def stability(report, mesh, profile, np, footprint):
    """Centre-of-mass margin inside the support footprint for solid and hollow-shell mass models."""
    settings = base_settings(profile)
    margin_min, tip_min = settings["min_stability_margin_mm"], settings["min_tip_angle_deg"]
    method = ("centre of mass projected into the convex hull of support points; evaluated for a uniform solid, "
              "a uniform thin shell and 9 blends between them (the printed part lies between those mass models)")
    thresholds = {"min_stability_margin_mm": margin_min, "min_tip_angle_deg": tip_min,
                  "plane_tolerance_mm": settings["plane_tolerance_mm"]}
    limitations = [
        "Rigid body on a flat, level surface in the fixed print orientation with gravity along -Z; supports, "
        "brim and external loads are excluded.",
        "Uniform material density is assumed; multi-material parts, inserts or weights change the result.",
        "Margin is the distance to the nearest footprint edge line; negative means the centre of mass is outside.",
    ]
    if report["checks"]["outward_normals"]["status"] != "PASS" or report["checks"]["components"]["status"] != "PASS":
        set_check(report, "stability", "UNKNOWN", method, thresholds=thresholds,
                  limitations=["A single closed shell with verified outward normals is required for mass properties."] + limitations)
        return
    if footprint is None:
        set_check(report, "stability", "UNKNOWN", method, thresholds=thresholds,
                  limitations=["scipy.spatial is required for the support footprint."] + limitations)
        return
    triangles = mesh.triangles
    # Solid centroid from signed tetrahedra against the origin; valid for a closed outward shell.
    volumes = np.einsum("ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])) / 6
    solid = (volumes[:, None] * triangles.sum(axis=1) / 4).sum(axis=0) / volumes.sum()
    shell = (mesh.area_faces[:, None] * triangles.mean(axis=1)).sum(axis=0) / mesh.area_faces.sum()
    blends = np.linspace(0, 1, 11)[:, None]
    centres = shell * (1 - blends) + solid * blends
    measured = {"solid_centre_of_mass_mm": solid.tolist(), "shell_centre_of_mass_mm": shell.tolist(),
                "footprint_hull_area_mm2": float(footprint["hull"].volume) if footprint["hull"] else 0.0}
    if footprint["hull"] is None:
        measured.update(min_margin_mm=None, min_tip_angle_deg=0.0)
        set_check(report, "stability", "FAIL", method, measured, thresholds,
                  ["The support footprint is a point or a line; the part cannot stand without support."] + limitations)
        return
    equations = footprint["hull"].equations
    margins = -(centres[:, :2] @ equations[:, :2].T + equations[:, 2]).max(axis=1)
    heights = np.maximum(centres[:, 2] - footprint["bottom"], 1e-9)
    tips = np.degrees(np.arctan2(margins, heights))
    worst = int(np.argmin(margins))
    measured.update(min_margin_mm=float(margins.min()), min_tip_angle_deg=float(tips.min()),
                    worst_mass_model={"solid_fraction": float(blends[worst, 0]), "centre_of_mass_mm": centres[worst].tolist()},
                    centre_height_mm=float(heights[worst]))
    if margins.min() <= 0:
        status = "FAIL"
        limitations = ["The centre of mass lies on or outside the support footprint."] + limitations
    elif margin_min is None and tip_min is None:
        status = "UNKNOWN"
        limitations = ["No base.min_stability_margin_mm or base.min_tip_angle_deg is configured; values are diagnostic."] + limitations
    else:
        failed = (margin_min is not None and margins.min() < margin_min) or (tip_min is not None and tips.min() < tip_min)
        status = "FAIL" if failed else "PASS"
    set_check(report, "stability", status, method, measured, thresholds, limitations)


def _unit_normals(t, np):
    normal = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    return normal / np.linalg.norm(normal, axis=1, keepdims=True)


def _segment_hits_triangle(p0, p1, t, eps, np):
    """Segment crosses or touches a non-coplanar triangle. Segments lying in the plane return False:
    their contacts are found by the remaining edge tests of the same pair."""
    normal = _unit_normals(t, np)
    d0 = np.einsum("ij,ij->i", p0 - t[:, 0], normal)
    d1 = np.einsum("ij,ij->i", p1 - t[:, 0], normal)
    in_plane = (np.abs(d0) <= eps) & (np.abs(d1) <= eps)
    crosses = ~(((d0 > eps) & (d1 > eps)) | ((d0 < -eps) & (d1 < -eps))) & ~in_plane
    denominator = d0 - d1
    safe = np.where(np.abs(denominator) > 0, denominator, 1.0)
    fraction = np.clip(np.where(np.abs(denominator) > 0, d0 / safe, 0.0), 0, 1)
    point = p0 + fraction[:, None] * (p1 - p0)
    inside = np.ones(len(p0), dtype=bool)
    for a, b in ((0, 1), (1, 2), (2, 0)):
        edge = t[:, b] - t[:, a]
        side = np.einsum("ij,ij->i", np.cross(edge, point - t[:, a]), normal) / np.linalg.norm(edge, axis=1)
        inside &= side >= -eps
    return crosses & inside


def _project(points, drop, np):
    keep = np.array([[1, 2], [0, 2], [0, 1]])[drop]
    return np.take_along_axis(points, np.repeat(keep[:, None, :], points.shape[1], axis=1), axis=2)


def _cross2(u, v):
    return u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]


def _segments_touch_2d(p1, p2, q1, q2, eps, np):
    def side(a, b, c):
        value = _cross2(b - a, c - a) / np.maximum(np.linalg.norm(b - a, axis=1), 1e-300)
        return np.where(np.abs(value) <= eps, 0, np.sign(value))
    s1, s2, s3, s4 = side(p1, p2, q1), side(p1, p2, q2), side(q1, q2, p1), side(q1, q2, p2)
    collinear = (s1 == 0) & (s2 == 0)
    overlap = np.all((np.minimum(p1, p2) <= np.maximum(q1, q2) + eps) & (np.minimum(q1, q2) <= np.maximum(p1, p2) + eps), axis=1)
    return np.where(collinear, overlap, (s1 * s2 <= 0) & (s3 * s4 <= 0))


def _point_in_triangle_2d(p, t, eps, np):
    orientation = np.sign(_cross2(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]))
    inside = np.ones(len(p), dtype=bool)
    for a, b in ((0, 1), (1, 2), (2, 0)):
        edge = t[:, b] - t[:, a]
        inside &= orientation * _cross2(edge, p - t[:, a]) / np.linalg.norm(edge, axis=1) >= -eps
    return inside


def _coplanar_overlap(a, b, shared_a, shared_b, count, normal, eps, np):
    """Positive-area overlap (or touching for unrelated faces) of coplanar triangle pairs."""
    result = np.zeros(len(a), dtype=bool)
    drop = np.argmax(np.abs(normal), axis=1)
    a2, b2 = _project(a, drop, np), _project(b, drop, np)
    rows = np.arange(len(a))
    # Shared edge: overlap when the opposite vertices lie on the same side of that edge.
    edge = count == 2
    if edge.any():
        order = np.argsort(~shared_a[edge], axis=1, kind="stable")
        r = rows[edge]
        s, t, opposite_a = a2[r, order[:, 0]], a2[r, order[:, 1]], a2[r, order[:, 2]]
        opposite_b = b2[r, np.argmin(shared_b[edge], axis=1)]
        length = np.linalg.norm(t - s, axis=1)
        side_a, side_b = _cross2(t - s, opposite_a - s) / length, _cross2(t - s, opposite_b - s) / length
        result[r] = ((side_a > eps) & (side_b > eps)) | ((side_a < -eps) & (side_b < -eps))
    # Shared vertex: overlap when the two open angular sectors at that vertex overlap.
    vertex = count == 1
    if vertex.any():
        r = rows[vertex]
        order_a = np.argsort(shared_a[vertex], axis=1, kind="stable")
        order_b = np.argsort(shared_b[vertex], axis=1, kind="stable")
        s = a2[r, order_a[:, 2]]

        def direction(points, index):
            value = points[r, index] - s
            return value / np.linalg.norm(value, axis=1, keepdims=True)

        u1, u2 = direction(a2, order_a[:, 0]), direction(a2, order_a[:, 1])
        w1, w2 = direction(b2, order_b[:, 0]), direction(b2, order_b[:, 1])
        angular = 1e-9

        def in_sector(v, first, second):
            sign = np.sign(_cross2(first, second))
            return (sign * _cross2(first, v) > angular) & (sign * _cross2(v, second) > angular)

        def same(v, w):
            return (np.abs(_cross2(v, w)) <= angular) & (np.einsum("ij,ij->i", v, w) > 0)

        result[r] = (in_sector(w1, u1, u2) | in_sector(w2, u1, u2) | in_sector(u1, w1, w2) | in_sector(u2, w1, w2)
                     | (same(u1, w1) & same(u2, w2)) | (same(u1, w2) & same(u2, w1)))
    # Unrelated coplanar faces: any edge contact or containment.
    free = count == 0
    if free.any():
        r = rows[free]
        pa, pb = a2[r], b2[r]
        hit = _point_in_triangle_2d(pa[:, 0], pb, eps, np) | _point_in_triangle_2d(pb[:, 0], pa, eps, np)
        for i, j in ((0, 1), (1, 2), (2, 0)):
            for k, m in ((0, 1), (1, 2), (2, 0)):
                hit |= _segments_touch_2d(pa[:, i], pa[:, j], pb[:, k], pb[:, m], eps, np)
        result[r] = hit
    return result


def triangle_pairs_intersect(triangles, faces, first, second, eps, np):
    """Adjacency-aware triangle pair test. Contact only at shared vertices/edges is not an intersection."""
    a, b = triangles[first], triangles[second]
    equal = faces[first][:, :, None] == faces[second][:, None, :]
    shared_a, shared_b = equal.any(axis=2), equal.any(axis=1)
    count = shared_a.sum(axis=1)
    normal_a, normal_b = _unit_normals(a, np), _unit_normals(b, np)
    distance_b = np.where(shared_b, 0.0, np.einsum("pvj,pj->pv", b - a[:, :1], normal_a))
    distance_a = np.where(shared_a, 0.0, np.einsum("pvj,pj->pv", a - b[:, :1], normal_b))
    separated = ((distance_b > eps).all(axis=1) | (distance_b < -eps).all(axis=1)
                 | (distance_a > eps).all(axis=1) | (distance_a < -eps).all(axis=1))
    coplanar = (np.abs(distance_a) <= eps).all(axis=1) & (np.abs(distance_b) <= eps).all(axis=1)
    result = count == 3
    rows = np.arange(len(first))
    planar = coplanar & (count < 3)
    if planar.any():
        result[planar] = _coplanar_overlap(a[planar], b[planar], shared_a[planar], shared_b[planar],
                                           count[planar], normal_a[planar], eps, np)
    free = ~separated & ~coplanar & (count == 0)
    if free.any():
        r = rows[free]
        hit = np.zeros(len(r), dtype=bool)
        for i, j in ((0, 1), (1, 2), (2, 0)):
            hit |= _segment_hits_triangle(a[r, i], a[r, j], b[r], eps, np)
            hit |= _segment_hits_triangle(b[r, i], b[r, j], a[r], eps, np)
        result[r] = hit
    # One shared vertex: the faces meet elsewhere only if an edge opposite the shared vertex hits the other face.
    corner = ~separated & ~coplanar & (count == 1)
    if corner.any():
        r = rows[corner]
        order_a = np.argsort(shared_a[r], axis=1, kind="stable")
        order_b = np.argsort(shared_b[r], axis=1, kind="stable")
        result[r] = (_segment_hits_triangle(a[r, order_a[:, 0]], a[r, order_a[:, 1]], b[r], eps, np)
                     | _segment_hits_triangle(b[r, order_b[:, 0]], b[r, order_b[:, 1]], a[r], eps, np))
    # Two shared vertices and distinct planes meet only along the shared edge.
    return result


def self_intersection(report, mesh, profile, np, trimesh):
    limit = profile.get("intersection_search", {}).get("max_candidate_pairs", MAX_CANDIDATE_PAIRS)
    method = ("exhaustive axis-aligned bounding-box broadphase (rtree) over every face pair, then float64 "
              "triangle-pair tests aware of shared vertices/edges and coplanar overlap")
    thresholds = {"maximum_intersecting_pairs": 0, "max_candidate_pairs": limit}
    if report["checks"]["degenerate_faces"]["status"] != "PASS":
        set_check(report, "self_intersection", "UNKNOWN", method, thresholds=thresholds,
                  limitations=["Degenerate faces have no defined plane; remove them before this test."])
        return
    if importlib.util.find_spec("rtree") is None:
        set_check(report, "self_intersection", "UNKNOWN", method, thresholds=thresholds,
                  limitations=["Optional rtree backend is not installed (requirements-modeling.txt)."])
        return
    triangles = np.asarray(mesh.triangles, dtype=np.float64)
    faces = np.asarray(mesh.faces)
    eps = max(float(np.max(mesh.extents)) * 1e-9, 1e-12)
    low, high = triangles.min(axis=1) - eps, triangles.max(axis=1) + eps
    tree = trimesh.util.bounds_tree(np.column_stack((low, high)))
    candidates, intersecting, samples = 0, 0, []
    for start in range(0, len(faces), QUERY_CHUNK):
        stop = min(start + QUERY_CHUNK, len(faces))
        ids, counts = tree.intersection_v(low[start:stop], high[start:stop])
        first = np.repeat(np.arange(start, stop), np.asarray(counts, dtype=np.int64))
        second = np.asarray(ids, dtype=np.int64)
        keep = first < second
        first, second = first[keep], second[keep]
        candidates += len(first)
        if candidates > limit:
            set_check(report, "self_intersection", "UNKNOWN", method,
                      {"candidate_pairs_so_far": candidates, "intersecting_pairs_so_far": intersecting,
                       "sample_face_pairs": samples, "tolerance_mm": eps}, thresholds,
                      ["Candidate pair budget exceeded before the search completed; raise "
                       "intersection_search.max_candidate_pairs or simplify the mesh."])
            return
        if not len(first):
            continue
        hits = triangle_pairs_intersect(triangles, faces, first, second, eps, np)
        intersecting += int(hits.sum())
        for pair in np.column_stack((first[hits], second[hits]))[:64 - len(samples)]:
            samples.append([int(pair[0]), int(pair[1])])
    set_check(report, "self_intersection", "FAIL" if intersecting else "PASS", method,
              {"candidate_pairs": candidates, "intersecting_pairs": intersecting,
               "sample_face_pairs": samples, "tolerance_mm": eps}, thresholds,
              [f"Floating-point predicates with a {eps:.3g} mm tolerance, not exact arithmetic; touching "
               "within that tolerance counts as intersecting.",
               "Vertices are shared only when coordinates are identical; an unwelded touching contact counts "
               "as an intersection.",
               "No intersection does not establish wall thickness, connection strength or printability."])


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
    footprint = base_contact(report, mesh, profile, np)
    stability(report, mesh, profile, np, footprint)
    self_intersection(report, mesh, profile, np, trimesh)
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
