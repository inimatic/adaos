"""Qualify only retained planned repairs; no model calls or changed probes."""
import argparse
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from unittest.mock import patch
from dotenv import load_dotenv

from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import acceptance, browser_review, native_builder, preserved_preview, read, source_fingerprints

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run', type=Path, required=True)
parser.add_argument('--host', required=True)
parser.add_argument('--posthoc-label', default='')
args = parser.parse_args()
load_dotenv()
logging.disable(logging.INFO)
from adaos.apps.cli.app import Settings, init_ctx
from adaos.services.agent_context import get_ctx
from adaos.sdk.llm import llm_client
init_ctx(Settings.from_sources())
run = args.run.resolve()
if not run.is_relative_to(Path.cwd() / 'e2e/artifacts/builder'):
    parser.error('Owned E2E cohort required')
plan = read(run / 'plan.json')
fingerprints = source_fingerprints(Path.cwd())
changed = {key for key in set(fingerprints) | set(plan['runtime_source_fingerprints'])
           if fingerprints.get(key) != plan['runtime_source_fingerprints'].get(key)}
if changed and (not args.posthoc_label or changed - {'e2e/stand/browser/prototype-navigation.mjs'}):
    raise ValueError('Frozen source drift outside the explicit public-selection probe correction')
if args.posthoc_label and not re.fullmatch(r'[a-z0-9-]+', args.posthoc_label):
    parser.error('Safe explicit qualification label required')
handler = Path(get_ctx().paths.dev_skills_dir()) / 'builder_skill/handlers/main.py'
assert 'sha256:' + hashlib.sha256(handler.read_bytes()).hexdigest() == plan['native_builder_sha256']
builder = native_builder(handler)
os.environ['ADAOS_CONTROL_URL'] = 'http://127.0.0.1:8777'
os.environ['ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED'] = '0'
os.environ['ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY'] = 'full'
output = run / ('planned-repair-qualification-' + args.posthoc_label if args.posthoc_label else 'planned-repair-qualification')
output.mkdir(exist_ok=False)
_write_json(output / 'source-fingerprints.json', fingerprints)
_write_json(output / 'configuration.json', {'posthoc_label': args.posthoc_label,
    'changed_sources': sorted(changed), 'model_outputs_unchanged': True, 'new_llm_calls': 0})
brief = read(run / 'source-brief.json')
rows = []
with preserved_preview(args.host, output), patch.object(llm_client, 'submit_response_job', side_effect=AssertionError('No model allowed during qualification')):
    for path in sorted(run.glob('[0-9][0-9]-*/planned-repair-result.json')):
        sample = path.parent
        result = read(path)
        target = output / sample.name
        target.mkdir()
        structural = acceptance(result, brief, task=plan['task'])
        checked = {'ok': False, 'skipped': 'planned_repair_did_not_compile'}
        if result.get('ok'):
            try:
                checked = browser_review(builder, read(sample / 'session.json'), result, target,
                    read(sample / 'creation.json'), Path(get_ctx().paths.dev_dir()).name, run.name, host=args.host,
                    selected_probes=['prototype-review', 'prototype-capabilities', 'prototype-interactions'] if plan['task'] == 'support-desk-ru' else None)
            except Exception as error:
                checked = {'ok': False, 'error': type(error).__name__, 'detail': str(error)}
        row = {'sample': sample.name, 'structural': structural, **checked}
        _write_json(target / 'result.json', row)
        rows.append(row)
        _write_json(output / 'results.json', {'complete': False, 'samples': rows, 'new_llm_calls': 0})
        print(json.dumps({'stage': 'planned_repair_qualified', **row}, ensure_ascii=False), flush=True)
_write_json(output / 'results.json', {'complete': True, 'samples': rows, 'new_llm_calls': 0,
    'preview_restoration': read(output / 'preview-restored.json')})
