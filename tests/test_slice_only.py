"""Narrow Bambu package conversion must preserve data and refuse unknown layouts."""
import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest


def test_supported_single_plate_removes_only_editable_geometry(tmp_path, load_helper, single_plate_entries, write_package):
    helper = load_helper("slice_only")
    source = write_package(tmp_path / "source.3mf", single_plate_entries)
    original = source.read_bytes()
    output, report_path = tmp_path / "output.gcode.3mf", tmp_path / "report.json"

    report = helper.convert(source, output, report_path)

    assert source.read_bytes() == original
    assert json.loads(report_path.read_text()) == report
    assert report["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    with zipfile.ZipFile(output) as archive:
        removed = {"3D/Objects/object_1.model", "3D/_rels/3dmodel.model.rels"}
        edited = {"3D/3dmodel.model", "Metadata/model_settings.config"}
        assert set(archive.namelist()) == set(single_plate_entries) - removed
        for name in set(single_plate_entries) - removed - edited:
            assert archive.read(name) == single_plate_entries[name], name
        assert archive.comment == b"synthetic offline fixture"
        model = ET.fromstring(archive.read("3D/3dmodel.model"))
        core = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
        assert len(model.find(core + "resources")) == 0
        assert len(model.find(core + "build")) == 0
        settings = ET.fromstring(archive.read("Metadata/model_settings.config"))
        assert settings.findall("object") == []
        assert len(settings.findall("plate")) == 1
    # Independent audit of the resulting package still sees the original warning.
    audit = load_helper("audit").slice_report(output)
    assert audit["md5_match"] is True
    assert audit["warnings"][0]["error_code"] == "fixture-only"


@pytest.mark.parametrize("existing", ["output", "report"])
def test_existing_artifacts_are_not_overwritten(tmp_path, load_helper, single_plate_entries, write_package, existing):
    helper = load_helper("slice_only")
    source = write_package(tmp_path / "source.3mf", single_plate_entries)
    original = source.read_bytes()
    output, report = tmp_path / "output.gcode.3mf", tmp_path / "report.json"
    target = output if existing == "output" else report
    target.write_bytes(b"existing content")
    with pytest.raises(helper.Unsupported):
        helper.convert(source, output, report)
    assert target.read_bytes() == b"existing content"
    assert not (report if existing == "output" else output).exists()
    assert source.read_bytes() == original


@pytest.mark.parametrize("mutation", ["version", "second_plate", "duplicate", "corrupt_md5", "assembly"])
def test_unsupported_input_produces_no_output(tmp_path, load_helper, single_plate_entries, write_package, mutation):
    helper = load_helper("slice_only")
    duplicate = None
    if mutation == "version":
        single_plate_entries["3D/3dmodel.model"] = single_plate_entries["3D/3dmodel.model"].replace(
            b"BambuStudio-02.07.01.62", b"BambuStudio-99.00.00.00")
    elif mutation == "second_plate":
        single_plate_entries["Metadata/plate_2.gcode"] = b"; another plate\n"
    elif mutation == "duplicate":
        duplicate = "Metadata/plate_1.gcode"
    elif mutation == "corrupt_md5":
        single_plate_entries["Metadata/plate_1.gcode.md5"] = b"0" * 32
    else:
        single_plate_entries["Metadata/model_settings.config"] = single_plate_entries["Metadata/model_settings.config"].replace(
            b"<assemble/>", b'<assemble><assemble_item object_id="1"/></assemble>')
    if duplicate:
        with pytest.warns(UserWarning):
            source = write_package(tmp_path / "source.3mf", single_plate_entries, duplicate=duplicate)
    else:
        source = write_package(tmp_path / "source.3mf", single_plate_entries)
    original = source.read_bytes()
    output, report = tmp_path / "output.gcode.3mf", tmp_path / "report.json"

    with pytest.raises(helper.Unsupported):
        helper.convert(source, output, report)

    assert source.read_bytes() == original
    assert not output.exists()
    assert not report.exists()
