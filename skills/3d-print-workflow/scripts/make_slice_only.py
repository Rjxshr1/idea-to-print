#!/usr/bin/env python3
"""Convert a verified Bambu CLI single-plate 3MF to the sliced-file layout.

Offline only: this does not slice, assess machine compatibility, upload, or print.
Supported producer: BambuStudio-02.07.01.62, core/production XML namespaces,
external object_N.model geometry, exactly plate_1, no assembly items. Other
layouts fail closed. Original source, G-code, settings, warnings and all entries
outside the two edited XML files and explicitly removed geometry are preserved.
A successful result still needs official Studio preview and dispatch preflight.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import sys
import xml.dom.minidom as DOM
from xml.parsers import expat
from xml.etree import ElementTree as ET
import zipfile

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
MODEL_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
BBL = "http://schemas.bambulab.com/package/2021"
PRODUCER = "BambuStudio-02.07.01.62"
MODEL = "3D/3dmodel.model"
SETTINGS = "Metadata/model_settings.config"
GEOM_RELS = "3D/_rels/3dmodel.model.rels"
GCODE = "Metadata/plate_1.gcode"
MD5 = GCODE + ".md5"
EDITED = {MODEL, SETTINGS}
FIXED = {
    "[Content_Types].xml", "_rels/.rels", MODEL, GEOM_RELS,
    "Metadata/plate_1.png", "Metadata/plate_1_small.png",
    "Metadata/plate_no_light_1.png", "Metadata/top_1.png", "Metadata/pick_1.png",
    "Metadata/plate_1.json", "Metadata/project_settings.config", GCODE, MD5,
    "Metadata/_rels/model_settings.config.rels", SETTINGS,
    "Metadata/slice_info.config", "Metadata/cut_information.xml",
    "Metadata/filament_sequence.json",
}
OPTIONAL = {"Metadata/cut_information.xml", "Metadata/filament_sequence.json"}


class Unsupported(ValueError):
    """Invalid input or an unverified package layout."""


def require(condition, message):
    if not condition:
        raise Unsupported(message)


def xml(data, name):
    parser = expat.ParserCreate()
    def reject(*args):
        raise Unsupported(f"DTD/entity declarations are unsupported: {name}")
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    try:
        parser.Parse(data, True)
        return ET.fromstring(data)
    except (expat.ExpatError, ET.ParseError) as exc:
        raise Unsupported(f"Malformed XML in {name}: {exc}") from exc


def one(parent, tag, label):
    found = parent.findall(tag)
    require(len(found) == 1, f"Expected exactly one {label}")
    return found[0]


def metadata(parent):
    result = {}
    for elem in parent.findall("metadata"):
        key = elem.get("key")
        require(key and key not in result, "Missing or duplicate plate metadata key")
        result[key] = elem.get("value")
    return result


def relationships(data, name, names):
    root = xml(data, name)
    require(root.tag == f"{{{REL}}}Relationships", f"Unsupported relationships root: {name}")
    rows = []
    ids = set()
    for elem in root:
        require(elem.tag == f"{{{REL}}}Relationship", f"Unknown relationship element: {name}")
        require(set(elem.attrib) == {"Target", "Id", "Type"}, f"Unsupported relationship attributes: {name}")
        target = elem.get("Target")
        require(target.startswith("/") and target[1:] in names, f"Missing/nonlocal relationship target: {target}")
        require(elem.get("Id") not in ids, f"Duplicate relationship ID: {name}")
        ids.add(elem.get("Id"))
        rows.append((target[1:], elem.get("Type")))
    return rows


def geometry_name(name):
    p = PurePosixPath(name)
    stem = p.stem
    return str(p.parent) == "3D/Objects" and p.suffix == ".model" and stem.startswith("object_") and stem[7:].isdigit()


def validate(entries):
    names = set(entries)
    require(FIXED - OPTIONAL <= names, f"Missing required entries: {sorted((FIXED - OPTIONAL) - names)}")
    geometry = {n for n in names if geometry_name(n)}
    require(geometry and names == (names & FIXED) | geometry,
            f"Unsupported entries/layout (only plate_1 accepted): {sorted(names - FIXED - geometry)}")
    digest_text = entries[MD5].decode("ascii").strip()
    require(len(digest_text) == 32 and all(c in "0123456789abcdefABCDEF" for c in digest_text), "Invalid G-code MD5 sidecar")
    digest = hashlib.md5(entries[GCODE]).hexdigest()
    require(digest == digest_text.lower(), "G-code MD5 does not match sidecar")
    require(entries[GCODE].strip(), "Empty G-code")
    model = xml(entries[MODEL], MODEL)
    require(model.tag == f"{{{CORE}}}model" and model.get("unit") == "millimeter", "Unsupported model root/unit")
    require(model.get("requiredextensions") == "p", "Unsupported required extensions")
    applications = [m.text for m in model.findall(f"{{{CORE}}}metadata") if m.get("name") == "Application"]
    require(applications == [PRODUCER], f"Unsupported producer; verified only {PRODUCER}")
    require(all(c.tag in {f"{{{CORE}}}metadata", f"{{{CORE}}}resources", f"{{{CORE}}}build"} for c in model), "Unknown root model children")
    resources = one(model, f"{{{CORE}}}resources", "model resources")
    build = one(model, f"{{{CORE}}}build", "model build")
    require(not resources.attrib and set(build.attrib) <= {f"{{{PROD}}}UUID"}, "Unsupported resources/build attributes")
    resource_ids, referenced_geometry = set(), set()
    for obj in resources:
        require(obj.tag == f"{{{CORE}}}object" and obj.get("id") and obj.get("id") not in resource_ids, "Unsupported/duplicate model resource")
        require(set(obj.attrib) <= {"id", "type", f"{{{PROD}}}UUID"} and obj.get("type") == "model", "Unsupported resource object attributes")
        resource_ids.add(obj.get("id"))
        comp = one(obj, f"{{{CORE}}}components", "external components")
        require(len(obj) == 1 and len(comp) > 0 and not comp.attrib, "Unsupported resource object layout")
        for part in comp:
            require(part.tag == f"{{{CORE}}}component" and len(part) == 0, "Unsupported component layout")
            require(set(part.attrib) <= {"objectid", "transform", f"{{{PROD}}}path", f"{{{PROD}}}UUID"}, "Unsupported component attributes")
            path = part.get(f"{{{PROD}}}path", "")
            require(path.startswith("/") and path[1:] in geometry and part.get("objectid"), "Missing external component geometry")
            referenced_geometry.add(path[1:])
            external = xml(entries[path[1:]], path[1:])
            require(external.tag == f"{{{CORE}}}model" and external.get("unit") == "millimeter", "Unsupported external geometry model")
            require(all(c.tag in {f"{{{CORE}}}metadata", f"{{{CORE}}}resources", f"{{{CORE}}}build"} for c in external), "Unsupported external geometry layout")
            external_build = one(external, f"{{{CORE}}}build", "external empty build")
            require(len(external_build) == 0 and not external_build.attrib, "External model build is not empty")
            ext_resources = one(external, f"{{{CORE}}}resources", "external resources")
            require(all(c.tag == f"{{{CORE}}}object" and len(c) == 1 and c[0].tag == f"{{{CORE}}}mesh" for c in ext_resources), "Only external mesh objects supported")
            require(sum(c.get("id") == part.get("objectid") for c in ext_resources) == 1, "External object ID missing/duplicated")
    require(referenced_geometry == geometry, "Unreferenced geometry entries")
    require(len(build) > 0, "Already sliced-only or empty build")
    for item in build:
        require(item.tag == f"{{{CORE}}}item" and len(item) == 0 and item.get("objectid") in resource_ids, "Unsupported build reference")
        require(set(item.attrib) <= {"objectid", "transform", "printable", f"{{{PROD}}}UUID"}, "Unsupported build attributes")
    geom_rows = relationships(entries[GEOM_RELS], GEOM_RELS, names)
    require(len(geom_rows) == len(geometry) and set(geom_rows) == {(n, MODEL_REL) for n in geometry}, "Geometry relationship mismatch")
    for rel_name in ("_rels/.rels", "Metadata/_rels/model_settings.config.rels"):
        rows = relationships(entries[rel_name], rel_name, names)
        require(all(target not in geometry for target, _ in rows), "Geometry referenced outside removable relationships")
    require(relationships(entries["Metadata/_rels/model_settings.config.rels"], "Metadata/_rels/model_settings.config.rels", names) == [(GCODE, BBL + "/gcode")], "Unsupported G-code relationships")
    root_rows = relationships(entries["_rels/.rels"], "_rels/.rels", names)
    require(root_rows == [(MODEL, MODEL_REL),
                         ("Metadata/plate_1.png", REL + "/metadata/thumbnail"),
                         ("Metadata/plate_1.png", BBL + "/cover-thumbnail-middle"),
                         ("Metadata/plate_1_small.png", BBL + "/cover-thumbnail-small")], "Unsupported root relationships")
    types = xml(entries["[Content_Types].xml"], "[Content_Types].xml")
    ct = "http://schemas.openxmlformats.org/package/2006/content-types"
    require(types.tag == f"{{{ct}}}Types" and all(c.tag == f"{{{ct}}}Default" and set(c.attrib) == {"Extension", "ContentType"} for c in types), "Unsupported content types/overrides")
    expected_types = {"rels": "application/vnd.openxmlformats-package.relationships+xml",
                      "model": "application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
                      "png": "image/png", "gcode": "text/x.gcode"}
    require(len(types) == len(expected_types) and {c.get("Extension"): c.get("ContentType") for c in types} == expected_types, "Unsupported content type mappings")
    if "Metadata/cut_information.xml" in entries:
        cuts = xml(entries["Metadata/cut_information.xml"], "Metadata/cut_information.xml")
        require(cuts.tag == "objects" and all(c.tag == "object" and len(c) == 1 and c[0].tag == "cut_id" and c[0].get("connectors_cnt") == "0" for c in cuts), "Cut connectors/unknown cut layouts unsupported")
    settings = xml(entries[SETTINGS], SETTINGS)
    require(settings.tag == "config" and not settings.attrib and all(c.tag in {"object", "plate", "assemble"} for c in settings), "Unsupported model settings layout")
    require({c.get("id") for c in settings.findall("object")} == resource_ids and len(settings.findall("object")) == len(resource_ids), "Settings/resource object mismatch")
    plate = one(settings, "plate", "model settings plate")
    plate_values = metadata(plate)
    require(plate_values.get("plater_id") == "1" and plate_values.get("gcode_file") == GCODE, "Only single plate 1 is supported")
    require(not plate.attrib and all(c.tag == "metadata" for c in plate), "Unsupported model settings plate contents")
    assemble = one(settings, "assemble", "assemble")
    require(len(assemble) == 0 and not assemble.attrib, "Assembly items are unsupported")
    slices = xml(entries["Metadata/slice_info.config"], "Metadata/slice_info.config")
    require(slices.tag == "config" and all(c.tag in {"header", "plate"} for c in slices), "Unsupported slice info layout")
    slice_plate = one(slices, "plate", "slice info plate")
    require(metadata(slice_plate).get("index") == "1", "Slice info is not single plate 1")
    for name in ("Metadata/plate_1.json", "Metadata/project_settings.config"):
        require(isinstance(json.loads(entries[name]), dict), f"Expected JSON object: {name}")
    if "Metadata/filament_sequence.json" in entries:
        require(set(json.loads(entries["Metadata/filament_sequence.json"])) == {"plate_1"}, "Unsupported filament sequence plates")
    return geometry, digest


def convert_xml(entries):
    # DOM preserves namespace declarations, including the now-unused required p
    # namespace. All changed files are reparsed; non-geometry children are checked.
    result = {}
    for name in EDITED:
        doc = DOM.parseString(entries[name])
        root = doc.documentElement
        for child in list(root.childNodes):
            if child.nodeType != child.ELEMENT_NODE:
                continue
            if name == MODEL and child.namespaceURI == CORE and child.localName in {"resources", "build"}:
                while child.firstChild:
                    child.removeChild(child.firstChild)
                for attribute in list(child.attributes.keys()):
                    child.removeAttribute(attribute)
            elif name == SETTINGS and child.tagName == "object":
                root.removeChild(child)
        result[name] = doc.toxml(encoding="UTF-8")
        before, after = xml(entries[name], name), xml(result[name], name)
        excluded = {f"{{{CORE}}}resources", f"{{{CORE}}}build"} if name == MODEL else {"object"}
        def semantic(elem):
            return (elem.tag, sorted(elem.attrib.items()), (elem.text or "").strip(), [semantic(c) for c in elem])
        require(before.attrib == after.attrib and [semantic(c) for c in before if c.tag not in excluded] == [semantic(c) for c in after if c.tag not in excluded], f"Non-geometry XML changed: {name}")
        if name == MODEL:
            require(all(len(c) == 0 and not c.attrib for c in after if c.tag in excluded), "Geometry removal incomplete")
        else:
            require(not after.findall("object"), "Settings object removal incomplete")
        doc.unlink()
    return result


def sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def convert(source, output, report):
    require(not Path(output).is_symlink() and not Path(report).is_symlink(), "Refusing output/report symlinks")
    source, output, report = (Path(p).resolve() for p in (source, output, report))
    require(len({source, output, report}) == 3, "Source, output and report must be distinct paths")
    require(not output.exists() and not report.exists(), "Refusing to overwrite output/report")
    require(output.name.endswith(".gcode.3mf"), "Output must end in .gcode.3mf")
    require(output.parent.is_dir() and report.parent.is_dir(), "Output/report parent directories must exist")
    source_sha = sha256(source)
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        require(len(names) == len(set(n.casefold() for n in names)), "Duplicate/case-colliding ZIP entries")
        require(sum(i.file_size for i in infos) <= 512 * 1024 * 1024, "Package exceeds supported 512 MiB uncompressed limit")
        require(all(not i.is_dir() and not i.flag_bits & 1 and i.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED} for i in infos), "Unsupported ZIP directory/encryption/compression")
        entries = {i.filename: archive.read(i) for i in infos}  # validates ZIP CRCs
        archive_comment = archive.comment
    removed, gcode_md5 = validate(entries)
    removed = removed | {GEOM_RELS}
    edited = convert_xml(entries)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.comment = archive_comment
        for info in infos:
            if info.filename not in removed:
                archive.writestr(info, edited.get(info.filename, entries[info.filename]))
    unchanged = sorted(set(entries) - removed - EDITED)
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        require(set(archive.namelist()) == set(entries) - removed, "Output entry set mismatch")
        require(all(archive.read(n) == entries[n] for n in unchanged), "Unchanged entry differs")
        require(hashlib.md5(archive.read(GCODE)).hexdigest() == gcode_md5, "Output G-code digest mismatch")
    require(sha256(source) == source_sha, "Source changed during conversion")
    payload = buffer.getvalue()
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(), "status": "verified_offline",
        "supported_producer": PRODUCER, "plate": 1, "source": str(source), "output": str(output),
        "source_sha256": source_sha, "output_sha256": hashlib.sha256(payload).hexdigest(),
        "gcode_md5": gcode_md5, "removed_geometry": sorted(removed),
        "edited_xml_entries": sorted(EDITED), "byte_identical_entries": unchanged,
        "source_unchanged": True, "gcode_byte_identical": True,
        "settings_and_warnings_retained": True, "non_geometry_xml_semantically_identical": True,
        "official_studio_preview_required": True, "printer_dispatch_performed": False,
    }
    created = []
    try:
        with output.open("xb") as handle:
            created.append(output)
            handle.write(payload)
        require(sha256(output) == result["output_sha256"], "Output disk digest mismatch")
        with report.open("x", encoding="utf-8") as handle:
            created.append(report)
            json.dump(result, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = convert(args.source, args.output, args.report)
    except (Unsupported, OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
