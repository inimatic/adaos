import copy

import pytest

from adaos.sdk.builder import automation as sdk
from adaos.services.builder.automation import BuilderAutomationService
from adaos.services.skill_factory_worker import _generated_test_budget


def assignment():
    return {'realize_request': {'artifacts': {
        'development_context': {'execution_budget': {'max_wall_seconds': 600},
            'validation_budget': {'schema': 'adaos.builder.validation_budget.v1',
                'packaged_pytest_wall_seconds': 60, 'execution_max_wall_seconds': 600,
                'source': 'development_session.execution_budget'}},
        'continuation_checkpoint': {'mode': 'validate_preserved_candidate', 'model_policy': 'forbid',
            'validation_budget': {'schema': 'adaos.builder.validation_budget.v1',
                'packaged_pytest_wall_seconds': 300, 'execution_max_wall_seconds': 600,
                'source': 'builder.operator_revalidation'}}}}}


def test_worker_admits_longer_checks_only_for_model_free_owned_revalidation():
    value = assignment()
    assert _generated_test_budget(value)['packaged_pytest_wall_seconds'] == 300
    del value['realize_request']['artifacts']['continuation_checkpoint']['validation_budget']
    assert _generated_test_budget(value)['packaged_pytest_wall_seconds'] == 60


@pytest.mark.parametrize('key,value', [('model_policy', 'allow'), ('mode', 'resume_preserved_candidate')])
def test_worker_rejects_model_execution_with_validation_override(key, value):
    task = assignment()
    task['realize_request']['artifacts']['continuation_checkpoint'][key] = value
    with pytest.raises(ValueError, match='model-free'):
        _generated_test_budget(task)


@pytest.mark.parametrize('key,value', [('packaged_pytest_wall_seconds', 901),
    ('execution_max_wall_seconds', 601), ('source', 'unverified')])
def test_worker_rejects_validation_budget_expansion(key, value):
    task = assignment()
    task['realize_request']['artifacts']['continuation_checkpoint']['validation_budget'][key] = value
    with pytest.raises(ValueError):
        _generated_test_budget(task)


def test_service_carries_bounded_budget_with_candidate_and_keeps_model_forbidden(monkeypatch):
    service = object.__new__(BuilderAutomationService)
    captured = {}
    monkeypatch.setattr(BuilderAutomationService, 'preflight_preserved_candidate', lambda *_a, **_k: {
        'session_id': 'owned', 'iteration': 3, 'preflight': {'eligible': True,
            'checkpoint': {'mode': 'validate_preserved_candidate', 'model_policy': 'forbid'}}})
    monkeypatch.setattr(BuilderAutomationService, 'get_session', lambda *_a: {
        'execution_budget': {'max_wall_seconds': 600}})
    monkeypatch.setattr(BuilderAutomationService, 'submit_turn',
        lambda _self, **kwargs: captured.update(copy.deepcopy(kwargs)) or {'ok': True})
    result = service.validate_preserved_candidate(object_type='scenario', object_id='owned',
                                                 validation_timeout_seconds=300)
    checkpoint = captured['_continuation_checkpoint_override']
    assert result['model_policy'] == checkpoint['model_policy'] == 'forbid'
    assert checkpoint['validation_budget']['packaged_pytest_wall_seconds'] == 300
    with pytest.raises(ValueError, match='platform bounds'):
        service.validate_preserved_candidate(object_type='scenario', object_id='owned', validation_timeout_seconds=901)
    monkeypatch.setattr(BuilderAutomationService, 'get_session', lambda *_a: {
        'execution_budget': {'max_wall_seconds': 120}})
    with pytest.raises(ValueError, match='execution authority'):
        service.validate_preserved_candidate(object_type='scenario', object_id='owned', validation_timeout_seconds=300)


def test_sdk_forwards_explicit_timeout_only(monkeypatch):
    captured = []
    class Service:
        def validate_preserved_candidate(self, **kwargs):
            captured.append(kwargs)
            return {'ok': True}
    monkeypatch.setattr(sdk, '_service', lambda: Service())
    sdk.validate_preserved_candidate(object_type='scenario', object_id='owned')
    sdk.validate_preserved_candidate(object_type='scenario', object_id='owned', validation_timeout_seconds=300)
    assert 'validation_timeout_seconds' not in captured[0]
    assert captured[1]['validation_timeout_seconds'] == 300


def test_longer_operator_allowance_does_not_expand_ordinary_development_budget():
    from adaos.domain.development_validation import normalize_validation_budget, derive_validation_budget
    value = assignment()['realize_request']['artifacts']['continuation_checkpoint']['validation_budget']
    value['packaged_pytest_wall_seconds'] = 600
    with pytest.raises(ValueError, match='platform bounds'):
        normalize_validation_budget(value)
    assert normalize_validation_budget(value, operator_revalidation=True)['packaged_pytest_wall_seconds'] == 600
    assert derive_validation_budget({'max_wall_seconds': 100000})['packaged_pytest_wall_seconds'] == 300
    assert _generated_test_budget(assignment())['packaged_pytest_wall_seconds'] == 300
