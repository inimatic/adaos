import copy

import pytest

from adaos.services.builder.task_spec import active_requirements, compile_task_spec, source_clauses
from adaos.services.builder_intent import capture_intent, compile_prototype_brief


def task(text, **kwargs):
    intent = capture_intent(text, project_ref="project:qualification", change_ref="change:one")
    return compile_task_spec(intent, compile_prototype_brief(intent), **kwargs)


def test_source_projection_preserves_wrapped_sentences_code_numbers_and_blank_lines():
    text = '1. CPU 92.5; https://example.test/v1\n\nНе меняй `x.y`.\r\nЕщё строка'
    clauses = source_clauses(text)
    assert ''.join(row['text'] for row in clauses) == text
    assert all(text[row['start']:row['end']] == row['text'] for row in clauses)


def test_unextracted_is_not_reported_as_missing_user_information():
    value = task('Оператор видит три сервера. RU CPU 92, disk 78.')
    assert value['knowledge']['actors']['state'] == 'unextracted'
    assert value['interpretation_status'] == 'proposed'
    assert not value['questions']


def test_tampered_digest_or_mismatched_brief_is_refused_before_execution():
    intent = capture_intent('Show servers')
    brief = compile_prototype_brief(intent)
    intent['statement'] = 'Delete servers'
    with pytest.raises(ValueError, match='identity'):
        compile_task_spec(intent, brief)
    other = capture_intent('Show incidents')
    with pytest.raises(ValueError, match='disagree'):
        compile_task_spec(other, brief)


def test_explicit_supersession_preserves_unmodified_requirements_and_evidence():
    before = task('Search all fields. Show server details.')
    combined = task('Search only by name.', previous=before)
    old = next(row for row in combined['requirements'] if row['statement'] == 'Search all fields')
    new = next(row for row in combined['requirements'] if row['source_digest'] != before['source']['digest'])
    after = task('Search only by name.', previous=before, amendments=[{
        'requirement_id': old['id'], 'status': 'superseded',
        'replacement_id': new['id'], 'source_ref': 'intent:correction'}])
    active = active_requirements(after)
    assert old['id'] not in {row['id'] for row in active}
    assert any(row['statement'] == 'Show server details' for row in active)
    assert next(row for row in after['requirements'] if row['id'] == old['id'])['status'] == 'superseded'
    assert before == task('Search all fields. Show server details.')


def test_conflict_is_visible_and_cannot_be_used_as_generation_authority():
    before = task('Show exactly three views.')
    ref = before['requirements'][0]['id']
    after = task('Show exactly two views.', previous=before, amendments=[{
        'requirement_id': ref, 'status': 'conflicting', 'source_ref': 'intent:correction'}])
    with pytest.raises(ValueError, match='Conflicting'):
        active_requirements(after)


def test_scope_and_mutable_history_cannot_silently_retarget_task():
    before = task('Show servers')
    tampered = copy.deepcopy(before)
    tampered['requirements'][0]['statement'] = 'Delete servers'
    with pytest.raises(ValueError, match='identity'):
        task('Show incidents', previous=tampered)
    intent = capture_intent('Show incidents', project_ref='project:other')
    with pytest.raises(ValueError, match='different'):
        compile_task_spec(intent, compile_prototype_brief(intent), previous=before)


def test_capability_selection_reuses_the_admitted_brief_without_reinterpreting(monkeypatch):
    from adaos.services import ui_capabilities
    intent = capture_intent('List servers and edit their names.')
    brief = compile_prototype_brief(intent)
    monkeypatch.setattr(ui_capabilities, 'capture_intent', lambda *_: pytest.fail('Second interpretation'))
    monkeypatch.setattr(ui_capabilities, 'compile_prototype_brief', lambda *_: pytest.fail('Second Brief'))
    result = ui_capabilities.selected_ui_capabilities(intent['statement'], intent=intent, prototype_brief=brief)
    assert result['qualification']['prototype_brief'] == brief
    assert result['qualification']['requirements']['prototype_brief_ref'] == brief['brief_id']
    with pytest.raises(ValueError, match='differs'):
        ui_capabilities.qualify_ui_request('Delete servers', intent=intent, prototype_brief=brief)


def test_model_schema_refs_match_model_context_and_exclude_core_restored_aliases():
    from adaos.sdk.builder import prototype
    intent = capture_intent('List servers and edit their names.')
    brief = compile_prototype_brief(intent)
    context = prototype.model_context(brief, compact=True)
    contract = prototype.semantic_provider_contract(version='v2', locales=('en',), brief=brief)
    expected = {item['id'] for item in context['required_references']}
    assert set(contract['$defs']['requirementBinding']['properties']['requirement_ref']['enum']) == expected
    assert set(contract['$defs']['capabilityGap']['properties']['requirement_ref']['enum']) == expected


def test_source_admission_keeps_explicit_scenario_scope_and_refuses_retargeting():
    from adaos.services.builder.task_pipeline import admit_task_source
    metadata = {'builder_selected_target': {'object_type': 'scenario', 'object_id': 'qualification'}}
    original = admit_task_source('Show servers', metadata=metadata)
    assert original['task_spec']['scope']['project_ref'] == 'scenario:qualification'
    metadata['builder_selected_target']['object_id'] = 'other'
    metadata['prototype_intent'] = original['intent']
    with pytest.raises(ValueError, match='different selected target'):
        admit_task_source('Show servers', metadata=metadata)
