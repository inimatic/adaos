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
])
def test_native_connection_rejects_urls_credentials_and_unbounded_polling(invalid):
    assert list(Draft202012Validator(INPUTS).iter_errors({'connection': invalid}))


def test_native_widgets_and_connection_contract_are_registered():
    assert not list(Draft202012Validator(INPUTS).iter_errors({'connection': {
        'service': 'project_chat', 'session': 'private-chat', 'pollMs': 2500,
        'contextStateKey': 'contextOpen', 'maxFileBytes': 2097152, 'accept': ['.txt', '.docx'],
        'account': {'label': 'ChatGPT', 'buttonLabel': 'Continue with ChatGPT',
                    'authOrigin': 'https://auth.openai.com', 'usageUrl': 'https://chatgpt.com/settings/usage'},
    }}))
    encoded = json.dumps(SCHEMA)
    for widget in ('collection.projectList', 'ui.projectConversation', 'item.conversationContext'):
        assert widget in encoded
