"""Bounded, explicitly selected vertex edits; no anatomy or printability inference.

All coordinates are millimetres in the pipeline's normalized frame. Operations
preserve topology and return measurements, not a manufacturing approval.
Requires numpy only; usable inside Blender's bundled Python and offline tests.
"""
from __future__ import annotations

import math
import numpy as np


class OperationError(ValueError):
    """Invalid or unsafe local operation specification."""


def _number(value, name, minimum=0.0, positive=False):
    if isinstance(value, bool):
        raise OperationError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise OperationError(f"{name} must be a finite number") from None
    if not math.isfinite(number) or number < minimum or (positive and number <= minimum):
        raise OperationError(f"{name} is outside its allowed range")
    return number


def _vector(value, name):
    try:
        result = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError):
        raise OperationError(f"{name} must contain three finite numbers") from None
    if result.shape != (3,) or not np.isfinite(result).all():
        raise OperationError(f"{name} must contain three finite numbers")
    return result


def _box(region):
    if not isinstance(region, dict) or set(region) != {"min_mm", "max_mm"}:
        raise OperationError("Region needs exactly min_mm and max_mm")
    lo = _vector(region["min_mm"], "min_mm")
    hi = _vector(region["max_mm"], "max_mm")
    if np.any(hi <= lo):
        raise OperationError("Each region maximum must exceed its minimum")
    return lo, hi


def region_weights(vertices, region, feather_mm, protect_regions, faces=None):
    """Smooth inward feather and protection mask.

    With faces, freeze every vertex of every triangle whose AABB overlaps a
    protected box. This is deliberately conservative: even a triangle passing
    through the box with all vertices outside remains immobile. Without faces
    this helper returns vertex-only weights, never a surface-protection claim.
    """
    lo, hi = _box(region)
    feather = _number(feather_mm, "feather_mm")
    if not isinstance(protect_regions, list):
        raise OperationError("protect_regions must be an explicit list")
    distances = np.minimum(vertices - lo, hi - vertices).min(axis=1)
    if feather:
        t = np.clip(distances / feather, 0.0, 1.0)
        weights = t * t * (3.0 - 2.0 * t)
    else:
        weights = (distances >= 0.0).astype(np.float64)
    protected = np.zeros(len(vertices), dtype=bool)
    for box in protect_regions:
        p_lo, p_hi = _box(box)
        protected |= np.all((vertices >= p_lo) & (vertices <= p_hi), axis=1)
        if faces is not None:
            overlapping = np.ones(len(faces), dtype=bool)
            # Work one axis at a time instead of allocating an Mx3x3 coordinate array.
            for axis in range(3):
                coordinates = vertices[faces, axis]
                overlapping &= ((coordinates.max(axis=1) >= p_lo[axis]) &
                                (coordinates.min(axis=1) <= p_hi[axis]))
            protected[faces[overlapping].ravel()] = True
    weights[protected] = 0.0
    return weights, protected


def _mesh(vertices, faces):
    verts = np.asarray(vertices, dtype=np.float64)
    triangles = np.asarray(faces)
    if verts.ndim != 2 or verts.shape[1] != 3 or len(verts) < 3 or not np.isfinite(verts).all():
        raise OperationError("vertices must be a finite Nx3 array")
    if triangles.ndim != 2 or triangles.shape[1] != 3 or triangles.dtype.kind not in "iu":
        raise OperationError("faces must be integer Mx3 triangles")
    if len(triangles) == 0 or triangles.min() < 0 or triangles.max() >= len(verts):
        raise OperationError("faces contain no geometry or invalid vertex indices")
    return verts.copy(), triangles.astype(np.int64, copy=False)


def _edges(faces):
    edge_array = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
    edge_array.sort(axis=1)
    return np.unique(edge_array, axis=0)


def _clip_displacement(candidate, original, limit):
    displacement = candidate - original
    magnitude = np.linalg.norm(displacement, axis=1)
    factors = np.minimum(1.0, limit / np.maximum(magnitude, np.finfo(float).tiny))
    return original + displacement * factors[:, None]


def apply_operation(vertices, faces, spec):
    """Apply one shape/fabrication edit with exact protection and displacement cap.

    soften: alpha (0,1], iterations (1..30).
    blunt_tip: axis, tip_plane_mm; retract points beyond that absolute axis plane.
    thicken_root: axis, center_mm, amount_mm; radial outward offset, no new topology.
    """
    original, faces = _mesh(vertices, faces)
    required = {"kind", "stage", "region", "feather_mm", "protect_regions",
                "max_displacement_mm", "parameters"}
    if not isinstance(spec, dict) or not required <= spec.keys():
        raise OperationError(f"Each operation requires {sorted(required)}")
    unknown = set(spec) - required - {"id", "reason"}
    if unknown:
        raise OperationError(f"Unknown operation fields: {sorted(unknown)}")
    kind = spec["kind"]
    if kind not in {"soften", "blunt_tip", "thicken_root"}:
        raise OperationError(f"Unsupported local operation: {kind}")
    if spec["stage"] not in {"shape", "fabrication"}:
        raise OperationError("stage must be shape or fabrication")
    if kind in {"blunt_tip", "thicken_root"} and spec["stage"] != "fabrication":
        raise OperationError("Tip/root edits belong to the fabrication stage")
    limit = _number(spec["max_displacement_mm"], "max_displacement_mm", positive=True)
    weights, protected = region_weights(original, spec["region"], spec["feather_mm"],
                                        spec["protect_regions"], faces=faces)
    active = weights > 0
    if not active.any():
        raise OperationError("Region has no editable vertices after feather/protection")
    if kind == "soften" and active.all():
        raise OperationError("Global smoothing is unsupported: retain an unaffected or protected region")
    parameters = spec["parameters"]
    if not isinstance(parameters, dict):
        raise OperationError("parameters must be an object")
    current = original.copy()
    limitations = ["Vertex displacement only; no intersection, thickness, connection or strength proof",
                   "Topology is unchanged; export and recheck faces, thin areas and slices"]
    if kind == "soften":
        if set(parameters) != {"alpha", "iterations"}:
            raise OperationError("soften requires exactly alpha and iterations")
        alpha = _number(parameters["alpha"], "alpha", positive=True)
        iterations = parameters["iterations"]
        if alpha > 1 or type(iterations) is not int or not 1 <= iterations <= 30:
            raise OperationError("soften requires 0 < alpha <= 1 and 1..30 iterations")
        # Only faces incident to the explicit ROI contribute to editable averages.
        edges = _edges(faces[np.any(active[faces], axis=1)])
        degree = np.bincount(edges.ravel(), minlength=len(original)).astype(float)
        if np.any(active & (degree == 0)):
            raise OperationError("Editable region contains isolated vertices")
        for _ in range(iterations):
            sums = np.zeros_like(current)
            np.add.at(sums, edges[:, 0], current[edges[:, 1]])
            np.add.at(sums, edges[:, 1], current[edges[:, 0]])
            average = sums / np.maximum(degree, 1)[:, None]
            candidate = current + (average - current) * (alpha * weights)[:, None]
            current = _clip_displacement(candidate, original, limit)
            current[~active] = original[~active]
        limitations.append("Local Laplacian softening may reduce detail/volume inside the explicit region")
    elif kind == "blunt_tip":
        if set(parameters) != {"axis", "tip_plane_mm"}:
            raise OperationError("blunt_tip requires exactly axis and tip_plane_mm")
        axis = _vector(parameters["axis"], "axis")
        length = float(np.linalg.norm(axis))
        if length < 1e-12:
            raise OperationError("axis must be nonzero")
        axis /= length
        try:
            plane = float(parameters["tip_plane_mm"])
        except (TypeError, ValueError):
            raise OperationError("tip_plane_mm must be finite") from None
        if not math.isfinite(plane):
            raise OperationError("tip_plane_mm must be finite")
        excess = np.maximum(original @ axis - plane, 0.0)
        current -= axis[None, :] * (excess * weights)[:, None]
        current = _clip_displacement(current, original, limit)
        limitations.append("Axial tip retraction can collapse triangles; it does not guarantee a minimum tip radius")
    else:
        if set(parameters) != {"axis", "center_mm", "amount_mm"}:
            raise OperationError("thicken_root requires exactly axis, center_mm and amount_mm")
        axis = _vector(parameters["axis"], "axis")
        length = float(np.linalg.norm(axis))
        if length < 1e-12:
            raise OperationError("axis must be nonzero")
        axis /= length
        center = _vector(parameters["center_mm"], "center_mm")
        amount = _number(parameters["amount_mm"], "amount_mm", positive=True)
        delta = original - center
        radial = delta - (delta @ axis)[:, None] * axis
        radius = np.linalg.norm(radial, axis=1)
        radial /= np.maximum(radius, 1e-12)[:, None]
        current += radial * (amount * weights)[:, None]
        current = _clip_displacement(current, original, limit)
        limitations.append("Radial thickening assumes the supplied root axis; on-axis vertices remain unchanged")
    current[~active] = original[~active]
    displacement = np.linalg.norm(current - original, axis=1)
    changed = displacement > 1e-12
    if not changed.any():
        raise OperationError("Operation produced no measurable change; revise its explicit region/parameters")
    return current, {
        "kind": kind, "stage": spec["stage"], "parameters": parameters,
        "region": spec["region"], "feather_mm": spec["feather_mm"],
        "protect_regions": spec["protect_regions"], "max_displacement_mm": limit,
        "id": spec.get("id"), "reason": spec.get("reason"),
        "editable_vertices": int(active.sum()), "changed_vertices": int(changed.sum()),
        "protected_vertices": int(protected.sum()),
        "fully_protected_triangles": int(np.all(protected[faces], axis=1).sum()),
        "protection_method": "Freeze all vertices of triangles with AABBs overlapping a protected box",
        "protection_coverage": "Conservative original-surface protection; AABB overlap may protect extra faces",
        "protection_limitations": "New surface intrusions/intersections into protected volumes are not checked",
        "measured_max_displacement_mm": float(displacement.max()),
        "unselected_unchanged": bool(np.array_equal(current[~active], original[~active])),
        "protected_unchanged": bool(np.array_equal(current[protected], original[protected])),
        "topology_changed": False, "manufacturing_status": "UNKNOWN",
        "limitations": limitations,
    }


def apply_operations(vertices, faces, operations):
    """Shape edits precede fabrication edits; each edit has its own frozen ROI."""
    if not isinstance(operations, list):
        raise OperationError("operations must be a list")
    current, faces = _mesh(vertices, faces)
    initial = current.copy()
    reports = []
    fabrication_started = False
    for spec in operations:
        if not isinstance(spec, dict):
            raise OperationError("Every operation must be an object")
        stage = spec.get("stage")
        if stage == "fabrication":
            fabrication_started = True
        elif stage == "shape" and fabrication_started:
            raise OperationError("Shape operations must precede fabrication operations")
        current, report = apply_operation(current, faces, spec)
        reports.append(report)
    return current, {
        "operations": reports, "stage_order": ["shape", "fabrication"],
        "measured_total_max_displacement_mm": float(np.linalg.norm(current - initial, axis=1).max()),
        "manufacturing_status": "UNKNOWN",
        "note": "Per-operation caps do not impose a cumulative cap; inspect reported total displacement",
    }
