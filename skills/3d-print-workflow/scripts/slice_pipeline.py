#!/usr/bin/env python3
"""Prepare an immutable Bambu slice request and collect its actual result.

This program never launches a printer or a slicer. The host executes launch.json
as an argument array. Collection checks the exact inputs and native result,
preserves warnings, and records provenance rather than claiming printability.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys
import zipfile
from xml.sax.saxutils import escape


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def artifact(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'sha256': sha(path), 'bytes': path.stat().st_size}


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def unchanged(items):
    for item in items:
        if not Path(item['path']).is_file() or sha(item['path']) != item['sha256']:
            raise ValueError('STALE slice input: ' + item['path'])


def host_path(path, distribution):
    value = str(Path(path).resolve())
    if not distribution or sys.platform == 'win32':
        return value
    if value.startswith('/mnt/') and len(value) > 7 and value[6] == '/':
        return value[5].upper() + ':\\' + value[7:].replace('/', '\\')
    return '\\\\wsl.localhost\\' + distribution + value.replace('/', '\\')


def position(mesh_path, target, bed, reserve=0.0):
    """Exact-coordinate welding; center a millimetre STL without changing shape."""
    import numpy as np
    import trimesh
    mesh = trimesh.load_mesh(str(mesh_path), process=False)
    if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
        raise ValueError('One nonempty STL mesh is required')
    if not np.isfinite(mesh.vertices).all():
        raise ValueError('Nonfinite mesh coordinates')
    vertices, inverse = np.unique(mesh.vertices, axis=0, return_inverse=True)
    faces = inverse[mesh.faces]
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    size = high - low
    if any(not math.isfinite(v) or v <= 0 for v in bed):
        raise ValueError('Build volume must be finite and positive')
    if not math.isfinite(reserve) or reserve < 0 or np.any(size > np.array(bed) - reserve):
        raise ValueError('Model exceeds build volume with the requested total reserve')
    shift = np.array([bed[0] / 2, bed[1] / 2, 0]) - np.array([(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, low[2]])
    ns = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
    types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>'
    rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="r1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', types)
        archive.writestr('_rels/.rels', rels)
        with archive.open('3D/3dmodel.model', 'w') as output:
            def emit(text):
                output.write(text.encode('utf-8'))
            emit(f'<?xml version="1.0" encoding="UTF-8"?><model xmlns="{ns}" unit="millimeter"><resources><object id="1" type="model" name="{escape(Path(mesh_path).stem, {chr(34): "&quot;"})}"><mesh><vertices>')
            for start in range(0, len(vertices), 8000):
                emit(''.join('<vertex x="%.17g" y="%.17g" z="%.17g"/>' % tuple(row) for row in vertices[start:start+8000]))
            emit('</vertices><triangles>')
            for start in range(0, len(faces), 8000):
                emit(''.join('<triangle v1="%d" v2="%d" v3="%d"/>' % tuple(row) for row in faces[start:start+8000]))
            emit('</triangles></mesh></object></resources><build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 ' + ' '.join('%.12g' % x for x in shift) + '"/></build></model>')
    return {'translation_mm': shift.tolist(), 'rotation_degrees': [0, 0, 0], 'scale': 1.0,
            'source_bounds_mm': [low.tolist(), high.tolist()], 'dimensions_mm': size.tolist(),
            'positioned_bounds_mm': [(low+shift).tolist(), (high+shift).tolist()],
            'vertices': len(vertices), 'faces': len(faces), 'geometry_change': False,
            'scope': 'Fixed orientation object bounds only; support/brim envelope requires actual slice review.'}


def prepare(mesh, out, machine, process, filament, slicer, version, bed, reserve=0.0, distribution=None):
    out = Path(out).resolve()
    if out.exists():
        raise ValueError('Slice run already exists; preserve it or collect its result')
    source = artifact(mesh)
    originals = {key: artifact(path) for key, path in [('machine', machine), ('process', process), ('filament', filament)]}
    out.mkdir(parents=True)
    settings = {}
    for key, info in originals.items():
        # Validate JSON and preserve the exact bytes used by the slicer.
        json.loads(Path(info['path']).read_text(encoding='utf-8-sig'))
        target = out / (key + '.json')
        shutil.copyfile(info['path'], target)
        settings[key] = artifact(target)
    positioned = out / 'positioned.3mf'
    transform = position(mesh, positioned, bed, reserve)
    unchanged([source, *originals.values()])
    args = ['--debug', '2', '--load-settings', ';'.join(host_path(settings[k]['path'], distribution) for k in ('machine', 'process')),
            '--load-filaments', host_path(settings['filament']['path'], distribution), '--curr-bed-type', 'Textured PEI Plate',
            '--scale', '1', '--arrange', '0', '--slice', '1', '--export-3mf', 'result.gcode.3mf',
            '--outputdir', host_path(out, distribution), host_path(positioned, distribution)]
    request = {'schema_version': 1, 'mesh': source, 'profiles': settings,
               'source_profiles': originals, 'positioned': artifact(positioned), 'transform': transform,
               'slicer': {'executable': str(slicer), 'version': version}, 'bed_mm': bed,
               'reserve_total_mm': reserve, 'package_path': str(out / 'result.gcode.3mf'),
               'printer_actions': 'none', 'status': 'PREPARED',
               'expected_command': {'executable': str(slicer), 'arguments': args,
                                    'working_directory': host_path(out, distribution)}}
    save(out / 'request.json', request)
    launch = {'executable': str(slicer), 'arguments': args, 'working_directory': host_path(out, distribution),
              'request_sha256': sha(out / 'request.json'), 'slicer_version': version, 'printer_actions': 'none'}
    save(out / 'launch.json', launch)
    return request


def collect(run):
    run = Path(run).resolve(strict=True)
    request = json.loads((run / 'request.json').read_text(encoding='utf-8'))
    launch = json.loads((run / 'launch.json').read_text(encoding='utf-8'))
    if launch['request_sha256'] != sha(run / 'request.json'):
        raise ValueError('STALE launch request')
    if any(launch.get(key) != value for key, value in request['expected_command'].items()):
        raise ValueError('Launch arguments differ from the bound slice request')
    unchanged([request['mesh'], request['positioned'], *request['profiles'].values()])
    execution_file = run / 'execution.json'
    if not execution_file.is_file():
        raise ValueError('Missing host execution receipt; shell exit alone is insufficient')
    execution = json.loads(execution_file.read_text(encoding='utf-8-sig'))
    if execution.get('launch_sha256') != sha(run / 'launch.json') or execution.get('exit_code') != 0:
        raise ValueError('Slicer execution failed or does not match this launch')
    if execution.get('slicer_version') != request['slicer']['version']:
        raise ValueError('Slicer version changed from the prepared request')
    native = json.loads((run / 'result.json').read_text(encoding='utf-8-sig'))
    if native.get('return_code') != 0:
        raise ValueError('Slicer did not report successful completion')
    package = artifact(request['package_path'])
    helper = Path(__file__).with_name('print_audit.py')
    spec = importlib.util.spec_from_file_location('slice_pipeline_audit', helper)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    result = audit.slice_report(package['path'], 1)
    state = 'FAIL' if not result['md5_match'] or result['metadata'].get('outside') == 'true' else 'PASS' if result['metadata'].get('outside') == 'false' else 'UNKNOWN'
    report = {'schema_version': 1, 'status': state, 'mesh': request['mesh'], 'profiles': request['profiles'],
              'positioned': request['positioned'], 'transform': request['transform'], 'slicer': request['slicer'],
              'request': artifact(run / 'request.json'), 'launch': artifact(run / 'launch.json'),
              'execution': artifact(execution_file), 'native_result': artifact(run / 'result.json'),
              'package': package, 'audit': result, 'printer_actions': 'none',
              'scope': 'Verified prepared inputs, host execution, native completion and G-code integrity; visual layer/support review remains separate.'}
    save(run / 'slice-provenance.json', report)
    return report


def preview(run, output=None):
    """Extract the exact audited plate toolpaths for Studio's G-code viewer."""
    run = Path(run).resolve(strict=True)
    receipt = collect(run)
    target = Path(output).resolve() if output else run / 'exact-toolpaths.gcode'
    if target.suffix.lower() != '.gcode' or target.parent != run:
        raise ValueError('Preview must be a .gcode file in this slice run')
    with zipfile.ZipFile(receipt['package']['path']) as archive:
        member = 'Metadata/plate_1.gcode'
        if archive.namelist().count(member) != 1:
            raise ValueError('Expected exactly one audited plate G-code member')
        data = archive.read(member)
    if hashlib.md5(data).hexdigest() != receipt['audit']['md5_actual']:
        raise ValueError('Package changed during preview extraction')
    unchanged([receipt['package']])
    if target.exists():
        if target.read_bytes() != data:
            raise ValueError('Existing preview differs from the audited toolpaths')
    else:
        with target.open('xb') as stream:
            stream.write(data)
    report = {'schema_version': 1, 'package': receipt['package'],
              'member': member, 'gcode': artifact(target),
              'gcode_md5': receipt['audit']['md5_actual'],
              'toolpath_bytes_changed': False, 'printer_actions': 'none',
              'scope': 'Exact native toolpaths for G-code viewer; original package retains settings and warnings. GUI review remains separate.'}
    save(run / 'preview-extraction.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    for name in ('mesh', 'out', 'machine', 'process', 'filament', 'slicer', 'slicer-version'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--bed', nargs=3, type=float, required=True)
    p.add_argument('--reserve-total', type=float, default=0)
    p.add_argument('--windows-distribution')
    sub.add_parser('collect').add_argument('--run', required=True)
    p = sub.add_parser('preview')
    p.add_argument('--run', required=True)
    p.add_argument('--output', help='Optional .gcode path inside this run')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare(args.mesh, args.out, args.machine, args.process, args.filament,
                             args.slicer, args.slicer_version, args.bed, args.reserve_total, args.windows_distribution)
        elif args.command == 'preview':
            result = preview(args.run, args.output)
        else:
            result = collect(args.run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == 'collect':
            return {'PASS': 0, 'FAIL': 2, 'UNKNOWN': 3}[result['status']]
        return 0
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        print(json.dumps({'error': str(exc), 'printer_actions': 'none'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
