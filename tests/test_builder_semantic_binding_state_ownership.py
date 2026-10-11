from adaos.services.builder.semantic_bindings import close_bindings


def document(state_view='tickets'):
    return {'views': [
        {'id': 'tickets', 'role': 'collection', 'resource_ref': 'tickets', 'query_controls': [
            {'id': 'status', 'kind': 'filter'}, {'id': 'search', 'kind': 'search'}]},
        {'id': 'other', 'role': 'collection', 'resource_ref': 'other', 'query_controls': [
            {'id': 'other_filter', 'kind': 'filter'}]}],
        'commands': [], 'representative_states': [{'id': 'no_results', 'view_ref': state_view}],
        'requirement_bindings': [{'requirement_ref': 'operation:filter', 'semantic_refs': ['state:no_results']}]}


def test_query_state_binding_closes_only_declared_view_ownership():
    value=document()
    close_bindings(value, {'operations': [{'id': 'operation:filter', 'kind': 'filter'}]})
    assert set(value['requirement_bindings'][0]['semantic_refs']) == {'state:no_results','view:tickets','resource:tickets','query:status'}


def test_unknown_state_view_cannot_infer_a_query_from_other_views():
    value=document('missing')
    close_bindings(value, {'operations': [{'id': 'operation:filter', 'kind': 'filter'}]})
    assert value['requirement_bindings'][0]['semantic_refs'] == ['state:no_results']


def test_state_ownership_does_not_repair_fixtures_or_change_proof():
    value=document()
    value['representative_states'][0]['proof']={'kind':'query_empty','visible_field_refs':[]}
    original=dict(value['representative_states'][0])
    close_bindings(value, {'operations': [{'id': 'operation:filter', 'kind': 'filter'}]})
    assert value['representative_states'][0] == original
