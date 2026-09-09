#!/usr/bin/env python3
"""Windows-only bounded launcher for slice_pipeline's immutable request.

Starts only Bambu Studio's offline slice CLI, never a printer command. Execute
with the Windows Python runtime because Bambu Studio is a Windows application.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def file_version(path):
    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise ValueError('Cannot read installed slicer file version')
    data = ctypes.create_string_buffer(size)
    if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, data):
        raise ValueError('Cannot read installed slicer version information')
    pointer, length = ctypes.c_void_p(), ctypes.c_uint()
    if not ctypes.windll.version.VerQueryValueW(data, '\\', ctypes.byref(pointer), ctypes.byref(length)):
        raise ValueError('Missing slicer version resource')
    words = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
    return '.'.join(map(str, (words[2] >> 16, words[2] & 65535, words[3] >> 16, words[3] & 65535)))


def save(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def verify_inputs(root, prepared):
    # Request paths can be WSL paths; these exact snapshots live beside launch.json
    # and are the files read by the native CLI. Check before reserving execution.
    for name, expected in [('positioned.3mf', prepared['positioned']),
                           *[(key + '.json', prepared['profiles'][key])
                             for key in ('machine', 'process', 'filament')]]:
        path = root / name
        if not path.is_file() or sha(path) != expected['sha256']:
            raise ValueError('STALE prepared slicer input: ' + name)


def claim_execution(root, intent):
    # The initial claim must be exclusive, not exists-then-replace. It remains
    # after exit so ambiguous or duplicate host invocations cannot run again.
    with (root / 'execution-intent.json').open('x', encoding='utf-8') as output:
        json.dump(intent, output, ensure_ascii=False, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())


def run(launch_file, timeout=1800):
    if os.name != 'nt':
        raise ValueError('Run this host launcher with Windows Python')
    launch_file = Path(launch_file).resolve(strict=True)
    request = json.loads(launch_file.read_text(encoding='utf-8'))
    root = launch_file.parent
    prepared = json.loads((root / 'request.json').read_text(encoding='utf-8'))
    if request.get('request_sha256') != sha(root / 'request.json'):
        raise ValueError('Slice request changed since launch preparation')
    if any(request.get(key) != value for key, value in prepared['expected_command'].items()):
        raise ValueError('Launch arguments differ from the bound request')
    verify_inputs(root, prepared)
    if (root / 'execution.json').exists() or (root / 'execution-intent.json').exists():
        raise ValueError('Execution already attempted; inspect existing process/result before any new run')
    if request.get('printer_actions') != 'none':
        raise ValueError('Only offline slicing requests are supported')
    expected = tuple(int(x) for x in request['slicer_version'].split('.'))
    actual = file_version(request['executable'])
    if tuple(int(x) for x in actual.split('.')) != expected:
        raise ValueError('Installed slicer version differs from prepared request')
    args = request['arguments']
    if '--slice' not in args or '--export-3mf' not in args or any(x in args for x in ('--send', '--print', '--connect')):
        raise ValueError('Unsupported launch action')
    intent = {'at': datetime.now(timezone.utc).isoformat(), 'launch_sha256': sha(launch_file),
              'slicer_version': request['slicer_version'], 'slicer_sha256': sha(request['executable']),
              'host_pid': os.getpid(), 'printer_actions': 'none'}
    claim_execution(root, intent)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    with (root / 'stdout.log').open('wb') as stdout, (root / 'stderr.log').open('wb') as stderr:
        process = subprocess.Popen([request['executable'], *args], cwd=request['working_directory'],
                                   stdout=stdout, stderr=stderr, startupinfo=startup,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        intent['slicer_pid'] = process.pid
        save(root / 'execution-intent.json', intent)
        try:
            code = process.wait(timeout=timeout)
            outcome = 'exited'
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            code, outcome = process.returncode, 'owned_process_timed_out'
    result = {**intent, 'exit_code': code, 'outcome': outcome,
              'completed_at': datetime.now(timezone.utc).isoformat()}
    save(root / 'execution.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--launch', required=True)
    p.add_argument('--timeout', type=int, default=1800)
    args = p.parse_args()
    try:
        if not 1 <= args.timeout <= 7200:
            raise ValueError('Timeout must be between 1 and 7200 seconds')
        result = run(args.launch, args.timeout)
        print(json.dumps(result))
        return 0 if result['exit_code'] == 0 else 2
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'error': str(exc), 'printer_actions': 'none'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
