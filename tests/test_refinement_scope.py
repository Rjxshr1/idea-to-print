"""A real diagnostic gate PASS must not become a full sculpture workflow PASS."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/idea-to-print/scripts/refinement_job.py'


@pytest.fixture
def scope_ledger():
    spec = importlib.util.spec_from_file_location('scope_ledger', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scope_job(tmp_path, scope_ledger):
    trimesh = pytest.importorskip('trimesh')
    folder = tmp_path / 'scope-job'
    folder.mkdir()
    (folder / 'job.json').write_text('{}')
    model = trimesh.creation.box([10, 10, 10])
    model.apply_translation([0, 0, 5])
    model.export(folder / 'model.stl')
    (folder / 'editable.blend').write_bytes(b'synthetic source fixture')
    (folder / 'preview.png').write_bytes(b'synthetic evidence fixture')
    assert scope_ledger.main([
        'register', '--job', str(folder), '--id', 'v1', '--mesh', str(folder / 'model.stl'),
        '--editable', str(folder / 'editable.blend'), '--preview', str(folder / 'preview.png'),
        '--note', 'Synthetic box to test coverage, not an actual design review']) == 0
    return folder


def read_job(folder):
    return json.loads((folder / 'job.json').read_text())


def narrow_check(ledger, folder, volume=(180, 180, 180)):
    profile = {'schema_version': 1, 'id': 'diagnostic-input-only', 'units': 'mm',
               'required_checks': ['input_integrity'], 'build_volume_mm': list(volume)}
    path = folder / 'profile.json'
    path.write_text(json.dumps(profile))
    assert ledger.main(['check', '--job', str(folder), '--id', 'v1', '--profile', str(path)]) == 0
    return read_job(folder)


def pass_other_stages(ledger, folder, package):
    assert ledger.main(['slice', '--job', str(folder), '--id', 'v1', '--package', str(package),
                        '--note', 'Synthetic package fixture']) == 0
    common = ['--job', str(folder), '--id', 'v1', '--reviewer', 'agent', '--status', 'PASS',
              '--evidence', str(folder / 'preview.png'), '--note', 'Synthetic attributed review fixture']
    assert ledger.main(['review', '--kind', 'appearance', *common]) == 0
    checks = [part for name in ledger.SLICE_CHECKS for part in ('--check', name + '=PASS')]
    assert ledger.main(['review', '--kind', 'slice', *common, *checks]) == 0


def test_real_narrowed_profile_stays_unknown_after_other_stage_passes(
        scope_ledger, scope_job, single_plate_entries, write_package):
    job = narrow_check(scope_ledger, scope_job)
    geometry = job['refinement']['revisions']['v1']['geometry']
    report = json.loads(Path(geometry['report']['path']).read_text())
    assert report['overall'] == 'PASS'  # Actual subprocess gate result, not a mocked verdict.
    assert report['checks']['self_intersection']['status'] == 'UNKNOWN'
    assert geometry['status'] == 'UNKNOWN'
    coverage = geometry['coverage']
    assert coverage['gate_scoped_status'] == 'PASS'
    assert coverage['profile_required'] == ['input_integrity']
    assert len(coverage['full_required']) == 16
    assert len(coverage['excluded']) == 15
    assert 'minimum_wall' in coverage['unknown_full']
    # Even automatically passed omitted checks must be explicit requirements for full acceptance.
    assert 'watertight' in coverage['unknown_full']
    package = write_package(scope_job / 'ready.gcode.3mf', single_plate_entries)
    pass_other_stages(scope_ledger, scope_job, package)
    result = scope_ledger.status(read_job(scope_job), 'v1')
    assert result['overall'] == 'UNKNOWN'
    assert result['stages']['geometry']['coverage'] == coverage
    assert all(result['stages'][key]['status'] == 'PASS'
               for key in ('appearance', 'slice_package', 'slice_review'))
    assert scope_ledger.main(['status', '--job', str(scope_job), '--id', 'v1']) == 3


def test_legacy_scoped_pass_record_is_recomputed_from_unchanged_report(scope_ledger, scope_job):
    job = narrow_check(scope_ledger, scope_job)
    geometry = job['refinement']['revisions']['v1']['geometry']
    geometry['status'] = 'PASS'
    geometry.pop('coverage')
    result = scope_ledger.status(job, 'v1')
    assert result['stages']['geometry']['status'] == 'UNKNOWN'
    assert result['stages']['geometry']['coverage']['gate_scoped_status'] == 'PASS'


def test_narrow_scope_and_attributed_reviews_cannot_mask_automatic_failure(
        scope_ledger, scope_job, single_plate_entries, write_package):
    job = narrow_check(scope_ledger, scope_job, volume=(9, 180, 180))
    geometry = job['refinement']['revisions']['v1']['geometry']
    assert geometry['status'] == 'FAIL'
    assert geometry['coverage']['failed_checks'] == ['build_volume']
    assert 'build_volume' in geometry['coverage']['excluded']
    package = write_package(scope_job / 'ready.gcode.3mf', single_plate_entries)
    pass_other_stages(scope_ledger, scope_job, package)
    result = scope_ledger.status(read_job(scope_job), 'v1')
    assert result['overall'] == 'FAIL'
    assert result['stages']['geometry']['status'] == 'FAIL'


def complete_report(ledger):
    return {'schema_version': 1, 'overall_scope': 'geometry', 'overall': 'PASS',
            'required_checks': list(ledger.FULL_GEOMETRY_CHECKS),
            'checks': {key: {'category': 'geometry', 'required': True, 'status': 'PASS'}
                       for key in ledger.FULL_GEOMETRY_CHECKS}}


def test_coverage_requires_every_fixed_check_and_preserves_unknown_tool_result(scope_ledger):
    report = complete_report(scope_ledger)
    assert scope_ledger.geometry_coverage(report)['status'] == 'PASS'
    for mutation in ('missing', 'excluded', 'not_required', 'wrong_category', 'unknown', 'invalid_schema', 'unknown_gate'):
        changed = copy.deepcopy(report)
        if mutation == 'missing':
            changed['checks'].pop('minimum_connection')
        elif mutation == 'excluded':
            changed['required_checks'].remove('minimum_connection')
        elif mutation == 'not_required':
            changed['checks']['minimum_connection']['required'] = False
        elif mutation == 'wrong_category':
            changed['checks']['minimum_connection']['category'] = 'appearance'
        elif mutation == 'unknown':
            changed['checks']['minimum_connection']['status'] = 'UNKNOWN'
        elif mutation == 'invalid_schema':
            changed['schema_version'] = 999
        else:
            changed['overall'] = 'UNKNOWN'
        assert scope_ledger.geometry_coverage(changed)['status'] == 'UNKNOWN', mutation


def test_missing_coverage_cannot_erase_fail_and_external_reviews_do_not_enter_geometry(scope_ledger):
    report = {'overall': 'FAIL'}
    assert scope_ledger.geometry_coverage(report)['status'] == 'FAIL'
    report = complete_report(scope_ledger)
    report['checks']['stability']['status'] = 'FAIL'
    report['required_checks'].remove('stability')
    report['checks']['stability']['category'] = 'appearance'  # Bad category cannot hide a fixed geometry failure.
    assert scope_ledger.geometry_coverage(report)['status'] == 'FAIL'
    report = complete_report(scope_ledger)
    report['checks']['appearance_fidelity'] = {'category': 'appearance', 'required': False, 'status': 'UNKNOWN'}
    assert scope_ledger.geometry_coverage(report)['status'] == 'PASS'
