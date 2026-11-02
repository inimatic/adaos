"""Owner preservation controls independent of model and browser rendering."""
import copy

import pytest

from adaos.services.builder.semantic_prototype import (
    compile_semantic_prototype_candidate, SemanticPrototypeValidationError,
)
from adaos.services.builder.semantic_graph_model import (
    semantic_document_to_graph, semantic_graph_dependency_closure,
)
from test_builder_semantic_construction import constructed


def two_collections():
    brief, candidate = constructed()
    original = next(view for view in candidate['views'] if view['id'] == 'incident-list')
    other = copy.deepcopy(original)
    other.update(id='incident-secondary', title={'ru': 'Secondary incidents'})
    candidate['views'].append(other)
    candidate['commands'][0].update(kind='source_transition_context', view_ref=other['id'])
    return brief, candidate


def opener_owners(compiled):
    return [widget['id'] for widget in compiled['webui']['ui']['application']['desktop']['pageSchema']['widgets']
            if any(button['id'] == 'accept' for button in widget.get('inputs', {}).get('buttons', []))]


@pytest.mark.parametrize('reverse', [False, True])
def test_source_context_preserves_the_exact_collection_independent_of_declaration_order(reverse):
    brief, candidate = two_collections()
    if reverse:
        candidate['views'].reverse()
    before = copy.deepcopy(candidate)
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    assert candidate == before
    assert opener_owners(compiled) == ['incident-secondary']
    document = compiled['semantic_document']
    assert document['commands'][0]['exposure']['collection_view_ref'] == 'incident-secondary'
    graph = semantic_document_to_graph(document)
    assert 'view:incident-secondary' in semantic_graph_dependency_closure(graph, ['command:accept'])


def test_explicit_context_cannot_be_silently_redirected():
    brief, candidate = two_collections()
    candidate['commands'][0]['exposure']['collection_view_ref'] = 'incident-list'
    with pytest.raises(SemanticPrototypeValidationError) as error:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    assert error.value.findings[0]['code'] == 'semantic.construction_transition_context_conflict'


def test_compact_revision_context_preserves_the_invocation_owner_and_next_compile():
    from adaos.services.builder.prototype_context import compile_semantic_revision_model_context
    brief, candidate = two_collections()
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    packet = compile_semantic_revision_model_context(compiled['semantic_document'], brief,
        source_ref='semantic.webui.json', revision='001')
    assert packet['document']['commands'][0]['exposure']['collection_view_ref'] == 'incident-secondary'
    evolved = compile_semantic_prototype_candidate(candidate, brief=brief, previous=packet)
    assert opener_owners(evolved) == ['incident-secondary']


def test_independent_component_receipt_rejects_a_moved_opener_or_missing_availability_projection():
    from adaos.services.ui_component_contracts import lowering_receipt
    brief, candidate = two_collections()
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    document = compiled['semantic_document']
    for fault in ('owner', 'availability', 'selected_record', 'modal'):
        webui = copy.deepcopy(compiled['webui'])
        widgets = webui['ui']['application']['desktop']['pageSchema']['widgets']
        owner = next(row for row in widgets if row['id'] == 'incident-secondary')
        button = next(row for row in owner['inputs']['buttons'] if row['id'] == 'accept')
        assert button['whenKey'] == 'status' and button['whenEquals'] == 'open'
        if fault == 'owner':
            owner['inputs']['buttons'].remove(button)
            next(row for row in widgets if row['id'] == 'incident-list')['inputs'].setdefault('buttons', []).append(button)
        elif fault == 'availability':
            button.pop('whenKey')
        elif fault == 'selected_record':
            action = next(row for row in owner['actions'] if row['on'] == 'click:accept' and row['type'] == 'updateState')
            action['params'] = {key: 'another-record' for key in action['params']}
        else:
            next(row for row in owner['actions'] if row['on'] == 'click:accept' and row['type'] == 'navigateModal')['params']['modalId'] = 'foreign'
        with pytest.raises(ValueError, match='component.command_'):
            lowering_receipt(document, webui)


def test_ambiguous_legacy_editor_is_not_assigned_to_the_first_collection():
    brief, candidate = two_collections()
    candidate['commands'][0].update(kind='source_transition', view_ref='incident-editor')
    with pytest.raises(SemanticPrototypeValidationError) as error:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    assert any('ambiguous command collection owner' in row['detail'] for row in error.value.findings)


def test_create_header_and_record_opener_can_have_distinct_explicit_collections():
    from test_builder_semantic_prototype import _multi_resource_fixture
    from adaos.services.builder.semantic_prototype import compile_semantic_prototype
    _, document = _multi_resource_fixture()
    other = copy.deepcopy(document['views'][0])
    other['id'] = 'record-inspection'
    document['views'].append(other)
    next(view for view in document['views'] if view['id'] == 'work-editor')['surface'] = 'modal'
    create, update = document['commands']
    create.update(kind='create', exposure={'placement': 'collection_header', 'presentation': 'button',
                                          'collection_view_ref': 'work-list'})
    update['exposure'] = {'placement': 'editor_primary', 'presentation': 'button',
                          'collection_view_ref': other['id']}
    compiled = compile_semantic_prototype(document)
    widgets = {row['id']: row for row in compiled['webui']['ui']['application']['desktop']['pageSchema']['widgets']}
    assert [row['id'] for row in widgets['work-list']['inputs']['headerActions']] == [create['id']]
    assert [row['id'] for row in widgets[other['id']]['inputs']['buttons']] == ['open-record-work-editor']


@pytest.mark.parametrize('ref', ['missing', 'servers', 'incident-editor'])
def test_unknown_wrong_resource_or_noncollection_owner_is_rejected(ref):
    brief, candidate = two_collections()
    candidate['commands'][0].update(kind='source_transition', view_ref='incident-editor')
    candidate['commands'][0]['exposure']['collection_view_ref'] = ref
    with pytest.raises(SemanticPrototypeValidationError) as error:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    assert any(row['code'] == 'semantic.command_exposure_invalid' for row in error.value.findings)
