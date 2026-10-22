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
# sample_spacing_mm None = half the smallest configured thickness rule, at least MIN_AUTO_SPACING.
THICKNESS_DEFAULTS = {"sample_spacing_mm": None, "max_thin_patch_area_mm2": 0.0, "max_samples": 5_000_000}
THICKNESS_RULES = {"minimum_wall": "wall_min_mm", "minimum_detail": "detail_min_mm",
                   "minimum_connection": "connection_min_mm"}
MIN_AUTO_SPACING = 0.1  # mm; finer features are below any FDM nozzle.
OPPOSING_ALIGNMENT = 0.5  # Exit face within 60 degrees of facing the sample point.


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
               "thickness_sampling", "thickness", "base", "intersection_search", "process", "scope"}
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
    # Legacy key from the sampled wall-risk probe; still validated, no longer used.
    sampling = profile.get("thickness_sampling", {})
    if not isinstance(sampling, dict) or set(sampling) - {"enabled", "max_samples", "max_candidates"}:
        raise ValueError("Invalid thickness_sampling configuration")
    if not isinstance(sampling.get("enabled", False), bool):
        raise ValueError("thickness_sampling.enabled must be boolean")
    for key, default, limit in (("max_samples", 128, 4096), ("max_candidates", 32, 4096)):
        value = sampling.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= limit:
            raise ValueError(f"thickness_sampling.{key} must be 1..{limit}")
    thickness = profile.get("thickness", {})
    if not isinstance(thickness, dict) or set(thickness) - set(THICKNESS_DEFAULTS):
        raise ValueError(f"thickness supports only {sorted(THICKNESS_DEFAULTS)}")
    if thickness.get("sample_spacing_mm") is not None:
        finite_number(thickness["sample_spacing_mm"], "thickness.sample_spacing_mm", strictly_positive=True)
    if thickness.get("max_thin_patch_area_mm2") is not None:
        finite_number(thickness["max_thin_patch_area_mm2"], "thickness.max_thin_patch_area_mm2")
    samples = thickness.get("max_samples", THICKNESS_DEFAULTS["max_samples"])
    if isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= 50_000_000:
        raise ValueError("thickness.max_samples must be 1..50000000")
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
        "minimum_wall": "Not executed: requires a closed outward mesh without self-intersections, rtree and a wall_min_mm rule.",
        "minimum_detail": "Not executed: requires a closed outward mesh without self-intersections, rtree and a detail_min_mm rule.",
        "minimum_connection": "Not executed: requires a closed outward mesh without self-intersections, rtree and a connection_min_mm rule.",
        "base_contact": "Not executed: requires consistent outward normals and base.min_contact_area_mm2.",
        "stability": "Not executed: requires a single closed outward shell, a support footprint and a base stability threshold.",
        "appearance_fidelity": "Photo/design fidelity is an independent visual review; topology cannot certify it.",
        "slicer_islands": "Unsupported starts require a sliced layer/toolpath inspection of this exact model.",
        "support_removal": "Support accessibility, trapped supports and removal damage require separate review.",
    }.items():
        set_check(report, key, "UNKNOWN", "explicit coverage boundary", thresholds=rules if key.startswith("minimum_") else {},
                  limitations=[reason])


def thickness_settings(profile):
    configured = {key: value for key, value in profile.get("thickness", {}).items() if value is not None}
    return {**THICKNESS_DEFAULTS, **configured}


def required_thickness(points, profile, rule, np):
    """Per-sample minimum for one rule: a containing region overrides the global value;
    overlapping regions use the strictest. NaN means no rule applies."""
    required = np.full(len(points), np.nan)
    global_value = profile.get("rules", {}).get(rule)
    if global_value is not None:
        required[:] = global_value
    regional = np.full(len(points), np.nan)
    names = [[] for _ in range(len(points))]
    for region in profile.get("regions", []):
        value = region.get("rules", {}).get(rule)
        if value is None:
            continue
        bounds = np.asarray(region["bounds_mm"], dtype=float)
        inside = np.flatnonzero(np.all((points >= bounds[0]) & (points <= bounds[1]), axis=1))
        regional[inside] = np.fmax(regional[inside], value)
        for index in inside:
            names[index].append(region["id"])
    overridden = np.isfinite(regional)
    required[overridden] = regional[overridden]
    return required, names


def surface_samples(mesh, spacing, limit, np):
    """Split every face at the midpoint of its longest edge until all pieces are at most
    `spacing` across, and sample each piece at its centroid. Every surface point then lies
    within `spacing` of a sample, and the sample count grows with area, not edge length."""
    pieces = np.asarray(mesh.triangles, dtype=float)
    owners = np.arange(len(pieces))
    done_pieces, done_owners = [], []
    while len(pieces):
        lengths = np.linalg.norm(pieces - np.roll(pieces, -1, axis=1), axis=2)  # edge i: vertex i -> i+1
        small = lengths.max(axis=1) <= spacing
        done_pieces.append(pieces[small])
        done_owners.append(owners[small])
        pieces, owners, lengths = pieces[~small], owners[~small], lengths[~small]
        if sum(len(x) for x in done_pieces) + 2 * len(pieces) > limit:
            return None, sum(len(x) for x in done_pieces) + 2 * len(pieces)
        if not len(pieces):
            break
        edge = lengths.argmax(axis=1)
        rows = np.arange(len(pieces))
        a, b, c = pieces[rows, edge], pieces[rows, (edge + 1) % 3], pieces[rows, (edge + 2) % 3]
        middle = (a + b) / 2
        pieces = np.concatenate((np.stack((a, middle, c), axis=1), np.stack((middle, b, c), axis=1)))
        owners = np.concatenate((owners, owners))
    pieces, owners = np.concatenate(done_pieces), np.concatenate(done_owners)
    cross = np.cross(pieces[:, 1] - pieces[:, 0], pieces[:, 2] - pieces[:, 0])
    return (pieces.mean(axis=1), owners, np.linalg.norm(cross, axis=1) / 2), len(pieces)


def opposing_distances(mesh, points, faces, lengths, np, trimesh):
    """Distance from each sample along its inward normal to the first exit face within
    `lengths`, and that exit face's alignment with the ray. inf when nothing is hit."""
    triangles = mesh.triangles
    normals = mesh.face_normals
    directions = -normals[faces]
    eps = max(float(np.max(mesh.extents)) * 1e-9, 1e-12)
    ends = points + directions * lengths[:, None]
    low, high = np.minimum(points, ends) - eps, np.maximum(points, ends) + eps
    tree = trimesh.util.bounds_tree(np.column_stack((triangles.min(axis=1) - eps, triangles.max(axis=1) + eps)))
    best = np.full(len(points), np.inf)
    best_face = np.full(len(points), -1, dtype=np.int64)
    for start in range(0, len(points), QUERY_CHUNK):
        stop = min(start + QUERY_CHUNK, len(points))
        ids, counts = tree.intersection_v(low[start:stop], high[start:stop])
        sample = np.repeat(np.arange(start, stop), np.asarray(counts, dtype=np.int64))
        face = np.asarray(ids, dtype=np.int64)
        # A closed outward mesh without self-intersections is exited before it is re-entered.
        keep = (face != faces[sample]) & (np.einsum("ij,ij->i", normals[face], directions[sample]) > 0)
        sample, face = sample[keep], face[keep]
        if not len(sample):
            continue
        # Moller-Trumbore ray/triangle distance.
        a, b, c = triangles[face, 0], triangles[face, 1], triangles[face, 2]
        d, origin = directions[sample], points[sample]
        e1, e2 = b - a, c - a
        p = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, p)
        inverse = 1 / np.where(np.abs(det) > 1e-300, det, 1)
        s = origin - a
        u = np.einsum("ij,ij->i", s, p) * inverse
        q = np.cross(s, e1)
        v = np.einsum("ij,ij->i", d, q) * inverse
        t = np.einsum("ij,ij->i", e2, q) * inverse
        tol = 1e-9
        hit = ((np.abs(det) > 1e-300) & (u >= -tol) & (v >= -tol) & (u + v <= 1 + tol)
               & (t > eps * 4) & (t <= lengths[sample] + eps))
        sample, face, t = sample[hit], face[hit], t[hit]
        order = np.lexsort((t, sample))
        sample, face, t = sample[order], face[order], t[order]
        first = np.ones(len(sample), dtype=bool)
        first[1:] = sample[1:] != sample[:-1]
        sample, face, t = sample[first], face[first], t[first]
        closer = t < best[sample]
        best[sample[closer]] = t[closer]
        best_face[sample[closer]] = face[closer]
    alignment = np.zeros(len(points))
    found = best_face >= 0
    alignment[found] = np.einsum("ij,ij->i", normals[best_face[found]], directions[found])
    return best, best_face, alignment


def thin_patches(mesh, faces, areas, thin, distances, points, np):
    """Group thin samples into patches of edge-connected faces; return patches largest first."""
    if not thin.any():
        return []
    graph = importlib.import_module("scipy.sparse.csgraph")
    sparse = importlib.import_module("scipy.sparse")
    thin_faces = np.unique(faces[thin])
    index = np.full(len(mesh.faces), -1)
    index[thin_faces] = np.arange(len(thin_faces))
    pairs = mesh.face_adjacency
    linked = (index[pairs[:, 0]] >= 0) & (index[pairs[:, 1]] >= 0)
    rows, cols = index[pairs[linked, 0]], index[pairs[linked, 1]]
    adjacency = sparse.coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(thin_faces),) * 2)
    _, labels = graph.connected_components(adjacency, directed=False)
    label = labels[index[faces[thin]]]
    patch_area = np.bincount(label, weights=areas[thin])
    patches = []
    thin_ids = np.flatnonzero(thin)
    for patch in np.argsort(-patch_area):
        members = thin_ids[label == patch]
        worst = members[np.argmin(distances[members])]
        patches.append({"area_mm2": float(patch_area[patch]), "samples": int(len(members)),
                        "faces": int(len(np.unique(faces[members]))),
                        "min_thickness_mm": float(distances[worst]), "at_mm": points[worst].tolist()})
    return patches


def thickness_checks(report, mesh, profile, np, trimesh):
    """Opposing-surface thickness on every face, compared with wall/detail/connection rules."""
    settings = thickness_settings(profile)
    keys = list(THICKNESS_RULES)
    method = ("samples on every face at the stated spacing; from each, the distance along the inward normal to the "
              "first exit face, counted as thickness when that face is within 60 degrees of facing back")
    common = [
        "Every surface point lies within sample_spacing_mm of a sample; a thin region narrower than that spacing "
        "in every direction can be missed.",
        "Thickness is measured along the surface normal. Where the first exit face is more than 60 degrees from "
        "facing back (inside corners, steps), the sample does not measure a wall.",
        "Region rules override the global rule inside their bounding boxes (strictest of overlapping regions); "
        "regions select samples in unchanged mesh coordinates.",
        "Geometric thickness only; strength, layer adhesion and print orientation effects are not modelled.",
    ]
    preconditions = ("watertight", "winding", "outward_normals", "degenerate_faces", "self_intersection")
    blocked = [key for key in preconditions if report["checks"][key]["status"] != "PASS"]
    if blocked or importlib.util.find_spec("rtree") is None:
        reason = (f"Preconditions not met: {', '.join(blocked)} must PASS." if blocked
                  else "Optional rtree backend is not installed (requirements-modeling.txt).")
        for key in keys:
            set_check(report, key, "UNKNOWN", method, thresholds={"rule": THICKNESS_RULES[key]},
                      limitations=[reason] + common)
        return
    configured = [value for value in [profile.get("rules", {}).get(rule) for rule in THICKNESS_RULES.values()]
                  + [region.get("rules", {}).get(rule) for region in profile.get("regions", [])
                     for rule in THICKNESS_RULES.values()] if value is not None]
    if not configured:
        for key in keys:
            set_check(report, key, "UNKNOWN", method, thresholds={"rule": THICKNESS_RULES[key]},
                      limitations=[f"No {THICKNESS_RULES[key]} rule is configured for this part."] + common)
        return
    spacing = settings["sample_spacing_mm"] or max(min(configured) / 2, MIN_AUTO_SPACING)
    tolerance = settings["max_thin_patch_area_mm2"]
    sampled, total = surface_samples(mesh, spacing, settings["max_samples"], np)
    if sampled is None:
        for key in keys:
            set_check(report, key, "UNKNOWN", method, {"samples_needed": total},
                      {"rule": THICKNESS_RULES[key], "sample_spacing_mm": spacing, "max_samples": settings["max_samples"]},
                      ["Sample budget exceeded; raise thickness.max_samples or sample_spacing_mm, or simplify the mesh."] + common)
        return
    points, faces, areas = sampled
    requirements = {key: required_thickness(points, profile, THICKNESS_RULES[key], np) for key in keys}
    lengths = np.fmax.reduce([requirements[key][0] for key in keys])
    lengths = np.where(np.isfinite(lengths), lengths, 0.0)
    distances, _, alignment = opposing_distances(mesh, points, faces, lengths, np, trimesh)
    measured_wall = np.where(alignment > OPPOSING_ALIGNMENT, distances, np.inf)
    for key in keys:
        required, names = requirements[key]
        ruled = np.isfinite(required)
        thresholds = {"rule": THICKNESS_RULES[key], "global_mm": profile.get("rules", {}).get(THICKNESS_RULES[key]),
                      "sample_spacing_mm": spacing, "max_thin_patch_area_mm2": tolerance}
        if not ruled.any():
            set_check(report, key, "UNKNOWN", method, {"samples": int(len(points))}, thresholds,
                      [f"No {THICKNESS_RULES[key]} rule applies to any part of this mesh."] + common)
            continue
        thin = ruled & (measured_wall < required)
        patches = thin_patches(mesh, faces, areas, thin, measured_wall, points, np)
        regions_hit = sorted({name for index in np.flatnonzero(thin) for name in names[index]})
        largest = patches[0]["area_mm2"] if patches else 0.0
        finite = measured_wall[ruled][np.isfinite(measured_wall[ruled])]
        measured = {"samples": int(len(points)), "samples_with_rule": int(ruled.sum()),
                    "thin_samples": int(thin.sum()), "thin_area_mm2": float(areas[thin].sum()),
                    "thin_patches": len(patches), "largest_thin_patch_mm2": largest,
                    "patches": patches[:32], "regions_with_thin_samples": regions_hit,
                    # Only distances shorter than the rule are searched, so a missing value means thicker.
                    "min_measured_thickness_mm": float(finite.min()) if len(finite) else None}
        limitations = list(common)
        if ruled.sum() < len(points):
            limitations.insert(0, f"{int(len(points) - ruled.sum())} samples have no {THICKNESS_RULES[key]} rule and are not checked.")
        set_check(report, key, "FAIL" if largest > tolerance else "PASS", method, measured, thresholds, limitations)


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
    thickness_checks(report, mesh, profile, np, trimesh)
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
