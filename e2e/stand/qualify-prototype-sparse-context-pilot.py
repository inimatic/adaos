"""Replay retained compact candidates on isolated, owned DEV fixtures; no LLM calls."""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv
from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import browser_review, native_builder, read, source_fingerprints

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run', type=Path, required=True)
parser.add_argument('--label', default='release')
args = parser.parse_args()
run = args.run.resolve()
if not run.is_relative_to(Path.cwd() / 'e2e/artifacts/builder'):
    parser.error('Owned E2E artifact directory required')
load_dotenv()
logging.disable(logging.INFO)
if os.getenv('ENV_TYPE') != 'dev':
    parser.error('DEV only')
from adaos.apps.cli.active_control import resolve_control_token
from adaos.apps.cli.app import Settings, init_ctx
from adaos.services.agent_context import get_ctx
from adaos.services.scenario.webspace_runtime import WebspaceService
from adaos.sdk.developer import compositions, projects
from adaos.sdk.builder import preview, workflow
from adaos.sdk.llm import llm_client

init_ctx(Settings.from_sources())
os.environ['ADAOS_CONTROL_URL'] = 'http://127.0.0.1:8777'
ctx = get_ctx()
builder = native_builder(Path(ctx.paths.dev_skills_dir()) / 'builder_skill/handlers/main.py')
task = next(task for task in read(run / 'preregistration.json')['tasks'] if task['id'] == 'book-catalog-ru')
brief = read(run / task['id'] / 'brief.json')
baseline = read(Path('src/adaos/scenario_templates/scenario_default/webui.json'))
output = run / ('browser-posthoc-' + args.label)
output.mkdir(exist_ok=True)
configuration = {'sources': source_fingerprints(Path.cwd()), 'label': args.label,
    'candidates': ['gpt-6-astra-full-mcp-v2', 'gpt-5-task-aware-v3',
                   'gpt-6-astra-task-aware-v4', 'gpt-5-task-aware-v4'],
    'probe_sha256': hashlib.sha256(Path('e2e/stand/browser/prototype-sparse-catalog-query.mjs').read_bytes()).hexdigest(),
    'new_llm_calls': 0, 'fresh_owned_fixtures': True,
    'scope': 'All retained compilable compact candidates. Not a balanced new generation ablation; primary failures remain failures.'}
config_path = output / 'configuration.json'
if config_path.exists() and read(config_path) != configuration:
    raise ValueError('Qualification source drift: use a new label, never overwrite retained results')
_write_json(config_path, configuration)
host = 'test_sparsequal_' + hashlib.sha256(str(output).encode()).hexdigest()[:14]
service = WebspaceService()
if host in {str(info.id) for info in service.list(mode='mixed')}:
    if not (output / 'owned-host.json').exists():
        raise ValueError('Unowned existing qualification host')
else:
    info = asyncio.run(service.create(host, 'TEST compact context qualification', scenario_id='web_desktop'))
    assert info.id == host
    _write_json(output / 'owned-host.json', {'host': host, 'test': True, 'existing_user_preview_untouched': True})
preview.ensure(host, runtime_scenario_id='web_desktop', wait_for_rebuild=True)
rows = []
with patch.object(llm_client, 'submit_response_job', side_effect=AssertionError('Qualification cannot submit LLM requests')):
    for name in configuration['candidates']:
        assert source_fingerprints(Path.cwd()) == configuration['sources'], 'Qualification source drift'
        retained = run / task['id'] / name
        target = output / name
        target.mkdir(exist_ok=True)
        if (target / 'result.json').exists():
            rows.append(read(target / 'result.json'))
            continue
        candidate = read(retained / 'candidate.json')
        assert candidate.get('ok') is True
        scenario = 'test_sparsefinal_' + hashlib.sha256(str(target).encode()).hexdigest()[:18]
        root = projects.resolve_root('scenario', scenario, required=False)
        if (target / 'creation.json').exists():
            created = read(target / 'creation.json')
        else:
            if root.exists() or compositions.resolve_root(scenario, required=False).exists():
                raise ValueError('Unowned existing scenario namespace')
            created = compositions.create_with_primary_component(scenario, kind='scenario', template='scenario_default',
                title='Каталог книг [TEST]', description='Retained compact context candidate qualification', tags=['test', 'context-composition'])
            _write_json(target / 'creation.json', created)
        if (target / 'session.json').exists():
            session = read(target / 'session.json')
        else:
            builder._write_webui_payload(str(root), baseline)
            transition = workflow.transition('scenario', scenario, 'plan_change_set', actor='agent:builder-e2e', metadata={
                'change_set_id': scenario, 'request': task['instruction'], 'issues': [{'issue_id': 'compact-qualification',
                'title': 'Retained held-out candidate', 'lane': 'prototype', 'acceptance_criteria': ['Public search, genre, reset and four durable records.']}]})['workflow']
            session = {'id': 'scenario.' + scenario, 'scenario_id': scenario, 'project_ref': 'project:' + scenario,
                'title': 'Каталог книг [TEST]', 'source_idea': task['instruction'], 'artifact_root': str(root),
                'initial_webui': baseline, 'webui_payload': baseline, 'ui_revision': '001', 'patches': [],
                'active_change_id': transition['change']['change_id'], 'builder_llm_model': read(retained / 'result.json')['model']}
            _write_json(target / 'session.json', session)
        fresh = builder._parse_llm_webui_transform_output(output_text=read(retained / 'terminal.json')['output_text'],
            previous_preview=baseline.get('preview_state') or {}, before_webui=baseline, prototype_brief=brief,
            project_ref=session['project_ref'], output_mode='semantic_v2')
        if fresh['semantic_document'] != candidate['semantic_document']:
            raise ValueError('Fresh namespace changed retained model semantics')
        checked = browser_review(builder, session, fresh, target, created, Path(ctx.paths.dev_dir()).name, run.name,
            host=host, selected_probes=['prototype-review', 'prototype-capabilities'], observe_delivery=False)
        probe_output = target / 'prototype-sparse-catalog-query'
        env = {**os.environ, 'ADAOS_E2E_CHECKPOINT': str((target / 'checkpoint.json').resolve()),
            'ADAOS_E2E_HUB_URL': 'http://127.0.0.1:8777', 'ADAOS_E2E_HUB_TOKEN': resolve_control_token(base_url='http://127.0.0.1:8777'),
            'ADAOS_E2E_SUBNET_ID': Path(ctx.paths.dev_dir()).name, 'ADAOS_E2E_CLIENT_URL': 'http://127.0.0.1:8100/',
            'ADAOS_E2E_OUTPUT': str(probe_output.resolve())}
        probe = subprocess.run(['node', 'e2e/stand/browser/prototype-sparse-catalog-query.mjs'], env=env,
            capture_output=True, text=True, encoding='utf-8', timeout=300)
        probe_output.mkdir(exist_ok=True)
        (probe_output / 'process.log').write_text(probe.stdout + probe.stderr, encoding='utf-8')
        checked['probes']['prototype-sparse-catalog-query'] = probe.returncode
        row = {'candidate': name, **checked, 'ok': checked['ok'] and probe.returncode == 0, 'new_llm_calls': 0}
        _write_json(target / 'result.json', row)
        rows.append(row)
        _write_json(output / 'results.json', {'complete': False, 'samples': rows, 'new_llm_calls': 0})
        print(json.dumps({k: row[k] for k in ('candidate', 'ok', 'probes')}, ensure_ascii=False), flush=True)
_write_json(output / 'results.json', {'complete': len(rows) == 4, 'samples': rows, 'new_llm_calls': 0,
    'protected_user_preview_never_selected': True, 'acceptance': 'not_approved'})
