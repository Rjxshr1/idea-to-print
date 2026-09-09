#!/usr/bin/env python3
"""Actual-mesh preparation and neutral previews, run with Blender 4.2+.

blender --background --factory-startup --disable-autoexec --python-use-system-env
  --python model_pipeline.py --
  --source /abs/model.stl --out-dir /abs/new-revision --config /abs/config.json

Input files are preserved byte-for-byte. The output directory must be absent or
empty. No automatic anatomical inference, global smoothing, repair, slicing or
printer dispatch is performed. numpy must be available to Blender's Python;
--python-use-system-env enables the verified host environment but does not install
it. Recover only missing previews with the same launch prefix followed by
  -- resume-render --out-dir /abs/interrupted-revision [--config /abs/original.json]
Resume verifies saved export/config/image hashes and never repeats operations.
It retains earlier report snapshots and unrecorded partial PNGs for diagnosis.
Millimetres are the editing/STL coordinate unit;
GLB is exported in metres. A completed run is evidence generation, not approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import traceback
from pathlib import Path


DEFAULT_VIEWS = ["front", "side", "left45", "right45", "back", "bottom"]
AXES = {"X": (1, 0, 0), "-X": (-1, 0, 0), "Y": (0, 1, 0),
        "-Y": (0, -1, 0), "Z": (0, 0, 1), "-Z": (0, 0, -1)}


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def artifact(path):
    path = Path(path)
    return {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False,
                                    allow_nan=False) + "\n", encoding="utf-8")


def positive(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite positive number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return float(value)


def vector(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must have three finite values")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
           for x in value):
        raise ValueError(f"{label} must have three finite values")
    return list(map(float, value))


def validate_config(config):
    if not isinstance(config, dict) or type(config.get("schema_version")) is not int or config["schema_version"] != 1:
        raise ValueError("config schema_version must be 1")
    allowed = {"schema_version", "orientation", "normalize", "proxy", "render",
               "operations", "object_names", "profile_label"}
    if set(config) - allowed:
        raise ValueError(f"Unknown configuration fields: {sorted(set(config) - allowed)}")
    orientation = config.get("orientation", {})
    if set(orientation) != {"up_axis", "front_axis"}:
        raise ValueError("Explicit orientation.up_axis and orientation.front_axis are required")
    if any(orientation[key] not in AXES for key in ("up_axis", "front_axis")):
        raise ValueError("Axes must be X, -X, Y, -Y, Z or -Z")
    if orientation["up_axis"].lstrip("-") == orientation["front_axis"].lstrip("-"):
        raise ValueError("Front and up axes cannot be parallel")
    normalization = config.get("normalize", {})
    if set(normalization) not in ({"longest_mm"}, {"axis", "size_mm"}, {"preserve_mm"}):
        raise ValueError("normalize needs longest_mm OR axis plus size_mm OR preserve_mm:true")
    if "preserve_mm" in normalization:
        if normalization["preserve_mm"] is not True:
            raise ValueError("preserve_mm must be explicitly true")
        if orientation != {"up_axis": "Z", "front_axis": "-Y"}:
            raise ValueError("preserve_mm requires already canonical Z-up/front -Y axes")
    elif "longest_mm" in normalization:
        positive(normalization["longest_mm"], "longest_mm")
    else:
        if normalization["axis"] not in {"X", "Y", "Z"}:
            raise ValueError("Size axis must be X, Y or Z in the normalized frame")
        positive(normalization["size_mm"], "size_mm")
    proxy = config.setdefault("proxy", {})
    if set(proxy) - {"max_faces"}:
        raise ValueError("proxy only accepts max_faces")
    count = proxy.setdefault("max_faces", 80000)
    if type(count) is not int or not 100 <= count <= 1000000:
        raise ValueError("proxy.max_faces must be an integer from 100 to 1000000")
    render = config.setdefault("render", {})
    if set(render) - {"resolution", "samples", "threads", "views", "face", "custom_views", "denoise"}:
        raise ValueError("Unknown render option")
    for key, default, lower, upper in (
            ("resolution", 800, 64, 4096), ("samples", 32, 1, 512), ("threads", 4, 1, 64)):
        value = render.setdefault(key, default)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"render.{key} must be an integer in {lower}..{upper}")
    if type(render.setdefault("denoise", True)) is not bool:
        raise ValueError("render.denoise must be boolean")
    views = render.setdefault("views", DEFAULT_VIEWS.copy())
    if not isinstance(views, list) or not views or len(set(views)) != len(views):
        raise ValueError("render.views must be a nonempty unique name list")
    custom = render.setdefault("custom_views", {})
    if not isinstance(custom, dict):
        raise ValueError("custom_views must be an object")
    builtins = set(DEFAULT_VIEWS) | {"left", "right", "top", "face"}
    for name, specification in custom.items():
        if not isinstance(name, str) or not name or any(x not in
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for x in name):
            raise ValueError("Custom camera names must be filename-safe")
        if name in builtins:
            raise ValueError("Custom cameras cannot replace standard camera definitions")
        if set(specification) != {"camera_mm", "target_mm", "span_mm"}:
            raise ValueError("Custom cameras require camera_mm, target_mm and span_mm")
        vector(specification["camera_mm"], "camera_mm")
        vector(specification["target_mm"], "target_mm")
        positive(specification["span_mm"], "span_mm")
        if specification["camera_mm"] == specification["target_mm"]:
            raise ValueError("Camera and target cannot coincide")
    if any(not isinstance(name, str) or name not in builtins | set(custom) for name in views):
        raise ValueError("Unknown camera view")
    face = render.get("face")
    if face is not None:
        if set(face) != {"center_mm", "span_mm"}:
            raise ValueError("render.face requires exactly center_mm and span_mm")
        vector(face["center_mm"], "face.center_mm")
        positive(face["span_mm"], "face.span_mm")
    if "face" in views and face is None:
        raise ValueError("Face camera needs an explicit normalized center_mm and span_mm")
    if not isinstance(config.setdefault("operations", []), list):
        raise ValueError("operations must be a list")
    names = config.get("object_names")
    if names is not None and (not isinstance(names, list) or not names or
                             any(not isinstance(x, str) for x in names)):
        raise ValueError("object_names must be a nonempty list of exact mesh names")
    return config


def select_only(objects):
    import bpy
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_select = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def import_source(path, names):
    import bpy
    from types import SimpleNamespace
    if path.suffix.lower() == ".blend":
        # Reject embedded script execution even if the caller omitted --disable-autoexec.
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
    else:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        suffix = path.suffix.lower()
        if suffix == ".stl":
            bpy.ops.wm.stl_import(filepath=str(path))
        elif suffix == ".glb":
            bpy.ops.import_scene.gltf(filepath=str(path))
        elif suffix == ".fbx":
            try:
                bpy.ops.import_scene.fbx.get_rna_type()
            except (AttributeError, RuntimeError):
                from io_scene_fbx import import_fbx
                outcome = import_fbx.load(
                    SimpleNamespace(report=lambda level, message: print(level, message)),
                    bpy.context, filepath=str(path), use_anim=False)
            else:
                outcome = bpy.ops.import_scene.fbx(filepath=str(path), use_anim=False)
            if outcome != {"FINISHED"}:
                raise RuntimeError("FBX import did not finish")
        else:
            raise ValueError("Supported inputs are STL, FBX, GLB and BLEND")
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if names is not None:
        missing = set(names) - {obj.name for obj in mesh_objects}
        if missing:
            raise ValueError(f"Requested mesh objects not found: {sorted(missing)}")
        objects = [obj for obj in mesh_objects if obj.name in names]
    else:
        objects = [obj for obj in mesh_objects if obj.visible_get() and not obj.hide_render]
    if not objects:
        raise ValueError("No visible mesh imported; supply exact object_names for hidden source objects")
    for obj in objects:
        if len(obj.modifiers) or obj.data.shape_keys:
            raise ValueError(f"{obj.name}: modifiers/shape keys require a separately chosen baked source state")
        if any(not math.isfinite(value) for row in obj.matrix_world for value in row):
            raise ValueError("Imported object transform contains nonfinite values")
    return objects


def apply_mesh_world_transform(mesh, matrix):
    """Apply a world transform and preserve winding under reflection.

    Blender's Mesh.transform moves vertices but does not reverse polygon order
    when a negative object scale reflects the geometry.
    """
    import numpy as np
    transform = np.asarray(matrix, dtype=float)
    if transform.shape != (4, 4) or not np.isfinite(transform).all():
        raise ValueError("World transform must be a finite 4x4 matrix")
    determinant = float(np.linalg.det(transform[:3, :3]))
    if determinant == 0:
        raise ValueError("Singular world transform would collapse the source geometry")
    mesh.transform(matrix)
    if determinant < 0:
        mesh.flip_normals()
    mesh.update()
    return {"world_linear_determinant": determinant,
            "winding_reversed": determinant < 0,
            "method": "Apply world coordinates; reverse polygon winding for negative determinant",
            "limitation": "Preserves incoming winding orientation; does not repair preexisting inconsistent faces"}


def copy_world_meshes(objects):
    import bpy
    from mathutils import Matrix
    copied = []
    transform_reports = []
    for source in objects:
        mesh = source.data.copy()
        transform_reports.append({"object": source.name,
                                  **apply_mesh_world_transform(mesh, source.matrix_world)})
        obj = bpy.data.objects.new("Working_" + source.name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.matrix_world = Matrix.Identity(4)
        copied.append(obj)
    original_mesh_data = {obj.data for obj in bpy.context.scene.objects
                          if obj.type == "MESH" and obj not in copied}
    for obj in list(bpy.context.scene.objects):
        if obj not in copied:
            bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in original_mesh_data:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    select_only(copied)
    bpy.ops.object.join()
    result = bpy.context.object
    result.name = "Final_model_mm"
    return result, transform_reports


def vertex_array(obj):
    import numpy as np
    values = np.empty(len(obj.data.vertices) * 3, dtype=np.float64)
    obj.data.vertices.foreach_get("co", values)
    return values.reshape((-1, 3))


def triangles(obj):
    import numpy as np
    obj.data.calc_loop_triangles()
    values = np.empty(len(obj.data.loop_triangles) * 3, dtype=np.int32)
    obj.data.loop_triangles.foreach_get("vertices", values)
    return values.reshape((-1, 3))


def replace_vertices(obj, values):
    obj.data.vertices.foreach_set("co", values.ravel())
    obj.data.update()


def orient_and_normalize(obj, config):
    import numpy as np
    values = vertex_array(obj)
    if len(values) < 3 or not np.isfinite(values).all():
        raise ValueError("Imported mesh has no valid finite geometry")
    original_bounds = [values.min(axis=0).tolist(), values.max(axis=0).tolist()]
    if config["normalize"].get("preserve_mm") is True:
        if (values.max(axis=0) - values.min(axis=0)).max() <= 0:
            raise ValueError("Cannot preserve zero-extent geometry")
        return {"imported_world_bounds": original_bounds,
                "orientation_matrix": np.eye(3).tolist(), "uniform_scale_factor": 1.0,
                "translation_mm": [0.0, 0.0, 0.0], "source_axes": config["orientation"],
                "coordinate_frame": "Declared existing X right, Z up, front -Y, mm",
                "requested_size": config["normalize"],
                "operation": "Preserved all world coordinates; no rotation, scaling or centering",
                "unit_evidence": "Caller explicitly declares source coordinates are millimetres"}
    up = np.array(AXES[config["orientation"]["up_axis"]], dtype=float)
    front = np.array(AXES[config["orientation"]["front_axis"]], dtype=float)
    rotation = np.stack((np.cross(up, front), -front, up))
    values = values @ rotation.T
    lo, hi = values.min(axis=0), values.max(axis=0)
    extent = hi - lo
    normal = config["normalize"]
    selected_extent = extent.max() if "longest_mm" in normal else extent["XYZ".index(normal["axis"])]
    if selected_extent <= 0:
        raise ValueError("Cannot normalize a zero-extent dimension")
    desired = normal.get("longest_mm", normal.get("size_mm"))
    factor = desired / selected_extent
    values *= factor
    lo, hi = values.min(axis=0), values.max(axis=0)
    translation = np.array((-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]))
    values += translation
    replace_vertices(obj, values)
    return {"imported_world_bounds": original_bounds, "orientation_matrix": rotation.tolist(),
            "uniform_scale_factor": float(factor), "translation_mm": translation.tolist(),
            "coordinate_frame": "X right, Z up, face outward -Y; coordinates in mm",
            "source_axes": config["orientation"], "requested_size": normal}


def neutral(obj):
    import bpy
    material = bpy.data.materials.new("Neutral_actual_geometry_no_texture")
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (.64, .64, .64, 1)
    shader.inputs["Roughness"].default_value = .62
    obj.data.materials.clear()
    obj.data.materials.append(material)
    for face in obj.data.polygons:
        face.material_index = 0
        face.use_smooth = True


def export_stl(obj, path):
    import bpy
    select_only([obj])
    bpy.ops.wm.stl_export(filepath=str(path), export_selected_objects=True,
                          global_scale=1.0, use_scene_unit=False, apply_modifiers=True,
                          ascii_format=False, forward_axis="Y", up_axis="Z")


def export_glb(obj, path):
    import bpy
    scene = bpy.context.scene
    previous_unit, previous_scale = scene.unit_settings.scale_length, obj.scale.copy()
    select_only([obj])
    try:
        scene.unit_settings.scale_length = 1.0
        obj.scale = (.001, .001, .001)
        bpy.context.view_layer.update()
        bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB",
                                  use_selection=True, export_apply=True, export_yup=True,
                                  export_draco_mesh_compression_enable=False)
    finally:
        obj.scale = previous_scale
        scene.unit_settings.scale_length = previous_unit
        bpy.context.view_layer.update()


def create_proxy(obj, max_faces, directory):
    import bpy
    proxy = bpy.data.objects.new("PREVIEW_PROXY_NOT_PRINT_GEOMETRY", obj.data.copy())
    bpy.context.scene.collection.objects.link(proxy)
    face_count = len(proxy.data.polygons)
    select_only([proxy])
    if face_count > max_faces:
        modifier = proxy.modifiers.new("Display_decimation_only", "DECIMATE")
        modifier.ratio = max_faces / face_count
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    proxy["purpose"] = "Interactive preview only; never use this mesh for acceptance or printing"
    export_glb(proxy, directory / "preview-proxy.glb")
    measured = len(proxy.data.polygons)
    proxy.hide_render = True
    proxy.hide_set(True)
    return proxy, {"artifact": artifact(directory / "preview-proxy.glb"),
                   "faces": measured, "requested_max_faces": max_faces,
                   "acceptance_mesh": False,
                   "note": "Decimate target is approximate; preserved source/final meshes are unchanged"}


def configure_denoising(scene, view_layers, requested, build_options=None):
    """Select only a CPU denoiser advertised by RNA; unsupported is explicit.

    Some Blender builds expose the boolean but have an empty denoiser enum.
    A missing build flag is not evidence that OpenImageDenoise is available.
    """
    cycles = scene.cycles
    oidn_build = getattr(build_options, "openimagedenoise", None)
    available = []
    capability_error = None
    try:
        property_info = cycles.bl_rna.properties.get("denoiser")
        if property_info is None:
            capability_error = "RNA denoiser property is absent"
        else:
            available = [item.identifier for item in property_info.enum_items]
    except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
        capability_error = f"RNA denoiser capability could not be read: {exc}"
    cycles.use_denoising = False
    enabled = False
    fallback = None
    if requested:
        if "OPENIMAGEDENOISE" not in available:
            fallback = (capability_error or
                        f"CPU OpenImageDenoise unsupported; RNA available denoisers: {available}")
        elif oidn_build is False:
            fallback = "Build explicitly reports no OpenImageDenoise support"
        else:
            try:
                cycles.denoiser = "OPENIMAGEDENOISE"
                cycles.use_denoising = True
                enabled = bool(cycles.use_denoising)
                if not enabled:
                    fallback = "Runtime did not enable the requested OpenImageDenoise"
            except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                cycles.use_denoising = False
                fallback = f"Runtime rejected OpenImageDenoise ({type(exc).__name__}): {exc}"
    for layer in view_layers:
        layer_cycles = getattr(layer, "cycles", None)
        if layer_cycles is not None and hasattr(layer_cycles, "use_denoising"):
            layer_cycles.use_denoising = enabled
    if fallback:
        fallback += "; denoising disabled, raw CPU render samples retained"
        print("DENOISE_UNSUPPORTED " + fallback, flush=True)
    return {"requested": requested, "enabled": enabled,
            "openimagedenoise_build_support": oidn_build,
            "available_denoisers": available,
            "capability_source": "Cycles RNA denoiser enum plus runtime assignment",
            "method": "OPENIMAGEDENOISE" if enabled else "none", "fallback": fallback}


def setup_scene(config, extent, center):
    import bpy
    from mathutils import Vector
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = .001
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = config["samples"]
    denoise_report = configure_denoising(scene, scene.view_layers, config["denoise"], bpy.app.build_options)
    scene.render.threads_mode = "FIXED"
    scene.render.threads = config["threads"]
    scene.render.resolution_x = scene.render.resolution_y = config["resolution"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.exposure = 0
    scene.world = bpy.data.worlds.new("Neutral_mesh_review_world")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes["Background"]
    background.inputs[0].default_value = (.07, .08, .10, 1)
    background.inputs[1].default_value = .35
    bpy.ops.object.camera_add()
    camera = bpy.context.object
    camera.name = "Review_camera"
    camera.data.type = "ORTHO"
    camera.data.clip_start = max(extent * .0001, .001)
    camera.data.clip_end = extent * 30
    scene.camera = camera
    # Scale distance and area/energy together so any explicit model size is usable.
    scale = extent / 160.0
    target = Vector(center)
    for name, location, energy, size in (
            ("Key", (-170, -210, 240), 300000, 120),
            ("Fill", (190, -65, 165), 140000, 140),
            ("Rim", (90, 180, 230), 240000, 110),
            ("Bottom", (0, -100, -180), 140000, 120)):
        bpy.ops.object.light_add(type="AREA", location=target + Vector(location) * scale)
        light = bpy.context.object
        light.name = "Review_" + name
        light.data.energy = energy * scale * scale
        light.data.shape = "DISK"
        light.data.size = size * scale
        light.rotation_euler = (target - light.location).to_track_quat("-Z", "Y").to_euler()
    # No floor: bottom/back visibility cannot be hidden by a presentation prop.
    return scene, camera, denoise_report


def camera_specs(render, bounds):
    import numpy as np
    lo, hi = np.array(bounds[0]), np.array(bounds[1])
    center = (lo + hi) / 2
    extent = float((hi - lo).max())
    distance = extent * 3
    directions = {"front": (0, -1, 0), "side": (1, 0, 0),
                  "right": (1, 0, 0), "left": (-1, 0, 0),
                  "left45": (-1, -1, 0), "right45": (1, -1, 0),
                  "back": (0, 1, 0), "bottom": (0, 0, -1), "top": (0, 0, 1)}
    result = {}
    for name in render["views"]:
        if name in directions:
            direction = np.array(directions[name], dtype=float)
            direction /= np.linalg.norm(direction)
            # Fit the projection, including sqrt(2)-wide diagonal views of boxy forms.
            if abs(direction[2]) > .99:
                camera_right, camera_up = np.array((1, 0, 0)), np.array((0, 1, 0))
            else:
                camera_right = np.cross(-direction, (0, 0, 1))
                camera_right /= np.linalg.norm(camera_right)
                camera_up = np.cross(camera_right, -direction)
            fit_span = max(float((hi - lo) @ np.abs(camera_right)),
                           float((hi - lo) @ np.abs(camera_up))) * 1.2
            result[name] = {"camera_mm": (center + direction * distance).tolist(),
                            "target_mm": center.tolist(), "span_mm": fit_span,
                            "fit": "All bounding-box corners, 20 percent span margin"}
        elif name == "face":
            face = render["face"]
            target = np.array(face["center_mm"])
            result[name] = {"camera_mm": (target + (0, -distance, 0)).tolist(),
                            "target_mm": target.tolist(), "span_mm": face["span_mm"]}
        else:
            result[name] = dict(render["custom_views"][name])
    return result


def position_camera(camera, specification):
    import bpy
    from mathutils import Vector
    camera.location = specification["camera_mm"]
    camera.rotation_euler = (Vector(specification["target_mm"]) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.ortho_scale = specification["span_mm"]
    bpy.context.view_layer.update()


def run(source, output, config_path):
    import bpy
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from mesh_operations import apply_operations
    config = validate_config(json.loads(config_path.read_text(encoding="utf-8")))
    if source.suffix.lower() not in {".stl", ".fbx", ".glb", ".blend"}:
        raise ValueError("Supported inputs are STL, FBX, GLB and BLEND")
    if config["normalize"].get("preserve_mm") and source.suffix.lower() not in {".stl", ".blend"}:
        raise ValueError("preserve_mm requires an explicitly millimetre STL/BLEND; normalize GLB/FBX first")
    if output == source or output in source.parents:
        raise ValueError("Output directory cannot contain the input model")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory is not empty; use a new revision directory")
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "raw"
    raw_dir.mkdir()
    original_hash = digest(source)
    raw = raw_dir / source.name
    shutil.copy2(source, raw)
    shutil.copy2(config_path, output / "pipeline-config.input.json")
    write_json(output / "pipeline-config.resolved.json", config)
    report = {"schema_version": 1, "status": "RUNNING", "actual_mesh": False,
              "source": artifact(source), "preserved_raw": artifact(raw),
              "config": artifact(output / "pipeline-config.input.json"),
              "resolved_config": artifact(output / "pipeline-config.resolved.json"),
              "blender_version": bpy.app.version_string, "numpy_version": np.__version__,
              "implementation": {
                  "pipeline": artifact(Path(__file__).resolve()),
                  "operations": artifact(Path(__file__).resolve().with_name("mesh_operations.py"))},
              "profile_label": config.get("profile_label"),
              "appearance": "UNKNOWN", "geometry": "UNKNOWN", "slice": "UNKNOWN",
              "physical_print": "NOT_RUN", "print_authorized": False,
              "limitations": ["No automated resemblance judgment or anatomy segmentation",
                              "No global smoothing, automatic repair or new geometry",
                              "Proxies and smooth display normals are not printable detail",
                              "No complete thickness, self-intersection, contact or stability proof"]}
    write_json(output / "model-report.json", report)
    objects = import_source(raw, config.get("object_names"))
    report["source_objects"] = [{"name": obj.name, "vertices": len(obj.data.vertices),
                                "polygons": len(obj.data.polygons),
                                "world_matrix": [list(row) for row in obj.matrix_world]}
                               for obj in objects]
    # Save actual imported state before even rigid transforms, materials or proxy changes.
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "imported-highpoly.blend"))
    report["imported_highpoly"] = artifact(output / "imported-highpoly.blend")
    obj, report["world_transform_handling"] = copy_world_meshes(objects)
    report["normalization"] = orient_and_normalize(obj, config)
    original_vertices = vertex_array(obj)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = .001
    neutral(obj)
    operation_specs = config["operations"]
    # A precheck catches stage order and all operations before modifying the object.
    if operation_specs:
        source_faces = triangles(obj)
        triangle_count = len(source_faces)
        changed_vertices, operations_report = apply_operations(original_vertices, source_faces, operation_specs)
        replace_vertices(obj, changed_vertices)
        del source_faces
    else:
        # Do not duplicate high-poly arrays or build adjacency for a normalization-only run.
        changed_vertices = original_vertices
        triangle_count = sum(len(face.vertices) - 2 for face in obj.data.polygons)
        operations_report = {"operations": [], "stage_order": ["shape", "fabrication"],
                             "measured_total_max_displacement_mm": 0.0,
                             "manufacturing_status": "UNKNOWN",
                             "note": "No local operations requested; input detail is preserved"}
    report["operations"] = operations_report
    report["shape_modified"] = bool(operation_specs)
    report["topology_changed"] = False
    report["geometry_counts"] = {"vertices": len(obj.data.vertices),
                                 "polygons": len(obj.data.polygons),
                                 "triangles": triangle_count}
    report["bounds_mm"] = [changed_vertices.min(axis=0).tolist(), changed_vertices.max(axis=0).tolist()]
    report["dimensions_mm"] = (changed_vertices.max(axis=0) - changed_vertices.min(axis=0)).tolist()
    del changed_vertices, original_vertices
    report["units"] = {"stl": "mm", "blend": "mm; scene scale_length=0.001",
                       "glb": "metres, Y-up"}
    stl = output / "model.stl"
    export_stl(obj, stl)
    export_glb(obj, output / "model.glb")
    proxy, report["proxy"] = create_proxy(obj, config["proxy"]["max_faces"], output)
    bounds = report["bounds_mm"]
    extent = max(report["dimensions_mm"])
    center = ((np.array(bounds[0]) + np.array(bounds[1])) / 2).tolist()
    scene, camera, denoise_report = setup_scene(config["render"], extent, center)
    report["denoise"] = denoise_report
    specifications = camera_specs(config["render"], bounds)
    report["camera_specifications"] = specifications
    position_camera(camera, next(iter(specifications.values())))
    select_only([obj])
    obj["source_sha256"] = original_hash
    obj["units"] = "millimetres"
    obj["manufacturing_status"] = "UNKNOWN; independent geometry and slice review required"
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "model.blend"))
    report["exports"] = {name: artifact(output / name)
                         for name in ("model.stl", "model.glb", "model.blend")}
    report["stl_sha256"] = digest(stl)
    report["status"] = "EXPORTED"
    write_json(output / "model-report.json", report)
    print("MODEL_EXPORTED " + json.dumps({"out": str(output), "stl_sha256": report["stl_sha256"],
                                         "dimensions_mm": report["dimensions_mm"]}), flush=True)
    # Acceptance images use the exact exported STL, never the proxy or a texture.
    obj.hide_render = True
    obj.hide_set(True)
    bpy.ops.wm.stl_import(filepath=str(stl))
    rendered = [item for item in bpy.context.selected_objects if item.type == "MESH"]
    if not rendered:
        raise RuntimeError("STL export could not be reimported")
    for item in rendered:
        neutral(item)
    exported_points = np.concatenate([vertex_array(item) for item in rendered])
    reimport_bounds = [exported_points.min(axis=0).tolist(), exported_points.max(axis=0).tolist()]
    tolerance = max(extent * 2e-6, 1e-5)
    if not np.allclose(reimport_bounds, bounds, atol=tolerance, rtol=0):
        raise RuntimeError("Exported STL bounds disagree with the model")
    render_report = {"schema_version": 1, "status": "RUNNING", "actual_mesh": True,
                     "stl_sha256": report["stl_sha256"], "mesh_path": str(stl),
                     "method": "Cycles CPU rendering after actual STL reimport; neutral material",
                     "reimport_bounds_mm": reimport_bounds, "bounds_tolerance_mm": tolerance,
                     "render_settings": config["render"], "denoise": denoise_report,
                     "implementation": report["implementation"], "views": {},
                     "appearance": "UNKNOWN", "manufacturing_status": "UNKNOWN"}
    previews = output / "previews"
    previews.mkdir()
    for name, specification in specifications.items():
        position_camera(camera, specification)
        path = previews / (name + ".png")
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        render_report["views"][name] = {
            **specification, "ortho_span_mm": specification["span_mm"],
            "render_path": str(path), "sha256": digest(path), "actual_mesh": True,
            "stl_sha256": report["stl_sha256"], "proxy": False, "denoise": denoise_report,
            "render_implementation_sha256": report["implementation"]["pipeline"]["sha256"]}
        write_json(output / "render-report.json", render_report)
        print("RENDERED " + name, flush=True)
    unchanged = digest(source) == original_hash and digest(raw) == original_hash
    if not unchanged:
        raise RuntimeError("Input/raw source bytes changed during processing")
    render_report["status"] = "COMPLETED"
    write_json(output / "render-report.json", render_report)
    report.update({"status": "COMPLETED", "actual_mesh": True,
                   "source_unchanged": unchanged, "render_report": artifact(output / "render-report.json"),
                   "views": render_report["views"]})
    write_json(output / "model-report.json", report)
    print("COMPLETED " + str(output / "model-report.json"), flush=True)
    return report


def inspect_render_resume(output, config_path=None):
    """Validate existing evidence and plan a render-only resume without Blender."""
    import numpy as np
    output = Path(output).resolve(strict=True)
    report_path = output / "model-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    exports = report.get("exports", {})
    mesh = output / "model.stl"
    expected_hash = report.get("stl_sha256")
    if not expected_hash or exports.get("model.stl", {}).get("sha256") != expected_hash:
        raise ValueError("Resume needs a recorded exported STL hash; unrecorded partial exports cannot be adopted")
    for name, expected in exports.items():
        if name not in {"model.stl", "model.glb", "model.blend"}:
            raise ValueError("Unexpected export name in the saved model report")
        path = output / name
        if Path(expected["path"]).resolve() != path or digest(path) != expected["sha256"]:
            raise ValueError(f"Export hash/path changed: {name}")
    if digest(mesh) != expected_hash:
        raise ValueError("STL hash changed; resume will not use another geometry revision")
    for key, name in (("config", "pipeline-config.input.json"),
                      ("resolved_config", "pipeline-config.resolved.json")):
        descriptor = report.get(key, {})
        path = output / name
        if Path(descriptor.get("path", "")).resolve() != path or digest(path) != descriptor.get("sha256"):
            raise ValueError(f"Saved configuration hash/path changed: {name}")
    if config_path is not None and digest(config_path) != report["config"]["sha256"]:
        raise ValueError("Supplied config differs from the original input; create a new revision")
    original_config = json.loads((output / "pipeline-config.input.json").read_text(encoding="utf-8"))
    resolved_config = json.loads((output / "pipeline-config.resolved.json").read_text(encoding="utf-8"))
    config = validate_config(original_config)
    if config != resolved_config:
        raise ValueError("Resolved configuration differs from the saved input/defaults")
    bounds = report.get("bounds_mm")
    array_bounds = np.asarray(bounds, dtype=float)
    if array_bounds.shape != (2, 3) or not np.isfinite(array_bounds).all() or np.any(array_bounds[1] < array_bounds[0]):
        raise ValueError("Resume requires recorded finite model bounds")
    specifications = report.get("camera_specifications")
    specification_source = "recorded_before_render"
    if specifications is None:
        specifications = camera_specs(config["render"], bounds)
        specification_source = "reconstructed_from_verified_config_and_bounds"
    if set(specifications) != set(config["render"]["views"]):
        raise ValueError("Recorded camera views do not match the saved configuration")
    render_path = output / "render-report.json"
    render_report = json.loads(render_path.read_text(encoding="utf-8")) if render_path.exists() else None
    completed = {}
    if render_report is not None:
        if render_report.get("actual_mesh") is not True or render_report.get("stl_sha256") != expected_hash:
            raise ValueError("Render report does not identify the same exported STL")
        if render_report.get("status") not in {"RUNNING", "COMPLETED"}:
            raise ValueError("Render report has no supported execution state")
        for name, view in render_report.get("views", {}).items():
            if name not in specifications:
                raise ValueError(f"Unexpected existing render view: {name}")
            expected_path = output / "previews" / (name + ".png")
            if (Path(view.get("render_path", "")).resolve() != expected_path or
                    digest(expected_path) != view.get("sha256")):
                raise ValueError(f"Existing render hash/path changed: {name}; will not overwrite recorded evidence")
            if view.get("stl_sha256", expected_hash) != expected_hash or view.get("proxy") is True:
                raise ValueError(f"Existing view uses another mesh or a display proxy: {name}")
            for key in ("camera_mm", "target_mm", "span_mm"):
                if key not in view or not np.allclose(view[key], specifications[name][key], rtol=0, atol=1e-7):
                    raise ValueError(f"Existing camera differs from recorded configuration: {name}/{key}")
            completed[name] = view
    pending = [name for name in config["render"]["views"] if name not in completed]
    # A PNG without its report entry may be a interrupted render. Preserve those
    # bytes separately, then regenerate the view; never count it as checked evidence.
    unrecorded = [output / "previews" / (name + ".png") for name in pending
                  if (output / "previews" / (name + ".png")).exists()]
    return {"output": output, "report": report, "config": config, "mesh": mesh,
            "specifications": specifications, "specification_source": specification_source,
            "render_report": render_report, "completed": completed, "pending": pending,
            "unrecorded_images": unrecorded}


def resume_render(output, config_path=None):
    """Only load the final STL and render missing views; never repeat mesh edits."""
    import bpy
    import numpy as np
    from datetime import datetime, timezone
    state = inspect_render_resume(output, config_path)
    output, report, config = state["output"], state["report"], state["config"]
    render_report = state["render_report"]
    if not state["pending"] and render_report["status"] == "COMPLETED" and report.get("status") == "COMPLETED":
        print("RESUME_ALREADY_COMPLETE " + str(output), flush=True)
        return report
    snapshots = output / "resume-history"
    snapshots.mkdir(exist_ok=True)
    previous = {}
    for name in ("model-report.json", "render-report.json"):
        live = output / name
        if not live.exists():
            previous[name] = None
            continue
        original = artifact(live)
        preserved = snapshots / (live.stem + "-" + original["sha256"] + ".json")
        if preserved.exists():
            if digest(preserved) != original["sha256"]:
                raise ValueError("Conflicting preserved report snapshot")
        else:
            shutil.copy2(live, preserved)
        previous[name] = artifact(preserved)
    previous_model_report = previous["model-report.json"]
    previous_render_report = previous["render-report.json"]
    event = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
             "operations_executed": False, "normalization_executed": False,
             "source": "Verified exported STL only", "stl_sha256": report["stl_sha256"],
             "previous_model_report": previous_model_report,
             "previous_render_report": previous_render_report,
             "reused_views": list(state["completed"]), "requested_missing_views": state["pending"],
             "rendered_views": [], "camera_specification_source": state["specification_source"],
             "implementation": artifact(Path(__file__).resolve()), "preserved_unrecorded_images": []}
    for path in state["unrecorded_images"]:
        original = artifact(path)
        preserved = path.with_name(path.stem + ".incomplete-" + original["sha256"] + path.suffix)
        if preserved.exists():
            if digest(preserved) != original["sha256"]:
                raise ValueError("Conflicting preserved unrecorded render")
            # Keep the original until rendering overwrites it; a checked copy exists.
        else:
            path.rename(preserved)
        event["preserved_unrecorded_images"].append(artifact(preserved))
    report.setdefault("render_resume_history", []).append(event)
    report["status"] = "RENDERING_RESUME"
    report["camera_specifications"] = state["specifications"]
    write_json(output / "model-report.json", report)
    rendered = import_source(state["mesh"], None)
    for obj in rendered:
        neutral(obj)
    points = np.concatenate([vertex_array(obj) for obj in rendered])
    actual_bounds = [points.min(axis=0).tolist(), points.max(axis=0).tolist()]
    extent = float((points.max(axis=0) - points.min(axis=0)).max())
    tolerance = max(extent * 2e-6, 1e-5)
    if not np.allclose(actual_bounds, report["bounds_mm"], atol=tolerance, rtol=0):
        raise ValueError("Reimported STL bounds differ from the recorded model")
    center = ((points.max(axis=0) + points.min(axis=0)) / 2).tolist()
    del points
    scene, camera, denoise_report = setup_scene(config["render"], extent, center)
    if render_report is None:
        render_report = {"schema_version": 1, "actual_mesh": True,
                         "stl_sha256": report["stl_sha256"], "mesh_path": str(state["mesh"]),
                         "method": "Cycles CPU rendering after actual STL reimport; neutral material",
                         "reimport_bounds_mm": actual_bounds, "bounds_tolerance_mm": tolerance,
                         "render_settings": config["render"], "denoise": denoise_report,
                         "implementation": report.get("implementation"), "views": {},
                         "appearance": "UNKNOWN", "manufacturing_status": "UNKNOWN"}
    render_report["status"] = "RUNNING"
    render_report.setdefault("resume_history", []).append(event)
    render_report["last_resume_denoise"] = denoise_report
    write_json(output / "render-report.json", render_report)
    previews = output / "previews"
    previews.mkdir(exist_ok=True)
    for name in state["pending"]:
        specification = state["specifications"][name]
        position_camera(camera, specification)
        path = previews / (name + ".png")
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        render_report["views"][name] = {
            **specification, "ortho_span_mm": specification["span_mm"], "render_path": str(path),
            "sha256": digest(path), "actual_mesh": True, "stl_sha256": report["stl_sha256"],
            "proxy": False, "denoise": denoise_report,
            "render_implementation_sha256": event["implementation"]["sha256"]}
        event["rendered_views"].append(name)
        write_json(output / "render-report.json", render_report)
        write_json(output / "model-report.json", report)
        print("RESUME_RENDERED " + name, flush=True)
    if digest(state["mesh"]) != report["stl_sha256"]:
        raise RuntimeError("Exported STL changed during render-only resume")
    event["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    render_report["status"] = "COMPLETED"
    write_json(output / "render-report.json", render_report)
    report.update({"status": "COMPLETED", "actual_mesh": True,
                   "render_report": artifact(output / "render-report.json"),
                   "views": render_report["views"]})
    write_json(output / "model-report.json", report)
    print("RESUME_COMPLETED " + str(output / "model-report.json"), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", nargs="?", choices=("prepare", "resume-render"), default="prepare")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    output = args.out_dir.expanduser().resolve()
    config_path = args.config.expanduser().resolve(strict=True) if args.config else None
    if args.stage == "resume-render":
        if args.source is not None:
            parser.error("resume-render reads the recorded STL; --source is not accepted")
        try:
            resume_render(output, config_path)
        except Exception:
            traceback.print_exc()
            raise SystemExit(1)
        return
    if args.source is None or config_path is None:
        parser.error("prepare requires --source and --config")
    source = args.source.expanduser().resolve(strict=True)
    # No failure file is written for a preexisting output directory.
    was_empty = not output.exists() or (output.is_dir() and not any(output.iterdir()))
    try:
        run(source, output, config_path)
    except Exception as exc:
        if was_empty and output.is_dir():
            failure = {"status": "FAILED", "error": str(exc),
                       "manufacturing_status": "UNKNOWN",
                       "note": "Partial artifacts are not completed evidence; retain for diagnosis"}
            write_json(output / "failure.json", failure)
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
