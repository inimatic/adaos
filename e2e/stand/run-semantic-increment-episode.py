"""Thin local adapter for the ordinary Builder E2E runner; no model calls."""
import argparse
import os
from pathlib import Path
import json

from dotenv import load_dotenv

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host', required=True)
parser.add_argument('--run-id', required=True)
args = parser.parse_args()
load_dotenv()
os.environ['ADAOS_CONTROL_URL'] = 'http://127.0.0.1:8777'
os.environ['ADAOS_BUILDER_E2E_WEBSPACE_ID'] = args.host
from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import BuilderE2ERunner
init_ctx(Settings.from_sources())
runner = BuilderE2ERunner(Path('e2e/builder/development/semantic-increments/suite.yaml'),
    output_root=Path('e2e/artifacts/builder'), run_id=args.run_id,
    progress=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
result = runner.run()
print(json.dumps({'status': result.get('status'), 'run_id': args.run_id}, ensure_ascii=False))
raise SystemExit(0 if result.get('status') == 'passed' else 1)
