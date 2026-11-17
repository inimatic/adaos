"""Run isolated RTC/YWS fault qualification with managed scratch and evidence."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

from adaos.services.storage_hygiene import managed_storage_run


def main():
    root = Path(__file__).resolve().parents[2]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', default=f'.tmp/channel-reliability-{stamp}')
    parser.add_argument('--output', default=f'e2e/artifacts/channel-reliability-{stamp}')
    parser.add_argument('--initial-delay-ms', type=int, default=6500)
    args = parser.parse_args()
    task, output = ((root / value).resolve() for value in (args.task, args.output))
    if not task.is_relative_to((root / '.tmp').resolve()) or not output.is_relative_to((root / 'e2e/artifacts').resolve()):
        parser.error('scratch must be under .tmp/ and output under e2e/artifacts/')
    env = dict(os.environ, ADAOS_CHANNEL_TASK=str(task), ADAOS_E2E_OUTPUT=str(output),
               ADAOS_E2E_PYTHON=sys.executable, ADAOS_CHANNEL_INITIAL_DELAY_MS=str(args.initial_delay_ms))
    try:
        with managed_storage_run(task, owner='e2e.channel-reliability', retention_class='e2e_run',
                                 success_retain_seconds=0, failure_retain_seconds=7*86400), \
             managed_storage_run(output, owner='e2e.channel-reliability', retention_class='e2e_run',
                                 success_retain_seconds=14*86400, failure_retain_seconds=14*86400):
            command = ['node', str(root/'e2e/stand/browser/channel-reliability-regression.mjs')]
            result = subprocess.run(command, cwd=root, env=env, capture_output=True, timeout=180)
            (output/'runner.stdout.log').write_bytes(result.stdout)
            (output/'runner.stderr.log').write_bytes(result.stderr)
            print(result.stdout.decode('utf-8', errors='replace').strip())
            if result.returncode:
                raise subprocess.CalledProcessError(result.returncode, command)
    except subprocess.CalledProcessError as exc:
        print(f'Qualification failed; retained evidence: {output}', file=sys.stderr)
        return exc.returncode
    print(f'Evidence: {output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
