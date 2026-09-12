"""Profile a read-only local Prototype query; never profile model execution."""

import argparse
import cProfile
import io
import json
import os
from pathlib import Path
import pstats
import time

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.services.resources import ResourceWorkbenchService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path, help='Retained evaluation artifact selecting a real local resource')
    parser.add_argument('--output', type=Path, required=True)
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
    service = ResourceWorkbenchService()
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
    receipt = {'resource_type': resource, 'samples': samples,
               'scope': 'in-process query including definition resolution, authorization and trace; not HTTP or LLM'}
    (args.output / 'summary.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
