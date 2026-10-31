import json
from pathlib import Path
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator

from adaos.e2e.semantic_coverage import audit, candidate_fixture, inspect_recorded_request, inventory_freshness
from adaos.services.builder.semantic_prototype import semantic_prototype_generation_guidance, semantic_prototype_provider_contract
from adaos.services.builder.semantic_owner_references import owner_reference_contract, owner_reference_findings

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope='module')
def report():
    return audit(ROOT)


def test_audit_separates_inventory_witnesses_and_behavior(report):
    assert report['ok']
    assert report['model_calls'] == 0
    assert not report['semantic_equivalence_proven']
    assert not report['equal_abi_expressiveness_proven']
    assert not report['llm_understanding_proven']
    assert report['summary']['witnesses_passed'] == report['summary']['witnesses_total'] == 11
    assert all(row['runtime_behavior'] == 'not_executed' for row in report['widgets'])
    index = {row['id']: row for row in report['widgets']}
    assert index['ui.table']['semantic_lowering'] == 'witnessed'
    assert index['ui.chat']['semantic_lowering'] == 'not_witnessed'  # Not a proof of inexpressibility.
    assert len(index) == len(report['widgets'])
    assert set(report['source_pins'])


def test_same_renderer_can_witness_different_semantic_presentations(report):
    index = {row['id']: row for row in report['widgets']}
    assert {'presentation.list', 'presentation.cards', 'presentation.accordion'} <= set(index['ui.list']['witnesses'])
    assert all(row['provider_schema_accepted'] for row in report['witnesses'])
    assert report['catalog_coverage']['complete']
    assert report['catalog_coverage']['semantic_generation'] == 'not_established_by_catalog_enumeration'


def test_actual_provider_rejects_a_section_identity_as_a_view_owner():
    candidate = candidate_fixture()
    candidate['commands'][0]['view_ref'] = 'section-overview'
    # Schema accepts a string; cross-field meaning is enforced by Core.
    Draft202012Validator(semantic_prototype_provider_contract(version='v2')).validate(candidate)
    findings = owner_reference_findings(candidate)
    assert findings[0]['unknown_reference'] == 'section-overview'
    assert 'item-editor' in findings[0]['allowed_refs']


def test_saved_input_audit_reports_missing_and_changed_context_without_copying_text():
    from adaos.services.builder.prototype_mcp import library_snapshot
    guidance = semantic_prototype_generation_guidance()
    guidance['modeling'] = 'PRIVATE_PROMPT_VALUE'
    del guidance['coverage']
    record = {'messages': [{'role': 'user', 'content': json.dumps({'stable_builder_context': {
        'semantic_invariants': guidance}, 'authorization': 'SECRET_VALUE'})}]}
    result = inspect_recorded_request(record, library_snapshot())
    assert result['guidance_fragments_absent'] == ['coverage']
    assert result['guidance_fragments_changed'] == ['modeling']
    assert 'PRIVATE_PROMPT_VALUE' not in json.dumps(result) and 'SECRET_VALUE' not in json.dumps(result)


def test_saved_input_audit_refuses_files_outside_retained_evidence():
    with pytest.raises(ValueError, match='retained Builder evidence'):
        audit(ROOT, recorded_request_ref='.env')


def test_inventory_freshness_uses_the_client_generators_newline_contract(tmp_path):
    import hashlib
    source = tmp_path / 'source.ts'
    source.write_bytes(b'first\r\nsecond\r\n')
    inventory = {'generated_from': {'sources': [{'path': 'source.ts',
        'sha256': hashlib.sha256(b'first\nsecond\n').hexdigest()}]}}
    assert inventory_freshness(tmp_path, inventory)['status'] == 'source_hashes_match'
    source.write_bytes(b'changed')
    assert inventory_freshness(tmp_path, inventory)['stale_sources'] == ['source.ts']
    inventory['generated_from']['sources'][0]['path'] = '../escape.ts'
    with pytest.raises(ValueError, match='escapes its owner'):
        inventory_freshness(tmp_path, inventory)


def test_audit_projects_the_enforced_owner_edges_without_claiming_model_delivery():
    contract = owner_reference_contract()
    assert 'reference_ownership' not in semantic_prototype_generation_guidance()
    for rule in contract['rules']:
        candidate = {rule['source_array']: [{'id': 'source', rule['reference_field']: 'missing'}],
            rule['target_array']: [{'id': 'target'}]}
        # Other owner fields may be absent in this deliberately partial witness;
        # inspect the exact independently enforced edge, not the aggregate size.
        findings = owner_reference_findings(candidate)
        assert any(row['path'] == f"$.{rule['source_array']}[0].{rule['reference_field']}"
            and row['allowed_refs'] == ['target'] for row in findings)
    contract['rules'].clear()
    assert owner_reference_contract()['rules']


def test_lowering_failure_is_retained_not_reported_as_a_success():
    candidate = candidate_fixture()
    with patch('adaos.e2e.semantic_coverage.witnesses', return_value=iter([
        {'id': 'negative-control', 'candidate': candidate, 'expected_widget': 'ui.chat',
         'semantic_path': '$.views', 'fragments': []}])):
        result = audit(ROOT)
    assert not result['ok']
    assert result['witnesses'][0]['provider_schema_accepted']
    assert not result['witnesses'][0]['lowering_accepted']
    assert 'Expected renderer was not emitted' in result['witnesses'][0]['error']


def test_coverage_uses_registered_model_free_e2e_step(tmp_path):
    from adaos.e2e.builder import SdkBuilderExecutor, load_builder_e2e_suite
    suite = load_builder_e2e_suite(ROOT / 'e2e/builder/development/semantic-coverage/suite.yaml')
    assert suite.cases[0]['steps'][0]['type'] == 'prototype.semantic_coverage'
    with patch('adaos.e2e.semantic_coverage.audit', return_value={'ok': True, 'widgets': [], 'witnesses': []}):
        result = SdkBuilderExecutor(repo_root=ROOT, browser_mode='off').execute(
            'prototype.semantic_coverage', {}, {'bundle_dir': str(tmp_path)})
    assert result['ok']
    assert json.loads((tmp_path / 'semantic-coverage/report.json').read_text())['ok']


def test_inspection_does_not_activate_builder_or_require_a_live_host(tmp_path):
    from adaos.e2e.builder import BuilderE2ERunner, SdkBuilderExecutor
    with patch.object(SdkBuilderExecutor, 'prepare_host', side_effect=AssertionError('runtime mutation')):
        with patch('adaos.e2e.semantic_coverage.audit', return_value={'ok': True,
            'model_calls': 0, 'widgets': [], 'witnesses': [], 'semantic_equivalence_proven': False,
            'equal_abi_expressiveness_proven': False, 'llm_understanding_proven': False}):
            report = BuilderE2ERunner(ROOT / 'e2e/builder/development/semantic-coverage/suite.yaml',
                repo_root=ROOT, output_root=tmp_path, run_id='offline-inspection').run()
    assert report['summary']['passed'] == 1
