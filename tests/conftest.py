"""Tiny synthetic fixtures; no owner geometry, printer details, or network."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import zipfile

from PIL import Image
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = {
    "hunyuan": ROOT / "skills/printable-modeling/scripts/hunyuan_shape.py",
    "audit": ROOT / "skills/3d-print-workflow/scripts/print_audit.py",
    "slice_only": ROOT / "skills/3d-print-workflow/scripts/make_slice_only.py",
}


@pytest.fixture
def load_helper():
    def load(name):
        spec = importlib.util.spec_from_file_location(f"tested_{name}", SCRIPTS[name])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return load


@pytest.fixture
def write_glb():
    def write(path, label="synthetic"):
        # A valid GLB 2 envelope with a JSON scene, intentionally no real model.
        data = json.dumps({"asset": {"version": "2.0", "generator": label}}).encode()
        data += b" " * (-len(data) % 4)
        payload = struct.pack("<4sII", b"glTF", 2, 20 + len(data))
        payload += struct.pack("<I4s", len(data), b"JSON") + data
        path.write_bytes(payload)
        return path
    return write


@pytest.fixture
def tetrahedron():
    # Consistent outward winding; volume is 1/6 mm^3.
    a, b, c, d = (0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)
    return [(a, c, b), (a, b, d), (a, d, c), (b, c, d)]


@pytest.fixture
def write_stl():
    def write(path, triangles):
        data = bytearray(b"synthetic offline fixture".ljust(80, b"\0"))
        data.extend(struct.pack("<I", len(triangles)))
        for triangle in triangles:
            coordinates = [x for point in triangle for x in point]
            data.extend(struct.pack("<12fH", 0, 0, 0, *coordinates, 0))
        path.write_bytes(data)
        return path
    return write


@pytest.fixture
def single_plate_entries():
    """Only the explicitly supported BambuStudio-02.07.01.62 package layout."""
    core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    prod = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
    rel = "http://schemas.openxmlformats.org/package/2006/relationships"
    model_rel = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
    bbl = "http://schemas.bambulab.com/package/2021"

    def relationships(rows):
        children = "".join(
            f'<Relationship Target="/{target}" Id="r{i}" Type="{kind}"/>'
            for i, (target, kind) in enumerate(rows)
        )
        return f'<Relationships xmlns="{rel}">{children}</Relationships>'

    gcode = b"; synthetic fixture only\nM140 S55\nM190 S55 ; bed wait\nG1 X1 Y1 Z0.2\n"
    image = io.BytesIO()
    Image.new("RGB", (1, 1), (255, 255, 255)).save(image, format="PNG")
    geometry = "3D/Objects/object_1.model"
    types = {
        "rels": "application/vnd.openxmlformats-package.relationships+xml",
        "model": "application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
        "png": "image/png", "gcode": "text/x.gcode",
    }
    entries = {
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            + "".join(f'<Default Extension="{ext}" ContentType="{kind}"/>' for ext, kind in types.items())
            + '</Types>'
        ),
        "_rels/.rels": relationships([
            ("3D/3dmodel.model", model_rel),
            ("Metadata/plate_1.png", rel + "/metadata/thumbnail"),
            ("Metadata/plate_1.png", bbl + "/cover-thumbnail-middle"),
            ("Metadata/plate_1_small.png", bbl + "/cover-thumbnail-small"),
        ]),
        "3D/3dmodel.model": (
            f'<model xmlns="{core}" xmlns:p="{prod}" unit="millimeter" requiredextensions="p">'
            '<metadata name="Application">BambuStudio-02.07.01.62</metadata>'
            '<resources><object id="1" type="model"><components>'
            f'<component objectid="1" p:path="/{geometry}"/>'
            '</components></object></resources><build><item objectid="1" printable="1"/></build></model>'
        ),
        geometry: (
            f'<model xmlns="{core}" unit="millimeter"><resources><object id="1" type="model"><mesh>'
            '<vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/>'
            '<vertex x="0" y="1" z="0"/><vertex x="0" y="0" z="1"/></vertices>'
            '<triangles><triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/>'
            '<triangle v1="0" v2="3" v3="2"/><triangle v1="1" v2="2" v3="3"/></triangles>'
            '</mesh></object></resources><build/></model>'
        ),
        "3D/_rels/3dmodel.model.rels": relationships([(geometry, model_rel)]),
        "Metadata/_rels/model_settings.config.rels": relationships([("Metadata/plate_1.gcode", bbl + "/gcode")]),
        "Metadata/model_settings.config": (
            '<config><object id="1"><metadata key="name" value="Synthetic tetrahedron"/></object><plate>'
            '<metadata key="plater_id" value="1"/><metadata key="gcode_file" value="Metadata/plate_1.gcode"/>'
            '</plate><assemble/></config>'
        ),
        "Metadata/slice_info.config": (
            '<config><header/><plate><metadata key="index" value="1"/>'
            '<metadata key="outside" value="false"/><metadata key="prediction" value="10"/>'
            '<warning msg="synthetic_temperature_warning" level="3" error_code="fixture-only"/>'
            '<filament id="1" type="PLA" color="#FFFFFF" used_g="0.1"/>'
            '<object id="1" name="Synthetic tetrahedron"/>'
            '<layer_filament_lists><layer_filament_list layer_id="1" filaments="1"/></layer_filament_lists>'
            '</plate></config>'
        ),
        "Metadata/project_settings.config": json.dumps({
            "printer_model": "Synthetic test printer", "nozzle_diameter": ["0.4"],
            "filament_type": ["PLA"], "filament_colour": ["#FFFFFF"], "textured_plate_temp": ["55"],
            "temperature_vitrification": ["45"], "layer_height": "0.2",
        }),
        "Metadata/plate_1.json": '{}',
        "Metadata/plate_1.gcode": gcode,
        "Metadata/plate_1.gcode.md5": hashlib.md5(gcode).hexdigest(),
        "Metadata/cut_information.xml": '<objects><object id="1"><cut_id connectors_cnt="0"/></object></objects>',
        "Metadata/filament_sequence.json": '{"plate_1": [1]}',
    }
    for name in ("plate_1", "plate_1_small", "plate_no_light_1", "top_1", "pick_1"):
        entries[f"Metadata/{name}.png"] = image.getvalue()
    return {name: data.encode() if isinstance(data, str) else data for name, data in entries.items()}


@pytest.fixture
def write_package():
    def write(path, entries, duplicate=None):
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.comment = b"synthetic offline fixture"
            for name, data in entries.items():
                archive.writestr(name, data)
            if duplicate:
                archive.writestr(duplicate, entries[duplicate])
        return path
    return write
