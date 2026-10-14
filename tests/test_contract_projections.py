import copy
import pytest
from adaos.services.contract_projections import project_context_units, consume_context_units, publish_context_units, CONSUMERS
from adaos.services.root_mcp.descriptor_search import get_descriptor_item


def test_common_sdk_component_units_are_readable_with_identical_consumer_masks():
    projection = project_context_units(sdk_symbols=['adaos.sdk.resources.query', 'adaos.sdk.builder.semantic_ui.admit'])
    publication = publish_context_units(projection)
    required = ['ui.form', 'adaos.sdk.resources.query']
    consumers = [consume_context_units(projection, consumer=consumer, required_ids=required) for consumer in CONSUMERS]
    assert all(value == consumers[0] for value in consumers)
    assert publication['status'] == 'verified'
    sdk = next(unit for unit in projection['units'] if unit['id'].startswith('adaos.sdk.'))
    assert get_descriptor_item(**sdk['locator'])['item'] == sdk['content']
    with pytest.raises(ValueError, match='snapshot_incompatible'):
        get_descriptor_item(**{**sdk['locator'], 'expected_source_digest': 'sha256:' + '0' * 64})


def test_stale_projection_and_unadmitted_consumers_fail_before_reuse():
    projection = project_context_units()
    mutant = copy.deepcopy(projection)
    mutant['discovery_policy']['known_locator'] = 'changed without rebuild'
    with pytest.raises(ValueError, match='integrity_conflict'):
        consume_context_units(mutant, consumer='e2e', required_ids=['ui.form'])
    with pytest.raises(ValueError, match='consumer_mask_unknown'):
        consume_context_units(projection, consumer='unowned', required_ids=['ui.form'])
