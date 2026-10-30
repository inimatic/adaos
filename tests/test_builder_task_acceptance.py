import copy

import pytest

from adaos.services.builder.task_acceptance import task_candidate_findings, task_acceptance_receipt
from adaos.services.builder.task_interpretation import admit_interpretation
from test_builder_task_interpretation import prepared


def fixture():
    intent, task, proposal = prepared()
    _, brief = admit_interpretation(task, proposal, intent)
    transition = brief['task_contract']['transitions'][0]
    document = {'title': {'ru': 'Инфраструктура'}, 'resources': [
        {'id': 'servers', 'fields': [{'id': 'name', 'label': {'ru': 'Имя'}, 'value_type': 'short_text'},
            {'id': 'cpu', 'label': {'en': 'CPU %'}, 'value_type': 'number'},
            {'id': 'disk', 'label': {'en': 'Disk %'}, 'value_type': 'number'}],
         'records': [{'id': 'ru', 'name': 'RU', 'cpu': 92, 'disk': 78}]},
        {'id': 'incidents', 'fields': [{'id': 'status', 'label': {'ru': 'Статус'}, 'value_type': 'choice',
            'options': [{'value': 'open', 'label': {'en': 'open'}}, {'value': 'accepted', 'label': {'en': 'accepted'}}]}],
         'records': [{'id': 'incident-1', 'status': 'open'}]}],
        'views': [{'id': 'servers', 'resource_ref': 'servers', 'role': 'collection',
                   'section': {'id': 'backups', 'label': {'ru': 'Копии'}}},
                  {'id': 'incident-editor', 'resource_ref': 'incidents', 'role': 'editor'}],
        'commands': [{'id': 'accept', 'kind': 'transition', 'label': {'ru': 'Принять'}, 'view_ref': 'incident-editor',
                      'fixed_values': {'status': 'accepted'}, 'input_field_refs': []}],
        'requirement_bindings': [{'requirement_ref': transition['requirement_id'], 'semantic_refs': ['command:accept']}],
        'capability_surfaces': []}
    return brief, document


def test_static_source_acceptance_does_not_claim_browser_acceptance():
    brief, doc = fixture()
    assert task_candidate_findings(doc, brief) == []
    receipt = task_acceptance_receipt(doc, brief)
    assert receipt['static_status'] == 'passed'
    assert receipt['status'] == 'runtime_acceptance_pending'
    assert all(row['status'] == 'not_run' for row in receipt['runtime_checks'])


@pytest.mark.parametrize('fault,code', [
    ('cpu', 'fixture_value_mismatch'), ('bool_cpu', 'fixture_value_mismatch'),
    ('effect', 'transition_effect_mismatch'), ('other_field', 'transition_preservation_mismatch'),
    ('section', 'section_title_mismatch'), ('dependency', 'external_dependency_forbidden'),
    ('duplicate_record', 'fixture_subject_unresolved'),
])
def test_mutations_rejected_even_when_model_authored_bindings_remain_intact(fault, code):
    brief, doc = fixture()
    if fault == 'cpu': doc['resources'][0]['records'][0]['cpu'] = 0
    elif fault == 'bool_cpu': doc['resources'][0]['records'][0]['cpu'] = True
    elif fault == 'effect': doc['commands'][0]['fixed_values']['status'] = 'open'
    elif fault == 'other_field': doc['commands'][0]['fixed_values']['owner'] = 'anyone'
    elif fault == 'section': doc['views'][0]['section']['label']['ru'] = 'Другое'
    elif fault == 'dependency': doc['capability_surfaces'].append({'id': 'invented-site'})
    elif fault == 'duplicate_record': doc['resources'][0]['records'].append(copy.deepcopy(doc['resources'][0]['records'][0]))
    findings = task_candidate_findings(doc, brief)
    assert any(code in row['code'] for row in findings)


def test_a_foreign_key_value_cannot_retarget_an_unrelated_resource_fact():
    brief, doc = fixture()
    doc['resources'][1]['records'][0]['server_ref'] = 'RU'
    assert task_candidate_findings(doc, brief) == []


def test_search_scope_can_identify_a_named_section_without_renaming_its_collection():
    brief, doc = fixture()
    brief['task_contract']['constraints'].append({'kind': 'search_fields', 'subject': 'Копии',
        'values': ['name'], 'clause_ids': ['clause:004']})
    doc['views'][0]['title'] = {'ru': 'Серверы'}
    doc['views'][0]['query_controls'] = [{'kind': 'search', 'field_ref': 'name'}]
    assert task_candidate_findings(doc, brief) == []
    doc['views'][0]['query_controls'][0]['field_ref'] = 'cpu'
    assert any(row['code'] == 'task.query_scope_mismatch' for row in task_candidate_findings(doc, brief))


def test_disclosure_in_metadata_is_not_a_visible_ui_explanation():
    brief, doc = fixture()
    brief['task_contract']['constraints'].append({'kind': 'visible_text', 'subject': '',
        'values': ['Демонстрационные данные'], 'clause_ids': ['clause:004']})
    doc['title']['ru'] += ' · Демонстрационные данные'
    assert any(row['code'] == 'task.disclosure_text_missing' for row in task_candidate_findings(doc, brief))
    doc['views'][0]['title'] = {'ru': 'Демонстрационные данные'}
    assert task_candidate_findings(doc, brief) == []
