import copy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from adaos.services.builder.semantic_prototype import semantic_prototype_provider_contract
from adaos.services.builder.semantic_presentations import presentation_findings
from test_builder_semantic_prototype import _multi_resource_fixture, _multi_resource_candidate


def schema_and_view(presentation):
    _, doc = _multi_resource_fixture()
    view = copy.deepcopy(next(v for v in _multi_resource_candidate(doc)['views'] if v['role'] == 'collection'))
    view.update(presentation=presentation, surface='inline', media=None, compact_presentation=None,
                presentation_options=None, field_display=[], section=None, scope_filters=[],
                selection=None, selection_filter=None)
    if presentation in {'board', 'tree', 'chart', 'accordion'}:
        active = {'board': {'group_field_ref'}, 'tree': {'parent_field_ref'},
                  'chart': {'group_field_ref', 'value_field_ref'}, 'accordion': {'group_field_ref'}}[presentation]
        view['presentation_options'] = {name: ('field' if name in active else None)
            for name in ('group_field_ref', 'parent_field_ref', 'value_field_ref', 'order_field_ref')}
        view['presentation_options']['draggable'] = False
    contract = semantic_prototype_provider_contract(version='v2')
    validator = Draft202012Validator({'$ref': '#/$defs/view', '$defs': contract['$defs']})
    validator.validate(view)
    return validator, view


@pytest.mark.parametrize('presentation', ['list', 'table', 'cards', 'board', 'tree', 'chart', 'accordion'])
def test_supported_presentations_remain_expressible(presentation):
    schema_and_view(presentation)


@pytest.mark.parametrize('presentation', ['list', 'cards', 'board', 'tree', 'chart', 'accordion'])
def test_non_table_compact_override_rejected_before_generation(presentation):
    validator, view = schema_and_view(presentation)
    view['compact_presentation'] = 'cards'
    with pytest.raises(ValidationError):
        validator.validate(view)


@pytest.mark.parametrize('presentation', ['board', 'tree', 'chart'])
def test_unrendered_field_display_is_not_advertised(presentation):
    validator, view = schema_and_view(presentation)
    view['field_display'] = [{'field_ref': view['field_refs'][0], 'overflow': 'wrap', 'align': 'start'}]
    with pytest.raises(ValidationError):
        validator.validate(view)


@pytest.mark.parametrize('presentation', ['board', 'tree', 'chart', 'accordion'])
def test_required_presentation_options_cannot_be_omitted(presentation):
    validator, view = schema_and_view(presentation)
    view['presentation_options'] = None
    with pytest.raises(ValidationError):
        validator.validate(view)


def test_table_compact_cards_remains_valid_and_compiler_agrees_on_saved_board_mutant():
    validator, view = schema_and_view('table')
    view['compact_presentation'] = 'cards'
    validator.validate(view)
    _, document = _multi_resource_fixture()
    board = next(v for v in document['views'] if v['role'] == 'collection')
    board.update(presentation='board', compact_presentation='cards')
    assert any('compact_presentation' in finding['detail'] for finding in presentation_findings(document))


def test_plain_and_ranked_lists_have_separate_query_contracts():
    validator, view = schema_and_view('list')
    view['query_controls'] = [{'id': 'search', 'kind': 'search', 'field_ref': None,
                               'label': {'en': 'Search', 'ru': 'Поиск'}, 'fulfills': []}]
    validator.validate(view)
    view['presentation_options'] = dict(group_field_ref=None, parent_field_ref=None,
        value_field_ref=None, order_field_ref='rank', draggable=True)
    with pytest.raises(ValidationError):
        validator.validate(view)
    view['query_controls'] = []
    validator.validate(view)


@pytest.mark.parametrize('policy', [
    {'mode': 'none', 'indicator': 'radio', 'row_activation': 'none'},
    {'mode': 'none', 'indicator': 'none', 'row_activation': 'select'},
    {'mode': 'single', 'indicator': 'radio', 'row_activation': 'none'},
])
def test_inconsistent_selection_properties_rejected_structurally(policy):
    validator, view = schema_and_view('table')
    view['selection'] = policy
    with pytest.raises(ValidationError): validator.validate(view)


@pytest.mark.parametrize('presentation', ['list', 'cards', 'board', 'tree', 'chart', 'accordion'])
def test_emphasis_is_advertised_only_for_a_table_target(presentation):
    validator, view = schema_and_view(presentation)
    view['selection_filter'] = dict(field_ref='owner', source_view_ref='people', source_field_ref=None,
                                    effect='emphasize', empty_selection='show_all')
    with pytest.raises(ValidationError): validator.validate(view)


def test_chart_does_not_advertise_record_selection():
    validator, view = schema_and_view('chart')
    view['selection'] = {'mode': 'single', 'indicator': 'radio', 'row_activation': 'select'}
    with pytest.raises(ValidationError): validator.validate(view)
    view['selection'] = {'mode': 'none', 'indicator': 'none', 'row_activation': 'none'}
    validator.validate(view)
