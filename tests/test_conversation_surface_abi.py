import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


@pytest.fixture
def surface_validator():
    schema = json.loads((Path(__file__).parents[1] / 'src/adaos/abi/webui.v1.schema.json').read_text(encoding='utf-8'))
    return Draft202012Validator({'$ref': '#/$defs/conversationSurface', '$defs': schema['$defs']})


def test_fixed_owner_surface(surface_validator):
    surface_validator.validate({'version': 1, 'owner': 'application:research',
                                'agent': {'mode': 'fixed', 'id': 'agent:researcher', 'label': 'Researcher'},
                                'history': {'mode': 'auto', 'maxMessages': 200}, 'voice': False})


@pytest.mark.parametrize('agent', [{'mode': 'fixed'}, {'mode': 'select'}, {'mode': 'any'}])
def test_rejects_undeclared_agent_policy(surface_validator, agent):
    assert not surface_validator.is_valid({'version': 1, 'owner': 'app', 'agent': agent})


def test_attachment_bounds_and_actions_are_not_arbitrary(surface_validator):
    surface = {'version': 1, 'owner': 'app', 'agent': {'mode': 'fixed', 'id': 'a', 'label': 'A'}}
    surface['attachments'] = {'uploadTarget': 'app.upload', 'readTarget': 'app.read',
                              'accept': ['text/plain'], 'maxFiles': 3, 'maxBytes': 1024}
    surface_validator.validate(surface)
    surface['attachments']['maxBytes'] = 0
    assert not surface_validator.is_valid(surface)
    surface.pop('attachments')
    surface['executeJavascript'] = 'untrusted'
    assert not surface_validator.is_valid(surface)


def test_consumer_migration_is_idempotent_and_leaves_voice_alone():
    from scripts.migrate_conversation_surfaces import migrate, validate_surfaces

    doc = {'widgets': [
        {'id': 'desktop-chat', 'type': 'ui.chat', 'inputs': {}},
        {'id': 'chat-channel-selector', 'type': 'navigation.tabs'},
        {'id': 'chat-agent-selector', 'type': 'navigation.tabs'},
        {'id': 'chat-voice-input', 'type': 'ui.voiceInput'},
    ]}
    result = migrate(doc, 'web_desktop')
    assert [w['id'] for w in result['widgets']] == ['desktop-chat']
    assert result == migrate(result, 'web_desktop')
    assert validate_surfaces(result) == 1
    assert result['widgets'][0]['inputs']['openCommand'] == 'voice.chat.open'
    assert len(doc['widgets']) == 4
    with pytest.raises(ValueError):
        migrate(doc, 'voice')


@pytest.mark.parametrize('app,widget_id', [('builder', 'design-conversation-side-task'), ('builder', 'builder-chat'), ('research_workbench', 'research-chat')])
def test_owner_migration_preserves_routing_and_declares_model_once(app, widget_id):
    from scripts.migrate_conversation_surfaces import migrate, validate_surfaces

    route = {'conversation_id': '$state.conversation', 'thread_id': '$state.task'}
    doc = {'widgets': [{'id': widget_id, 'type': 'ui.chat', 'inputs': {'meta': route},
                        'dataSource': {'kind': 'stream', 'receiver': 'voice_chat.messages', 'params': route}}]}
    result = migrate(doc, app)
    widget = result['widgets'][0]
    assert widget['inputs']['meta'] == route
    assert widget['dataSource']['params'] == route
    assert widget['inputs']['conversation']['agent']['mode'] == 'fixed'
    assert validate_surfaces(result) == 1
    assert result == migrate(result, app)


def test_builder_capability_discovery_preserves_shared_chat_contract():
    from adaos.services.builder.domain_packs.application_manager_legacy import get_ui_capability

    capability = get_ui_capability('ui.chat')
    assert 'conversation_contract' in capability['manifest']
    assert any('model.change' in event for event in capability['events'])


@pytest.mark.parametrize('capability,event', [('attachments', 'send'), ('model', 'model.change')])
def test_widget_requires_an_owner_action_for_advertised_mutation(capability, event):
    schema = json.loads((Path(__file__).parents[1] / 'src/adaos/abi/webui.v1.schema.json').read_text(encoding='utf-8'))
    validator = Draft202012Validator({'$ref': '#/$defs/widgetCatalogEntry', '$defs': schema['$defs']})
    profile = {'version': 1, 'owner': 'application:example',
               'agent': {'mode': 'fixed', 'id': 'agent:example', 'label': 'Example'}}
    profile[capability] = ({'uploadTarget': 'app.upload', 'readTarget': 'app.read',
                            'accept': ['text/plain'], 'maxFiles': 2, 'maxBytes': 1024}
                           if capability == 'attachments' else
                           {'source': {'kind': 'static', 'value': {}}, 'valuePath': 'value', 'optionsPath': 'options'})
    widget = {'id': 'chat', 'type': 'ui.chat', 'area': 'main', 'inputs': {'conversation': profile}}
    assert not validator.is_valid(widget)
    widget['actions'] = [{'on': event, 'type': 'callSkill', 'target': 'app.change'}]
    validator.validate(widget)
