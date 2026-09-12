import copy

import pytest

from adaos.services.builder.semantic_prototype import compile_semantic_prototype_candidate
from adaos.services.builder.workflow import BuilderWorkflowError
from test_builder_semantic_prototype import _multi_resource_candidate, _multi_resource_fixture, _text


def scoped_fixture():
    brief, semantic = _multi_resource_fixture()
    resource = semantic['resources'][0]
    resource['fields'].append({'id': 'flag', 'value_type': 'boolean', 'label': _text('flag', 'Flag', 'Flag'),
                               'required': True, 'editable': True})
    for index, record in enumerate(resource['records']):
        record['flag'] = index == 0
    view = copy.deepcopy(semantic['views'][0])
    view.update(id='scoped', scope_filters=[{'field_ref': 'flag', 'value': False}],
                query_controls=[{'id': 'search-scoped', 'kind': 'search', 'field_ref': None, 'label': _text('search', 'Search', 'Search')}])
    semantic['views'].append(view)
    semantic['representative_states'].append({
        'id': 'scoped-records', 'label': _text('scope-state', 'Scoped records', 'Scoped records'),
        'view_ref': 'scoped', 'proof': {'kind': 'collection_items', 'visible_field_refs': ['title']},
        'filters': [], 'min_items': 1, 'max_items': 1,
    })
    return brief, semantic


def test_scope_is_literal_and_separate_from_resettable_controls_and_state_evidence():
    brief, semantic = scoped_fixture()
    result = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    page = result['webui']['ui']['application']['desktop']['pageSchema']
    collection = next(widget for widget in page['widgets'] if widget['id'] == 'scoped')
    assert collection['dataSource']['query']['filters'] == {'flag': False}
    toolbar = next(widget for widget in page['widgets'] if widget['id'] == 'queries-scoped')
    assert len(toolbar['inputs']['controls']) == 1
    assert toolbar['inputs']['controls'][0]['kind'] == 'search'
    check = next(check for check in result['representative_state_checks'] if check['state_id'] == 'scoped-records')
    assert check['matching_record_ids'] == [semantic['resources'][0]['records'][1]['id']]
    assert any('.dataSource.query.filters.flag' in ref for ref in result['source_map']['field:flag'])
    assert result['semantic_document']['representative_states'][-1]['filters'] == []


def test_scope_cannot_claim_records_outside_its_selection():
    brief, semantic = scoped_fixture()
    semantic['representative_states'][-1].update(min_items=2, max_items=2)
    with pytest.raises(BuilderWorkflowError, match='found 1'):
        compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)


@pytest.mark.parametrize('defect', ['duplicate', 'type', 'overlap', 'missing'])
def test_invalid_scope_is_reported_not_silently_overridden(defect):
    brief, semantic = scoped_fixture()
    view = semantic['views'][-1]
    if defect == 'duplicate':
        view['scope_filters'].append({'field_ref': 'flag', 'value': True})
    elif defect == 'type':
        view['scope_filters'][0]['value'] = 'false'
    elif defect == 'missing':
        view['scope_filters'][0]['field_ref'] = 'absent'
    else:
        view['query_controls'].append({'id': 'flag-filter', 'kind': 'filter', 'field_ref': 'flag',
                                       'label': _text('flag-filter', 'Flag', 'Flag')})
    with pytest.raises(BuilderWorkflowError, match='scope_filters'):
        compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
