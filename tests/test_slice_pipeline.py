"""Native completion and exact profile/geometry bindings; synthetic assets only."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pipeline():
    spec = importlib.util.spec_from_file_location('tested_slice_pipeline', ROOT / 'skills/3d-print-workflow/scripts/slice_pipeline.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def run(pipeline, tmp_path, write_stl, tetrahedron):
    mesh = write_stl(tmp_path / 'input.stl', tetrahedron)
    profiles = []
    for name in ('machine', 'process', 'filament'):
        p = tmp_path / (name + '.json')
        p.write_text(json.dumps({'name': name}))
        profiles.append(p)
    target = tmp_path / 'slice'
    pipeline.prepare(mesh, target, *profiles, 'slicer.exe', '2.7.1.62', [180, 180, 180], 5)
    return target


def finish(pipeline, run, write_package, entries):
    write_package(run / 'result.gcode.3mf', entries)
    (run / 'result.json').write_text(json.dumps({'return_code': 0, 'error_string': 'Success.'}))
    (run / 'execution.json').write_text(json.dumps({'launch_sha256': pipeline.sha(run / 'launch.json'),
        'exit_code': 0, 'slicer_version': '2.7.1.62'}))


def test_real_result_required_and_warnings_preserved(pipeline, run, write_package, single_plate_entries):
    write_package(run / 'result.gcode.3mf', single_plate_entries)
    with pytest.raises(ValueError, match='execution receipt'):
        pipeline.collect(run)
    finish(pipeline, run, write_package, single_plate_entries)
    result = pipeline.collect(run)
    assert result['audit']['warnings']
    assert result['audit']['md5_match']
    assert result['status'] == 'PASS'
    assert result['mesh']['sha256'] == json.loads((run / 'request.json').read_text())['mesh']['sha256']
    assert result['printer_actions'] == 'none'


@pytest.mark.parametrize('target', ['machine.json', 'positioned.3mf'])
def test_edited_inputs_invalidate_slice(pipeline, run, write_package, single_plate_entries, target):
    finish(pipeline, run, write_package, single_plate_entries)
    with (run / target).open('ab') as output:
        output.write(b'changed')
    with pytest.raises(ValueError, match='STALE'):
        pipeline.collect(run)


def test_launch_cannot_substitute_another_mesh(pipeline, run, write_package, single_plate_entries):
    launch = json.loads((run / 'launch.json').read_text())
    launch['arguments'][-1] = 'different-model.3mf'
    (run / 'launch.json').write_text(json.dumps(launch))
    finish(pipeline, run, write_package, single_plate_entries)
    with pytest.raises(ValueError, match='arguments differ'):
        pipeline.collect(run)


def test_shell_success_does_not_hide_native_failure(pipeline, run, write_package, single_plate_entries):
    finish(pipeline, run, write_package, single_plate_entries)
    (run / 'result.json').write_text(json.dumps({'return_code': 7, 'error_string': 'Cannot slice'}))
    with pytest.raises(ValueError, match='successful completion'):
        pipeline.collect(run)


def test_position_preserves_size_and_checks_reserve(pipeline, tmp_path, write_stl, tetrahedron):
    mesh = write_stl(tmp_path / 'mesh.stl', tetrahedron)
    report = pipeline.position(mesh, tmp_path / 'placed.3mf', [180, 180, 180], 5)
    assert report['dimensions_mm'] == [1, 1, 1]
    assert report['positioned_bounds_mm'] == [[89.5, 89.5, 0.0], [90.5, 90.5, 1.0]]
    with pytest.raises(ValueError, match='exceeds'):
        pipeline.position(mesh, tmp_path / 'bad.3mf', [1, 1, 1], 0.1)


def test_cli_does_not_return_success_for_outside_plate(pipeline, run, write_package, single_plate_entries):
    entries = dict(single_plate_entries)
    entries['Metadata/slice_info.config'] = entries['Metadata/slice_info.config'].replace(
        b'key="outside" value="false"', b'key="outside" value="true"')
    finish(pipeline, run, write_package, entries)
    assert pipeline.main(['collect', '--run', str(run)]) == 2
    assert json.loads((run / 'slice-provenance.json').read_text())['status'] == 'FAIL'


def test_host_preflight_refuses_stale_inputs_before_launch(run):
    spec = importlib.util.spec_from_file_location('tested_bambu_launcher', ROOT / 'skills/3d-print-workflow/scripts/run_bambu_slice.py')
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    prepared = json.loads((run / 'request.json').read_text())
    launcher.verify_inputs(run, prepared)
    (run / 'machine.json').write_text('{}')
    with pytest.raises(ValueError, match='STALE prepared'):
        launcher.verify_inputs(run, prepared)
    assert not (run / 'execution-intent.json').exists()


def test_only_one_concurrent_host_can_claim_run(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    spec = importlib.util.spec_from_file_location('tested_claim_launcher', ROOT / 'skills/3d-print-workflow/scripts/run_bambu_slice.py')
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    def claim(index):
        try:
            launcher.claim_execution(tmp_path, {'pid': index})
            return index
        except FileExistsError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = [x for x in pool.map(claim, range(8)) if x is not None]
    assert len(winners) == 1
    assert json.loads((tmp_path / 'execution-intent.json').read_text())['pid'] == winners[0]


def test_preview_preserves_original_toolpaths_and_reuses_exact_copy(pipeline, run, write_package, single_plate_entries):
    finish(pipeline, run, write_package, single_plate_entries)
    source_hash = pipeline.sha(run / 'result.gcode.3mf')
    report = pipeline.preview(run)
    assert Path(report['gcode']['path']).read_bytes() == single_plate_entries['Metadata/plate_1.gcode']
    assert pipeline.preview(run) == report
    assert pipeline.sha(run / 'result.gcode.3mf') == source_hash
    assert report['toolpath_bytes_changed'] is False
    assert pipeline.collect(run)['audit']['warnings']


def test_preview_does_not_overwrite_edited_toolpaths(pipeline, run, write_package, single_plate_entries):
    finish(pipeline, run, write_package, single_plate_entries)
    pipeline.preview(run)
    target = run / 'exact-toolpaths.gcode'
    target.write_bytes(b'changed')
    with pytest.raises(ValueError, match='Existing preview differs'):
        pipeline.preview(run)
    assert target.read_bytes() == b'changed'
