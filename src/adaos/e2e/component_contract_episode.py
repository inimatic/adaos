"""Seven controlled stages on an existing relational Prototype and one Preview."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import subprocess
import time
from unittest.mock import patch

from adaos.e2e.component_contract_conformance import load_slice, matched_runtime_episodes
from adaos.e2e.semantic_proposer_comparison import provision, read, write, carry_revision_resources


def execute(inputs, context, *, repo_root: Path):
    from adaos.apps.cli.active_control import resolve_control_token
    from adaos.e2e.builder_host import step_builder_host
    from adaos.sdk.builder import preview, semantic_ui, workflow
    from adaos.sdk.llm import llm_client
    from adaos.services.agent_context import get_ctx
    from adaos.services.ui_component_contracts import project_component_contract, load_component_contract, contract_impact, component_contract_scope, component_contract_findings, component_registration_types
    from adaos.services.contract_context_admission import prepare_component_input, ContractInputRejected
    from adaos.services.context_control import ContextControlService

    if os.getenv('ENV_TYPE') != 'dev' or context.get('profile') != 'generic':
        raise ValueError('Owned DEV fixture required')
    output = Path(context['bundle_dir']) / 'contract-episodes' / f"{context['case_id']}-{context['repetition']}"
    output.mkdir(parents=True, exist_ok=False)
    host = step_builder_host(inputs, context)
    document, spec = load_slice(repo_root)
    scenario = 'test_semantic_' + hashlib.sha256(('contract:' + context['run_id']).encode()).hexdigest()[:18]
    root, source = provision(scenario, document, output)
    snapshot = project_component_contract()
    state = ContextControlService(output / 'context-state')
    rows = []
    def stage(number, data):
        row = {'stage': number, 'ok': True, **data}
        rows.append(row)
        write(output / f'stage-{number}.json', row)
        write(output / 'progress.json', {'ok': False, 'rows': rows, 'model_calls': 0, 'usd': 0})
        print(f'contract-stage={number} ok=true', flush=True)
    stage(1, {'scenario_id': scenario, 'host': host, 'preview': host + '-dev', 'fixture': spec,
        'sources': snapshot['provenance'], 'semantic_digest': hashlib.sha256((root / 'semantic.webui.json').read_bytes()).hexdigest()})
    stage(2, {'mappings': snapshot['mappings'], 'projection': snapshot['provenance']})
    with patch.object(llm_client, 'submit_response_job', side_effect=AssertionError('model-free contract episode must not invoke LLM')):
        state.publish_component_projection(snapshot)
        stage(3, {'units': snapshot['retrieval_units'], 'discovery_policy': snapshot['discovery_policy']})
        materialized = preview.select_target('scenario', scenario, source_webspace_id=host, stage='prototype', via_owner=True)
        write(output / 'materialization.json', materialized)
        if not materialized.get('ok'):
            raise ValueError('Owned Preview materialization failed')
        environment = {**os.environ, 'ENV_TYPE': 'dev', 'ADAOS_E2E_HUB_URL': 'http://127.0.0.1:8777',
            'ADAOS_E2E_HUB_TOKEN': resolve_control_token(base_url='http://127.0.0.1:8777'),
            'ADAOS_E2E_SUBNET_ID': Path(get_ctx().paths.dev_dir()).name, 'ADAOS_E2E_WEBSPACE_ID': host + '-dev',
            'ADAOS_E2E_SCENARIO_ID': scenario, 'ADAOS_E2E_CLIENT_URL': 'http://127.0.0.1:8100/',
            'ADAOS_E2E_EXPECTED_WEBUI': str(root / 'webui.json'), 'ADAOS_E2E_OUTPUT': str(output / 'browser-baseline')}
        completed = subprocess.run(['node', str(repo_root / 'e2e/stand/browser/component-contract-conformance.mjs')],
            cwd=repo_root, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=240)
        (output / 'browser.log').write_text(completed.stdout + completed.stderr, encoding='utf-8')
        browser = read(output / 'browser-baseline/result.json') if (output / 'browser-baseline/result.json').exists() else {'ok': False}
        write(output / 'browser-status.json', {'returncode': completed.returncode, 'result': browser})
        if completed.returncode or not browser.get('ok'):
            raise ValueError('Independent renderer contract conformance failed; retained failure evidence')
        stage(4, {'browser': browser})
        envelope, _ = prepare_component_input({'messages': [{'role': 'user', 'content': 'Rename the table'}], 'tools': []},
            snapshot=snapshot, required_ids=['ui.table'])
        admission = state.admit_model_input(envelope, snapshot=snapshot, required_ids=['ui.table'], run_ref='builder-run:' + scenario)
        stage(5, {'input_admission': admission})
        current = copy.deepcopy(load_component_contract())
        current['version'] = '2.0.0'
        current['input_shapes']['ContractTableColumn']['properties']['label']['maxLength'] = 24
        evolution = state.evolve_component_contract(load_component_contract(), current, event_ref='contract-evolution:' + scenario)
        rejected = None
        try:
            state.admit_model_input(envelope, snapshot=project_component_contract(current), required_ids=['ui.table'])
        except ContractInputRejected as error:
            rejected = error.findings
        if not rejected or state.get_artifact(admission['snapshot_ref']) != snapshot:
            raise ValueError('Contract change rebased or failed to reject stale input')
        from adaos.services.builder.semantic_prototype import compile_semantic_prototype
        from adaos.services.root_mcp.descriptor_search import get_descriptor_item
        with component_contract_scope(current) as evolved_snapshot:
            projection_findings = component_contract_findings(read(root / 'webui.json'))
            if 'component.projection_snapshot_incompatible' not in {finding['code'] for finding in projection_findings}:
                raise ValueError('A reused incompatible projection was not detected')
            rebuilt = compile_semantic_prototype(source, project_ref='project:' + scenario)
            if rebuilt['semantic_document'] != source or component_contract_findings(rebuilt['webui']):
                raise ValueError('Scoped compiler rebuild lost semantics or retained a stale projection')
            write(output / 'evolved-compiler.json', rebuilt)
            (output / 'evolved-component-contract.generated.ts').write_text(component_registration_types(current), encoding='utf-8')
            descriptor = get_descriptor_item(**evolved_snapshot['retrieval_units'][0]['locator'])
            if descriptor['item']['component_contract'] != evolved_snapshot['retrieval_units'][0]:
                raise ValueError('Root MCP projection did not rebuild from the same declaration')
            write(output / 'evolved-mcp-descriptor.json', descriptor)
        stage(6, {'impact': evolution['impact'], 'stale_findings': rejected, 'projection_findings': projection_findings,
                  'pinned_snapshot_ref': admission['snapshot_ref'], 'scoped_rebuild': 'compiler/client-types/catalog/mcp/context'})
        before = read(root / 'semantic.webui.json')
        prepared = semantic_ui.prepare_request(project_ref='scenario:' + scenario,
            instruction='Rename view "Records" to "Contract queue" in English', source_message_id='contract-delta:' + scenario)
        if prepared.get('status') != 'planned':
            raise ValueError('Bounded semantic replay fixture not recognized')
        receipt = semantic_ui.apply(prepared['request'])
        replay = semantic_ui.apply(prepared['request'])
        expected = copy.deepcopy(before)
        next(view for view in expected['views'] if view['id'] == 'records-list')['title']['en'] = 'Contract queue'
        if read(root / 'semantic.webui.json') != expected or not replay.get('replayed'):
            raise ValueError('Semantic delta/replay did not preserve the complete source')
        carry_revision_resources(scenario, root, receipt)
        undo = semantic_ui.prepare_request(project_ref='scenario:' + scenario,
            instruction='Undo the last label change', source_message_id='contract-undo:' + scenario)
        inverse = semantic_ui.apply(undo['request'])
        carry_revision_resources(scenario, root, inverse)
        if read(root / 'semantic.webui.json') != before:
            raise ValueError('Independent source equality failed after undo')
        final_materialization = preview.select_target('scenario', scenario, source_webspace_id=host,
            revision=inverse['revision'], stage='prototype', via_owner=True)
        write(output / 'final-materialization.json', final_materialization)
        if not final_materialization.get('ok'):
            raise ValueError('Undo revision was not materialized by the live Preview owner')
        final_environment = {**environment, 'ADAOS_E2E_OUTPUT': str(output / 'browser-after-undo')}
        final_probe = subprocess.run(['node', str(repo_root / 'e2e/stand/browser/component-contract-conformance.mjs')],
            cwd=repo_root, env=final_environment, capture_output=True, text=True, encoding='utf-8', timeout=240)
        (output / 'browser-after-undo.log').write_text(final_probe.stdout + final_probe.stderr, encoding='utf-8')
        final_browser = read(output / 'browser-after-undo/result.json') if (output / 'browser-after-undo/result.json').exists() else {'ok': False}
        if final_probe.returncode or not final_browser.get('ok'):
            raise ValueError('Independent renderer behavior was not preserved through replay/undo')
        compiled = compile_semantic_prototype(before, project_ref='project:' + scenario)
        episodes = matched_runtime_episodes(compiled, output / 'matched-state')
        write(output / 'matched-runtime-episodes.json', episodes)
        if not all(row['ok'] for row in episodes):
            raise ValueError('Matched runtime evolution episode failed; preserve its original evidence')
        stage(7, {'revision': inverse['revision'], 'replay': replay, 'undo': inverse,
            'final_browser': final_browser,
            'matched_episodes': episodes,
            'cost': {'llm_calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'usd': 0},
            'qualification_scope': 'bounded renderer, provider evolution, source preservation/replay/undo; no whole-platform closure'})
    result = {'ok': True, 'scenario_id': scenario, 'revision': inverse['revision'], 'model_calls': 0, 'usd': 0, 'rows': rows}
    result.update(draft_id=scenario, project_id=scenario, project={'id': scenario, 'primary_ref': 'scenario:' + scenario},
        review_preview={'webspace_id': host + '-dev', 'scenario_id': scenario, 'revision': inverse['revision'], 'test': True, 'stage': 'prototype'})
    result['cleanup'] = {'test': True, 'acceptance': 'not_approved', 'owned_artifacts': [
        {'primary_ref': 'scenario:' + scenario, 'project_id': scenario}],
        'previews': [{'webspace_id': host + '-dev', 'scenario_id': scenario, 'test': True, 'stage': 'prototype'}]}
    write(output / 'result.json', result)
    return result
