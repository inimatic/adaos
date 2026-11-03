from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator, ValidationError

from adaos.domain.application import ApplicationOperation
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.root_mcp.applications_plane import contracts


def operation():
    plan = {'schema': 'adaos.application.lifecycle_plan.v1', 'action': 'select_runtime',
        'application_id': 'fixture', 'target_position_id': 'private_stable',
        'source_version': '1.2.1', 'target_version': '1.2.0',
        'summary': 'Select retained Stable 1.2.0', 'data_policy': 'retain',
        'data_disposition': 'Keep the data of each edition.', 'required_apply_capability': 'applications.apply'}
    return ApplicationOperation(operation_id='applifecycle.fixture', application_id='fixture', kind='reconcile',
        status='planned', actor_ref='user:fixture', subnet_ref='subnet:fixture',
        plan_digest=canonical_payload_digest(plan), idempotency_key='fixture-reviewed-intent',
        expected_revision=0, revision=1, plan=plan)


@pytest.mark.parametrize('tool', ['applications.lifecycle.plan', 'applications.lifecycle.apply',
    'applications.lifecycle.publish', 'applications.lifecycle.recover', 'applications.cancel_operation',
    'applications.get_operation'])
def test_review_consumers_receive_one_typed_operation_at_the_same_result_path(tool):
    contract = next(item for item in contracts() if item.id == tool)
    schema = contract.output_schema['properties']['result']
    validator = Draft202012Validator(schema)
    for status in ('planned', 'applying', 'unknown', 'reconciling', 'succeeded', 'failed', 'cancelled'):
        result = replace(operation(), status=status).to_dict()
        validator.validate({'operation': result})
    broken = operation().to_dict()
    broken['status'] = 'completed'
    with pytest.raises(ValidationError):
        validator.validate({'operation': broken})


def test_dry_run_does_not_claim_an_operation_was_created():
    contract = next(item for item in contracts() if item.id == 'applications.lifecycle.plan')
    Draft202012Validator(contract.output_schema['properties']['result']).validate({
        'would_plan': True, 'action': 'select_runtime', 'application_id': 'fixture'})


def test_release_selection_contract_preserves_exact_identity_and_channel_authority():
    from adaos.sdk.applications import _release_read_model
    contract = next(item for item in contracts() if item.id == 'applications.list_releases')
    validator = Draft202012Validator(contract.output_schema['properties']['result'])
    release = _release_read_model({'application_id': 'fixture', 'version': '1.2.0',
        'release_digest': 'sha256:' + 'a' * 64, 'channels': ['stable'], 'lifecycle': 'trial',
        'published_at': '2026-11-03T10:00:00+00:00', 'project_release': {'version': '1.2.0'}})
    validator.validate({'releases': [release]})
    validator.validate({'releases': []})
    assert release['lifecycle'] == 'trial' and release['channels'] == ['stable']
    with pytest.raises(ValidationError):
        validator.validate({'releases': [{'version': '1.2.0'}]})
    with pytest.raises(ValidationError):
        validator.validate({'items': [release]})
