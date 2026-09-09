#!/usr/bin/env python3
"""Bounded, resumable local workflow. Never generates, slices or starts a printer.

The job manifest is authoritative. `next` returns one action for the host agent;
this module does not keep a daemon or promise unattended execution. Evidence is
immutable by hash and visual judgments remain attributed judgments.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

_spec = importlib.util.spec_from_file_location('workflow_refinement_ledger', Path(__file__).with_name('refinement_job.py'))
ledger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger)

DEFAULT_BUDGETS = {'generate': 2, 'refine': 3, 'reference': 1}
STAGE_CHECKS = {
    'reference': {'identity', 'pose_consistency', 'input_quality'},
    'form': {'silhouette', 'pose', 'key_features'},
    'detail': {'surface_style', 'transitions', 'fragile_details'},
}
REQUIRED_VIEWS = {'front', 'side', 'left45', 'right45', 'back', 'bottom'}
STAGES = ('reference', 'form', 'detail', 'geometry', 'slice')


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', value):
        raise ValueError('Expected a short filename-safe identifier')
    return value


def _root(job_dir):
    root = Path(job_dir).expanduser().resolve(strict=True)
    if not (root / 'job.json').is_file():
        raise ValueError('An existing job.json is required; use the image intake first')
    return root


def _load(job_dir):
    root = _root(job_dir)
    job = json.loads((root / 'job.json').read_text(encoding='utf-8'))
    _workflow(job)
    return root, job


def _workflow(job):
    result = job.get('workflow_v2')
    if not isinstance(result, dict) or result.get('schema_version') != 2:
        raise ValueError('Initialize this job with workflow_v2 init first')
    return result


def _atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as out:
            json.dump(data, out, ensure_ascii=False, indent=2)
            out.write('\n'); out.flush(); os.fsync(out.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _paths(root, paths):
    result = []
    for value in paths or []:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = root / path
        item = ledger.artifact(path)
        if item not in result:
            result.append(item)
    return result


def _progress(root, job):
    # These are derived convenience views, never inputs to recovery decisions.
    result = status_data(job)
    _atomic_json(root / 'work/workflow-v2/status.json', result)
    path = root / 'work/workflow-v2/PROGRESS.md'
    text = ('# Workflow V2\n\nAuthoritative state: job.json.workflow_v2\n\n'
            f"Stage: {result['next_action']['stage']}\n\n"
            f"Next action: {result['next_action']['action']}\n\n"
            f"Candidate: {result['candidate_revision']}\n\nBest: {result['best_revision']}\n\n"
            f"Delivered: {result['delivered_revision']}\n\n"
            f"Preview complete: {result['preview_complete']}\n\nPrint ready: {result['print_ready']}\n\n"
            'This workflow grants no print authorization. See status.json for evidence and limitations.\n')
    temp = path.with_suffix('.tmp')
    temp.write_text(text, encoding='utf-8'); temp.replace(path)


@contextmanager
def _transaction(job_dir):
    with ledger.transaction(job_dir) as (root, job):
        _workflow(job)
        yield root, job
        _workflow(job)['updated_at'] = ledger.utc()
        _progress(root, job)


def init(job_dir, *, budgets=None):
    """Initialize once. Reinitializing never resets attempts or changes budgets."""
    root = _root(job_dir)
    chosen = dict(DEFAULT_BUDGETS if budgets is None else budgets)
    if set(chosen) != set(DEFAULT_BUDGETS) or any(type(v) is not int or v < 0 for v in chosen.values()):
        raise ValueError('Budgets must contain nonnegative integer generate/refine/reference limits')
    with ledger.transaction(root) as (_, job):
        if 'workflow_v2' in job:
            workflow = _workflow(job)
            if budgets is not None and workflow['budgets'] != chosen:
                raise ValueError('Reinitialization cannot reset or raise a persisted attempt budget')
        else:
            workflow = job['workflow_v2'] = {
                'schema_version': 2, 'created_at': ledger.utc(), 'budgets': chosen,
                'attempts': {}, 'records': [], 'owner_reviews': [],
                'candidate_revision': None, 'best_revision': None, 'delivered_revision': None,
                'exports': [],
                'migration_note': 'Existing job fields and authorization preserved. Pre-V2 history is retained; new attempts are counted here.',
            }
        _progress(root, job)
        return json.loads(json.dumps(workflow))


def _owner_veto(job, mesh_hash):
    records = list(_workflow(job).get('owner_reviews', []))
    for revision in job.get('refinement', {}).get('revisions', {}).values():
        records += revision.get('owner_reviews', [])
        records += [r for r in revision.get('history', []) if r.get('kind') in ('review', 'appearance')]
        latest = revision.get('reviews', {}).get('appearance')
        if latest:
            records.append(latest)
    return any(r.get('reviewer') == 'owner' and r.get('status') == 'FAIL'
               and r.get('mesh_sha256') == mesh_hash for r in records)


def _review_status(job, stage, revision=None):
    workflow = _workflow(job)
    records = [r for r in workflow['records'] if r.get('type') == 'review'
               and r.get('stage') == stage and r.get('revision') == revision]
    if not records:
        return {'status': 'UNKNOWN', 'reason': 'not reviewed', 'performed': False}
    record = records[-1]
    changed = ledger.stale(record.get('evidence', []))
    if revision is not None:
        rev = ledger.get_revision(job, revision)
        changed += ledger.stale(rev['artifacts'].values())
        if record.get('mesh_sha256') != rev['artifacts']['mesh']['sha256']:
            changed.append('mesh identity')
        if not changed and _owner_veto(job, rev['artifacts']['mesh']['sha256']):
            return {'status': 'FAIL', 'reason': 'Owner rejected these exact mesh bytes', 'performed': True}
    if changed:
        return {'status': 'UNKNOWN', 'reason': 'STALE evidence', 'changed': sorted(set(changed)), 'performed': False}
    return {'status': record['status'], 'checks': record['checks'], 'note': record['note'],
            'reviewer': record['reviewer'], 'performed': True, 'record_id': record['id']}


def _stages(job, revision):
    result = {'reference': _review_status(job, 'reference')}
    if not revision:
        return result
    rev = ledger.get_revision(job, revision)
    result.update({s: _review_status(job, s, revision) for s in ('form', 'detail')})
    current = ledger.status(job, revision)
    geometry = current['stages']['geometry']
    result['geometry'] = {**geometry, 'performed': bool(rev.get('geometry')) and not geometry.get('reason', '').startswith('STALE')}
    slice_records = [r for r in _workflow(job)['records'] if r.get('type') == 'slice_binding' and r['revision'] == revision]
    bound = slice_records[-1] if slice_records else None
    valid_binding = bool(bound) and not ledger.stale(bound['evidence'])
    if bound:
        valid_binding = valid_binding and bound['mesh_sha256'] == rev['artifacts']['mesh']['sha256']
        valid_binding = valid_binding and bound['package_sha256'] == rev.get('slice', {}).get('package', {}).get('sha256')
    slice_parts = [current['stages']['slice_package']['status'], current['stages']['slice_review']['status']]
    current_slice = [current['stages']['slice_package'], current['stages']['slice_review']]
    fresh_reviews = bool(rev.get('slice')) and bool(rev.get('reviews', {}).get('slice'))
    fresh_reviews = fresh_reviews and not any(p.get('reason', '').startswith('STALE') for p in current_slice)
    result['slice'] = {'status': ledger.aggregate(slice_parts) if valid_binding else 'UNKNOWN',
                       'performed': valid_binding and fresh_reviews,
                       'package': rev.get('slice', {}).get('package'),
                       'reason': '' if valid_binding else 'Missing or STALE model-to-slice provenance/screenshots'}
    return result


def _pending(workflow):
    return [a for a in workflow['attempts'].values() if a['status'] in ('running', 'uncertain')]


def _exhausted(workflow, kind):
    return sum(a['kind'] == kind for a in workflow['attempts'].values()) >= workflow['budgets'][kind]


def _stalled(workflow, strategy, defect):
    # Interleaving another strategy or inventing attempt names cannot erase the
    # last two outcomes of this same strategy/defect pair.
    attempts = [a for a in workflow['attempts'].values() if a['strategy'] == strategy and a['defect'] == defect
                and a['status'] in ('completed', 'failed')]
    return len(attempts) >= 2 and all(a.get('improved') is not True for a in attempts[-2:])


def _current_records(job, chosen):
    """Select effective stage records; superseded records remain in history."""
    selected = {}
    for index, record in enumerate(_workflow(job)['records']):
        kind = record.get('type')
        revision = record.get('revision')
        if kind == 'review' and ((record.get('stage') == 'reference' and revision is None)
                                 or (record.get('stage') in ('form', 'detail') and revision == chosen)):
            key = ('review', record['stage'])
        elif kind in ('slice_binding', 'best_selection') and revision == chosen:
            key = (kind,)
        else:
            continue
        selected[key] = (index, record)
    return sorted(selected.values(), key=lambda item: item[0])


def _history_data(job, chosen):
    workflow = _workflow(job)
    current_ids = [record['id'] for _, record in _current_records(job, chosen)]
    return {'schema_version': 2, 'revision': chosen, 'current_record_ids': current_ids,
            'workflow_records': workflow['records'], 'owner_reviews': workflow.get('owner_reviews', []),
            'refinement_ledger': job.get('refinement', {}),
            'legacy_feedback': {k: job[k] for k in ('feedback', 'likeness_feedback') if k in job},
            'authorization': job.get('authorization'),
            'scope': 'Complete historical judgments and original hashes are retained. Superseded paths may be absent or changed; historical PASS is not current acceptance. Same-mesh owner rejection remains effective.'}


def _export_assets(job, chosen):
    rev = ledger.get_revision(job, chosen)
    assets = dict(rev['artifacts'])
    for name, item in (('geometry_report', rev.get('geometry', {}).get('report')),
                       ('slice_package', rev.get('slice', {}).get('package')),
                       ('slice_report', rev.get('slice', {}).get('report'))):
        if item:
            assets[name] = item
    for index, rec in _current_records(job, chosen):
        for n, item in enumerate(rec.get('evidence', [])):
            assets[f'evidence_{index}_{n}'] = item
    return assets


def _quality(workflow, stages):
    visual = all(stages.get(s, {}).get('status') == 'PASS' for s in ('reference', 'form', 'detail'))
    geometry = stages.get('geometry', {})
    preview = (visual and stages.get('slice', {}).get('status') == 'PASS'
               and geometry.get('performed', False) and geometry.get('status') != 'FAIL'
               and not _stale_attempts(workflow))
    return bool(preview), bool(preview and geometry.get('status') == 'PASS')


def _snapshot_signature(job, chosen, assets=None, stages=None):
    assets = _export_assets(job, chosen) if assets is None else assets
    stages = _stages(job, chosen) if stages is None else stages
    preview, ready = _quality(_workflow(job), stages)
    identity = {'revision': chosen, 'assets': assets, 'stages': stages,
                'preview_complete': preview, 'print_ready': ready,
                'history': _history_data(job, chosen)}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _context_signature(workflow):
    # Exclude delivery bookkeeping, but invalidate completion when new work or
    # a new attributed observation arrives, even if the mesh has not changed.
    context = {key: workflow[key] for key in ('candidate_revision', 'best_revision', 'records', 'attempts', 'budgets')}
    return hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()


def _current_delivery(job):
    workflow = _workflow(job)
    if not workflow['exports']:
        return None
    record = workflow['exports'][-1]
    chosen = record['revision']
    if record.get('context_sha256') != _context_signature(workflow) or workflow['delivered_revision'] != chosen:
        return None
    try:
        assets = _export_assets(job, chosen)
        if ledger.stale(assets.values()) or ledger.stale([record['manifest']]):
            return None
        if _snapshot_signature(job, chosen, assets=assets) != record['snapshot_sha256']:
            return None
        manifest_path = Path(record['manifest']['path'])
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('snapshot_sha256') != record['snapshot_sha256']:
            return None
        root = manifest_path.parent.resolve()
        for item in manifest['files']:
            path = (root / item['path']).resolve()
            if not path.is_relative_to(root) or ledger.sha(path) != item['sha256']:
                return None
        return {'stage': 'complete', 'action': 'complete', 'terminal': True,
                'revision': chosen, 'manifest': str(manifest_path), 'delivery_status': manifest['status'],
                'print_authorization_granted': False}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _next(job, stages):
    workflow = _workflow(job)
    pending = _pending(workflow)
    if pending:
        attempt = pending[0]
        alive = ledger.owner_alive(attempt['owner'])
        return {'stage': 'attempt', 'action': 'reconcile' if attempt['status'] == 'uncertain' or alive is not True else 'wait',
                'attempt_id': attempt['id'], 'owner_alive': alive, 'reason': 'Do not submit another operation while outcome is unresolved'}
    stale_attempts = _stale_attempts(workflow)
    if stale_attempts:
        return {'stage': 'evidence', 'action': 'reconcile_artifacts', 'attempt_ids': sorted(stale_attempts),
                'reason': 'Immutable attempt inputs or outputs changed; restore evidence before reusing it'}
    delivered = _current_delivery(job)
    if delivered:
        return delivered
    reference = stages['reference']
    if reference['status'] != 'PASS':
        if reference['status'] == 'UNKNOWN' and reference.get('performed'):
            return {'stage': 'reference', 'action': 'needs_design_decision', 'terminal': True,
                    'reason': 'Reference review completed with insufficient evidence; repeated review is not a recovery strategy'}
        if reference['status'] == 'FAIL' and _exhausted(workflow, 'reference'):
            return {'stage': 'reference', 'action': 'needs_design_decision', 'terminal': True, 'reason': 'Reference correction budget exhausted'}
        return {'stage': 'reference', 'action': 'review_reference' if reference['status'] == 'UNKNOWN' else 'correct_reference'}
    if not workflow['candidate_revision']:
        return {'stage': 'form', 'action': 'needs_design_decision' if _exhausted(workflow, 'generate') else 'generate_or_import'}
    for stage in ('form', 'detail'):
        if stages[stage]['status'] != 'PASS':
            if stages[stage]['status'] == 'UNKNOWN' and stages[stage].get('performed'):
                return {'stage': stage, 'action': 'needs_specialist', 'terminal': True,
                        'diagnostic_export_available': bool(workflow.get('best_revision')),
                        'reason': 'Actual review completed as UNKNOWN; hand off the current evidence instead of repeating unchanged review'}
            if stages[stage]['status'] == 'FAIL':
                return {'stage': stage, 'action': 'needs_specialist' if _exhausted(workflow, 'refine') else 'refine_or_change_strategy'}
            return {'stage': stage, 'action': 'review_actual_mesh'}
    if not stages['geometry']['performed']:
        return {'stage': 'geometry', 'action': 'check_geometry'}
    if not stages['slice']['performed']:
        return {'stage': 'slice', 'action': 'diagnostic_slice_and_review'}
    return {'stage': 'export', 'action': 'export', 'reason': 'Keep unresolved limitations explicit; no print authorization is granted'}


def status_data(job, revision=None):
    workflow = _workflow(job)
    selected = revision or workflow['candidate_revision']
    stages = _stages(job, selected)
    stale_attempts = _stale_attempts(workflow)
    preview_complete, print_ready = _quality(workflow, stages)
    attempts = workflow['attempts']
    return {'schema_version': 2, 'revision': selected, 'candidate_revision': workflow['candidate_revision'],
            'best_revision': workflow['best_revision'], 'delivered_revision': workflow['delivered_revision'],
            'stages': stages, 'preview_complete': bool(preview_complete), 'print_ready': bool(print_ready),
            'print_authorization_granted': False,
            'budgets': {kind: {'limit': limit, 'used': sum(a['kind'] == kind for a in attempts.values())}
                        for kind, limit in workflow['budgets'].items()},
            'pending_attempts': [dict(a) for a in _pending(workflow)],
            'stale_attempt_artifacts': stale_attempts,
            'next_action': _next(job, stages),
            'owner_review': _owner_status(job, selected),
            'limitations': [f'{key}: {value["status"]}' for key, value in stages.items() if value['status'] != 'PASS'],
            'scope': 'Local workflow and attributed evidence; no automated identity certificate or printer command.'}


def _stale_attempts(workflow):
    return {a['id']: changed for a in workflow['attempts'].values()
            if a['status'] in ('completed', 'failed')
            and (changed := ledger.stale(a['inputs'] + a.get('artifacts', [])))}


def _owner_status(job, revision):
    if not revision:
        return 'UNKNOWN'
    mesh_hash = ledger.get_revision(job, revision)['artifacts']['mesh']['sha256']
    if ledger.stale(ledger.get_revision(job, revision)['artifacts'].values()):
        return 'UNKNOWN'
    if _owner_veto(job, mesh_hash):
        return 'FAIL'
    records = _workflow(job).get('owner_reviews', [])
    return 'PASS' if any(r.get('status') == 'PASS' and r.get('mesh_sha256') == mesh_hash for r in records) else 'UNKNOWN'


def status(job_dir, revision=None):
    return status_data(_load(job_dir)[1], revision)


def next_action(job_dir):
    return status(job_dir)['next_action']


def start_attempt(job_dir, kind, attempt_id, strategy, defect, *, inputs=None, adapter=None, stage=None):
    """Durably reserve once BEFORE any remote submission or modeling mutation."""
    _identifier(attempt_id); _identifier(strategy); _identifier(defect)
    if kind not in DEFAULT_BUDGETS:
        raise ValueError('Attempt kind must be generate, refine or reference')
    with _transaction(job_dir) as (root, job):
        workflow = _workflow(job)
        if attempt_id in workflow['attempts']:
            raise ValueError('Attempt ID already exists; reconcile it, never resubmit')
        if _pending(workflow):
            raise ValueError('An unresolved attempt must be reconciled first')
        if _exhausted(workflow, kind):
            raise ValueError(f'{kind} attempt budget exhausted')
        if _stalled(workflow, strategy, defect):
            raise ValueError('Strategy stopped after two attempts without evidenced improvement')
        artifacts = _paths(root, inputs)
        if not artifacts:
            raise ValueError('Attempt inputs must contain actual hashable files')
        if kind == 'generate':
            if _review_status(job, 'reference')['status'] != 'PASS':
                raise ValueError('Generation requires a current reference review PASS')
            reviewed = [r for r in workflow['records'] if r.get('stage') == 'reference'][-1]
            reviewed_hashes = {a['sha256'] for a in reviewed['evidence']}
            if any(a['sha256'] not in reviewed_hashes for a in artifacts):
                raise ValueError('Every generation input must be hash-bound to the reference review')
        if kind == 'refine':
            revision = workflow['candidate_revision']
            if not revision or not any(_review_status(job, s, revision)['performed'] for s in ('form', 'detail')):
                raise ValueError('Review the actual candidate defect before reserving a repair')
            form_pass = _review_status(job, 'form', revision)['status'] == 'PASS'
            stage = stage or ('detail' if form_pass else 'form')
            if stage not in ('form', 'detail') or (stage == 'detail' and not form_pass):
                raise ValueError('Detail processing requires the current form review to PASS first')
        attempt = {'id': attempt_id, 'kind': kind, 'strategy': strategy, 'defect': defect,
                   'adapter': adapter, 'stage': stage, 'status': 'running', 'owner': ledger.process_owner(),
                   'started_at': ledger.utc(), 'inputs': artifacts, 'events': []}
        workflow['attempts'][attempt_id] = attempt
        return json.loads(json.dumps(attempt))


def _attempt(job, attempt_id):
    try:
        return _workflow(job)['attempts'][attempt_id]
    except KeyError:
        raise ValueError('Unknown attempt ID; reserve it before external work')


def get_attempt(job_dir, attempt_id):
    """Read authoritative state after a crash between remote receipt and job writes."""
    return json.loads(json.dumps(_attempt(_load(job_dir)[1], attempt_id)))


def record_remote(job_dir, attempt_id, remote_id, evidence=None):
    if not isinstance(remote_id, str) or not remote_id.strip():
        raise ValueError('A nonempty remote task ID is required')
    with _transaction(job_dir) as (root, job):
        attempt = _attempt(job, attempt_id)
        if attempt['status'] not in ('running', 'uncertain'):
            raise ValueError('Cannot change a completed attempt')
        if attempt.get('remote_id') not in (None, remote_id):
            raise ValueError('Remote task identity cannot change')
        attempt['remote_id'] = remote_id
        if evidence:
            attempt['receipt'] = _paths(root, [evidence])[0]
        attempt['events'].append({'at': ledger.utc(), 'event': 'remote_id_recorded', 'remote_id': remote_id})
        return dict(attempt)


def _finish(root, job, attempt_id, outcome, artifacts, improved, note, remote_id, *, reconciliation=False, evidence=None):
    attempt = _attempt(job, attempt_id)
    if outcome not in ('completed', 'failed', 'uncertain'):
        raise ValueError('Outcome must be completed, failed or uncertain')
    if attempt['status'] in ('completed', 'failed'):
        repeated = _paths(root, artifacts)
        same_outputs = not repeated or repeated == attempt.get('artifacts', [])
        same_remote = not remote_id or remote_id == attempt.get('remote_id')
        if outcome == attempt['status'] and same_outputs and same_remote:
            return json.loads(json.dumps(attempt))
        raise ValueError('Final attempt outcome is immutable')
    if attempt['status'] == 'uncertain' and outcome != 'uncertain' and not reconciliation:
        raise ValueError('An uncertain attempt requires explicit reconciliation')
    files = _paths(root, artifacts)
    proof = _paths(root, evidence)
    if outcome == 'completed' and not files:
        raise ValueError('Completed attempts require actual output artifacts')
    if improved not in (None, True, False):
        raise ValueError('Improved must be true, false or null')
    if improved is True and (outcome != 'completed' or not files or not note.strip()):
        raise ValueError('Improvement needs actual output evidence and a concrete comparison note')
    if remote_id:
        if attempt.get('remote_id') not in (None, remote_id):
            raise ValueError('Remote task identity cannot change')
        attempt['remote_id'] = remote_id
    if ledger.stale(attempt['inputs']):
        raise ValueError('Attempt inputs changed; preserve the output and reconcile the input provenance first')
    attempt.update(status=outcome, artifacts=files or attempt.get('artifacts', []),
                   improved=improved, note=note, updated_at=ledger.utc())
    attempt['events'].append({'at': ledger.utc(), 'event': 'reconciled' if reconciliation else outcome,
                              'status': outcome, 'evidence': proof})
    return json.loads(json.dumps(attempt))


def finish_attempt(job_dir, attempt_id, outcome, *, artifacts=None, improved=None, note='', remote_id=None):
    with _transaction(job_dir) as (root, job):
        return _finish(root, job, attempt_id, outcome, artifacts, improved, note, remote_id)


def reconcile_attempt(job_dir, attempt_id, outcome, *, artifacts=None, improved=None, note='', remote_id=None, evidence=None):
    if outcome not in ('completed', 'failed') or not note.strip() or not evidence:
        raise ValueError('Reconciliation requires completed/failed, a concrete note and saved evidence')
    with _transaction(job_dir) as (root, job):
        attempt = _attempt(job, attempt_id)
        if attempt['status'] == 'running' and ledger.owner_alive(attempt['owner']) is True:
            current = ledger.process_owner()
            if any(attempt['owner'].get(k) != current.get(k) for k in ('host', 'pid', 'process_start')):
                raise ValueError('A live operation owner must finish or explicitly release its attempt')
        return _finish(root, job, attempt_id, outcome, artifacts, improved, note, remote_id, reconciliation=True, evidence=evidence)


def _render_evidence(root, path, mesh_hash):
    report_file = _paths(root, [path])[0]
    report = json.loads(Path(report_file['path']).read_text(encoding='utf-8'))
    if report.get('status') not in (None, 'COMPLETED'):
        raise ValueError('Actual mesh rendering has not completed')
    if report.get('actual_mesh') is not True or report.get('stl_sha256') != mesh_hash:
        raise ValueError('Render report must bind the actual exported STL SHA256')
    views = report.get('views', {})
    if not REQUIRED_VIEWS.issubset(views):
        raise ValueError('Actual render evidence needs front/side/left45/right45/back/bottom views')
    evidence = [report_file]
    for view in views.values():
        artifact = _paths(root, [view.get('render_path', '')])[0]
        if artifact['sha256'] != view.get('sha256'):
            raise ValueError('Render image hash does not match its report')
        evidence.append(artifact)
    return evidence


def record_stage(job_dir, stage, verdict=None, *, revision=None, evidence=None, checks=None,
                 note='', reviewer='agent', render_report=None, provenance=None):
    if stage not in STAGES:
        raise ValueError('Unknown workflow stage')
    if stage == 'geometry':
        result = status(job_dir, revision)
        if not result['stages'].get('geometry', {}).get('performed'):
            raise ValueError('Run refinement_job check; geometry cannot be supplied as a hand-written verdict')
        return result['stages']['geometry']
    with _transaction(job_dir) as (root, job):
        workflow = _workflow(job)
        revision = None if stage == 'reference' else revision or workflow['candidate_revision']
        rev = ledger.get_revision(job, revision) if revision is not None else None
        if stage != 'reference' and rev is None:
            raise ValueError('Select a registered candidate revision first')
        if rev:
            ledger.current_revision(rev)
        files = _paths(root, evidence)
        if not files or not note.strip():
            raise ValueError('Every stage record needs actual evidence and a concrete observation')
        if stage == 'slice':
            current = ledger.status(job, revision)
            if not rev.get('slice') or current['stages']['slice_package'].get('reason', '').startswith('STALE'):
                raise ValueError('Audit the current package with refinement_job slice first')
            if not rev.get('reviews', {}).get('slice') or current['stages']['slice_review'].get('reason', '').startswith('STALE'):
                raise ValueError('Record the six real layer/support checks first')
            if not provenance:
                raise ValueError('A model-to-slice provenance receipt is required')
            receipt_file = _paths(root, [provenance])[0]
            receipt = json.loads(Path(receipt_file['path']).read_text(encoding='utf-8'))
            required = {'mesh', 'package', 'profiles', 'positioned', 'transform', 'slicer',
                        'request', 'launch', 'execution', 'native_result'}
            if not required.issubset(receipt) or set(receipt.get('profiles', {})) != {'machine', 'process', 'filament'}:
                raise ValueError('V2 slice receipt needs complete profiles, positioning, slicer and execution evidence')
            if not isinstance(receipt['transform'], dict) or not {'translation_mm', 'rotation_degrees', 'scale'}.issubset(receipt['transform']):
                raise ValueError('Slice receipt needs the actual positioning transform')
            if not receipt['slicer'].get('version') or not receipt['slicer'].get('executable'):
                raise ValueError('Slice receipt needs the actual slicer executable and version')
            artifact_fields = [receipt[k] for k in ('mesh', 'package', 'positioned', 'request', 'launch', 'execution', 'native_result')]
            artifact_fields += list(receipt['profiles'].values())
            if any(not isinstance(a, dict) or not {'path', 'sha256', 'bytes'}.issubset(a) for a in artifact_fields):
                raise ValueError('Every slice receipt artifact needs path, SHA256 and bytes')
            mesh_hash = rev['artifacts']['mesh']['sha256']
            package_hash = rev['slice']['package']['sha256']
            recorded_mesh = receipt.get('mesh_sha256', receipt.get('mesh', {}).get('sha256'))
            recorded_package = receipt.get('package_sha256', receipt.get('package', {}).get('sha256'))
            if recorded_mesh != mesh_hash or recorded_package != package_hash:
                raise ValueError('Slice provenance does not match the exact mesh and package')
            def collect_artifacts(value):
                found = []
                if isinstance(value, dict):
                    if 'path' in value and 'sha256' in value:
                        found.append(value)
                    for nested in value.values():
                        found.extend(collect_artifacts(nested))
                elif isinstance(value, list):
                    for nested in value:
                        found.extend(collect_artifacts(nested))
                return found
            nested = collect_artifacts(receipt)
            if ledger.stale(nested):
                raise ValueError('Slice receipt contains stale input/configuration/execution evidence')
            request, launch, execution, native = [json.loads(Path(receipt[k]['path']).read_text(encoding='utf-8-sig'))
                                                  for k in ('request', 'launch', 'execution', 'native_result')]
            if any(request.get(k) != receipt[k] for k in ('mesh', 'profiles', 'positioned', 'transform', 'slicer')):
                raise ValueError('Collected slice inputs differ from the original request')
            if launch.get('request_sha256') != receipt['request']['sha256'] or not request.get('expected_command'):
                raise ValueError('Slice launch does not bind the original request')
            if any(launch.get(k) != v for k, v in request['expected_command'].items()):
                raise ValueError('Slice launch arguments changed')
            if execution.get('launch_sha256') != receipt['launch']['sha256'] or execution.get('exit_code') != 0:
                raise ValueError('Slice execution did not complete this exact launch')
            if execution.get('slicer_version') != receipt['slicer']['version'] or native.get('return_code') != 0:
                raise ValueError('Slice native completion or version does not match')
            files += nested
            if not any(Path(f['path']).suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp') for f in files):
                raise ValueError('Slice review requires saved actual preview images')
            record = {'id': uuid.uuid4().hex, 'type': 'slice_binding', 'at': ledger.utc(),
                      'revision': revision, 'mesh_sha256': mesh_hash, 'package_sha256': package_hash,
                      'evidence': files + [receipt_file], 'note': note}
            workflow['records'].append(record)
            return record
        if verdict not in ledger.STATUSES or reviewer not in ('agent', 'owner'):
            raise ValueError('Review requires PASS/FAIL/UNKNOWN and an attributed reviewer')
        checks = checks or {}
        if set(checks) != STAGE_CHECKS[stage] or ledger.aggregate(checks.values()) != verdict:
            raise ValueError(f'{stage} review needs all checks {sorted(STAGE_CHECKS[stage])} and matching aggregate')
        if stage in ('form', 'detail'):
            if verdict == 'PASS' and _review_status(job, 'reference')['status'] != 'PASS':
                raise ValueError('A visual PASS requires a current reference PASS first')
            if stage == 'detail' and verdict == 'PASS' and _review_status(job, 'form', revision)['status'] != 'PASS':
                raise ValueError('Detail PASS requires the current form review to PASS first')
            if verdict == 'PASS' and not render_report:
                raise ValueError('A visual PASS requires a hash-bound actual mesh render report')
            if render_report:
                files += _render_evidence(root, render_report, rev['artifacts']['mesh']['sha256'])
        record = {'id': uuid.uuid4().hex, 'type': 'review', 'stage': stage, 'status': verdict,
                  'at': ledger.utc(), 'revision': revision, 'reviewer': reviewer, 'checks': checks,
                  'note': note, 'evidence': files}
        if rev:
            record['mesh_sha256'] = rev['artifacts']['mesh']['sha256']
        workflow['records'].append(record)
        if reviewer == 'owner' and rev:
            workflow['owner_reviews'].append(dict(record))
        return record


def select_candidate(job_dir, revision):
    with _transaction(job_dir) as (_, job):
        rev = ledger.get_revision(job, revision)
        ledger.current_revision(rev)
        workflow = _workflow(job)
        workflow['candidate_revision'] = revision
        if workflow['best_revision'] is None:
            workflow['best_revision'] = revision
            workflow['records'].append({'id': uuid.uuid4().hex, 'type': 'best_selection', 'at': ledger.utc(),
                                        'revision': revision, 'status': 'diagnostic',
                                        'note': 'First candidate retained as a diagnostic baseline, not an appearance approval'})
        return {'candidate_revision': revision, 'best_revision': workflow['best_revision']}


def promote_best(job_dir, revision=None, *, improved=None, baseline_revision=None, evidence=None, note=''):
    with _transaction(job_dir) as (root, job):
        workflow = _workflow(job)
        revision = revision or workflow['candidate_revision']
        if revision == workflow['best_revision']:
            return {'best_revision': revision, 'status': 'unchanged', 'appearance_approval_granted': False}
        if baseline_revision != workflow['best_revision'] or improved is not True or not note.strip():
            raise ValueError('Best promotion needs evidenced improvement against the current best baseline')
        current = ledger.get_revision(job, revision)
        previous = ledger.get_revision(job, baseline_revision)
        ledger.current_revision(current); ledger.current_revision(previous)
        if current['artifacts']['mesh']['sha256'] == previous['artifacts']['mesh']['sha256']:
            raise ValueError('Renaming the same mesh is not an improvement')
        if _owner_veto(job, current['artifacts']['mesh']['sha256']):
            raise ValueError('Owner-rejected geometry cannot replace the best baseline')
        files = _paths(root, evidence)
        hashes = {f['sha256'] for f in files}
        for candidate in (previous, current):
            previews = {a['sha256'] for key, a in candidate['artifacts'].items() if key.startswith('preview_')}
            if not previews or not previews.intersection(hashes):
                raise ValueError('Best promotion needs fresh actual previews for both compared revisions')
        workflow['best_revision'] = revision
        workflow['records'].append({'id': uuid.uuid4().hex, 'type': 'best_selection', 'at': ledger.utc(),
                                    'revision': revision, 'baseline_revision': baseline_revision,
                                    'improved': True, 'evidence': files, 'note': note,
                                    'mesh_sha256': current['artifacts']['mesh']['sha256'],
                                    'baseline_mesh_sha256': previous['artifacts']['mesh']['sha256']})
        return {'best_revision': revision, 'appearance_approval_granted': False}


def record(job_dir, event):
    """CLI JSON event entrypoint; all PASS paths still require bound evidence."""
    if not isinstance(event, dict):
        raise ValueError('Event must be a JSON object')
    args = dict(event)
    kind = args.pop('type', None)
    handlers = {'review': record_stage, 'candidate': select_candidate, 'promote': promote_best,
                'start_attempt': start_attempt, 'finish_attempt': finish_attempt, 'remote': record_remote}
    if kind not in handlers:
        raise ValueError('Unknown event type')
    return handlers[kind](job_dir, **args)


def export(job_dir, *, revision=None, destination=None):
    """Export either verified preview or explicitly labelled diagnostic evidence.

    It is legal to export FAIL/UNKNOWN with its exact limitations. Export never
    upgrades a review, silently chooses a failed trial over best, or prints.
    """
    with _transaction(job_dir) as (root, job):
        workflow = _workflow(job)
        if _pending(workflow):
            raise ValueError('Reconcile the active attempt before freezing a deliverable')
        chosen = revision or workflow['best_revision'] or workflow['candidate_revision']
        if not chosen:
            raise ValueError('No registered model is available to export')
        rev = ledger.get_revision(job, chosen)
        ledger.current_revision(rev)
        result = status_data(job, chosen)
        result['delivered_revision'] = chosen
        assets = _export_assets(job, chosen)
        changed = ledger.stale(assets.values())
        if changed:
            raise ValueError('Cannot export stale evidence; record current evidence first: ' + ', '.join(changed))
        signature = _snapshot_signature(job, chosen, assets=assets, stages=result['stages'])
        context_signature = _context_signature(workflow)
        dest = Path(destination).expanduser() if destination else root / 'outputs/workflow-v2' / f'{chosen}-{signature[:12]}'
        if not dest.is_absolute():
            dest = root / dest
        dest = dest.resolve()
        if not dest.is_relative_to(root / 'outputs'):
            raise ValueError('Local export destination must stay under this job outputs directory')
        manifest_path = dest / 'manifest.json'
        if dest.exists():
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            if manifest.get('snapshot_sha256') != signature:
                raise ValueError('Existing export represents another snapshot; choose a new destination')
            for item in manifest['files']:
                path = (dest / item['path']).resolve()
                if not path.is_relative_to(dest) or ledger.sha(path) != item['sha256']:
                    raise ValueError('Existing export is incomplete or altered')
        else:
            staging = root / 'work/workflow-v2' / ('export-' + uuid.uuid4().hex)
            staging.mkdir(parents=True)
            files = []
            for name, item in assets.items():
                source = Path(item['path'])
                relative = 'artifacts/' + name + source.suffix
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if ledger.sha(target) != item['sha256']:
                    raise ValueError('Export copy hash mismatch')
                files.append({'path': relative, 'sha256': item['sha256'], 'bytes': item['bytes'], 'source': item['path']})
            _atomic_json(staging / 'status.json', result)
            status_artifact = ledger.artifact(staging / 'status.json')
            files.append({'path': 'status.json', 'sha256': status_artifact['sha256'], 'bytes': status_artifact['bytes']})
            _atomic_json(staging / 'history.json', _history_data(job, chosen))
            history_artifact = ledger.artifact(staging / 'history.json')
            files.append({'path': 'history.json', 'sha256': history_artifact['sha256'], 'bytes': history_artifact['bytes']})
            manifest = {'schema_version': 2, 'revision': chosen, 'snapshot_sha256': signature,
                        'mesh_sha256': rev['artifacts']['mesh']['sha256'],
                        'status': 'preview_complete' if result['preview_complete'] else 'diagnostic_complete',
                        'print_ready': result['print_ready'], 'print_authorization_granted': False,
                        'history': {'path': 'history.json', 'sha256': history_artifact['sha256'],
                                    'scope': 'Historical assertions preserved separately from current effective evidence'},
                        'limitations': result['limitations'], 'files': files, 'created_at': ledger.utc()}
            _atomic_json(staging / 'manifest.json', manifest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(dest)
        workflow['delivered_revision'] = chosen
        if (not workflow['exports'] or workflow['exports'][-1]['snapshot_sha256'] != signature
                or workflow['exports'][-1].get('context_sha256') != context_signature):
            workflow['exports'].append({'revision': chosen, 'snapshot_sha256': signature,
                                        'context_sha256': context_signature,
                                        'manifest': ledger.artifact(manifest_path), 'at': ledger.utc()})
        return {'directory': str(dest), 'manifest': str(manifest_path), **{k: manifest[k] for k in ('revision', 'status', 'print_ready', 'limitations')}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('init', 'next', 'status', 'record', 'reconcile', 'export'):
        p = sub.add_parser(command); p.add_argument('--job', required=True)
        if command in ('record', 'reconcile'):
            p.add_argument('--event', required=True, help='Path to a JSON event; never eval shell text')
        if command in ('status', 'export'):
            p.add_argument('--revision')
        if command == 'export':
            p.add_argument('--destination')
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            result = init(args.job)
        elif args.command == 'next':
            result = next_action(args.job)
        elif args.command == 'status':
            result = status(args.job, args.revision)
        elif args.command == 'record':
            result = record(args.job, json.loads(Path(args.event).read_text(encoding='utf-8')))
        elif args.command == 'reconcile':
            result = reconcile_attempt(args.job, **json.loads(Path(args.event).read_text(encoding='utf-8')))
        else:
            result = export(args.job, revision=args.revision, destination=args.destination)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
