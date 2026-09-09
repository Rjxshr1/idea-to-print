"""Offline control-loop regressions; no model service, renderer, slicer or printer."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/idea-to-print/scripts/workflow_v2.py'


@pytest.fixture
def flow():
    spec = importlib.util.spec_from_file_location('tested_workflow_v2', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def job(tmp_path, flow, write_stl, tetrahedron):
    folder = tmp_path / 'job'
    folder.mkdir()
    (folder / 'job.json').write_text(json.dumps({'state': 'legacy', 'authorization': {'print': None}, 'private': 42}))
    Image.new('RGB', (8, 8), 'white').save(folder / 'reference.png')
    write_stl(folder / 'model.stl', tetrahedron)
    (folder / 'editable.blend').write_bytes(b'synthetic editable fixture')
    Image.new('RGB', (8, 8), 'grey').save(folder / 'preview.png')
    flow.init(folder)
    return folder


def read(job):
    return json.loads((job / 'job.json').read_text())


def reference(flow, job, verdict='PASS'):
    return flow.record_stage(job, 'reference', verdict, evidence=[job / 'reference.png'],
                            checks={key: verdict for key in flow.STAGE_CHECKS['reference']},
                            note='Synthetic single image: inspect identity, pose and usable input')


def register(flow, job, revision='v1', mesh=None, preview=None):
    assert flow.ledger.main(['register', '--job', str(job), '--id', revision,
                            '--mesh', str(mesh or job / 'model.stl'), '--editable', str(job / 'editable.blend'),
                            '--preview', str(preview or job / 'preview.png'), '--note', 'Synthetic baseline']) == 0
    flow.select_candidate(job, revision)


def renders(flow, job):
    views = {}
    for i, name in enumerate(sorted(flow.REQUIRED_VIEWS)):
        path = job / (name + '.png')
        Image.new('RGB', (8, 8), (i * 20, 20, 100)).save(path)
        views[name] = {'render_path': str(path), 'sha256': flow.ledger.sha(path)}
    report = job / 'render-report.json'
    report.write_text(json.dumps({'actual_mesh': True, 'stl_sha256': flow.ledger.sha(job / 'model.stl'), 'views': views}))
    return report


def visual(flow, job, stage, verdict='PASS', reviewer='agent'):
    return flow.record_stage(job, stage, verdict, evidence=[job / 'preview.png'],
                            checks={key: verdict for key in flow.STAGE_CHECKS[stage]},
                            note='Actual synthetic mesh evidence compared with reference', reviewer=reviewer,
                            render_report=renders(flow, job) if verdict == 'PASS' else None)


def geometry(flow, job):
    profile = job / 'profile.json'
    profile.write_text(json.dumps({'schema_version': 1, 'id': 'fixture', 'units': 'mm',
                                  'required_checks': list(flow.ledger.FULL_GEOMETRY_CHECKS)}))
    # Exact validator-consumer boundary. Existing tests run the real checker;
    # here we exercise preservation of its explicit unsupported check statuses.
    report = job / 'geometry.json'
    checks = {key: {'status': 'PASS', 'required': True, 'category': 'geometry'}
              for key in flow.ledger.FULL_GEOMETRY_CHECKS}
    checks['self_intersection']['status'] = 'UNKNOWN'
    report.write_text(json.dumps({'schema_version': 1, 'overall_scope': 'geometry', 'overall': 'UNKNOWN',
                                 'required_checks': list(checks), 'checks': checks}))
    with flow.ledger.transaction(job) as (_, data):
        rev = data['refinement']['revisions']['v1']
        rev['geometry'] = {'at': 'fixture', 'mesh_sha256': rev['artifacts']['mesh']['sha256'],
                           'status': 'UNKNOWN', 'report': flow.ledger.artifact(report),
                           'profile': flow.ledger.artifact(profile)}


def slice_review(flow, job, single_plate_entries, write_package, review_status='PASS'):
    package = write_package(job / 'slice.gcode.3mf', single_plate_entries)
    assert flow.ledger.main(['slice', '--job', str(job), '--id', 'v1', '--package', str(package), '--note', 'Synthetic package']) == 0
    checks = [part for key in flow.ledger.SLICE_CHECKS for part in ('--check', key + '=' + review_status)]
    assert flow.ledger.main(['review', '--job', str(job), '--id', 'v1', '--kind', 'slice', '--status', review_status,
                            '--reviewer', 'agent', '--note', 'Synthetic actual preview review',
                            '--evidence', str(job / 'preview.png'), *checks]) == 0
    receipt = job / 'slice-receipt.json'
    positioned = job / 'positioned.3mf'; positioned.write_bytes(b'synthetic positioned fixture')
    data = {'mesh': flow.ledger.artifact(job / 'model.stl'), 'package': flow.ledger.artifact(package),
            'profiles': {k: flow.ledger.artifact(job / 'profile.json') for k in ('machine', 'process', 'filament')},
            'positioned': flow.ledger.artifact(positioned),
            'transform': {'translation_mm': [0, 0, 0], 'rotation_degrees': [0, 0, 0], 'scale': 1.0},
            'slicer': {'executable': 'fixture-slicer', 'version': 'synthetic-1'}}
    request = job / 'request.json'
    request.write_text(json.dumps({**data, 'expected_command': {'executable': 'fixture-slicer', 'arguments': ['fixture']}}))
    launch = job / 'launch.json'
    launch.write_text(json.dumps({'request_sha256': flow.ledger.sha(request), 'executable': 'fixture-slicer', 'arguments': ['fixture']}))
    execution = job / 'execution.json'
    execution.write_text(json.dumps({'launch_sha256': flow.ledger.sha(launch), 'exit_code': 0, 'slicer_version': 'synthetic-1'}))
    native = job / 'result.json'; native.write_text('{"return_code":0}')
    data.update({k: flow.ledger.artifact(path) for k, path in [('request', request), ('launch', launch), ('execution', execution), ('native_result', native)]})
    receipt.write_text(json.dumps(data))
    flow.record_stage(job, 'slice', evidence=[job / 'preview.png'], provenance=receipt,
                      note='Actual package previews bound to synthetic input fixture')
    return receipt


def test_init_preserves_legacy_fields_and_cannot_reset_budget(flow, job):
    reference(flow, job)
    flow.start_attempt(job, 'generate', 'one', 'generator', 'shape', inputs=['reference.png'])
    flow.finish_attempt(job, 'one', 'failed', note='Service rejected fixture')
    flow.init(job)
    data = read(job)
    assert data['state'] == 'legacy' and data['authorization']['print'] is None and data['private'] == 42
    assert flow.status(job)['budgets']['generate']['used'] == 1
    with pytest.raises(ValueError, match='budget'):
        flow.init(job, budgets={'generate': 9, 'refine': 3, 'reference': 1})


def test_generation_requires_hash_bound_reference_and_fresh_input(flow, job):
    with pytest.raises(ValueError, match='reference review'):
        flow.start_attempt(job, 'generate', 'one', 'generator', 'shape', inputs=['reference.png'])
    reference(flow, job)
    with pytest.raises(ValueError, match='hash-bound'):
        flow.start_attempt(job, 'generate', 'one', 'generator', 'shape', inputs=['preview.png'])
    (job / 'reference.png').write_bytes(b'changed reference')
    with pytest.raises(ValueError, match='reference review'):
        flow.start_attempt(job, 'generate', 'one', 'generator', 'shape', inputs=['reference.png'])
    assert flow.status(job)['budgets']['generate']['used'] == 0


def test_arbitrary_attempt_names_do_not_bypass_global_budget(flow, job):
    reference(flow, job)
    for name in ('a', 'totally-different-name'):
        flow.start_attempt(job, 'generate', name, name, 'shape', inputs=['reference.png'])
        flow.finish_attempt(job, name, 'failed', note='Synthetic failure')
    with pytest.raises(ValueError, match='budget exhausted'):
        flow.start_attempt(job, 'generate', 'third', 'fresh-strategy', 'shape', inputs=['reference.png'])
    assert flow.next_action(job)['action'] == 'needs_design_decision'


def test_two_unimproved_repairs_stop_strategy_and_new_name_does_not_reset(flow, job):
    reference(flow, job); register(flow, job); visual(flow, job, 'form', 'FAIL')
    for name in ('repair-a', 'repair-b'):
        flow.start_attempt(job, 'refine', name, 'local-fairing', 'chin-curtain', inputs=['model.stl'])
        flow.finish_attempt(job, name, 'completed', artifacts=['model.stl'], improved=False, note='No visual improvement')
    with pytest.raises(ValueError, match='Strategy stopped'):
        flow.start_attempt(job, 'refine', 'repair-c', 'local-fairing', 'chin-curtain', inputs=['model.stl'])
    flow.start_attempt(job, 'refine', 'repair-d', 'different-method', 'chin-curtain', inputs=['model.stl'])
    flow.finish_attempt(job, 'repair-d', 'failed', note='No suitable operation')
    assert flow.next_action(job)['action'] == 'needs_specialist'


def test_uncertain_submission_blocks_new_attempt_and_recovery_is_idempotent(flow, job):
    reference(flow, job)
    flow.start_attempt(job, 'generate', 'one', 'generator', 'shape', inputs=['reference.png'])
    flow.record_remote(job, 'one', 'remote-123')
    flow.finish_attempt(job, 'one', 'uncertain', note='Response interrupted')
    with pytest.raises(ValueError, match='unresolved'):
        flow.start_attempt(job, 'generate', 'two', 'generator', 'shape', inputs=['reference.png'])
    with pytest.raises(ValueError, match='reconciliation'):
        flow.finish_attempt(job, 'one', 'completed', artifacts=['model.stl'])
    (job / 'receipt.json').write_text('{"remote_id":"remote-123","status":"DONE"}')
    result = flow.reconcile_attempt(job, 'one', 'completed', artifacts=['model.stl'],
                                    evidence=['receipt.json'], note='Recovered the same remote output', remote_id='remote-123')
    assert result['status'] == 'completed'
    flow.reconcile_attempt(job, 'one', 'completed', artifacts=['model.stl'], evidence=['receipt.json'],
                           note='Replay after local receipt persistence interruption', remote_id='remote-123')
    assert flow.status(job)['budgets']['generate']['used'] == 1
    with pytest.raises(ValueError, match='already exists'):
        flow.start_attempt(job, 'generate', 'one', 'generator', 'shape', inputs=['reference.png'])


def test_no_output_no_completion_and_no_evidence_no_pass(flow, job):
    reference(flow, job)
    flow.start_attempt(job, 'generate', 'one', 'generator', 'shape', inputs=['reference.png'])
    with pytest.raises(ValueError, match='actual output'):
        flow.finish_attempt(job, 'one', 'completed', improved=True, note='It worked')
    with pytest.raises(ValueError, match='actual evidence'):
        flow.record_stage(job, 'reference', 'PASS', checks={k: 'PASS' for k in flow.STAGE_CHECKS['reference']}, note='Trust me')


def test_render_report_requires_all_six_views_and_rehashes_images(flow, job):
    reference(flow, job); register(flow, job)
    with pytest.raises(ValueError, match='render report'):
        flow.record_stage(job, 'form', 'PASS', evidence=['preview.png'], checks={k: 'PASS' for k in flow.STAGE_CHECKS['form']}, note='No render')
    report = renders(flow, job)
    data = json.loads(report.read_text()); data['views'].pop('side'); report.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='front/side'):
        flow.record_stage(job, 'form', 'PASS', evidence=['preview.png'], checks={k: 'PASS' for k in flow.STAGE_CHECKS['form']},
                          note='Missing side view', render_report=report)
    visual(flow, job, 'form')
    (job / 'side.png').write_bytes(b'changed actual view')
    assert flow.status(job)['stages']['form']['status'] == 'UNKNOWN'
    assert 'STALE' in flow.status(job)['stages']['form']['reason']


def test_candidate_failure_does_not_replace_best_and_owner_rejection_survives_alias(flow, job):
    reference(flow, job); register(flow, job); visual(flow, job, 'form'); visual(flow, job, 'detail')
    flow.promote_best(job)
    register(flow, job, 'v2'); visual(flow, job, 'form', 'FAIL')
    assert flow.status(job)['best_revision'] == 'v1'
    with pytest.raises(ValueError, match='Best promotion'):
        flow.promote_best(job)
    visual(flow, job, 'form', 'FAIL', 'owner')
    visual(flow, job, 'form', 'PASS', 'agent')
    assert flow.status(job)['stages']['form']['status'] == 'FAIL'
    assert flow.status(job, 'v1')['owner_review'] == 'FAIL'  # identical mesh bytes, renamed revision


def test_best_failed_candidate_survives_worse_trial_and_can_improve_without_pass(flow, job, write_stl, tetrahedron):
    reference(flow, job); register(flow, job); visual(flow, job, 'form', 'FAIL')
    assert flow.status(job)['best_revision'] == 'v1'
    new_mesh = job / 'model-v2.stl'
    write_stl(new_mesh, [[tuple(c * 2 for c in p) for p in tri] for tri in tetrahedron])
    new_preview = job / 'preview-v2.png'; Image.new('RGB', (8, 8), 'blue').save(new_preview)
    register(flow, job, 'v2', new_mesh, new_preview)
    visual(flow, job, 'form', 'FAIL')
    assert flow.export(job)['revision'] == 'v1'  # latest failed candidate cannot silently take over
    with pytest.raises(ValueError, match='both compared revisions'):
        flow.promote_best(job, improved=True, baseline_revision='v1', evidence=[new_preview], note='Some improvement')
    result = flow.promote_best(job, improved=True, baseline_revision='v1', evidence=[job / 'preview.png', new_preview],
                               note='Comparing both actual renders: chin improved; mouth is still a documented failure')
    assert result['best_revision'] == 'v2' and result['appearance_approval_granted'] is False
    assert flow.export(job)['status'] == 'diagnostic_complete'


def test_failed_form_allows_diagnostic_detail_note_but_blocks_detail_pass_and_processing(flow, job):
    reference(flow, job); register(flow, job); visual(flow, job, 'form', 'FAIL')
    visual(flow, job, 'detail', 'FAIL')
    with pytest.raises(ValueError, match='Detail PASS'):
        visual(flow, job, 'detail', 'PASS')
    with pytest.raises(ValueError, match='Detail processing'):
        flow.start_attempt(job, 'refine', 'wrong-order', 'fur-relief', 'fur', inputs=['model.stl'], stage='detail')
    assert flow.status(job)['budgets']['refine']['used'] == 0


def test_stage_progress_and_preview_unknown_never_becomes_print_ready(flow, job, single_plate_entries, write_package):
    assert flow.next_action(job)['stage'] == 'reference'
    reference(flow, job)
    assert flow.next_action(job)['action'] == 'generate_or_import'
    register(flow, job)
    assert flow.next_action(job)['stage'] == 'form'
    visual(flow, job, 'form')
    assert flow.next_action(job)['stage'] == 'detail'
    visual(flow, job, 'detail')
    assert flow.next_action(job)['stage'] == 'geometry'
    geometry(flow, job)
    assert flow.record_stage(job, 'geometry')['status'] == 'UNKNOWN'
    assert flow.next_action(job)['stage'] == 'slice'
    slice_review(flow, job, single_plate_entries, write_package)
    result = flow.status(job)
    assert result['preview_complete'] is True and result['print_ready'] is False
    assert result['owner_review'] == 'UNKNOWN' and result['print_authorization_granted'] is False
    assert flow.next_action(job)['stage'] == 'export'
    assert result['stages']['geometry']['status'] == 'UNKNOWN'


def test_changed_slice_profile_invalidates_previews(flow, job, single_plate_entries, write_package):
    reference(flow, job); register(flow, job); visual(flow, job, 'form'); visual(flow, job, 'detail'); geometry(flow, job)
    slice_review(flow, job, single_plate_entries, write_package)
    (job / 'profile.json').write_text('{"changed":true}')
    result = flow.status(job)
    assert result['preview_complete'] is False
    assert result['stages']['slice']['status'] == 'UNKNOWN'


def test_minimal_hash_only_slice_receipt_cannot_claim_v2_preview(flow, job, single_plate_entries, write_package):
    reference(flow, job); register(flow, job); visual(flow, job, 'form'); visual(flow, job, 'detail'); geometry(flow, job)
    receipt = slice_review(flow, job, single_plate_entries, write_package)
    data = json.loads(receipt.read_text())
    receipt.write_text(json.dumps({'mesh_sha256': data['mesh']['sha256'], 'package_sha256': data['package']['sha256']}))
    with pytest.raises(ValueError, match='complete profiles'):
        flow.record_stage(job, 'slice', evidence=['preview.png'], provenance=receipt, note='Only matching filenames and hashes')
    assert flow.status(job)['preview_complete'] is False


def test_diagnostic_export_preserves_failure_and_is_idempotent(flow, job):
    reference(flow, job); register(flow, job); visual(flow, job, 'form', 'FAIL')
    before = read(job)['authorization']
    result = flow.export(job)
    manifest = json.loads(Path(result['manifest']).read_text())
    assert manifest['status'] == 'diagnostic_complete' and manifest['print_ready'] is False
    assert 'form: FAIL' in manifest['limitations']
    assert read(job)['authorization'] == before
    assert read(job)['workflow_v2']['delivered_revision'] == 'v1'
    assert flow.next_action(job)['action'] == 'complete'
    assert flow.next_action(job)['delivery_status'] == 'diagnostic_complete'
    assert flow.export(job)['directory'] == result['directory']
    assert len(read(job)['workflow_v2']['exports']) == 1
    first = Path(result['directory']) / manifest['files'][0]['path']
    first.write_bytes(b'corruption')
    with pytest.raises(ValueError, match='altered'):
        flow.export(job)


def test_crash_recovery_does_not_steal_active_transaction(flow, job):
    with flow.ledger.transaction(job):
        with pytest.raises(ValueError, match='live process'):
            flow.init(job)
    code = ("import importlib.util,os; from pathlib import Path; "
            f"s=importlib.util.spec_from_file_location('l',{str(SCRIPT.with_name('refinement_job.py'))!r}); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            f"c=m.transaction({str(job)!r}); c.__enter__(); os._exit(0)")
    subprocess.run([sys.executable, '-c', code], check=True, timeout=5)
    assert (job / '.refinement.lock').exists()
    flow.init(job)
    assert not (job / '.refinement.lock').exists()
    assert read(job)['lock_recoveries'][-1]['previous_owner']['pid'] != __import__('os').getpid()


def test_read_only_status_does_not_rewrite_manifest(flow, job):
    before = (job / 'job.json').read_bytes()
    assert flow.main(['next', '--job', str(job)]) == 0
    assert flow.main(['status', '--job', str(job)]) == 0
    assert (job / 'job.json').read_bytes() == before


def test_completed_unknown_visual_review_exits_but_unrun_or_stale_review_requests_evidence(flow, job):
    reference(flow, job); register(flow, job)
    assert flow.next_action(job)['action'] == 'review_actual_mesh'
    visual(flow, job, 'form', 'UNKNOWN')
    before = (job / 'job.json').read_bytes()
    decisions = [flow.next_action(job) for _ in range(3)]
    assert decisions[0] == decisions[1] == decisions[2]
    assert decisions[0]['action'] == 'needs_specialist' and decisions[0]['terminal'] is True
    assert decisions[0]['diagnostic_export_available'] is True
    assert (job / 'job.json').read_bytes() == before
    assert flow.export(job)['status'] == 'diagnostic_complete'
    (job / 'preview.png').write_bytes(b'changed review evidence')
    stale = flow.next_action(job)
    assert stale['action'] == 'review_actual_mesh' and not stale.get('terminal')


def test_completed_unknown_reference_review_ends_without_spending_correction_budget(flow, job):
    reference(flow, job, 'UNKNOWN')
    first = flow.next_action(job)
    assert first == flow.next_action(job)
    assert first['action'] == 'needs_design_decision' and first['terminal'] is True
    assert flow.status(job)['budgets']['reference']['used'] == 0


def test_fresh_unknown_slice_audit_and_review_export_diagnostics_without_repeat(flow, job, single_plate_entries, write_package):
    reference(flow, job); register(flow, job); visual(flow, job, 'form'); visual(flow, job, 'detail'); geometry(flow, job)
    entries = dict(single_plate_entries)
    entries['Metadata/slice_info.config'] = entries['Metadata/slice_info.config'].replace(
        b'<metadata key="outside" value="false"/>', b'')
    slice_review(flow, job, entries, write_package, review_status='UNKNOWN')
    result = flow.status(job)
    assert result['stages']['slice']['status'] == 'UNKNOWN'
    assert result['stages']['slice']['performed'] is True
    assert flow.next_action(job) == flow.next_action(job)
    assert flow.next_action(job)['action'] == 'export'
    assert result['preview_complete'] is False and result['print_ready'] is False
    assert flow.export(job)['status'] == 'diagnostic_complete'
    (job / 'execution.json').write_text('{"changed":true}')
    assert flow.next_action(job)['action'] == 'diagnostic_slice_and_review'


def test_exported_snapshot_is_terminal_until_evidence_or_candidate_changes(flow, job, single_plate_entries, write_package):
    reference(flow, job); register(flow, job); visual(flow, job, 'form'); visual(flow, job, 'detail'); geometry(flow, job)
    slice_review(flow, job, single_plate_entries, write_package)
    delivered = flow.export(job)
    before = (job / 'job.json').read_bytes()
    first = flow.next_action(job)
    assert first['action'] == 'complete' and first['terminal'] is True
    assert first['manifest'] == delivered['manifest']
    assert first['delivery_status'] == 'preview_complete'
    assert first == flow.next_action(job) == flow.next_action(job)
    assert flow.status(job)['print_ready'] is False  # the unknown geometry check is still unknown
    assert (job / 'job.json').read_bytes() == before
    visual(flow, job, 'form', 'UNKNOWN')
    assert flow.next_action(job)['action'] == 'needs_specialist'
    updated = flow.export(job)
    assert updated['directory'] != delivered['directory']
    assert flow.next_action(job)['action'] == 'complete'
    register(flow, job, 'v2')
    assert flow.next_action(job)['action'] == 'review_actual_mesh'


def test_altered_export_or_source_evidence_cannot_keep_complete(flow, job):
    reference(flow, job); register(flow, job); visual(flow, job, 'form', 'FAIL')
    result = flow.export(job)
    manifest = json.loads(Path(result['manifest']).read_text())
    copy = Path(result['directory']) / manifest['files'][0]['path']
    original = copy.read_bytes()
    copy.write_bytes(b'altered delivery')
    assert flow.next_action(job)['action'] != 'complete'
    copy.write_bytes(original)
    assert flow.next_action(job)['action'] == 'complete'
    (job / 'preview.png').write_bytes(b'altered actual preview evidence')
    assert flow.next_action(job)['action'] == 'review_actual_mesh'


def test_rebinding_replaced_proof_exports_current_evidence_and_preserves_history(flow, job, single_plate_entries, write_package):
    reference(flow, job); register(flow, job); visual(flow, job, 'form'); visual(flow, job, 'detail'); geometry(flow, job)
    receipt = slice_review(flow, job, single_plate_entries, write_package)
    proof = job / 'preview-extraction.json'
    proof.write_text('{"version":1,"same_toolpaths":true}')
    original_proof_hash = flow.ledger.sha(proof)
    previous = flow.record_stage(job, 'slice', evidence=['preview.png', proof], provenance=receipt,
                                  note='Actual preview using immutable native toolpaths; extraction proof v1')
    first = flow.export(job)
    assert flow.next_action(job)['action'] == 'complete'
    proof.write_text('{"schema_version":2,"same_toolpaths":true,"format":"artifact records"}')
    assert flow.next_action(job)['action'] != 'complete'
    current = flow.record_stage(job, 'slice', evidence=['preview.png', proof], provenance=receipt,
                                 note='Same actual preview/toolpaths; updated extraction proof schema only')
    second = flow.export(job)
    assert second['directory'] != first['directory']
    assert flow.next_action(job)['action'] == 'complete'
    history = json.loads((Path(second['directory']) / 'history.json').read_text())
    old_record = next(r for r in history['workflow_records'] if r['id'] == previous['id'])
    assert any(e['sha256'] == original_proof_hash for e in old_record['evidence'])
    assert previous['id'] not in history['current_record_ids']
    assert current['id'] in history['current_record_ids']
    manifest = json.loads(Path(second['manifest']).read_text())
    assert not any(item['sha256'] == original_proof_hash for item in manifest['files'])
    assert manifest['history']['path'] == 'history.json'


def test_export_keeps_historical_owner_rejection_even_when_effective_record_is_agent_pass(flow, job):
    reference(flow, job); register(flow, job)
    rejected = visual(flow, job, 'form', 'FAIL', 'owner')
    visual(flow, job, 'form', 'PASS', 'agent')
    delivered = flow.export(job)
    assert delivered['status'] == 'diagnostic_complete'
    assert 'form: FAIL' in delivered['limitations']
    history = json.loads((Path(delivered['directory']) / 'history.json').read_text())
    assert rejected['id'] not in history['current_record_ids']
    assert any(r['id'] == rejected['id'] and r['status'] == 'FAIL' for r in history['owner_reviews'])
    assert flow.status(job)['owner_review'] == 'FAIL'
