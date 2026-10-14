"""Matched native model authoring on two independent collection/editor consumers.

This is an engineering qualification, not a population benchmark. Full-document
regeneration and bounded ops share identical Core authority and outcome gates.
No manually fabricated model proposal can enter the positive authoring gate.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import time

from adaos.e2e.semantic_proposer_comparison import provision, read, write, retained_attempt, installed_runtime, check_series_budget
from adaos.e2e.component_contract_conformance import load_slice, matched_runtime_episodes


def cases(repo):
    records, _ = load_slice(repo)
    stock = read(repo / 'e2e/builder/development/contract-conformance/inventory-fixture.json')
    return [
        {'id': 'task_queue', 'source': records, 'view': 'records-list', 'field': 'team_id', 'before': 'team-1', 'after': 'team-2',
         'visible': 'Second task', 'hidden': 'First task', 'instruction': 'Show only team-2 records in records-list. Preserve every other setting, record, label, guard and command.',
         'provider_oracle': {}, 'browser_oracle': {}},
        {'id': 'warehouse_stock', 'source': stock, 'view': 'stock-list', 'field': 'warehouse_id', 'before': 'warehouse-1', 'after': 'warehouse-2',
         'visible': 'SKU-B', 'hidden': 'SKU-A', 'instruction': 'Show only warehouse-2 stock positions in stock-list. Preserve all records, quantities, relationships, reservation guards, labels and commands.',
         'provider_oracle': {'state_field': 'status', 'note_field': 'reserve_note', 'before_state': 'available',
             'after_state': 'reserved', 'command_id': 'stock-reserve', 'record_id': 'stock-1'},
         'browser_oracle': {'collectionId': 'stock-list', 'editorId': 'stock-editor', 'openCommand': 'stock-edit',
             'saveCommand': 'stock-save', 'mutationCommand': 'stock-reserve', 'recordId': 'stock-1', 'rowText': 'SKU-A', 'hiddenText': 'SKU-B',
             'nameField': 'sku', 'noteField': 'reserve_note', 'stateField': 'status', 'beforeState': 'available', 'afterState': 'reserved',
             'afterChoice': 'Reserved', 'basePayload': {'sku': 'SKU-A', 'status': 'available', 'warehouse_id': 'warehouse-1', 'quantity': 12, 'reserve_note': ''}}},
    ]


def browser_probe(repo, output, host, scenario, root, case, *, scoped):
    from adaos.apps.cli.active_control import resolve_control_token
    from adaos.services.agent_context import get_ctx
    from adaos.sdk.builder import preview
    opened = preview.select_target('scenario', scenario, source_webspace_id=host, stage='prototype', follow_active=True)
    write(output.parent / (output.name + '-materialization.json'), opened)
    if not opened.get('ok'):
        raise ValueError('Owned Preview failed to materialize')
    env = {**os.environ, 'ENV_TYPE': 'dev', 'ADAOS_E2E_HUB_URL': 'http://127.0.0.1:8777',
        'ADAOS_E2E_HUB_TOKEN': resolve_control_token(base_url='http://127.0.0.1:8777'),
        'ADAOS_E2E_SUBNET_ID': Path(get_ctx().paths.dev_dir()).name, 'ADAOS_E2E_WEBSPACE_ID': host + '-dev',
        'ADAOS_E2E_SCENARIO_ID': scenario, 'ADAOS_E2E_CLIENT_URL': 'http://127.0.0.1:8100/',
        'ADAOS_E2E_EXPECTED_WEBUI': str(root/'webui.json'), 'ADAOS_E2E_OUTPUT': str(output),
        'ADAOS_E2E_COLLECTION_ID': case['view'], 'ADAOS_E2E_VISIBLE_TEXT': case['visible'], 'ADAOS_E2E_HIDDEN_TEXT': case['hidden'],
        'ADAOS_E2E_CONTRACT_ORACLE': json.dumps(case['browser_oracle'])}
    script = 'component-contract-scope.mjs' if scoped else 'component-contract-conformance.mjs'
    process = subprocess.run(['node', str(repo/'e2e/stand/browser'/script)], cwd=repo, env=env,
        capture_output=True, text=True, encoding='utf-8', timeout=240)
    output.mkdir(exist_ok=True)
    (output/'browser.log').write_text(process.stdout + process.stderr, encoding='utf-8')
    result = read(output/'result.json') if (output/'result.json').is_file() else {'ok': False}
    return {'ok': process.returncode == 0 and result.get('ok') is True, 'result': result}


def execute(args):
    from dotenv import load_dotenv
    load_dotenv()
    logging.disable(logging.INFO)
    from adaos.apps.cli.app import Settings, init_ctx
    from adaos.apps.cli.active_control import resolve_control_token
    init_ctx(Settings.from_sources())
    from adaos.sdk.builder import prototype, semantic_ui, workflow
    from adaos.services.builder.semantic_graph import digest
    from adaos.services.builder.semantic_prototype import compile_semantic_prototype
    from adaos.services.builder.prototype_mcp import redact_credentials
    import requests
    if os.getenv('ENV_TYPE') != 'dev':
        raise ValueError('Qualification requires DEV')
    repo = Path.cwd().resolve()
    output = Path(args.output).resolve()
    if not output.is_relative_to(repo/'e2e/artifacts'):
        raise ValueError('Retain evidence under e2e/artifacts')
    output.mkdir(parents=True, exist_ok=False)
    http = requests.Session(); http.trust_env = False
    http.headers['X-AdaOS-Token'] = resolve_control_token(base_url='http://127.0.0.1:8777')
    runtime, _, handler = installed_runtime(http, repo)
    selected_cases = cases(repo)
    from adaos.services.contract_projections import project_context_units, publish_context_units, consume_context_units
    common_units = project_context_units(sdk_symbols=['adaos.sdk.builder.semantic_ui.admit', 'adaos.sdk.resources.query'])
    write(output/'shared-context-publication.json', publish_context_units(common_units))
    write(output/'e2e-consumer-units.json', consume_context_units(common_units, consumer='e2e',
        required_ids=[unit['id'] for unit in common_units['units']]))
    plan = {'schema': 'adaos.e2e.semantic_contract_qualification.v1', 'model': 'gpt-5', 'reasoning_effort': 'high',
        'max_output_tokens': 16384, 'max_usd': args.max_usd, 'repetitions': args.repetitions, 'runtime': runtime,
        'scope': 'Native prompt/model/proposal, exact preservation, renderer usability floors, writes, replay/undo and local prototype binding substitution; not unconstrained initial generation or production provider interoperability',
        'cases': [{key: value for key, value in case.items() if key != 'source'} for case in selected_cases],
        'fixture_digests': {case['id']: digest(case['source']) for case in selected_cases},
        'controls': ['bounded_ops', 'full_document'], 'model_tools': []}
    write(output/'plan.json', plan)
    if not args.run_paid:
        print(json.dumps({'requests': len(selected_cases)*2*args.repetitions, 'plan': str(output/'plan.json')})); return
    # Freeze source files, not only the compiler subset. Editing a running batch
    # cannot become evidence for a different implementation.
    paths = sorted((repo/'src/adaos/services/builder').glob('semantic_*.py'))
    paths += [handler, repo/'.adaos/workspace/skills/builder_skill/handlers/main.py',
        repo/'src/adaos/sdk/llm/llm_client.py', repo/'src/adaos/services/contract_context_admission.py']
    pins = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    write(output/'pins.json', pins)
    rows = []
    def save():
        write(output/'progress.json', {'rows': rows, 'known_cost_usd': sum(row.get('metrics', {}).get('cost_usd', 0) for row in rows),
            'unmetered_reserved_usd': sum(0.5 for row in rows if row.get('paid_job') and 'cost_usd' not in row.get('metrics', {}))})
    for repetition in range(args.repetitions):
        for case_index, case in enumerate(selected_cases):
            modes = ['bounded_ops', 'full_document'] if (case_index+repetition)%2 else ['full_document', 'bounded_ops']
            for mode in modes:
                spend = sum(row.get('metrics', {}).get('cost_usd', 0) for row in rows)
                reserve = sum(0.5 for row in rows if row.get('paid_job') and 'cost_usd' not in row.get('metrics', {}))
                check_series_budget(prior_paid_requests=args.prior_requests, reserved_attempts=len(rows),
                    prior_cost_usd=args.prior_usd, known_cost_usd=spend, unknown_reserved_usd=reserve, max_usd=args.max_usd)
                if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != pin for path, pin in pins.items()):
                    raise ValueError('Frozen source changed; start a separately accounted batch')
                identity = f'{case["id"]}-{repetition+1}-{mode}'
                trial = output/identity; trial.mkdir()
                scenario = 'test_semantic_' + hashlib.sha256((output.name+identity).encode()).hexdigest()[:18]
                root, baseline = provision(scenario, case['source'], trial)
                response = http.get('http://127.0.0.1:8777/api/builder/workbench/open', timeout=120, params={
                    'webspace_id': args.host, 'surface': 'authoring', 'selected_object_type': 'project',
                    'selected_object_id': scenario, 'base_url': 'http://127.0.0.1:8100'})
                response.raise_for_status(); write(trial/'authoring.json', response.json())
                message_id = output.name + ':' + identity
                row = {'id': identity, 'case': case['id'], 'mode': mode, 'scenario': scenario, 'ok': False}
                rows.append(row); save()
                result = {}; started = time.perf_counter()
                try:
                    result = prototype.submit_request(case['instruction'], webspace_id=args.host, source_kind='e2e', timeout_seconds=360,
                        metadata={'message_id': message_id, 'builder_selected_target': {'object_type': 'scenario', 'object_id': scenario},
                            'builder_semantic_edit_mode': mode, 'builder_semantic_write_ref': 'view:'+case['view'],
                            'builder_semantic_write_property': 'scope_filters.'+case['field']+'.value',
                            'builder_llm_model': 'gpt-5', 'builder_llm_reasoning_effort': 'high', 'builder_semantic_max_tokens': 16384})
                    write(trial/'native-result.json', redact_credentials(result))
                    row['status'] = result.get('status')
                    expected = copy.deepcopy(baseline)
                    next(view for view in expected['views'] if view['id'] == case['view'])['scope_filters'][0]['value'] = case['after']
                    row['exact_preservation'] = read(root/'semantic.webui.json') == expected
                    row['native_model_authoring'] = result.get('status') == 'semantic_proposal_applied' and result.get('model_calls') == 1
                    if not row['exact_preservation'] or not row['native_model_authoring']:
                        raise ValueError('Native model authoring failed exact independent outcome')
                    observed_message = (result.get('message_meta') or {}).get('source_message_id') or message_id
                    retained = retained_attempt(root, observed_message, admission_id=result.get('semantic_admission'))
                    prepared = retained['result']['request']
                    replay = semantic_ui.apply(prepared, execution_metrics=result['execution_metrics'])
                    row['replay_ok'] = replay.get('replayed') is True
                    row['scope_browser'] = browser_probe(repo, trial/'scope-browser', args.host, scenario, root, case, scoped=True)
                    undo = prototype.submit_request('Undo the last semantic change', webspace_id=args.host, source_kind='e2e',
                        metadata={'message_id': message_id+':undo', 'builder_selected_target': {'object_type': 'scenario', 'object_id': scenario}}, timeout_seconds=120)
                    write(trial/'native-undo.json', undo)
                    row['undo_ok'] = read(root/'semantic.webui.json') == baseline and undo.get('model_calls') == 0
                    row['behavior_browser'] = browser_probe(repo, trial/'behavior-browser', args.host, scenario, root, case, scoped=False)
                    compiled = compile_semantic_prototype(baseline, project_ref='project:'+scenario)
                    row['matched_episodes'] = matched_runtime_episodes(compiled, trial/'matched-state', episode_spec=case['provider_oracle'])
                    row['ok'] = all((row['replay_ok'], row['undo_ok'], row['scope_browser']['ok'], row['behavior_browser']['ok'],
                        all(episode['ok'] for episode in row['matched_episodes'])))
                except Exception as error:
                    row['error'] = type(error).__name__ + ': ' + str(error)
                finally:
                    observed_message = (result.get('message_meta') or {}).get('source_message_id') or message_id
                    retained = retained_attempt(root, observed_message, admission_id=result.get('semantic_admission'))
                    row['paid_job'] = result.get('root_job_id') or retained.get('job_id')
                    row['metrics'] = result.get('execution_metrics') or retained.get('execution_metrics', {})
                    row['requested_model'] = 'gpt-5'
                    row['provider_model'] = (retained.get('job', {}).get('response') or {}).get('model')
                    row['episode_wall_ms'] = round((time.perf_counter()-started)*1000)
                    write(trial/'retained-model.json', redact_credentials(retained)); save()
                    print(json.dumps({key: row[key] for key in ('id','ok','status','error','metrics') if key in row}), flush=True)
                if not row['ok']:
                    break  # Keep the failed original; repair software before another billed batch.
            if rows and not rows[-1]['ok']:
                break
        if rows and not rows[-1]['ok']:
            break
    result = {'plan': plan, 'rows': rows, 'complete': len(rows) == len(selected_cases)*2*args.repetitions,
        'ok': len(rows) == len(selected_cases)*2*args.repetitions and all(row['ok'] for row in rows),
        'known_cost_usd': sum(row.get('metrics', {}).get('cost_usd', 0) for row in rows),
        'paid_jobs': len({row['paid_job'] for row in rows if row.get('paid_job')}),
        'unmetered_jobs': sum(bool(row.get('paid_job')) and 'cost_usd' not in row.get('metrics', {}) for row in rows)}
    write(output/'summary.json', result)
    print(json.dumps({key: result[key] for key in ('ok','complete','known_cost_usd','paid_jobs','unmetered_jobs')}))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--host', default='test_ctxqual_be8422f1050250')
    parser.add_argument('--run-paid', action='store_true')
    parser.add_argument('--repetitions', type=int, choices=[1,2], default=1)
    parser.add_argument('--max-usd', type=float, default=15)
    parser.add_argument('--prior-usd', type=float, default=0)
    parser.add_argument('--prior-requests', type=int, default=0)
    args = parser.parse_args()
    if not 0 < args.max_usd <= 15 or not 0 <= args.prior_usd < args.max_usd or not 0 <= args.prior_requests <= 32:
        parser.error('Whole-series budget exceeds approved bounds')
    execute(args)


if __name__ == '__main__':
    main()
