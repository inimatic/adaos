import copy

import pytest

from adaos.services.builder.task_interpretation import admit_interpretation
from adaos.services.builder.task_preservation import fixture_write_scope, fixture_revision_findings
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder_intent import capture_intent, compile_prototype_brief
from test_builder_task_assertions import history, amendment
from test_builder_task_acceptance import fixture


def prepared_change():
    before = history()
    text = 'Сервер RU: CPU 65.\nОстальные данные, UI и поведение сохрани.'
    _, _, proposal, _ = amendment(before)
    intent = capture_intent(text, project_ref='project:qualification')
    task = compile_task_spec(intent, compile_prototype_brief(intent))
    proposal['source_digest'] = intent['digest']
    proposal['requirements'][0].update(statement='Сервер RU: CPU 65.', kind='data_state')
    proposal['facts'][0]['record_evidence'] = 'Сервер RU: CPU 65.'
    proposal['clause_roles'].append({'clause_id':'clause:002','roles':['verification']})
    proposal['requirements'].append({'id':'preserve','clause_ids':['clause:002'],
        'statement':'Остальные данные, UI и поведение сохрани.', 'kind':'verification','operation':None,
        'verification_family':'preservation','supersedes':[]})
    _, brief = admit_interpretation(task, proposal, intent, previous_brief=before)
    _, previous = fixture()
    previous['resources'][0]['records'][0]['id'] = 'RU'
    return previous, brief


def changed(previous):
    result = copy.deepcopy(previous)
    result['resources'][0]['records'][0]['cpu'] = 65
    return result


def test_core_derives_one_fixture_write_and_keeps_idempotent_new_value():
    previous, brief = prepared_change()
    snapshot = copy.deepcopy(previous)
    scope = fixture_write_scope(previous, brief)
    assert len(scope['writes']) == 1
    assert scope['writes'][0]['field_ref'] == 'cpu'
    assert scope['writes'][0]['before'] == 92 and scope['writes'][0]['after'] == 65
    assert fixture_revision_findings(previous, changed(previous), brief)[0] == []
    assert fixture_revision_findings(changed(previous), changed(previous), brief)[0] == []
    assert previous == snapshot


@pytest.mark.parametrize('mutation', ['guard','title','states','records','order'])
def test_literal_amendment_cannot_change_other_semantic_properties(mutation):
    previous, brief = prepared_change()
    current = changed(previous)
    if mutation == 'guard':current['commands'][0]['guard'] = {'kind':'invented'}
    elif mutation == 'title':current['views'][0]['section']['label']['en'] = 'Changed'
    elif mutation == 'states':current['representative_states'] = [{'id':'new'}]
    elif mutation == 'records':current['resources'][0]['records'][0]['disk'] = 20
    elif mutation == 'order':current['views'].reverse()
    findings, scope = fixture_revision_findings(previous,current,brief)
    assert scope and findings and all(row['code'] == 'semantic.task_write_scope_exceeded' for row in findings)


def test_broader_ui_work_cannot_claim_a_fixture_only_proof():
    previous, brief = prepared_change()
    current_rows = [row for row in brief['task_contract']['requirements'] if row['source_digest'] == brief['intent_digest'] and row['kind']=='data_state']
    current_rows[0]['kind'] = 'ui'
    assert fixture_write_scope(previous,brief) is None


def test_ambiguous_or_stale_fixture_scope_fails_before_generation():
    from adaos.services.builder.task_interpretation import TaskInterpretationRejected
    previous, brief = prepared_change()
    previous['resources'][0]['records'][0]['cpu'] = 42
    with pytest.raises(TaskInterpretationRejected) as error:fixture_write_scope(previous,brief)
    assert error.value.findings[0]['code']=='task.fixture_scope_stale'


def test_sdk_candidate_compilation_cannot_bypass_fixture_preservation(monkeypatch):
    from adaos.sdk.builder import prototype
    from adaos.services.builder import semantic_prototype
    from adaos.services.builder.semantic_revision import SemanticRevisionError
    previous, brief = prepared_change()
    current = changed(previous)
    current['commands'][0]['guard'] = {'kind':'invented'}
    monkeypatch.setattr(semantic_prototype,'compile_semantic_prototype_candidate',lambda *args,**kwargs:{'semantic_document':current})
    with pytest.raises(SemanticRevisionError):prototype.compile_semantic_candidate({},brief=brief,previous=previous)
