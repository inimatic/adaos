"""Publish process liveness before importing the Builder execution stack."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import sys


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser()
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--recover-validated-result', action='store_true')
    args = parser.parse_args(arguments)
    base = os.environ.get('ADAOS_BASE_DIR')
    if base:
        token = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in args.session_id).strip('._') or 'automation'
        root = Path(base).resolve() / 'state/builder/automation_workers' / token
        root.mkdir(parents=True, exist_ok=True)
        (root / 'ready.json').write_text(json.dumps({
            'schema': 'adaos.builder.automation_worker_ready.v1',
            'session_id': args.session_id, 'status': 'booting', 'pid': os.getpid(),
            'recorded_at': datetime.now(timezone.utc).isoformat(),
        }) + '\n', encoding='utf-8')
    previous = sys.argv
    try:
        sys.argv = ['adaos.services.builder.automation_worker', *arguments]
        runpy.run_module('adaos.services.builder.automation_worker', run_name='__main__')
    finally:
        sys.argv = previous


if __name__ == '__main__':
    main()
