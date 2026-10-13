import copy
import json

import pytest
from jsonschema import ValidationError

from adaos.services.builder.semantic_admission import SemanticAdmissionService, ADMISSION_SCHEMA, PROPOSAL_SCHEMA
from adaos.services.builder.semantic_graph import digest
from adaos.services.builder.workflow import BuilderWorkflowError
from test_builder_semantic_inspector import graph_project  # noqa: F401


def begin(graph_project, **kwargs):
    graph, _workflow, _root, _document = graph_project
    service = SemanticAdmissionService(graph)
    packet = service.begin(project_ref='scenario:semantic_labels', instruction='Place state before name',
        source_message_id='source.message', write_refs=['view:work-list'], **kwargs)
    return service, packet


def proposal(graph_project):
    _graph, _workflow, _root, document = graph_project
    view = next(row for row in document['views'] if row['id'] == 'work-list')
    return {'schema': PROPOSAL_SCHEMA, 'status': 'proposed', 'message': '',
        'ops': [{'kind': 'view.fields.reorder', 'ref': 'view:work-list', 'field_refs': list(reversed(view['field_refs']))}]}


def test_admission_contains_descriptors_not_records_and_binds_exact_base(graph_project):
    service, packet = begin(graph_project)
    assert packet['schema'] == ADMISSION_SCHEMA and packet['max_ops'] == 1
    assert packet['base_hash'] == digest(graph_project[3])
    assert packet['source_revision'] == '001' and packet['compiler_digest'].startswith('sha256:')
    assert [row['ref'] for row in packet['write_masks']] == ['view:work-list']
    assert 'records' not in packet['context'] and 'semantic_document' not in packet['context']
    assert service.begin(project_ref='scenario:semantic_labels', instruction='Place state before name',
        source_message_id='source.message', write_refs=['view:work-list']) == packet


def test_model_never_assigns_generation_preconditions_or_authority(graph_project):
    service, packet = begin(graph_project)
    prepared = service.prepare(project_ref=packet['project_ref'], admission_id=packet['admission_id'], proposal=proposal(graph_project))
    request = prepared['request']
    assert request['expected_generation'] == packet['expected_generation']
    assert request['source_revision'] == packet['source_revision']
    assert request['ops'][0]['expected'] != request['ops'][0]['field_refs']
    assert request['write_refs'] == ['view:work-list'] and request['ops'][0]['seq'] == 1
    before = copy.deepcopy(graph_project[3])
    result = graph_project[0].apply(request)
    assert result['revision'] == '002'
    assert graph_project[0].apply(request)['replayed']
    after = json.loads((graph_project[2] / 'semantic.webui.json').read_text(encoding='utf-8'))
    expected = copy.deepcopy(before)
    next(row for row in expected['views'] if row['id'] == 'work-list')['field_refs'].reverse()
    assert after == expected


@pytest.mark.parametrize('mutation', ['unknown_ref', 'extra_authority', 'data_operation', 'extra_operation', 'new_field'])
def test_invalid_proposal_cannot_mutate_source(graph_project, mutation):
    service, packet = begin(graph_project)
    value = proposal(graph_project)
    if mutation == 'unknown_ref': value['ops'][0]['ref'] = 'view:people-list'
    if mutation == 'extra_authority': value['expected_generation'] = 999
    if mutation == 'data_operation': value['ops'][0]['kind'] = 'resource.records.replace'
    if mutation == 'extra_operation': value['ops'].append(copy.deepcopy(value['ops'][0]))
    if mutation == 'new_field': value['ops'][0]['field_refs'][0] = 'new-field'
    before = (graph_project[2] / 'semantic.webui.json').read_bytes()
    with pytest.raises((BuilderWorkflowError, ValidationError)):
        service.prepare(project_ref=packet['project_ref'], admission_id=packet['admission_id'], proposal=value)
    assert (graph_project[2] / 'semantic.webui.json').read_bytes() == before
    assert graph_project[1].current_prototype_revision('scenario', 'semantic_labels') == '001'


def test_wrong_operation_family_is_rejected_even_for_an_admitted_ref(graph_project):
    service, packet = begin(graph_project, operation_kinds=['label.set'])
    with pytest.raises(BuilderWorkflowError, match='property write set'):
        service.prepare(project_ref=packet['project_ref'], admission_id=packet['admission_id'], proposal=proposal(graph_project))


@pytest.mark.parametrize('drift', ['generation', 'source', 'locale', 'locale_missing'])
def test_stale_or_drifted_admission_never_rebases_implicitly(graph_project, drift):
    service, packet = begin(graph_project)
    _graph, workflow, root, document = graph_project
    if drift == 'generation':
        workflow.transition('scenario', root.name, 'prototype_revision_recorded', metadata={'revision': '001'})
    elif drift == 'source':
        document['views'][0]['title']['en'] = 'Changed outside the admitted base'
        (root / 'semantic.webui.json').write_text(json.dumps(document), encoding='utf-8')
    elif drift == 'locale':
        (root / 'assets/i18n/en.json').write_text('{}', encoding='utf-8')
    else:
        (root / 'assets/i18n/en.json').unlink()
    with pytest.raises(BuilderWorkflowError):
        service.prepare(project_ref=packet['project_ref'], admission_id=packet['admission_id'], proposal=proposal(graph_project))
    if drift == 'locale_missing': assert not (root / 'assets/i18n/en.json').exists()


def test_conflicting_source_message_and_proposal_fail_closed(graph_project):
    service, packet = begin(graph_project)
    with pytest.raises(BuilderWorkflowError, match='conflicts'):
        service.begin(project_ref=packet['project_ref'], instruction='A different request', source_message_id='source.message', write_refs=['view:work-list'])
    value = proposal(graph_project)
    service.prepare(project_ref=packet['project_ref'], admission_id=packet['admission_id'], proposal=value)
    value['message'] = 'A different proposal'
    with pytest.raises(BuilderWorkflowError, match='conflicting proposal'):
        service.prepare(project_ref=packet['project_ref'], admission_id=packet['admission_id'], proposal=value)


def test_prepared_request_cannot_escape_admission_by_changing_payload(graph_project):
    service, packet = begin(graph_project)
    prepared = service.prepare(project_ref=packet['project_ref'], admission_id=packet['admission_id'], proposal=proposal(graph_project))
    request = prepared['request']
    request['ops'][0]['field_refs'].reverse()
    with pytest.raises(BuilderWorkflowError, match='Core-issued admission'):
        graph_project[0].apply(request)


@pytest.mark.parametrize('status', ['clarification', 'unsupported'])
def test_nonproposal_outcome_is_nonmutating(graph_project, status):
    service, packet = begin(graph_project)
    result = service.prepare(project_ref=packet['project_ref'], admission_id=packet['admission_id'],
        proposal={'schema': PROPOSAL_SCHEMA, 'status': status, 'message': 'Specify the target', 'ops': []})
    assert result['status'] == status
    assert graph_project[1].current_prototype_revision('scenario', 'semantic_labels') == '001'


def test_missing_locale_is_a_gate_for_both_deterministic_and_model_admission(graph_project):
    graph, _workflow, root, document = graph_project
    (root / 'assets/i18n/en.json').unlink()
    title = document['views'][0]['title']['en']
    with pytest.raises(BuilderWorkflowError, match='required locale'):
        graph.prepare_request(project_ref='scenario:semantic_labels', instruction=f'Rename view "{title}" to "Other" in English', source_message_id='real-message')
    with pytest.raises(BuilderWorkflowError, match='required locale'):
        begin(graph_project)
    assert not (root / 'assets/i18n/en.json').exists()


def test_full_document_reference_has_the_same_exact_write_scope(graph_project):
    service, packet = begin(graph_project)
    document = copy.deepcopy(graph_project[3])
    next(row for row in document['views'] if row['id'] == 'work-list')['field_refs'].reverse()
    planned = service.prepare_document(project_ref=packet['project_ref'], admission_id=packet['admission_id'],
        candidate={'status': 'proposed', 'message': '', 'semantic_document': document})
    assert planned['request']['write_refs'] == ['view:work-list']
    assert graph_project[0].apply(planned['request'])['revision'] == '002'


def test_full_document_cannot_hide_a_record_change(graph_project):
    service, packet = begin(graph_project)
    document = copy.deepcopy(graph_project[3])
    next(row for row in document['views'] if row['id'] == 'work-list')['field_refs'].reverse()
    document['resources'][0]['records'].clear()
    with pytest.raises(BuilderWorkflowError, match='outside its admitted'):
        service.prepare_document(project_ref=packet['project_ref'], admission_id=packet['admission_id'],
            candidate={'status': 'proposed', 'message': '', 'semantic_document': document})
    assert graph_project[1].current_prototype_revision('scenario', 'semantic_labels') == '001'
