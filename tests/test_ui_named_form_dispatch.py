import pytest

from adaos.services.ui_capabilities import validate_webui_capabilities


def conflicts(kind, buttons, actions):
    result = validate_webui_capabilities({'schema': 'adaos.webui.v1', 'ui': {
        'application': {'desktop': {'pageSchema': {'widgets': [{
            'id': 'form', 'type': kind, 'inputs': {'fields': [], 'buttons': buttons},
            'actions': actions,
        }]}}}}})
    return [item for item in result['findings'] if item['code'] == 'ui.action.conflicting_state_writes']


def write(identity, event='submit'):
    return {'id': identity, 'on': event, 'type': 'updateState', 'params': {'selection': identity}}


def test_distinct_declared_form_buttons_have_independent_write_scopes():
    assert not conflicts('ui.form', [{'id': 'save'}, {'id': 'reset'}], [write('save'), write('reset')])


@pytest.mark.parametrize('kind,buttons', [('ui.actions', [{'id': 'save'}, {'id': 'reset'}]), ('ui.form', [])])
def test_arbitrary_action_identifiers_do_not_hide_conflicting_events(kind, buttons):
    assert conflicts(kind, buttons, [write('save'), write('reset')])


def test_same_named_button_conflicts_even_when_on_labels_differ():
    assert conflicts('ui.form', [{'id': 'save'}], [write('save'), write('save', 'change')])


def test_named_buttons_still_share_generic_change_events():
    assert conflicts('ui.form', [{'id': 'save'}, {'id': 'reset'}], [write('save', 'change'), write('reset', 'change')])
