import json

import pytest

from adaos.services.builder.automation_contracts import retained_external_tool_ids


def test_continuation_keeps_previously_admitted_interface_names_without_stale_schemas(tmp_path):
    (tmp_path / 'input').mkdir()
    (tmp_path / 'input/external-mcp-contracts.json').write_text(json.dumps({'contracts': [
        {'id': 'dev_ticket.create', 'input_schema': {'old': True}}, {'id': 'applications.show'}]}), encoding='utf-8')
    artifacts = {'continuation_checkpoint': {'required_external_tool_ids': ['dev_ticket.operate', 'applications.show']}}
    assert retained_external_tool_ids(tmp_path, artifacts) == ['applications.show', 'dev_ticket.create', 'dev_ticket.operate']


def test_missing_bundle_preserves_the_existing_interface_closure(tmp_path):
    assert retained_external_tool_ids(tmp_path, {'continuation_checkpoint': {
        'required_external_tool_ids': ['dev_ticket.events']}}) == ['dev_ticket.events']


def test_corrupt_contract_evidence_is_not_silently_dropped(tmp_path):
    (tmp_path / 'input').mkdir()
    (tmp_path / 'input/external-mcp-contracts.json').write_text('broken', encoding='utf-8')
    with pytest.raises(ValueError):
        retained_external_tool_ids(tmp_path, {})
