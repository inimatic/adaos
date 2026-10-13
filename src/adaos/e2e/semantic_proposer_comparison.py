"""Frozen, budgeted full-document vs bounded-op edit chains through Builder SDK.

Controlled development qualification, not a held-out benchmark or initial
generation comparison. Both routes use the same Core admission/apply checks.
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


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    from adaos.services.artifact_pipeline.storage import atomic_write_json
    atomic_write_json(Path(path), value)


def fixtures(repo):
    small = read(repo / 'e2e/builder/development/semantic-increments/fixture.json')
    large = copy.deepcopy(small)
    records = large['resources'][0]['records']
    for index in range(3, 23):
        records.append({**records[0], 'id': f'record-{index}', 'name': f'Synthetic case {index}'})
    return [('two_records', small), ('twenty_two_records', large)]


def changes():
    return [
        {'id': 'heading', 'instruction': 'In English, call the Records collection "Work queue". Keep every other label and behavior unchanged.',
         'ref': 'view:records-list', 'locale': 'en', 'text': 'Work queue'},
        {'id': 'field', 'instruction': 'The English label for the name field of Records should be "Subject", wherever that field is shown. Do not change the stored values.',
         'ref': 'field:records.name', 'locale': 'en', 'text': 'Subject'},
        {'id': 'columns', 'instruction': 'In the Records collection table show State before Subject, then Team. Keep the fields and all other views unchanged.',
         'ref': 'view:records-list', 'field_refs': ['state', 'name', 'team_id']},
    ]


def expected_after(document, task):
    result = copy.deepcopy(document)
    ref = task['ref']
    if ref.startswith('view:'):
        view = next(v for v in result['views'] if 'view:' + v['id'] == ref)
        if 'field_refs' in task:
            view['field_refs'] = task['field_refs']
        else:
            view['title'][task['locale']] = task['text']
    else:
        resource_id, field_id = ref.removeprefix('field:').split('.')
        resource = next(r for r in result['resources'] if r['id'] == resource_id)
        next(f for f in resource['fields'] if f['id'] == field_id)['label'][task['locale']] = task['text']
    return result


def browser_observation(repo, output, host, scenario_id, root, *, columns):
    from adaos.apps.cli.active_control import resolve_control_token
    from adaos.sdk.builder import preview
    from adaos.services.agent_context import get_ctx
    output.mkdir(exist_ok=True)
    started = time.perf_counter()
    materialized = preview.select_target('scenario', scenario_id, source_webspace_id=host,
                                        stage='prototype', follow_active=True)
    write(output / 'materialization.json', materialized)
    environment = {**os.environ, 'ENV_TYPE': 'dev', 'ADAOS_E2E_HUB_URL': 'http://127.0.0.1:8777',
        'ADAOS_E2E_HUB_TOKEN': resolve_control_token(base_url='http://127.0.0.1:8777'),
        'ADAOS_E2E_SUBNET_ID': Path(get_ctx().paths.dev_dir()).name,
        'ADAOS_E2E_WEBSPACE_ID': host + '-dev', 'ADAOS_E2E_SCENARIO_ID': scenario_id,
        'ADAOS_E2E_CLIENT_URL': 'http://127.0.0.1:8100/', 'ADAOS_E2E_LOCALE': 'en',
        'ADAOS_E2E_LABEL_VIEW': 'Work queue', 'ADAOS_E2E_LABEL_FIELD': 'Subject',
        'ADAOS_E2E_EXPECTED_WEBUI': str(root / 'webui.json'), 'ADAOS_E2E_OUTPUT': str(output),
        'ADAOS_E2E_COLUMN_ORDER': json.dumps(columns)}
    completed = subprocess.run(['node', str(repo / 'e2e/stand/browser/semantic-increments.mjs')],
        cwd=repo, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=240)
    (output / 'browser.log').write_text(completed.stdout + completed.stderr, encoding='utf-8')
    result = read(output / 'result.json') if (output / 'result.json').is_file() else {'ok': False}
    return {'ok': completed.returncode == 0 and result.get('ok') is True,
            'wall_ms': round((time.perf_counter() - started) * 1000), 'result': result}


def provision(scenario_id, source, output):
    from adaos.sdk.builder import prototype, workflow
    from adaos.sdk.developer import compositions, projects, prototypes
    from adaos.services.artifact_pipeline.storage import atomic_write_bytes
    from adaos.services.builder.semantic_graph import digest
    root = projects.resolve_root('scenario', scenario_id, required=False)
    if root.exists():
        raise ValueError('Existing fixture needs retained-run recovery; never reseed it')
    creation = compositions.create_with_primary_component(scenario_id, kind='scenario', template='scenario_default',
        title='[TEST] Paired semantic proposals', description='Owned synthetic development fixture', tags=['test', 'semantic-proposals'])
    write(output / 'creation.json', creation)
    root = projects.resolve_root('scenario', scenario_id)
    compiled = prototype.compile_semantic(source, project_ref='project:' + scenario_id)
    document, ui = compiled['semantic_document'], compiled['webui']
    ui['ui']['application']['resources'] = {
        f"{document['document_id']}.i18n.{locale}": {'kind': 'data', 'role': 'i18n', 'locale': locale,
            'path': f'assets/i18n/{locale}.json', 'mime': 'application/json', 'delivery': 'core'}
        for locale in compiled['locale_dictionaries']}
    for name, value in {'semantic.webui.json': document, 'webui.json': ui,
                       **{f'assets/i18n/{locale}.json': values for locale, values in compiled['locale_dictionaries'].items()}}.items():
        projects.write_file('scenario', scenario_id, name, json.dumps(value, ensure_ascii=False, indent=2))
    write(root / 'ui_revisions/001.json', {'schema': 'adaos.builder.ui_revision.v1', 'revision': '001',
        'scenario_id': scenario_id, 'after_webui': ui, 'source_kind': 'diagnostic_fixture', 'fixture_digest': digest(document)})
    atomic_write_bytes(root / 'ui_revisions/current.txt', b'001\n')
    change_id = 'semantic-' + scenario_id
    workflow.transition('scenario', scenario_id, 'plan_change_set', actor='e2e:semantic-proposals', metadata={
        'change_set_id': change_id, 'request': 'Preserve records and behavior while changing labels and table column order.',
        'issues': [{'issue_id': 'bounded-edits', 'title': 'Bounded semantic edits', 'lane': 'prototype',
                    'acceptance_criteria': ['Exact requested delta, protected state and usable wide/compact Preview.']}]})
    specs = prototypes.derive_resource_specs(ui, compiled['prototype_resources'])
    write(output / 'resource-initialization.json', prototypes.materialize_resources(project_ref='project:' + scenario_id,
        change_id=change_id, revision='001', webui=ui, resources=specs))
    write(output / 'baseline.json', document)
    return root, document


def retained_attempt(root, source_message_id, *, admission_id=None):
    from adaos.services.builder.semantic_admission import SemanticAdmissionService
    from adaos.services.builder.semantic_graph import digest
    service = SemanticAdmissionService.from_context()
    admission_id = admission_id or 'admission-' + digest(['scenario:' + root.name, source_message_id])[7:55]
    _root, path = service._location('scenario:' + root.name, admission_id)
    return read(path.with_suffix('.model.json')) if path.with_suffix('.model.json').is_file() else {}


def installed_runtime(client, repo):
    """Pin the actual installed owner, never a dated activation artifact."""
    response = client.get('http://127.0.0.1:8777/api/skills/runtime/status/builder_skill', timeout=30)
    response.raise_for_status()
    packet = response.json()
    state = packet.get('state', packet)
    if not state.get('ready') or not state.get('active_slot'):
        raise ValueError('The installed Workspace Builder must be ready before qualification')
    manifest = Path(state['resolved_manifest']).resolve()
    manifest.relative_to(repo)
    handler = manifest.parent / 'src/skills/builder_skill/handlers/main.py'
    if handler.read_bytes() != (repo / '.adaos/workspace/skills/builder_skill/handlers/main.py').read_bytes():
        raise ValueError('Installed Builder differs from Workspace; activate it before freezing the batch')
    return state, manifest, handler


def carry_revision_resources(scenario_id, root, receipt):
    from adaos.sdk.builder import workflow
    from adaos.sdk.developer import prototypes
    state = workflow.get_state('scenario', scenario_id)
    return prototypes.carry_forward_resources(project_ref='project:' + scenario_id,
        change_id=state['change']['change_id'], revision=receipt['revision'], webui=read(root / 'webui.json'))


def check_series_budget(*, prior_paid_requests, reserved_attempts, prior_cost_usd,
                        known_cost_usd, unknown_reserved_usd, max_usd):
    if (prior_paid_requests + reserved_attempts >= 32
            or prior_cost_usd + known_cost_usd + unknown_reserved_usd + 0.5 > max_usd):
        raise ValueError('Whole-series conservative budget exhausted')


def execute(args):
    from dotenv import load_dotenv
    load_dotenv()
    logging.disable(logging.INFO)
    from adaos.apps.cli.app import Settings, init_ctx
    init_ctx(Settings.from_sources())
    from adaos.sdk.builder import prototype, semantic_ui, workflow
    from adaos.services.builder.semantic_graph import digest
    from adaos.services.builder.prototype_mcp import redact_credentials
    import requests

    if os.getenv('ENV_TYPE') != 'dev':
        raise ValueError('This rail requires DEV')
    repo = Path.cwd().resolve()
    output = Path(args.output).resolve()
    if not output.is_relative_to(repo / 'e2e/artifacts'):
        raise ValueError('Evidence must be under e2e/artifacts')
    from adaos.apps.cli.active_control import resolve_control_token
    client = requests.Session()
    client.trust_env = False
    client.headers['X-AdaOS-Token'] = resolve_control_token(base_url='http://127.0.0.1:8777')
    source_paths = ['src/adaos/services/builder/' + name for name in
        ('semantic_admission.py', 'semantic_proposer.py', 'semantic_graph.py', 'workflow.py')]
    source_paths += ['.adaos/workspace/skills/builder_skill/handlers/main.py',
                     'src/adaos/e2e/semantic_proposer_comparison.py',
                     'src/adaos/services/builder/prototype_requests.py',
                     'src/adaos/services/builder/semantic_request_scope.py',
                     'src/adaos/sdk/builder/prototype.py', 'src/adaos/adapters/builder/legacy_dev_skill.py']
    activation, manifest_path, runtime_handler = installed_runtime(client, repo)
    source_paths += [str(manifest_path.relative_to(repo)), str(runtime_handler.relative_to(repo))]
    pins = {path: hashlib.sha256((repo / path).read_bytes()).hexdigest() for path in source_paths}
    selected_fixtures = fixtures(repo)[:1] if args.pilot else fixtures(repo)
    plan = {'schema': 'adaos.e2e.semantic_proposer_comparison.v1', 'model': 'gpt-5', 'reasoning_effort': 'high',
        'max_output_tokens': 32768, 'max_paid_requests': 32, 'max_usd': args.max_usd,
        'request_reserve_usd': 0.5, 'prior_reserved_cost_usd': args.prior_cost_usd,
        'prior_paid_requests': args.prior_paid_requests,
        'installed_version': activation['version'], 'installed_slot': activation['active_slot'],
        'repetitions': args.repetitions, 'source_pins': pins,
        'fixtures': [{'id': name, 'source_digest': digest(source)} for name, source in selected_fixtures],
        'changes': changes(), 'controls': ['full_document', 'bounded_ops'], 'tools': [],
        'scope': 'Controlled full-document edits versus bounded proposals; not initial generation, MCP selection or a holdout.',
        'repair_policy': 'No automatic paid repairs; frozen failures retained. Exact Root jobs are resumable.',
        'quality': ['Exact complete semantic document', 'Protected records/relationships/commands',
                    'Wide/compact live labels, column order, selection, editor, guards, CRUD and clipping', 'Replay and mixed undo'],
        'order': 'Counterbalanced by fixture and repetition; three sequential edits per route.'}
    planned = len(selected_fixtures) * args.repetitions * 2 * 3
    if args.prior_paid_requests + planned > plan['max_paid_requests']:
        raise ValueError('Frozen batch exceeds the remaining whole-series request cap')
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'plan.json', plan)
    if not args.run_paid:
        print(json.dumps({'status': 'planned', 'planned_primary': len(selected_fixtures) * args.repetitions * 2 * 3, 'llm_calls': 0}))
        return
    rows, chains = [], []
    def save():
        write(output / 'progress.json', {'rows': rows, 'chains': chains,
            'known_cost_usd': sum(r.get('metrics', {}).get('cost_usd', 0) for r in rows),
            'unknown_reserved_usd': sum(0.5 for r in rows if r.get('paid_job') and 'cost_usd' not in r.get('metrics', {}))})
    for fixture_index, (fixture_name, source) in enumerate(selected_fixtures):
        for repetition in range(args.repetitions):
            modes = ['full_document', 'bounded_ops'] if (fixture_index + repetition) % 2 == 0 else ['bounded_ops', 'full_document']
            for mode in modes:
                chain_id = f'{fixture_name}-{repetition + 1}-{mode}'
                chain_out = output / chain_id
                chain_out.mkdir()
                scenario_id = 'test_semantic_' + hashlib.sha256((output.name + ':' + chain_id).encode()).hexdigest()[:18]
                root, expected = provision(scenario_id, source, chain_out)
                opened = client.get('http://127.0.0.1:8777/api/builder/workbench/open', timeout=120, params={
                    'webspace_id': args.host, 'surface': 'authoring', 'selected_object_type': 'project',
                    'selected_object_id': scenario_id, 'base_url': 'http://127.0.0.1:8100'})
                opened.raise_for_status()
                write(chain_out / 'authoring.json', opened.json())
                chain = {'id': chain_id, 'scenario_id': scenario_id, 'mode': mode, 'first_pass': True, 'base_hash': digest(expected)}
                chains.append(chain)
                previous = None
                for index, task in enumerate(changes()):
                    spent = sum(r.get('metrics', {}).get('cost_usd', 0) for r in rows)
                    unknown = sum(0.5 for r in rows if r.get('paid_job') and 'cost_usd' not in r.get('metrics', {}))
                    check_series_budget(prior_paid_requests=args.prior_paid_requests, reserved_attempts=len(rows),
                        prior_cost_usd=args.prior_cost_usd, known_cost_usd=spent,
                        unknown_reserved_usd=unknown, max_usd=args.max_usd)
                    if any(hashlib.sha256((repo / path).read_bytes()).hexdigest() != pin for path, pin in pins.items()):
                        raise ValueError('Frozen implementation changed; stop and retain this batch')
                    message_id = f'{output.name}:{chain_id}:{task["id"]}'
                    attempt = {'chain': chain_id, 'mode': mode, 'task': task['id'], 'source_message_id': message_id,
                               'expected_before_hash': digest(expected), 'ok': False}
                    rows.append(attempt)
                    save()  # reserve before entering the model path
                    started = time.perf_counter()
                    result = {}
                    try:
                        result = prototype.submit_request(task['instruction'], webspace_id=args.host, source_kind='e2e',
                            metadata={'message_id': message_id, 'builder_selected_target': {'object_type': 'scenario', 'object_id': scenario_id},
                                'builder_semantic_edit_mode': mode, 'builder_semantic_write_refs': [task['ref']],
                                'builder_semantic_write_properties': {task['ref']: [
                                    'field_refs' if 'field_refs' in task else ('title.en' if task['ref'].startswith('view:') else 'label.en')]},
                                'builder_llm_model': 'gpt-5', 'builder_llm_reasoning_effort': 'high'}, timeout_seconds=360)
                        write(chain_out / f'{index + 1}-result.json', redact_credentials(result))
                        attempt['status'] = result.get('status')
                        attempt['actual_scenario_id'] = result.get('scenario_id')
                        if result.get('scenario_id') != scenario_id:
                            raise ValueError('The execution receipt targets a different project; stop before further edits')
                        attempt['timing'] = result.get('turn_timing')
                        before = copy.deepcopy(expected)
                        expected = expected_after(expected, task)
                        actual = read(root / 'semantic.webui.json')
                        attempt['ok'] = result.get('status') == 'semantic_proposal_applied' and actual == expected
                        attempt['exact_preservation'] = actual == expected
                        attempt['actual_hash'] = digest(actual)
                        previous = result.get('semantic_transaction')
                        if previous:
                            request = retained_attempt(root, message_id)['result']['request']
                            generation = workflow.get_prototype_edit_context('scenario', scenario_id)['generation']
                            attempt['replay_ok'] = semantic_ui.apply(request, execution_metrics=result['execution_metrics']).get('replayed') is True
                            assert workflow.get_prototype_edit_context('scenario', scenario_id)['generation'] == generation
                            if task['id'] == 'columns':
                                chain['pre_order_document'] = before
                    except Exception as exc:
                        attempt.update(status='exception', error=f'{type(exc).__name__}: {exc}')
                    observed_id = (result.get('message_meta') or {}).get('source_message_id') or message_id
                    retained = retained_attempt(root, observed_id, admission_id=result.get('semantic_admission'))
                    attempt['actual_source_message_id'] = observed_id
                    attempt['paid_job'] = result.get('root_job_id') or retained.get('job_id')
                    attempt['metrics'] = result.get('execution_metrics') or retained.get('execution_metrics', {})
                    if retained.get('job'):
                        from adaos.e2e.composition_metrics import usage_record
                        accounting = usage_record(retained['job'], 'gpt-5')
                        if accounting:
                            attempt['accounting'] = accounting
                            attempt['metrics'].setdefault('cost_usd', accounting['usd_standard_estimate_without_unreported_cache_writes'])
                            attempt['metrics'].setdefault('cost_basis', 'estimate')
                    write(chain_out / f'{index + 1}-model.json', redact_credentials(retained))
                    attempt['wall_ms'] = round((time.perf_counter() - started) * 1000)
                    save()
                    print(json.dumps({k: attempt[k] for k in ('chain', 'task', 'ok', 'status', 'wall_ms', 'metrics') if k in attempt}), flush=True)
                    if not attempt['ok']:
                        chain['first_pass'] = False
                        break
                if chain['first_pass']:
                    chain['browser'] = browser_observation(repo, chain_out / 'final-browser', args.host, scenario_id, root,
                                                          columns=['State', 'Subject', 'Team'])
                    undo = semantic_ui.prepare_request(project_ref='scenario:' + scenario_id,
                        instruction='Undo the last column order change', source_message_id=chain_id + ':undo')
                    receipt = semantic_ui.apply(undo['request'])
                    chain['undo_ok'] = read(root / 'semantic.webui.json') == chain.pop('pre_order_document')
                    write(chain_out / 'undo.json', {'request': undo['request'], 'receipt': receipt})
                    write(chain_out / 'undo-resource-carry-forward.json', carry_revision_resources(scenario_id, root, receipt))
                    chain['undo_browser'] = browser_observation(repo, chain_out / 'undo-browser', args.host, scenario_id, root,
                                                                columns=['Subject', 'State', 'Team'])
                save()
    summary = {'schema': plan['schema'], 'complete': True, 'plan': plan, 'rows': rows, 'chains': chains,
        'paid_jobs': len({r['paid_job'] for r in rows if r.get('paid_job')}),
        'known_cost_usd': sum(r.get('metrics', {}).get('cost_usd', 0) for r in rows),
        'unmetered_jobs': sum(bool(r.get('paid_job')) and 'cost_usd' not in r.get('metrics', {}) for r in rows)}
    write(output / 'summary.json', summary)
    print(json.dumps({k: summary[k] for k in ('complete', 'paid_jobs', 'known_cost_usd', 'unmetered_jobs')}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--host', default='test_ctxqual_be8422f1050250')
    parser.add_argument('--repetitions', type=int, choices=(1, 2), default=2)
    parser.add_argument('--max-usd', type=float, default=15)
    parser.add_argument('--run-paid', action='store_true')
    parser.add_argument('--pilot', action='store_true', help='One fixture; use with repetitions=1 for six primary calls')
    parser.add_argument('--prior-cost-usd', type=float, default=0, help='Previous batches, including unknown-charge reserves')
    parser.add_argument('--prior-paid-requests', type=int, help='Previous paid/unknown attempts in this whole series')
    args = parser.parse_args()
    if not 0 < args.max_usd <= 15:
        parser.error('Budget must be within the approved $15')
    if not 0 <= args.prior_cost_usd < args.max_usd:
        parser.error('Prior cost must be nonnegative and below the whole-series budget')
    if args.prior_paid_requests is None:
        if args.prior_cost_usd:
            parser.error('Prior cost requires an explicit prior-paid-requests count')
        args.prior_paid_requests = 0
    if not 0 <= args.prior_paid_requests <= 32:
        parser.error('Prior request count must be within the whole-series 32-attempt cap')
    execute(args)


if __name__ == '__main__':
    main()
