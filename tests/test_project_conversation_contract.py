"""The native project widgets expose one governed declarative connection."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / 'src/adaos/abi/webui.v1.schema.json').read_text(encoding='utf-8'))
INPUTS = {'$ref': '#/$defs/projectConversationInputs', '$defs': SCHEMA['$defs']}


@pytest.mark.parametrize('invalid', [
    {'service': 'https://other.invalid', 'session': 'chat'},
    {'service': '../other', 'session': 'chat'},
    {'service': 'chat_service', 'session': 'chat', 'pollMs': 1},
    {'service': 'chat_service', 'session': 'chat', 'account': {'label': 'Provider', 'authOrigin': 'javascript:alert(1)', 'usageUrl': 'https://provider.invalid/usage'}},
    {'service': 'chat_service', 'session': 'chat', 'token': 'must-not-be-in-a-page'},
    {'service': 'chat_service', 'session': 'chat', 'maxFileBytes': 10485761},
    {'service': 'chat_service', 'session': 'chat', 'tools': ['arbitrary_tool']},
])
def test_native_connection_rejects_urls_credentials_and_unbounded_polling(invalid):
    assert list(Draft202012Validator(INPUTS).iter_errors({'connection': invalid}))


def test_native_widgets_and_connection_contract_are_registered():
    assert not list(Draft202012Validator(INPUTS).iter_errors({'connection': {
        'service': 'project_chat', 'session': 'private-chat', 'pollMs': 2500,
        'contextStateKey': 'contextOpen', 'maxFileBytes': 10485760, 'accept': ['.txt', '.docx', '.xlsx'], 'tools': ['files', 'web_search'],
        'account': {'label': 'ChatGPT', 'buttonLabel': 'Continue with ChatGPT',
                    'authOrigin': 'https://auth.openai.com', 'usageUrl': 'https://chatgpt.com/settings/usage'},
    }}))
    encoded = json.dumps(SCHEMA)
    for widget in ('collection.projectList', 'ui.projectConversation', 'item.conversationContext'):
        assert widget in encoded


def test_v2_chat_content_profiles_are_explicit_and_reject_future_actions():
    inputs={'connection':{'service':'owned_chat','session':'workspace','protocol':'adaos.project-conversation.v2',
                          'icon':'resource:owned.icon','content':{'format':'markdown','copy':True,'plainCopy':True}}}
    validator=Draft202012Validator(INPUTS)
    assert not list(validator.iter_errors(inputs))
    inputs['connection']['content']['share']=True
    assert list(validator.iter_errors(inputs))
    inputs['connection']['content'].pop('share')
    inputs['connection']['protocol']='unadmitted.v3'
    assert list(validator.iter_errors(inputs))


def test_shared_ui_chat_profile_binding_requires_an_owner_revision():
    contract={'$ref':'#/$defs/conversationSurface','$defs':SCHEMA['$defs']}
    surface={'version':1,'owner':'owned.chat','agent':{'mode':'fixed','id':'agent:owned','label':'Assistant'},
             'model':{'source':{'kind':'static','value':{}},'valuePath':'profile.model','optionsPath':'models','profilePath':'profile'},
             'presentation':{'content':{'format':'markdown','copy':True}}}
    validator=Draft202012Validator(contract)
    assert list(validator.iter_errors(surface))
    surface['model']['revisionPath']='profile_revision'
    assert not list(validator.iter_errors(surface))
