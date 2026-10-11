"""Qualify every final retained candidate with calibrated public-control probes.

No model calls. Primary outputs and planned repairs remain immutable. Text-only
guidance and delivery improvements must not change the evaluated compiler or
the admission/materialization functions from the original run.
"""
import argparse
import ast
import hashlib
import json
import logging
import os
import re
from functools import wraps
from pathlib import Path
from unittest.mock import patch
import requests

from dotenv import load_dotenv
from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import (acceptance, browser_review, native_builder,
    preserved_preview, read, source_fingerprints)


def semantic_hash(path, names=None, exclude=()):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    if names is not None:
        tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in tree.body} == set(names)
    else:
        tree.body = [node for node in tree.body if not isinstance(node, ast.FunctionDef) or node.name not in exclude]
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run', type=Path, required=True)
parser.add_argument('--host', required=True)
parser.add_argument('--capture-baseline', action='store_true')
parser.add_argument('--label', default='public-controls')
parser.add_argument('--resume', action='store_true')
parser.add_argument('--requalify-failures-from', default='',
    help='Retain positive witnesses on the same Client/assertions; rerun every failed probe after a navigation-only correction')
args = parser.parse_args()
if not re.fullmatch(r'[a-z0-9-]+', args.label):
    parser.error('Safe qualification label required')
if args.requalify_failures_from and not re.fullmatch(r'[a-z0-9-]+', args.requalify_failures_from):
    parser.error('Safe prior qualification label required')
if args.requalify_failures_from == args.label:
    parser.error('Original evidence must remain immutable')
repo = Path.cwd().resolve()
run = args.run.resolve()
if not run.is_relative_to(repo / 'e2e/artifacts/builder'):
    parser.error('Owned Builder evidence required')
registration = read(run / 'preregistration.json')
compiler = repo / 'src/adaos/services/builder/semantic_prototype.py'
harness = repo / 'src/adaos/e2e/prototype_context_ablation.py'
contract = {'compiler_excluding_guidance_ast_sha256': semantic_hash(compiler, exclude=('semantic_prototype_generation_guidance',)),
    'qualification_functions_ast_sha256': semantic_hash(harness, names=('acceptance', 'browser_review', 'native_builder', 'preserved_preview'))}
baseline = run / 'qualification-functional-contract.json'
if args.capture_baseline:
    assert not baseline.exists()
    for cohort in registration['cohorts']:
        plan = read(run / cohort['name'] / 'plan.json')
        assert 'sha256:' + hashlib.sha256(compiler.read_bytes()).hexdigest() == plan['runtime_source_fingerprints']['src/adaos/services/builder/semantic_prototype.py']
        assert 'sha256:' + hashlib.sha256(harness.read_bytes()).hexdigest() == plan['acceptance_source_sha256']
    _write_json(baseline, contract)
    print(json.dumps({'captured_original_functional_contract': True}))
    raise SystemExit(0)
assert contract == read(baseline), 'Compiler/admission/materialization changed, not just guidance or public-control probes'
load_dotenv()
if os.getenv('ENV_TYPE') != 'dev':
    parser.error('DEV only')
logging.disable(logging.CRITICAL)
from adaos.apps.cli.app import Settings, init_ctx
from adaos.services.agent_context import get_ctx
from adaos.sdk.llm import llm_client
from adaos.sdk.builder import preview
init_ctx(Settings.from_sources())
os.environ['ADAOS_CONTROL_URL'] = 'http://127.0.0.1:8777'
os.environ['ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED'] = '0'
os.environ['ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY'] = 'full'
handler = Path(get_ctx().paths.dev_skills_dir()) / 'builder_skill/handlers/main.py'
handler_sha = 'sha256:' + hashlib.sha256(handler.read_bytes()).hexdigest()
builder = native_builder(handler)
native_materializer = preview.materialize_revision_via_owner
@wraps(native_materializer)
def materialize_with_stand_deadline(**kwargs):
    # The local stand shares a memory-constrained development machine. Use the
    # SDK's supported deadline, not a different admission/materialization path.
    return native_materializer(**{**kwargs, 'timeout_s': 120})
frozen = source_fingerprints(repo)
allowed_changes = {'src/adaos/services/builder/semantic_prototype.py', 'src/adaos/services/builder/prototype_pipeline.py',
    'src/adaos/e2e/prototype_context_ablation.py', 'e2e/stand/repair-prototype-pipeline-cohort.py',
    'src/adaos/e2e/prototype_mcp_eval.py',
    'src/adaos/integrations/adaos-backend/backend/prototype-context-library.json',
    'src/adaos/integrations/adaos-backend/backend/app.ts',
    'e2e/stand/browser/prototype-navigation.mjs', 'e2e/stand/browser/prototype-procurement-lines.mjs',
    'e2e/stand/browser/prototype-procurement-fields.mjs', 'e2e/stand/browser/prototype-procurement.mjs',
    'e2e/stand/browser/prototype-capabilities.mjs',
    'e2e/stand/browser/prototype-interactions.mjs',
    'src/adaos/integrations/adaos-client/src/app/runtime/layout-render-plan.ts',
    *[f'src/adaos/integrations/adaos-client/src/app/renderer/desktop/desktop.component.{extension}'
      for extension in ('ts', 'html', 'scss')],
    'src/adaos/integrations/adaos-client/src/app/runtime/focus-runtime.service.ts'}
for cohort in registration['cohorts']:
    directory = run / cohort['name']
    plan, results = read(directory / 'plan.json'), read(directory / 'results.json')
    assert results['complete'] and handler_sha == plan['native_builder_sha256']
    changed = {key for key in set(frozen) | set(plan['runtime_source_fingerprints'])
               if frozen.get(key) != plan['runtime_source_fingerprints'].get(key)}
    assert changed <= allowed_changes, 'Unexpected change outside calibrated probes and non-evaluated delivery/guidance'
    output = directory / ('final-qualification-' + args.label)
    positive_witnesses = {}
    if args.requalify_failures_from:
        prior_output = directory / ('final-qualification-' + args.requalify_failures_from)
        prior_results = read(prior_output / 'results.json')
        assert prior_results['complete']
        prior_sources = read(prior_output / 'configuration.json')['source_fingerprints']
        drift = {key for key in set(frozen) | set(prior_sources) if frozen.get(key) != prior_sources.get(key)}
        navigation_sources = {'e2e/stand/browser/prototype-navigation.mjs', 'e2e/stand/browser/prototype-capabilities.mjs'}
        assert drift <= navigation_sources, 'Reuse requires identical Client, assertions and admission; navigation-only correction'
        capabilities_source = 'e2e/stand/browser/prototype-capabilities.mjs'
        if capabilities_source in drift:
            # Only a public return to navigation was added; all data assertions
            # must match the originally qualified executable byte for byte.
            lines = (repo / capabilities_source).read_bytes().splitlines(keepends=True)
            assert sum(line.strip() == b'await reveal(navigation)' for line in lines) == 1
            old_probe = b''.join(line for line in lines if line.strip() != b'await reveal(navigation)')
            assert 'sha256:' + hashlib.sha256(old_probe).hexdigest() == prior_sources[capabilities_source], 'Data assertions changed'
        positive_witnesses = {row['sample']: row for row in prior_results['samples']}
    if output.exists() and not args.resume:
        parser.error('New qualification label or explicit --resume required')
    output.mkdir(exist_ok=args.resume)
    configuration = {'label': args.label, 'host': args.host, 'new_llm_calls': 0,
        'local_materialization_deadline_s': 120,
        'requalify_failures_from': args.requalify_failures_from,
        'compiler_and_admission_unchanged': True, 'functional_contract': contract,
        'source_fingerprints': frozen, 'native_builder_sha256': handler_sha,
        'changed_sources': sorted(changed), 'qualifier_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'probe_corrections': ['public selection rather than activation', 'populate all submitted required line fields',
            'request need not duplicate delivery lead visible in source quote',
            'search disclosure panels inside each tab; CSS-closing panels are not logically open',
            'reveal inline editors through public supporting-panel controls after selecting/creating a record'],
        'client_corrections': ['restore opener focus after Ionic dismissal cleanup; do not focus hidden duplicates',
            'separate populated workbench/master-detail detail and inspector cells',
            'retain public disclosure for inline-editor detail panels in open-detail mode'],
        'model_outputs_and_original_results_unchanged': True}
    if (output / 'configuration.json').exists():
        assert read(output / 'configuration.json') == configuration
    else:
        _write_json(output / 'configuration.json', configuration)
    rows = []
    with patch.object(preview, 'materialize_revision_via_owner', materialize_with_stand_deadline), preserved_preview(args.host, output), patch.object(llm_client, 'submit_response_job', side_effect=AssertionError('Qualification must not submit a model')):
        for original in results['samples']:
            assert source_fingerprints(repo) == frozen
            requests.get('http://127.0.0.1:8777/health/ready', timeout=10).raise_for_status()
            sample = directory / f"{original['order']:02d}-{original['arm']}"
            target = output / sample.name
            if args.resume and (target / 'result.json').exists():
                rows.append(read(target / 'result.json'))
                continue
            target.mkdir(exist_ok=args.resume)
            repaired = sample / 'planned-repair-result.json'
            result = read(repaired if repaired.exists() else sample / 'generation-result.json')
            structural = acceptance(result, read(directory / 'source-brief.json'), task=plan['task'])
            checked = {'ok': False, 'skipped': 'final_candidate_did_not_compile'}
            reused = {}
            if result.get('ok'):
                selected_probes = ['prototype-review', 'prototype-capabilities', 'prototype-interactions'] if plan['task'] == 'support-desk-ru' else None
                prior_witness = positive_witnesses.get(sample.name)
                passed_witnesses = {}
                if prior_witness:
                    assert prior_witness['scenario_id'] == read(sample / 'session.json')['scenario_id']
                    assert prior_witness['planned_repair_attempted'] == repaired.exists()
                    passed_witnesses = {name: status for name, status in prior_witness['probes'].items() if status == 0}
                    selected_probes = [name for name, status in prior_witness['probes'].items() if status != 0]
                    reused = {'probes': passed_witnesses,
                        'evidence': str((prior_output / sample.name / 'result.json').relative_to(repo)),
                        'reason': 'positive witnesses on identical Client/assertions; all failed probes rerun after navigation-only correction',
                        'original_probes': prior_witness['probes'], 'changed_sources': sorted(drift)}
                prior_directory = directory / ('planned-repair-qualification-selection-marker' if repaired.exists() else 'browser-posthoc-selection-marker')
                prior_result = prior_directory / sample.name / 'result.json'
                if not prior_witness and plan['task'] == 'inventory-procurement-ru' and prior_result.exists():
                    prior = read(prior_result)
                    core = {key: value for key, value in prior.get('probes', {}).items() if key != 'prototype-procurement-composition'}
                    required = {'prototype-review', 'prototype-capabilities', 'prototype-procurement'}
                    if any(command.get('kind') == 'update' for command in result['semantic_document'].get('commands', [])):
                        required.add('prototype-interactions')
                    core_sources = [f'e2e/stand/browser/{name}.mjs' for name in ('prototype-review', 'prototype-capabilities',
                        'prototype-navigation', 'prototype-interactions', 'prototype-interaction-fields',
                        'prototype-procurement', 'prototype-procurement-fields')]
                    core_sources.append('src/adaos/integrations/adaos-client/src/app/runtime/focus-runtime.service.ts')
                    core_sources.extend(key for key in frozen if 'layout-render-plan.ts' in key or '/renderer/desktop/' in key)
                    prior_fingerprints = read(prior_directory / ('source-fingerprints.json'))
                    unchanged_core = all(prior_fingerprints.get(key) == frozen.get(key) for key in core_sources)
                    if unchanged_core and required == set(core) and all(value == 0 for value in core.values()):
                        reused = {'probes': core, 'evidence': str(prior_result.relative_to(repo)),
                                  'reason': 'unchanged passed core probes; only the supplemental line form filler changed'}
                        selected_probes = ['prototype-procurement-composition']
                if prior_witness and not selected_probes:
                    checked = {key: prior_witness[key] for key in ('ok', 'probes', 'scenario_id', 'webspace_id', 'revision')}
                else:
                    checked = browser_review(builder, read(sample / 'session.json'), result, target,
                        read(sample / 'creation.json'), Path(get_ctx().paths.dev_dir()).name, directory.name,
                        host=args.host, selected_probes=selected_probes)
                if reused:
                    checked['probes'] = {**reused['probes'], **checked['probes']}
                    checked['ok'] = all(value == 0 for value in checked['probes'].values())
                # A failed infrastructure check stops qualification before a model
                # outcome is recorded, even when a browser returned an exit code.
                requests.get('http://127.0.0.1:8777/health/ready', timeout=10).raise_for_status()
            row = {'sample': sample.name, 'arm': original['arm'], 'structural': structural,
                'planned_repair_attempted': repaired.exists(), 'reused_core_qualification': reused, **checked}
            _write_json(target / 'result.json', row)
            rows.append(row)
            _write_json(output / 'results.json', {'complete': False, 'samples': rows, 'new_llm_calls': 0})
            print(json.dumps({'cohort': cohort['name'], **row}, ensure_ascii=False), flush=True)
    _write_json(output / 'results.json', {'complete': True, 'samples': rows, 'new_llm_calls': 0,
        'configuration': configuration, 'preview_restoration': read(output / 'preview-restored.json')})
