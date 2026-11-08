"""Keep the user-confirmed Core completion path in mandatory SDK CI checks."""
from adaos.services.root_mcp.applications_plane import _lifecycle_contracts, handlers
from adaos.sdk import applications


def test_finalize_is_a_typed_publisher_mutation_with_exact_review_identity():
    contract = next(item for item in _lifecycle_contracts() if item.id == 'applications.lifecycle.finalize')
    assert contract.required_capability == 'applications.publish'
    assert contract.side_effects == 'write'
    assert set(contract.input_schema['required']) == {
        'application_id', 'candidate_id', 'release_digest', 'expected_revision', 'idempotency_key'}
    assert contract.input_schema['additionalProperties'] is False
    assert 'operation' in contract.output_schema['properties']['result']['properties']
    assert callable(handlers()[contract.id])
    assert 'finalize_local_beta' in applications.__all__
