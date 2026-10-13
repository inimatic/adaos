"""Thin local adapter for the ordinary Builder E2E runner; no model calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host', required=True)
parser.add_argument('--run-id', required=True)
parser.add_argument('--conversation', action='store_true', help='Use real installed Builder chat instead of direct graph SDK edits')
parser.add_argument('--column-order', action='store_true', help='Qualify the semantic inspector and table field-order operation through installed chat')
args = parser.parse_args()
load_dotenv()
os.environ['ADAOS_CONTROL_URL'] = 'http://127.0.0.1:8777'
os.environ['ADAOS_BUILDER_E2E_WEBSPACE_ID'] = args.host
from adaos.apps.cli.app import Settings, init_ctx  # noqa: E402
from adaos.e2e.builder import BuilderE2ERunner  # noqa: E402
from adaos.services.mutation_lock import mutation_lock  # noqa: E402
init_ctx(Settings.from_sources())
suite = 'column-order-suite.yaml' if args.column_order else 'conversation-suite.yaml' if args.conversation else 'suite.yaml'
runner = BuilderE2ERunner(Path('e2e/builder/development/semantic-increments') / suite,
    output_root=Path('e2e/artifacts/builder'), run_id=args.run_id,
    progress=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
# The stand reuses one fixed Preview per host. Keep local experiment runners
# from changing each other's selection; this is not platform preview leasing.
lock_path = Path('.tmp/e2e-builder-host-locks') / (hashlib.sha256(args.host.encode('utf-8')).hexdigest() + '.lock')
with mutation_lock(lock_path, timeout_s=1.0):
    result = runner.run()
print(json.dumps({'status': result.get('status'), 'run_id': args.run_id}, ensure_ascii=False))
raise SystemExit(0 if result.get('status') == 'passed' else 1)
