"""Check the installed consumer runtime inside Windows Sandbox; stdlib only."""
from __future__ import annotations

import argparse
import ctypes
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--case', type=Path, required=True)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--scenario', choices=['uv', 'pip', 'uv-failed'], required=True)
    args = parser.parse_args()
    assert not ctypes.windll.shell32.IsUserAnAdmin(), 'Consumer unexpectedly has administrator rights'
    release = json.loads(args.release.read_text(encoding='utf-8-sig'))
    assert '.'.join(map(str, sys.version_info[:3])) == release['python_version']
    assert importlib.metadata.version('adaos') == release['adaos_version']
    assert importlib.metadata.version('y-py') == '0.6.2+adaos.1'
    base = Path(sys._base_executable).resolve()
    assert base.is_relative_to((args.destination / '.adaos/runtime').resolve()), 'System Python selected'
    assert Path(sys.prefix).resolve().is_relative_to((args.destination / '.adaos/environments').resolve())
    try:
        with socket.create_connection(('1.1.1.1', 443), timeout=2):
            raise AssertionError('Sandbox unexpectedly has external connectivity')
    except OSError:
        pass
    with (args.case / 'cli.log').open('w', encoding='utf-8') as log:
        subprocess.run([sys.executable, '-m', 'adaos', '--help'], cwd=args.destination,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    if args.scenario == 'uv':
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0)); port = available.getsockname()[1]
        with (args.case / 'api.log').open('w', encoding='utf-8') as log:
            api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'adaos.apps.api.server:app',
                                    '--host', '127.0.0.1', '--port', str(port)],
                                   cwd=args.destination, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for _ in range(180):
                    try:
                        for endpoint in ('live', 'ready'):
                            with opener.open(f'http://127.0.0.1:{port}/health/{endpoint}', timeout=2) as response:
                                assert response.status == 200
                                (args.case / f'health-{endpoint}.json').write_bytes(response.read())
                        break
                    except OSError:
                        if api.poll() is not None:
                            raise RuntimeError('Consumer API exited before readiness')
                        time.sleep(1)
                else:
                    raise RuntimeError('Consumer API readiness timed out')
            finally:
                api.terminate(); api.wait(timeout=30)
    attempts = list((args.case / 'outbox').glob('attempt-*'))
    assert len(attempts) == 1
    request = json.loads((attempts[0] / 'request.json').read_text(encoding='utf-8-sig'))
    events = [json.loads(path.read_text(encoding='utf-8-sig')) for path in attempts[0].glob('event-*.json')]
    assert any(event['type'] == 'installation_succeeded' for event in events)
    assert not any(event['type'] == 'installation_failed' for event in events)
    recovered = any(event['reason'] == 'uv_install_failed' for event in events)
    assert recovered == (args.scenario == 'uv-failed')
    assert (request['uv_policy'] == 'disabled') == (args.scenario == 'pip')
    assert all(path.with_suffix('.ack').is_file() for path in attempts[0].glob('*.json'))
    proof = {'ok': True, 'scenario': args.scenario, 'administrator': False,
             'python': sys.version.split()[0], 'adaos': importlib.metadata.version('adaos'),
             'y_py': importlib.metadata.version('y-py'), 'base_python': str(base),
             'external_egress': False, 'pending_attempts': 0, 'uv_failure_recovered': recovered,
             'live': 200 if args.scenario == 'uv' else None,
             'ready': 200 if args.scenario == 'uv' else None}
    (args.case / 'verification.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
    print(json.dumps(proof), flush=True)


if __name__ == '__main__':
    main()
