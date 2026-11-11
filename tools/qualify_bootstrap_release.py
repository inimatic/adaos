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

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = """FROM ubuntu:20.04
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends wget ca-certificates python3-minimal && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --shell /bin/bash tester
USER tester
WORKDIR /home/tester
"""


def command(*args: str, **options):
    return subprocess.run(args, check=True, **options)


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
    (output / "Dockerfile").write_text(DOCKERFILE)
    command("docker", "build", "--quiet", "--tag", "adaos-bootstrap-qualification:local", str(output))
    command("docker", "pull", "--quiet", "node:22-slim")
    suffix = uuid.uuid4().hex[:10]
    network, server = "adaos-bootstrap-" + suffix, "adaos-bootstrap-root-" + suffix
    record = json.loads((args.release_dir / "release.json").read_text())
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
  for attempt in $(seq 1 90); do
    if wget --timeout=1 --tries=1 -qO /home/tester/health-live.json http://127.0.0.1:18777/health/live; then live=1; break; fi
    if ! kill -0 "$api_pid"; then cat /home/tester/api.log; exit 1; fi
    sleep 1
  done
  if [ "$live" != 1 ]; then cat /home/tester/api.log; exit 1; fi
  cat /home/tester/health-live.json
fi
test "$(python3 -c 'import sys;print(sys.version_info[:2])')" = '(3, 8)'
'''
            with (output / (name + ".log")).open("w", encoding="utf-8") as log:
                command("docker", "run", "--rm", "--network", network, "--mount", f"type=bind,source={installer},target=/installer.sh,readonly",
                    "-e", "ADAOS_BOOTSTRAP_ALLOW_HTTP_TEST=1", "adaos-bootstrap-qualification:local", "bash", "-c", script, "qualification", 'api' if name == 'uv' else 'cli', *flags,
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
