import json
import pytest
from adaos.services.contract_context_admission import component_input_scope, prepare_component_input, admit_contract_input, ContractInputRejected
from adaos.services.ui_component_contracts import project_component_contract
from adaos.sdk.llm.llm_client import _llm_root_payload


@pytest.mark.parametrize('route', ['generation', 'repair', 'increment', 'task_worker'])
def test_all_routes_validate_the_final_normalized_envelope_and_tool_scope(route, tmp_path, monkeypatch):
    from adaos.services.context_control import ContextControlService
    service = ContextControlService(tmp_path)
    original = ContextControlService.admit_model_input
    monkeypatch.setattr(ContextControlService, 'admit_model_input', lambda self, *args, **kwargs: original(service, *args, **kwargs))
    snapshot = project_component_contract()
    tools = [{'type': 'function', 'name': 'get_descriptor_item', 'parameters': {'type': 'object'}}]
    with component_input_scope(snapshot, required_ids=['ui.form'], route=route):
        envelope, _ = _llm_root_payload([{'role': 'user', 'content': 'Bounded task'}],
            tools=tools, tool_choice='auto', request_id='route:' + route)
    receipt = admit_contract_input(envelope, snapshot=snapshot, required_ids=['ui.form'])
    assert receipt['tool_scope'] == ['get_descriptor_item'] and 'ui.form' in receipt['required_closure']
    mutated = json.loads(json.dumps(envelope))
    mutated['tools'].append({'type': 'function', 'name': 'dangerous_write'})
    with pytest.raises(ContractInputRejected) as error:
        admit_contract_input(mutated, snapshot=snapshot, required_ids=['ui.form'])
    assert 'contract.callable_tools_conflict' in {finding['code'] for finding in error.value.findings}


def test_normalized_responses_receives_facts_in_actual_instructions_not_ignored_messages():
    snapshot = project_component_contract()
    envelope, receipt = prepare_component_input({'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': 'Task'}]}]},
        snapshot=snapshot, required_ids=['ui.table'])
    assert 'messages' not in envelope and 'platform_contracts' in envelope['instructions']
    assert receipt['status'] == 'admitted'
