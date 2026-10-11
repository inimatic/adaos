"""Free native delivery/UX qualification of a retained answer on a fresh owned source.

Does not claim a fresh generation or overwrite old experimental evidence.
Requires an owned, normally provisioned Builder host; never uses the user's host.
"""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
from unittest.mock import patch
from dotenv import load_dotenv
from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import acceptance, browser_review, native_builder, read, remap, source_fingerprints
from adaos.e2e.prototype_task_acceptance import task_mutation_contract


def retained_probes(task):
    """Default is latency-only; named tasks use the matched acceptance probes."""
    if task is None:
        return ['prototype-review']
    if task == 'support-desk-ru':
        return ['prototype-review', 'prototype-capabilities', 'prototype-interactions']
    if task == 'inventory-procurement-ru':
        return None
    raise ValueError('Unknown retained qualification task')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-sample', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--task', choices=('inventory-procurement-ru', 'support-desk-ru'))
    parser.add_argument('--brief', type=Path, help='Frozen owned task Brief; required with --task')
    args = parser.parse_args()
    repo, output, source = Path.cwd().resolve(), args.output.resolve(), args.source_sample.resolve()
    if not args.host.startswith('test_') or not output.is_relative_to(repo / 'e2e/artifacts/builder'):
        parser.error('An owned Builder host and artifact output are required')
    if not source.is_relative_to(repo / 'e2e/artifacts/builder') or output.exists():
        parser.error('Retained owned answer and a new evidence output required')
    if bool(args.task) != bool(args.brief):
        parser.error('--task and --brief must be supplied together')
    brief_path = args.brief.resolve() if args.brief else None
    if brief_path and (not brief_path.is_relative_to(repo / 'e2e/artifacts/builder') or not brief_path.is_file()):
        parser.error('An existing owned frozen task Brief is required')
    brief = read(brief_path) if brief_path else None
    probes = retained_probes(args.task)
    load_dotenv()
    if os.getenv('ENV_TYPE') != 'dev':
        parser.error('DEV only')
    logging.disable(logging.CRITICAL)
    from adaos.apps.cli.app import Settings, init_ctx
    from adaos.services.agent_context import get_ctx
    from adaos.sdk.developer import compositions, projects
    from adaos.sdk.builder import workflow
    from adaos.sdk.llm import llm_client
    from adaos.e2e.builder_host import require_builder_host
    init_ctx(Settings.from_sources())
    require_builder_host(args.host)
    ctx = get_ctx()
    builder = native_builder(Path(ctx.paths.dev_skills_dir()) / 'builder_skill/handlers/main.py')
    old_session = read(source / 'session.json')
    retained_path = source / ('planned-repair-result.json' if (source / 'planned-repair-result.json').exists() else 'generation-result.json')
    result = read(retained_path)
    assert result.get('ok'), 'Only a compiled retained answer is admissible'
    scenario = 'test_ctxlat_' + hashlib.sha256(str(output).encode()).hexdigest()[:16]
    assert not projects.resolve_root('scenario', scenario, required=False).exists()
    output.mkdir(parents=True)
    with patch.object(llm_client, 'submit_response_job', side_effect=AssertionError('No paid generation during latency qualification')):
        created = compositions.create_with_primary_component(scenario, kind='scenario', template='scenario_default',
            title='[TEST] Retained context delivery', tags=['test', 'context-latency'])
        root = projects.resolve_root('scenario', scenario)
        initial = read(repo / 'src/adaos/scenario_templates/scenario_default/webui.json')
        initial = remap(initial, 'template-id', scenario)
        builder._write_webui_payload(str(root), initial)
        change = workflow.transition('scenario', scenario, 'plan_change_set', actor='agent:builder-e2e',
            metadata={'change_set_id': scenario, 'request': old_session['source_idea'],
                'issues': [{'issue_id': 'retained-delivery', 'title': 'Retained delivery qualification', 'lane': 'prototype',
                    'acceptance_criteria': ['Native materialization and public desktop/mobile controls; no fresh model claims.']}]})['workflow']
        result = remap(result, old_session['scenario_id'], scenario)
        session = {**remap(old_session, old_session['scenario_id'], scenario), 'artifact_root': str(root),
            'initial_webui': initial, 'webui_payload': initial, 'active_change_id': change['change']['change_id']}
        _write_json(output / 'configuration.json', {'host': args.host, 'scenario_id': scenario,
            'retained_answer_sha256': hashlib.sha256(retained_path.read_bytes()).hexdigest(),
            'sources': source_fingerprints(repo), 'new_llm_calls': 0, 'task': args.task,
            'brief_sha256': hashlib.sha256(brief_path.read_bytes()).hexdigest() if brief_path else None,
            'selected_probes': probes, 'primary_series_unchanged': True})
        structural = acceptance(result, brief, task=args.task) if args.task else None
        contract = task_mutation_contract(result['semantic_document'], brief, task=args.task) if args.task else None
        if args.task:
            _write_json(output / 'structural-acceptance.json', structural)
            _write_json(output / 'task-mutation-contract.json', contract)
        checked = browser_review(builder, session, result, output, created, Path(ctx.paths.dev_dir()).name,
            output.name, host=args.host, selected_probes=probes, observe_delivery=True)
        if args.task:
            checked['ok'] = checked['ok'] and structural['ok'] and contract['ok']
        _write_json(output / 'result.json', {**checked, 'new_llm_calls': 0,
            'structural_acceptance': structural, 'task_mutation_contract': contract,
            'primary_series_unchanged': True,
            'scope': 'Supplemental retained-answer task qualification, not a replacement matched score' if args.task
                else 'Retained-answer delivery/UX replay, not new generation'})
        print(json.dumps({'ok': checked['ok'], 'timings': checked['timings'], 'new_llm_calls': 0}), flush=True)
    return 0 if checked['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
