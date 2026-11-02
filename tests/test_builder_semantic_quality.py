import copy

import pytest

from adaos.services.builder_intent import capture_intent, compile_prototype_brief
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder.task_interpretation import admit_interpretation
from adaos.services.builder.prototype_context import prototype_model_requirement_inventory, compile_prototype_model_context
from adaos.services.builder.semantic_prototype import compile_semantic_prototype_candidate, SemanticPrototypeValidationError
from adaos.services.builder.semantic_quality import responsive_quality_obligations
from test_builder_task_interpretation import prepared
from test_builder_semantic_construction import constructed


def quality_task(text):
    old_brief, candidate = constructed()
    old_intent, _, proposal = prepared()
    intent = capture_intent(old_intent['statement'] + '\n' + text, project_ref=old_intent['scope']['project_ref'])
    task = compile_task_spec(intent, compile_prototype_brief(intent))
    proposal['source_digest'] = intent['digest']
    proposal['clause_roles'].append({'clause_id': 'clause:005', 'roles': ['product', 'verification']})
    proposal['requirements'].append({'id': 'responsive', 'clause_ids': ['clause:005'], 'statement': text,
        'kind': 'ui', 'operation': None, 'verification_family': 'layout', 'supersedes': []})
    proposal['constraints'].append({'kind': 'viewport', 'clause_ids': ['clause:005'], 'subject': '', 'values': ['390']})
    _, brief = admit_interpretation(task, proposal, intent)
    by_statement = {row['statement']: row['id'] for row in brief['task_contract']['requirements']}
    aliases = {row['id']: by_statement[row['statement']] for row in old_brief['task_contract']['requirements']
               if row['statement'] in by_statement}
    for binding in candidate['requirement_bindings']:
        binding['requirement_ref'] = aliases[binding['requirement_ref']]
    candidate['commands'][0]['transition_ref'] = aliases[candidate['commands'][0]['transition_ref']]
    return brief, candidate


@pytest.mark.parametrize('text', [
    'На ширине 390 px все разделы, поиск и команда должны оставаться доступными без горизонтального переполнения.',
    'At width 390px all tabs, search and actions must remain available without horizontal overflow.',
])
def test_pure_responsive_obligation_is_preserved_for_runtime_not_fake_widget_evidence(text):
    brief, candidate = quality_task(text)
    before = copy.deepcopy(brief)
    obligation, = responsive_quality_obligations(brief)
    reference = obligation['id']
    assert reference not in {row['id'] for row in prototype_model_requirement_inventory(brief)}
    context = compile_prototype_model_context(brief, compact=True)
    assert obligation in context['process_constraints']
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    receipt = compiled['task_acceptance']
    assert receipt['status'] == 'runtime_acceptance_pending'
    assert any(row['requirement_ref'] == reference and row['status'] == 'not_run' for row in receipt['runtime_checks'])
    assert brief == before and any(row['id'] == reference for row in brief['task_contract']['requirements'])
    # Old harmless witnesses remain compatible but still cannot certify runtime.
    candidate['views'][0]['fulfills'] = [reference]
    retained = compile_semantic_prototype_candidate(candidate, brief=brief)
    assert retained['task_acceptance']['status'] == 'runtime_acceptance_pending'


@pytest.mark.parametrize('text', [
    'На ширине 390 px покажи отдельную кнопку.',
    'At width 390px all new drawers must remain available without horizontal overflow.',
    'At width 390px all controls must remain usable without horizontal overflow. Add a navigation button.',
])
def test_new_or_mixed_product_ui_obligations_cannot_disappear_into_quality(text):
    brief, candidate = quality_task(text)
    assert responsive_quality_obligations(brief) == []
    with pytest.raises(SemanticPrototypeValidationError) as error:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    assert any(row['code'] == 'requirement.coverage_missing' for row in error.value.findings)


def test_historical_clause_ids_cannot_reclassify_unrelated_current_source_requirements():
    brief, _ = quality_task('At width 390px all controls must remain usable without horizontal overflow.')
    original = responsive_quality_obligations(brief)
    assert len(original) == 1
    reference = original[0]['id']
    row = next(row for row in brief['task_contract']['requirements'] if row['id'] == reference)
    current_digest = row['source_digest']
    row['source_digest'] = 'sha256:' + 'f' * 64
    assert responsive_quality_obligations(brief) == []
    brief['task_contract']['derivation'] = {'kind': 'unrelated_history', 'meaning_source_digest': row['source_digest']}
    assert responsive_quality_obligations(brief) == []
    brief['task_contract']['derivation']['kind'] = 'exact_source_replay'
    assert responsive_quality_obligations(brief) == original
    row['source_digest'] = current_digest
    brief['task_contract']['constraints'] = []
    assert responsive_quality_obligations(brief) == []
