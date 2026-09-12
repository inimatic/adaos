"""Profile a read-only local Prototype query; never profile model execution."""

import argparse
import cProfile
import io
import json
import os
from pathlib import Path
import pstats
import shutil
import sqlite3
from contextlib import closing
import time

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.services.resources import ResourceWorkbenchService
from adaos.services.runtime_paths import current_state_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path, help='Retained evaluation artifact selecting a real local resource')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--isolated-snapshot', action='store_true', help='Profile a copied resource store without migrating or writing live state')
    args = parser.parse_args()
    load_dotenv()
    if os.getenv('ENV_TYPE') != 'dev':
        parser.error('Requires ENV_TYPE=dev')
    root = Path(__file__).resolve().parents[2] / 'e2e' / 'artifacts'
    if root not in args.output.resolve().parents:
        parser.error('Output must stay below e2e/artifacts')
    artifact = json.loads(args.artifact.read_text(encoding='utf-8'))
    resource = artifact['prototype_resources'][0]['resource_type']
    if not resource.startswith('prototype.'):
        parser.error('Requires a Prototype resource')
    args.output.mkdir(parents=True, exist_ok=False)
    init_ctx(Settings.from_sources())
    state_dir = None
    if args.isolated_snapshot:
        state_dir = args.output / 'state'
        source_root = current_state_dir() / 'resources'
        for relative in ('traces.json', 'resources.sqlite3', 'prototypes/registry.json', 'prototypes/resources.sqlite3'):
            source = source_root / relative
            if not source.is_file():
                continue
            target = state_dir / 'resources' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix == '.sqlite3':
                with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as source_db, closing(sqlite3.connect(target)) as target_db:
                    source_db.backup(target_db)
            else:
                shutil.copyfile(source, target)
    service = ResourceWorkbenchService(state_dir=state_dir)
    wall_samples = []
    for _ in range(5):
        started = time.perf_counter()
        service.query({'resource_type': resource, 'limit': 100})
        wall_samples.append(time.perf_counter() - started)
    samples = []
    for index in range(3):
        profile = cProfile.Profile()
        started = time.perf_counter()
        result = profile.runcall(service.query, {'resource_type': resource, 'limit': 100})
        elapsed = time.perf_counter() - started
        buffer = io.StringIO()
        pstats.Stats(profile, stream=buffer).sort_stats('cumulative').print_stats(30)
        (args.output / f'profile-{index + 1}.txt').write_text(buffer.getvalue(), encoding='utf-8')
        samples.append({'elapsed_s': elapsed, 'ok': result.get('ok'), 'items': len(result.get('items', []))})
    receipt = {'resource_type': resource, 'samples': samples, 'uninstrumented_seconds': wall_samples,
               'isolated_snapshot': args.isolated_snapshot,
               'scope': 'in-process query including definition resolution, authorization and trace; not HTTP or LLM'}
    (args.output / 'summary.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
