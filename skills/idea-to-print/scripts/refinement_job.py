#!/usr/bin/env python3
"""Revision-bound refinement evidence. Offline only: never generates or prints.

An appearance/support review is an attributed judgment, not an automatic mesh
measurement. Every stored artifact is rehashed before a result can be reused.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

STATUSES = {'PASS', 'FAIL', 'UNKNOWN'}
# The sculpture workflow has a fixed acceptance floor. A diagnostic profile
# may narrow its own gate but cannot silently narrow this ledger's coverage.
FULL_GEOMETRY_CHECKS = (
    'input_integrity', 'watertight', 'edge_manifold', 'vertex_manifold',
    'winding', 'degenerate_faces', 'duplicate_faces', 'components',
    'outward_normals', 'build_volume', 'self_intersection', 'minimum_wall',
    'minimum_detail', 'minimum_connection', 'base_contact', 'stability',
)
SLICE_CHECKS = {'first_layer', 'supports_access', 'thin_features',
                'machine_material', 'envelope', 'warnings'}
SKILLS = Path(__file__).resolve().parents[2]


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def artifact(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f'Not a file: {path}')
    return {'path': str(path), 'sha256': sha(path), 'bytes': path.stat().st_size}


def stale(items):
    changed = []
    for item in items:
        try:
            if sha(item['path']) != item['sha256']:
                changed.append(item['path'])
        except (OSError, KeyError):
            changed.append(item.get('path', 'missing artifact path'))
    return changed


def aggregate(states):
    states = list(states)
    if not states or any(state not in STATUSES for state in states):
        return 'UNKNOWN'
    if 'FAIL' in states:
        return 'FAIL'
    return 'UNKNOWN' if 'UNKNOWN' in states else 'PASS'


def geometry_coverage(report):
    """Recompute full acceptance from tool checks, never from a scoped PASS."""
    checks = report.get('checks', {})
    checks = checks if isinstance(checks, dict) else {}
    declared = report.get('required_checks', [])
    required = [key for key in declared if isinstance(key, str)] if isinstance(declared, list) else []
    excluded = [key for key in FULL_GEOMETRY_CHECKS if key not in required]
    failed = sorted(key for key, item in checks.items() if isinstance(item, dict)
                    and item.get('status') == 'FAIL'
                    and (key in FULL_GEOMETRY_CHECKS or item.get('category') == 'geometry'))
    unknown = []
    missing = []
    for key in FULL_GEOMETRY_CHECKS:
        item = checks.get(key)
        if not isinstance(item, dict):
            missing.append(key)
            unknown.append(key)
        elif key not in failed and (
                report.get('schema_version') != 1 or report.get('overall_scope') != 'geometry'
                or key not in required or item.get('required') is not True
                or item.get('category') != 'geometry' or item.get('status') != 'PASS'):
            unknown.append(key)
    scoped = report.get('overall')
    # Even malformed/incomplete check lists may not erase a tool's FAIL.
    verdict = 'FAIL' if failed or scoped == 'FAIL' else 'UNKNOWN' if unknown or scoped != 'PASS' else 'PASS'
    return {'status': verdict, 'gate_scoped_status': scoped if scoped in STATUSES else 'UNKNOWN',
            'full_required': list(FULL_GEOMETRY_CHECKS), 'profile_required': required,
            'excluded': excluded, 'unknown_full': unknown, 'missing_checks': missing,
            'failed_checks': failed,
            'scope': 'Full sculpture geometry: all 16 fixed checks must be required and PASS.'}


@contextmanager
def transaction(directory):
    directory = Path(directory).expanduser().resolve(strict=True)
    jobpath = directory / 'job.json'
    lockpath = directory / '.refinement.lock'
    # A concurrent invocation must inspect/retry later; never remove its lock.
    with lockpath.open('x', encoding='utf-8') as lock:
        lock.write(json.dumps({'pid': os.getpid(), 'at': utc()}))
    try:
        job = json.loads(jobpath.read_text(encoding='utf-8'))
        yield directory, job
        temp = directory / ('.refinement-' + uuid.uuid4().hex + '.tmp')
        try:
            temp.write_text(json.dumps(job, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temp.replace(jobpath)
        finally:
            if temp.exists():
                temp.unlink()
    finally:
        lockpath.unlink()


def get_revision(job, name):
    try:
        return job['refinement']['revisions'][name]
    except KeyError as exc:
        raise ValueError(f'Unknown revision: {name}') from exc


def current_revision(revision):
    changed = stale(revision['artifacts'].values())
    if changed:
        raise ValueError('Revision artifacts changed; register a new revision: ' + ', '.join(changed))


def run_report(command, out):
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if result.returncode not in (0, 2, 3) or not out.is_file():
        raise ValueError('Validator did not produce a report: ' + (result.stderr or result.stdout)[-2000:])
    return json.loads(out.read_text(encoding='utf-8'))


def check_status(record, revision, *, review=False, geometry=False):
    if not record:
        return {'status': 'UNKNOWN', 'reason': 'not performed'}
    files = list(record.get('evidence', []))
    for key in ('report', 'profile', 'package', 'validator'):
        if record.get(key):
            files.append(record[key])
    changed = stale(files)
    if changed or record.get('mesh_sha256') != revision['artifacts']['mesh']['sha256']:
        return {'status': 'UNKNOWN', 'reason': 'STALE evidence or mesh identity', 'changed': changed}
    if review and record.get('kind') == 'slice':
        package = revision.get('slice', {}).get('package')
        if not package or record.get('package_sha256') != package['sha256']:
            return {'status': 'UNKNOWN', 'reason': 'STALE slice review: different ready package'}
    result = {'status': record['status'], 'recorded_at': record['at'],
            'evidence_type': 'attributed visual review' if review else 'deterministic tool report',
            'note': record.get('note', ''), 'scope': record.get('scope')}
    if geometry:
        try:
            report = json.loads(Path(record['report']['path']).read_text(encoding='utf-8'))
            if not isinstance(report, dict):
                report = {}
        except (OSError, KeyError, ValueError):
            report = {}
        coverage = geometry_coverage(report)
        if record.get('status') == 'FAIL':
            coverage['status'] = 'FAIL'
        result.update(status=coverage['status'], coverage=coverage, scope=coverage['scope'])
    return result


def status(job, name):
    revision = get_revision(job, name)
    changed = stale(revision['artifacts'].values())
    stages = {
        'geometry': check_status(revision.get('geometry'), revision, geometry=True),
        'appearance': check_status(revision.get('reviews', {}).get('appearance'), revision, review=True),
        'slice_package': check_status(revision.get('slice'), revision),
        'slice_review': check_status(revision.get('reviews', {}).get('slice'), revision, review=True),
    }
    if changed:
        stages = {key: {'status': 'UNKNOWN', 'reason': 'STALE revision artifacts'} for key in stages}
    overall = aggregate(item['status'] for item in stages.values())
    if changed:
        action = 'Register changed geometry/previews as a new revision; do not reuse old checks.'
    elif stages['geometry']['status'] == 'FAIL':
        action = 'Refine the measured mesh defects, register a new revision and recheck.'
    elif stages['appearance']['status'] == 'FAIL':
        action = 'Refine the observed likeness/design mismatch; geometry results do not override it.'
    elif stages['slice_package']['status'] == 'FAIL' or stages['slice_review']['status'] == 'FAIL':
        action = 'Correct the slice or support plan and recheck the exact new package.'
    elif overall == 'UNKNOWN':
        action = 'Complete the unknown checks. Diagnostic slicing is permitted; do not call the model print-ready.'
    else:
        action = 'Checks pass within the declared profile/review scope. Reconcile existing print authorization and fresh physical preflight separately.'
    return {'schema_version': 1, 'revision': name, 'mesh': revision['artifacts']['mesh'],
            'stages': stages, 'overall': overall, 'stale_artifacts': changed,
            'next_action': action, 'print_authorization_granted': False,
            'scope': 'Evidence coordination; not a physical certification or a printer command.'}


def execute(args):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', args.id):
        raise ValueError('Revision ID must be a short filename-safe identifier')
    if args.command == 'status':
        job = json.loads((Path(args.job).expanduser().resolve() / 'job.json').read_text(encoding='utf-8'))
        return status(job, args.id)
    with transaction(args.job) as (directory, job):
        if args.command == 'register':
            if not args.note.strip():
                raise ValueError('A concrete change note is required')
            ledger = job.setdefault('refinement', {'schema_version': 1, 'revisions': {}})
            if args.id in ledger['revisions']:
                raise ValueError('Revision IDs are immutable; choose a new ID')
            if args.parent:
                get_revision(job, args.parent)
            if Path(args.mesh).suffix.lower() != '.stl':
                raise ValueError('Gate input must be an STL exported in millimetres')
            artifacts = {'mesh': artifact(args.mesh), 'editable': artifact(args.editable)}
            artifacts.update({f'preview_{i}': artifact(p) for i, p in enumerate(args.preview)})
            artifacts.update({f'reference_{i}': artifact(p) for i, p in enumerate(args.reference or [])})
            if args.operations:
                # Keep the exact recipe, not a normalized rewrite of its content.
                json.loads(Path(args.operations).read_text(encoding='utf-8'))
                artifacts['operations'] = artifact(args.operations)
            ledger['revisions'][args.id] = {'id': args.id, 'parent': args.parent, 'at': utc(),
                'note': args.note, 'units': 'mm', 'artifacts': artifacts, 'reviews': {}, 'history': []}
            ledger['active_revision'] = args.id
            return status(job, args.id)

        revision = get_revision(job, args.id)
        current_revision(revision)
        mesh_hash = revision['artifacts']['mesh']['sha256']
        stamp = {'at': utc(), 'mesh_sha256': mesh_hash}
        work = directory / 'work' / 'refinement-evidence' / args.id
        work.mkdir(parents=True, exist_ok=True)
        tag = uuid.uuid4().hex[:12]
        if args.command == 'check':
            profile = artifact(args.profile)
            validator = SKILLS / '3d-print-workflow/scripts/printability_gate.py'
            validator_record = artifact(validator)
            out = work / f'geometry-{tag}.json'
            report = run_report([sys.executable, str(validator), 'inspect', '--mesh',
                revision['artifacts']['mesh']['path'], '--profile', profile['path'], '--out', str(out)], out)
            if report.get('mesh', {}).get('sha256') != mesh_hash:
                raise ValueError('Validator report does not match the current mesh')
            verdict = report.get('overall')
            if verdict not in STATUSES:
                raise ValueError('Validator returned an invalid verdict')
            coverage = geometry_coverage(report)
            if stale([profile, validator_record]):
                raise ValueError('Profile or validator changed while checking')
            current_revision(revision)
            record = {**stamp, 'status': coverage['status'], 'report': artifact(out),
                'profile': profile, 'validator': validator_record,
                'scope': coverage['scope'], 'coverage': coverage}
            if revision.get('geometry'):
                revision['history'].append({'kind': 'geometry', **revision['geometry']})
            revision['geometry'] = record
        elif args.command == 'slice':
            package = artifact(args.package)
            helper = SKILLS / '3d-print-workflow/scripts/print_audit.py'
            spec = importlib.util.spec_from_file_location('refinement_slice_audit', helper)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            report = module.slice_report(package['path'], args.plate)
            if not report['md5_match'] or report['metadata'].get('outside') == 'true':
                verdict = 'FAIL'
            elif report['metadata'].get('outside') != 'false':
                verdict = 'UNKNOWN'
            else:
                verdict = 'PASS'
            if stale([package]):
                raise ValueError('Ready package changed during audit')
            current_revision(revision)
            out = work / f'slice-{tag}.json'
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            record = {**stamp, 'status': verdict, 'package': package, 'report': artifact(out),
                'validator': artifact(helper), 'note': args.note,
                'scope': 'G-code integrity and declared outside flag only; model-to-G-code relation is operator-recorded, not independently reconstructed.',
                'warnings': report['warnings']}
            if revision.get('slice'):
                revision['history'].append({'kind': 'slice', **revision['slice']})
            revision['slice'] = record
        elif args.command == 'review':
            if not args.note.strip():
                raise ValueError('A concrete observation note is required')
            checks = {}
            for value in args.check or []:
                name, sep, verdict = value.partition('=')
                if not sep or verdict not in STATUSES or name in checks:
                    raise ValueError('Each check must be a unique NAME=PASS|FAIL|UNKNOWN')
                checks[name] = verdict
            record = {**stamp, 'kind': args.kind, 'status': args.status,
                'reviewer': args.reviewer, 'note': args.note,
                'evidence': [artifact(p) for p in args.evidence], 'checks': checks,
                'scope': 'Attributed judgment from actual evidence; not an automated geometry proof.'}
            if args.kind == 'slice':
                if not revision.get('slice'):
                    raise ValueError('Record and audit a ready package before reviewing its toolpaths')
                if set(checks) != SLICE_CHECKS or aggregate(checks.values()) != args.status:
                    raise ValueError('Slice review requires all six checks; status must equal their aggregate: ' + ', '.join(sorted(SLICE_CHECKS)))
                if check_status(revision['slice'], revision)['status'] == 'UNKNOWN':
                    raise ValueError('Slice audit is missing or stale; repeat it first')
                record['package_sha256'] = revision['slice']['package']['sha256']
            elif checks and aggregate(checks.values()) != args.status:
                raise ValueError('Review status does not match check details')
            previous = revision['reviews'].get(args.kind)
            if previous:
                revision['history'].append({'kind': 'review', **previous})
            revision['reviews'][args.kind] = record
        return status(job, args.id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('register', 'check', 'review', 'slice', 'status'):
        p = sub.add_parser(name)
        p.add_argument('--job', required=True, help='Existing job directory containing job.json')
        p.add_argument('--id', required=True, help='Immutable revision ID')
        if name == 'register':
            p.add_argument('--mesh', required=True)
            p.add_argument('--editable', required=True)
            p.add_argument('--preview', action='append', required=True)
            p.add_argument('--reference', action='append')
            p.add_argument('--parent')
            p.add_argument('--operations')
        if name in ('register', 'review', 'slice'):
            p.add_argument('--note', required=True)
        if name == 'check':
            p.add_argument('--profile', required=True)
        if name == 'slice':
            p.add_argument('--package', required=True)
            p.add_argument('--plate', type=int, default=1)
        if name == 'review':
            p.add_argument('--kind', choices=('appearance', 'slice'), required=True)
            p.add_argument('--status', choices=sorted(STATUSES), required=True)
            p.add_argument('--reviewer', choices=('agent', 'owner'), required=True)
            p.add_argument('--evidence', action='append', required=True)
            p.add_argument('--check', action='append')
    args = parser.parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        # Mutation success is distinct from the report's verdict. `status` is
        # the explicit gate-like query; recording UNKNOWN evidence succeeds.
        return {'PASS': 0, 'FAIL': 2, 'UNKNOWN': 3}[result['overall']] if args.command == 'status' else 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
