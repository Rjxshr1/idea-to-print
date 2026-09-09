"""Evidence invalidation and real slice integrity, using only synthetic assets."""
import importlib.util
import json
from pathlib import Path

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ledger():
    spec = importlib.util.spec_from_file_location('ledger_under_test', ROOT / 'skills/idea-to-print/scripts/refinement_job.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def job(tmp_path, write_stl, tetrahedron):
    folder = tmp_path / 'job'
    folder.mkdir()
    (folder / 'job.json').write_text(json.dumps({'state': 'modeled', 'authorization': {'print': None}, 'custom': {'keep': 42}}))
    write_stl(folder / 'model.stl', tetrahedron)
    (folder / 'editable.blend').write_bytes(b'synthetic editable source placeholder')
    Image.new('RGB', (8, 8), 'white').save(folder / 'preview.png')
    return folder


def register(ledger, job, name='v1', parent=None):
    argv = ['register', '--job', str(job), '--id', name, '--mesh', str(job / 'model.stl'),
            '--editable', str(job / 'editable.blend'), '--preview', str(job / 'preview.png'),
            '--note', 'Synthetic shape baseline']
    if parent:
        argv += ['--parent', parent]
    return ledger.main(argv)


def read(job):
    return json.loads((job / 'job.json').read_text())


def review(ledger, job, kind='appearance', status='PASS', checks=None):
    args = ['review', '--job', str(job), '--id', 'v1', '--kind', kind, '--status', status,
            '--reviewer', 'agent', '--note', 'Reviewed actual synthetic fixture evidence',
            '--evidence', str(job / 'preview.png')]
    for check in checks or []:
        args += ['--check', check]
    return ledger.main(args)


def test_registration_preserves_scope_and_refuses_overwrite(ledger, job):
    assert register(ledger, job) == 0
    before = read(job)
    assert before['authorization']['print'] is None
    assert before['state'] == 'modeled' and before['custom'] == {'keep': 42}
    assert register(ledger, job) == 2
    assert read(job) == before
    assert ledger.status(before, 'v1')['overall'] == 'UNKNOWN'
    assert not (job / '.refinement.lock').exists()


@pytest.mark.parametrize('filename', ['model.stl', 'preview.png', 'editable.blend'])
def test_changed_artifact_invalidates_all_old_reviews(ledger, job, filename):
    register(ledger, job)
    assert review(ledger, job) == 0
    assert ledger.status(read(job), 'v1')['stages']['appearance']['status'] == 'PASS'
    with (job / filename).open('ab') as f:
        f.write(b'changed')
    result = ledger.status(read(job), 'v1')
    assert result['overall'] == 'UNKNOWN' and result['stale_artifacts']
    assert result['stages']['appearance']['status'] == 'UNKNOWN'
    assert review(ledger, job) == 2
    assert register(ledger, job, 'v2', 'v1') == 0
    assert 'appearance' not in read(job)['refinement']['revisions']['v2']['reviews']


def test_review_cannot_override_measured_failure(ledger, job):
    register(ledger, job)
    data = read(job)
    rev = data['refinement']['revisions']['v1']
    # This fixture is the ledger consumer boundary; validator tests establish
    # actual geometry measurements separately.
    evidence = job / 'geometry.json'
    evidence.write_text('{"overall":"FAIL"}')
    rev['geometry'] = {'status': 'FAIL', 'at': 'fixture',
        'mesh_sha256': rev['artifacts']['mesh']['sha256'], 'report': ledger.artifact(evidence)}
    (job / 'job.json').write_text(json.dumps(data))
    assert review(ledger, job) == 0
    result = ledger.status(read(job), 'v1')
    assert result['overall'] == 'FAIL'
    assert result['stages']['geometry']['status'] == 'FAIL'
    evidence.write_text('changed report')
    assert ledger.status(read(job), 'v1')['stages']['geometry']['status'] == 'UNKNOWN'


def test_invalid_revision_and_locked_job_do_not_write(ledger, job):
    before = (job / 'job.json').read_bytes()
    assert register(ledger, job, '../outside') == 2
    assert (job / 'job.json').read_bytes() == before
    lock = job / '.refinement.lock'
    lock.write_text('another writer')
    assert register(ledger, job) == 2
    assert lock.read_text() == 'another writer'
    assert (job / 'job.json').read_bytes() == before


def test_slice_review_requires_specific_checks_and_matching_package(ledger, job, single_plate_entries, write_package):
    register(ledger, job)
    assert review(ledger, job, kind='slice') == 2
    package = write_package(job / 'ready.gcode.3mf', single_plate_entries)
    args = ['slice', '--job', str(job), '--id', 'v1', '--package', str(package),
            '--note', 'Synthetic fixture toolpaths; no real printer']
    assert ledger.main(args) == 0
    assert read(job)['refinement']['revisions']['v1']['slice']['warnings']
    assert review(ledger, job, kind='slice') == 2
    checks = [name + '=PASS' for name in ledger.SLICE_CHECKS]
    assert review(ledger, job, kind='slice', checks=checks) == 0
    assert ledger.status(read(job), 'v1')['stages']['slice_review']['status'] == 'PASS'
    broken = dict(single_plate_entries)
    broken['Metadata/plate_1.gcode'] += b'; changed without checksum\n'
    write_package(package, broken)
    assert ledger.status(read(job), 'v1')['stages']['slice_package']['status'] == 'UNKNOWN'
    assert ledger.main(args) == 0
    assert ledger.status(read(job), 'v1')['stages']['slice_package']['status'] == 'FAIL'
    assert ledger.status(read(job), 'v1')['stages']['slice_review']['status'] == 'UNKNOWN'
    assert review(ledger, job, kind='slice', checks=checks) == 0
    assert ledger.status(read(job), 'v1')['overall'] == 'FAIL'


def test_installed_location_resolves_sibling_tools(ledger, job, single_plate_entries, write_package, tmp_path):
    # Exercise the portable installed layout, away from the repository cwd.
    import shutil
    target = tmp_path / 'installed'
    shutil.copytree(ROOT / 'skills', target)
    previous = ledger.SKILLS
    ledger.SKILLS = target
    try:
        register(ledger, job)
        package = write_package(job / 'ready.gcode.3mf', single_plate_entries)
        assert ledger.main(['slice', '--job', str(job), '--id', 'v1', '--package', str(package), '--note', 'Portable test']) == 0
        assert ledger.status(read(job), 'v1')['stages']['slice_package']['status'] == 'PASS'
    finally:
        ledger.SKILLS = previous


def test_real_geometry_check_binds_profile_and_preserves_unknown(ledger, job):
    register(ledger, job)
    profile = job / 'profile.json'
    profile.write_bytes((ROOT / 'skills/3d-print-workflow/references/printability-profile.example.json').read_bytes())
    assert ledger.main(['check', '--job', str(job), '--id', 'v1', '--profile', str(profile)]) == 0
    revision = read(job)['refinement']['revisions']['v1']
    report = json.loads(Path(revision['geometry']['report']['path']).read_text())
    assert report['checks']['watertight']['status'] == 'PASS'
    assert report['checks']['self_intersection']['status'] == 'UNKNOWN'
    assert report['mesh']['sha256'] == ledger.sha(job / 'model.stl')
    assert report['profile']['sha256'] == ledger.sha(profile)
    assert ledger.status(read(job), 'v1')['stages']['geometry']['status'] == 'UNKNOWN'
    changed = json.loads(profile.read_text())
    changed['build_volume_mm'] = [0.5, 0.5, 0.5]
    changed['reserve_total_mm'] = [0, 0, 0]
    profile.write_text(json.dumps(changed))
    assert 'STALE' in ledger.status(read(job), 'v1')['stages']['geometry']['reason']
    assert ledger.main(['check', '--job', str(job), '--id', 'v1', '--profile', str(profile)]) == 0
    assert review(ledger, job) == 0
    assert ledger.status(read(job), 'v1')['overall'] == 'FAIL'
