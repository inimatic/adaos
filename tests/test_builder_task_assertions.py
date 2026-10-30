import copy

import pytest

from adaos.services.builder.task_assertions import assertion_ledger
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder.task_interpretation import admit_interpretation, TaskInterpretationRejected, interpretation_messages
from adaos.services.builder_intent import capture_intent, compile_prototype_brief
from test_builder_task_interpretation import prepared


def history():
    intent, task, proposal = prepared()
    return admit_interpretation(task, proposal, intent)[1]


def amendment(brief, *, supersede=True, value=65):
    text = f'Теперь сервер RU: CPU {value}.'
    intent = capture_intent(text, project_ref='project:qualification')
    task = compile_task_spec(intent, compile_prototype_brief(intent))
    assertion = next(row for row in assertion_ledger(brief['task_contract'])
                     if row['kind'] == 'assertion' and row['assertion_group'] == 'facts'
                     and row['assertion']['field'] == 'CPU')
    proposal = {'schema': 'adaos.builder.task_interpretation.v1', 'source_digest': intent['digest'],
        'clause_roles': [{'clause_id': 'clause:001', 'roles': ['product', 'fact']}],
        'requirements': [{'id': 'correct-cpu', 'clause_ids': ['clause:001'], 'statement': text,
            'kind': 'information', 'operation': None, 'verification_family': 'value',
            'supersedes': [assertion['id']] if supersede else []}],
        'facts': [{'clause_ids': ['clause:001'], 'subject': 'RU', 'source_field': 'CPU', 'field': 'CPU',
            'value': value, 'record_evidence': text, 'field_evidence': f'CPU {value}'}],
        'transitions': [], 'constraints': [], 'questions': [], 'design_freedom': []}
    return intent, task, proposal, assertion


def test_exact_literal_amendment_retires_old_value_without_losing_other_assertions():
    before = history()
    snapshot = copy.deepcopy(before)
    intent, task, proposal, old = amendment(before)
    _, after = admit_interpretation(task, proposal, intent, previous_brief=before)
    contract = after['task_contract']
    assert [row['value'] for row in contract['facts'] if row['field'] == 'CPU'] == [65]
    assert next(row for row in contract['facts'] if row['field'] == 'disk')['value'] == 78
    assert next(row for row in contract['requirements'] if row['id'] == old['id'])['status'] == 'superseded'
    assert contract['transitions'] == before['task_contract']['transitions']
    assert contract['constraints'] == before['task_contract']['constraints']
    assert before == snapshot


def test_new_expected_value_without_explicit_assertion_supersession_is_a_conflict():
    before = history()
    intent, task, proposal, _ = amendment(before, supersede=False)
    with pytest.raises(TaskInterpretationRejected) as error:
        admit_interpretation(task, proposal, intent, previous_brief=before)
    assert error.value.findings[0]['code'] == 'task.assertion_conflict'


def test_repeated_unchanged_assertion_keeps_original_identity_and_provenance():
    before = history()
    intent, task, proposal, old = amendment(before, value=92, supersede=False)
    _, after = admit_interpretation(task, proposal, intent, previous_brief=before)
    facts = [row for row in after['task_contract']['facts'] if row['field'] == 'CPU']
    assert len(facts) == 1 and facts[0]['assertion_ref'] == old['id']
    assert facts[0]['source_digest'] == before['intent_digest']


def test_prior_literal_ids_and_values_are_present_before_interpretation_not_reconstructed_after_it():
    import json
    before = history()
    intent, task, _, old = amendment(before)
    messages = interpretation_messages(task, previous_brief=before)
    request = json.loads(messages[1]['content'])['interpretation_request']
    projected = next(row for row in request['prior_literal_assertions'] if row['id'] == old['id'])
    assert projected['source_digest'] == old['source_digest']
    assert projected['group'] == old['assertion_group']
    assert projected['assertion'] == {key:value for key,value in old['assertion'].items()
        if key not in {'clause_ids','record_evidence','field_evidence'}}
    assert 'statement' not in projected and 'record_evidence' not in projected['assertion']
    assert request['history_admission']['example']['correcting_requirement']['supersedes']
    assert all(row['kind'] != 'assertion' for row in request['prior_active_requirements'])


def test_provider_grammar_allows_only_active_retained_history_ids():
    from jsonschema import Draft202012Validator
    from adaos.services.builder.task_interpretation import interpretation_contract
    before = history()
    _, task, proposal, _ = amendment(before)
    validator = Draft202012Validator(interpretation_contract(provider=True, task=task, previous_brief=before))
    validator.validate(proposal)
    proposal['requirements'][0]['supersedes'] = ['assertion:invented']
    assert list(validator.iter_errors(proposal))
    without_history = Draft202012Validator(interpretation_contract(provider=True, task=task))
    assert list(without_history.iter_errors(proposal))


def test_admitted_history_cannot_cross_projects():
    before = history()
    intent, task, proposal = prepared()
    from adaos.services.builder.task_spec import seal
    intent = seal({**intent, 'scope': {'project_ref': 'project:other', 'change_ref': None}}, id_key='intent_id', prefix='intent:')
    task = compile_task_spec(intent, compile_prototype_brief(intent))
    proposal['source_digest'] = intent['digest']
    with pytest.raises(TaskInterpretationRejected) as error:
        admit_interpretation(task, proposal, intent, previous_brief=before)
    assert error.value.findings[0]['code'] == 'task.history_scope_conflict'
