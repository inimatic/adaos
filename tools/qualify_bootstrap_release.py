#!/usr/bin/env python3
"""Exercise a bootstrap kit on Ubuntu 20.04 in a network without Internet access.

Uses the real backend resolver and durable event store in a separate container.
All test output belongs in a repository .tmp/ directory. No cloud credentials.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid
import os
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap_runtime import PROFILES

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = """FROM ubuntu:20.04
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends wget ca-certificates python3-minimal && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --shell /bin/bash tester
USER tester
WORKDIR /home/tester
"""


def command(*args: str, **options):
    return subprocess.run(args, check=True, **options)


def qualify_windows(args, output: Path, record: dict) -> None:
    """Real PowerShell ingress and offline dependency installs in isolated dirs."""
    if sys.platform != 'win32':
        raise ValueError('Windows qualification requires a Windows execution host')
    with socket.socket() as available:
        available.bind(('127.0.0.1', 0))
        port = available.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    state = output / 'root-state'
    state.mkdir()
    release = {key: value for key, value in record.items() if key != 'filename'}
    release.update(channel='beta', zone_id='ru', bundle_url=base + '/bundles/' + record['filename'])
    (state / 'catalogue.json').write_text(json.dumps({'schema': 'adaos.bootstrap_catalogue.v1', 'releases': [release]}), encoding='utf-8')
    env = dict(os.environ, BOOTSTRAP_STAND_DIRECTORY=str(state), BOOTSTRAP_STAND_ADMIN_TOKEN='isolated-qualification-only',
               BOOTSTRAP_STAND_PORT=str(port), BOOTSTRAP_STAND_HOST='127.0.0.1',
               BOOTSTRAP_STAND_BUNDLES=str(args.release_dir.resolve()), ADAOS_BOOTSTRAP_ALLOW_HTTP_TEST='1')
    # Inherited supervisor/developer paths or credentials must not attach this
    # qualification to the operator's actual node or development environment.
    for key in tuple(env):
        if key.startswith(('ADAOS_', 'ROOT_', 'REG_RU_')) and key != 'ADAOS_BOOTSTRAP_ALLOW_HTTP_TEST':
            env.pop(key)
    dotenv = output / 'empty.env';dotenv.write_text('', encoding='utf-8')
    env['ADAOS_SHARED_DOTENV_PATH'] = str(dotenv)
    receiver_log = (output / 'receiver.log').open('w', encoding='utf-8')
    receiver = subprocess.Popen(['node', str(args.backend_dir.resolve() / 'backend/bootstrap-distribution-stand.mjs')],
        env=env, stdout=receiver_log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    results = []
    rejected_uv = output / 'rejected-uv.exe'
    rejected_uv.write_bytes(b'UV execution is deliberately unavailable during qualification.\n')
    try:
        for _ in range(60):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.5):
                    break
            except OSError:
                if receiver.poll() is not None:
                    raise RuntimeError('qualification receiver exited')
                time.sleep(.5)
        else:
            raise RuntimeError('qualification receiver did not become ready')
        for name, flags in (('uv', []), ('pip', ['-WithoutUv']), ('uv-failed', ['-UvBinary', str(rejected_uv)])):
            started = time.monotonic()
            case = output / name;case.mkdir()
            destination = case / 'node'
            case_env = dict(env, ADAOS_BOOTSTRAP_STATE_DIR=str(case / 'outbox'), ADAOS_BASE_DIR=str(case / 'state'),
                ADAOS_SUPERVISOR_ENABLED='0', ADAOS_NLU_RASA='0', ADAOS_GIT_MODE='disabled', ADAOS_TOKEN='isolated-qualification-only',
                ADAOS_DISABLE_PREFERRED_PYTHON_REEXEC='1', ADAOS_DISABLE_ACTIVE_SLOT_PYTHON_REEXEC='1')
            with (case / 'install.log').open('w', encoding='utf-8') as log:
                command('powershell.exe', '-NoProfile', '-File', str(args.release_dir.resolve() / 'bootstrap-distribution.ps1'),
                    '-Dest', str(destination), '-DistributionRoot', base, '-ZoneId', 'ru', '-Channel', 'beta', '-RuntimeOnly', *flags,
                    env=case_env, stdout=log, stderr=subprocess.STDOUT)
                python = destination / '.venv/Scripts/python.exe'
                command(str(python), '-I', '-c', 'import sys,importlib.metadata as m; assert sys.version_info[:3]==(3,11,18); assert m.version("y-py")=="0.6.2+adaos.1"; print(sys.version)',
                    cwd=destination, env=case_env, stdout=log, stderr=subprocess.STDOUT)
                command(str(python), '-m', 'adaos', '--help', cwd=destination, env=case_env, stdout=log, stderr=subprocess.STDOUT)
            if name == 'uv':
                with (case / 'managed-bootstrap.log').open('w', encoding='utf-8') as log:
                    command('powershell.exe', '-NoProfile', '-File', str(destination / 'tools/bootstrap.ps1'), '-RuntimeOnly',
                        cwd=destination, env=dict(case_env, ADAOS_BOOTSTRAP_RUNTIME_READY='1'), stdout=log, stderr=subprocess.STDOUT)
                with socket.socket() as available:
                    available.bind(('127.0.0.1', 0));api_port = available.getsockname()[1]
                api_log = (case / 'api.log').open('w', encoding='utf-8')
                api = subprocess.Popen([str(python), '-m', 'uvicorn', 'adaos.apps.api.server:app', '--host', '127.0.0.1', '--port', str(api_port)],
                    cwd=destination, env=case_env, stdout=api_log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    import urllib.request
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    for _ in range(180):
                        try:
                            with opener.open(f'http://127.0.0.1:{api_port}/health/live', timeout=1) as response:
                                (case / 'health-live.json').write_bytes(response.read())
                            with opener.open(f'http://127.0.0.1:{api_port}/health/ready', timeout=2) as response:
                                (case / 'health-ready.json').write_bytes(response.read())
                            break
                        except OSError:
                            if api.poll() is not None:
                                raise RuntimeError('managed Windows API exited')
                            time.sleep(1)
                    else:
                        raise RuntimeError('managed Windows API did not become ready')
                finally:
                    api.terminate();api.wait(timeout=30);api_log.close()
            attempts = sorted((case / 'outbox').glob('attempt-*'))
            assert len(attempts) == 1
            events = [json.loads(path.read_text()) for path in attempts[0].glob('event-*.json')]
            assert any(event['type'] == 'installation_succeeded' for event in events)
            assert all(path.with_suffix('.ack').is_file() for path in attempts[0].glob('*.json'))
            if name == 'uv-failed':
                assert any(event['reason'] == 'uv_install_failed' for event in events)
            results.append({'scenario': name, 'ok': True, 'seconds': round(time.monotonic() - started, 2)})
            print(json.dumps(results[-1]), flush=True)
        (output / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    finally:
        receiver.terminate();receiver.wait(timeout=15);receiver_log.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--backend-dir", type=Path, default=ROOT / "src/adaos/integrations/adaos-backend")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".tmp"):
        parser.error("qualification output must stay under repository .tmp/")
    output.mkdir(parents=True, exist_ok=False)
    record = json.loads((args.release_dir / 'release.json').read_text())
    if record['os'] == 'windows':
        qualify_windows(args, output, record)
        return
    profile = 'linux-' + record['arch'] + '-gnu'
    docker_platform = PROFILES[profile]['docker']
    (output / "Dockerfile").write_text(DOCKERFILE)
    image = 'adaos-bootstrap-qualification:' + profile
    command("docker", "build", '--platform', docker_platform, "--quiet", "--tag", image, str(output))
    command("docker", "pull", "--quiet", "node:22-slim")
    suffix = uuid.uuid4().hex[:10]
    network, server = "adaos-bootstrap-" + suffix, "adaos-bootstrap-root-" + suffix
    filename = record.pop("filename")
    record.update(channel="stable", zone_id="ru", bundle_url="http://bootstrap-root:8799/bundles/" + filename)
    catalogue = output / "catalogue.json"
    catalogue.write_text(json.dumps({"schema": "adaos.bootstrap_catalogue.v1", "releases": [record]}))
    installer = output / "installer.sh"
    installer.write_bytes((ROOT / "tools/bootstrap-distribution.sh").read_bytes().replace(b"\r\n", b"\n"))
    results = []
    command("docker", "network", "create", "--internal", network)
    try:
        command("docker", "run", "--detach", "--name", server, "--network", network, "--network-alias", "bootstrap-root",
            "--mount", f"type=bind,source={args.backend_dir.resolve()},target=/backend,readonly",
            "--mount", f"type=bind,source={args.release_dir.resolve()},target=/bundles,readonly",
            "-e", "BOOTSTRAP_STAND_DIRECTORY=/state", "-e", "BOOTSTRAP_STAND_ADMIN_TOKEN=qualification-only",
            "-e", "BOOTSTRAP_STAND_HOST=0.0.0.0", "-e", "BOOTSTRAP_STAND_BUNDLES=/bundles",
            "node:22-slim", "node", "/backend/backend/bootstrap-distribution-stand.mjs")
        for _ in range(60):
            ready = subprocess.run(["docker", "exec", server, "test", "-d", "/state/attempts"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("qualification receiver did not become ready")
        command("docker", "cp", str(catalogue), server + ":/state/catalogue.json")
        for name, flags in (("uv", []), ("pip", ["--without-uv"]), ('uv-failed', ['--uv-binary', '/bin/false'])):
            started = time.monotonic()
            script = '''set -euo pipefail
test "$(python3 -c 'import sys;print(sys.version_info[:2])')" = '(3, 8)'
! command -v uv
! command -v curl
if wget --timeout=2 --tries=1 -qO /dev/null https://pypi.org/simple/; then exit 71; fi
bash /installer.sh --zone ru --distribution-root http://bootstrap-root:8799 --dest /home/tester/adaos --runtime-only "${@:2}"
/home/tester/adaos/.venv/bin/python -c 'import sys,y_py,fastapi,adaos,importlib.metadata as m; assert sys.version_info[:2]==(3,11); assert m.version("y-py")=="0.6.2+adaos.1"; print(sys.version)'
GIT_PYTHON_REFRESH=quiet /home/tester/adaos/.venv/bin/adaos --help
if [ "$1" = api ]; then
  cd /home/tester/adaos
  export ADAOS_BASE_DIR=/home/tester/.adaos ADAOS_SUPERVISOR_ENABLED=0 ADAOS_NLU_RASA=0 ADAOS_GIT_MODE=disabled
  .venv/bin/python -m uvicorn adaos.apps.api.server:app --host 127.0.0.1 --port 18777 >/home/tester/api.log 2>&1 &
  api_pid=$!
  trap 'kill "$api_pid" 2>/dev/null || true' EXIT
  live=0
  for attempt in $(seq 1 300); do
    if wget --timeout=1 --tries=1 -qO /home/tester/health-live.json http://127.0.0.1:18777/health/live && wget --timeout=2 --tries=1 -qO /home/tester/health-ready.json http://127.0.0.1:18777/health/ready; then live=1; break; fi
    if ! kill -0 "$api_pid"; then cat /home/tester/api.log; exit 1; fi
    sleep 1
  done
  if [ "$live" != 1 ]; then cat /home/tester/api.log; exit 1; fi
  cat /home/tester/health-live.json
  cat /home/tester/health-ready.json
fi
test "$(python3 -c 'import sys;print(sys.version_info[:2])')" = '(3, 8)'
'''
            with (output / (name + ".log")).open("w", encoding="utf-8") as log:
                command("docker", "run", "--rm", '--platform', docker_platform, "--network", network, "--mount", f"type=bind,source={installer},target=/installer.sh,readonly",
                    "-e", "ADAOS_BOOTSTRAP_ALLOW_HTTP_TEST=1", image, "bash", "-c", script, "qualification", 'api' if name == 'uv' else 'cli', *flags,
                    stdout=log, stderr=subprocess.STDOUT)
            results.append({"scenario": name, "ok": True, "seconds": round(time.monotonic() - started, 2)})
            print(json.dumps(results[-1]), flush=True)
        command("docker", "cp", server + ":/state", str(output / "root-state"))
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    finally:
        with (output / 'receiver.log').open('w') as log:
            subprocess.run(["docker", "logs", server], stdout=log, stderr=subprocess.STDOUT)
        subprocess.run(["docker", "rm", "--force", server], stdout=subprocess.DEVNULL)
        subprocess.run(["docker", "network", "rm", network], stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
