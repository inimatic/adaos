import pytest

from adaos.e2e.builder import BuilderE2EError, _collect_usage
from adaos.services.contract_context_admission import prepare_component_input
from adaos.services.context_control import ContextControlService
from adaos.services.ui_component_contracts import project_component_contract


def test_input_admission_receipt_is_not_inference(tmp_path):
    snapshot = project_component_contract()
    payload, _ = prepare_component_input({'messages': [], 'tools': []}, snapshot=snapshot, required_ids=['ui.table'])
    result = ContextControlService(tmp_path).admit_model_input(payload, snapshot=snapshot, required_ids=['ui.table'], run_ref='builder-run:model-free')
    assert _collect_usage({'result': result})['model_calls'] == 0


def test_explicit_zero_calls_cannot_hide_provider_tokens():
    with pytest.raises(BuilderE2EError, match='inconsistent'):
        _collect_usage({'usage': {'model_calls': 0, 'provider_input_tokens': 10}})


def test_existing_provider_usage_without_call_count_still_counts_an_attempt():
    assert _collect_usage({'usage': {'input_tokens': 100, 'output_tokens': 0}})['model_calls'] == 1
