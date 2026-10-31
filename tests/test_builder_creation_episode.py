import json
from pathlib import Path

import pytest

from adaos.e2e.builder import load_builder_e2e_suite, SdkBuilderExecutor, BuilderE2EUnavailable, _collect_usage
from adaos.e2e.builder_creation_episode import attempts, episode_attribution, runtime_identity, creation_design


ROOT = Path(__file__).parents[1]


def test_creation_is_on_existing_e2e_rail_and_counterbalanced():
    suite = load_builder_e2e_suite(ROOT / 'e2e/builder/development/creation/suite.yaml')
    steps = [row['steps'][0] for row in suite.cases]
    assert [row['input']['arm'] for row in steps] == ['direct', 'v1', 'v1', 'direct']
    assert [row['input']['ordinal'] for row in steps] == list(range(4))
    assert {row['type'] for row in steps} == {'prototype.creation_episode'}
    assert all(row['input']['prompt_ref'].endswith('infrastructure-ru.txt') for row in steps)
    assert all('retry' not in row for row in steps)
    pair = load_builder_e2e_suite(ROOT / 'e2e/builder/development/creation/pair.yaml')
    assert [row['steps'][0]['input']['arm'] for row in pair.cases] == ['direct','v1']


def test_creation_cannot_succeed_without_browser():
    executor = SdkBuilderExecutor(repo_root=ROOT, browser_mode='off')
    with pytest.raises(BuilderE2EUnavailable, match='live browser'):
        executor.execute('prototype.creation_episode', {}, {})


def test_single_arm_qualification_is_explicit_and_cannot_claim_productivity_comparison():
    with pytest.raises(ValueError, match='matched pair'):
        creation_design(['v1'])
    with pytest.raises(ValueError, match='exactly one v1'):
        creation_design(['direct', 'v1'], qualification_only=True)
    design = creation_design(['v1'], qualification_only=True)
    assert 'NOT a productivity comparison' in design['scope']
    suite = load_builder_e2e_suite(ROOT / 'e2e/builder/development/creation/qualification.yaml')
    assert len(suite.cases) == 1
    assert suite.cases[0]['steps'][0]['input']['ordinal'] == 0
    assert suite.cases[0]['steps'][0]['input']['arm'] == 'v1'


def test_episode_counts_failed_calls_and_cached_tokens_without_history():
    rows = [{'id': 'one', 'usage': {'input_tokens': 100, 'output_tokens': 20,
            'input_tokens_details': {'cached_tokens': 40}, 'model_calls': 1},
             'status': 'failed', 'telemetry': {'provider_calls': 1}},
            {'id': 'two', 'usage': {'input_tokens': 80, 'output_tokens': 30, 'model_calls': 2},
             'status': 'completed', 'telemetry': {'provider_calls': 2}}]
    episode = {'schema': 'adaos.e2e.creation_episode.v1', 'scenario_id': 'own',
               'provider_attempts': rows, 'accounting_findings': []}
    report = episode_attribution([episode])
    assert report['observed_model_calls'] == 3
    assert report['unique_receipt_count'] == 2
    usage = _collect_usage(episode)
    assert usage['model_calls'] == 3
    assert usage['fresh_input_tokens'] == 140
    assert usage['cached_input_tokens'] == 40
    assert usage['output_tokens'] == 50
    episode['accounting_findings'] = ['unjoined_new_attempts']
    assert episode_attribution([episode])['status'] == 'failed'


def test_metadata_snapshot_is_read_only_and_excludes_output_and_binding(tmp_path):
    import sqlite3
    directory = tmp_path / 'ai/principal'
    directory.mkdir(parents=True)
    path = directory / 'ai.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE attempts (id,request_id,status,usage,telemetry,error,output,binding)')
        db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)',
                   ('one', 'request', 'completed', '{}', '{}', '', 'private-output', 'private-binding'))
    before = path.read_bytes()
    report = attempts(tmp_path)
    assert 'private-' not in json.dumps(report)
    assert path.read_bytes() == before


def test_creation_artifact_guard_canonicalizes_both_sides_of_junction():
    # The stand may relocate the ignored artifact tree through a Windows junction.
    import ast
    tree = ast.parse((ROOT / 'src/adaos/e2e/builder_creation_episode.py').read_text(encoding='utf-8'))
    guards = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute) and node.func.attr == 'is_relative_to']
    assert any(isinstance(node.args[0], ast.Call) and isinstance(node.args[0].func, ast.Attribute)
               and node.args[0].func.attr == 'resolve' for node in guards)


def test_runtime_pin_ignores_slot_bookkeeping_but_detects_code_drift(tmp_path):
    runtime = tmp_path / 'skills/.runtime/owned'
    for slot in ('A','B'):
        handler = runtime / f'v0.1/slots/{slot}/src/skills/owned/handlers/main.py'
        handler.parent.mkdir(parents=True)
        handler.write_text('same code',encoding='utf-8')
    marker = runtime / 'current_runtime.json'
    marker.write_text(json.dumps({'version':'0.1.1','runtime_bucket':'v0.1','slot':'A','updated_at':1}),encoding='utf-8')
    before = runtime_identity(tmp_path,'owned')
    marker.write_text(json.dumps({'version':'0.1.1','runtime_bucket':'v0.1','slot':'B','updated_at':2}),encoding='utf-8')
    assert runtime_identity(tmp_path,'owned') == before
    handler.write_text('different code',encoding='utf-8')
    assert runtime_identity(tmp_path,'owned') != before
