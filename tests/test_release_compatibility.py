from copy import deepcopy
from datetime import datetime, timezone, timedelta

import pytest

from adaos.services.release_compatibility import verify_composition, ReleaseCompatibilityError


@pytest.fixture
def composition():
    now = datetime.now(timezone.utc)
    contract = {'schema':'adaos.release.compatibility.v1',
        'requirements':{name:{'version_spec':'>=1.0,<2', 'source_branches':['main', 'dev2027']}
                        for name in ('client', 'backend')},
        'update_order':['backend', 'core', 'client'],
        'rolling':{'max_policy_revision_lag':1, 'max_observation_age_seconds':300},
        'data':{'node':{'readable_schemas':['v1', 'v2'], 'writes_schema':'v1'}}}
    observations = {'observed_at':now.isoformat(), 'members':[],
        'components':{name:{'branch':'main', 'version':'1.2.3', 'commit':'a'*40,
            'package_digest':'sha256:'+'b'*64, 'state':'confirmed'} for name in ('client', 'backend')},
        'data':{'node':{'schema':'v1', 'generation':7}}}
    return contract, observations, now


def test_exact_installed_composition_produces_bound_receipt(composition):
    contract, observations, now = composition
    result = verify_composition(contract, observations, policy_revision=4, now=now)
    assert result['status'] == 'passed' and result['data_rollback_performed'] is False
    changed = deepcopy(observations)
    changed['components']['client']['package_digest'] = 'sha256:'+'c'*64
    assert verify_composition(contract, changed, policy_revision=4, now=now)['observations_digest'] != result['observations_digest']


@pytest.mark.parametrize('fault,expected', [
    ('missing_identity','identity_unknown'), ('incompatible_version','incompatible'),
    ('wrong_branch','incompatible'), ('unknown_store','inventory_required'),
    ('unknown_schema','schema_incompatible'), ('unconfirmed_predecessor','predecessor_unconfirmed'),
    ('expired','observation_expired'), ('future','observation_expired'),
    ('member_lag','skew_exceeded'), ('member_expired','skew_exceeded'),
    ('missing_members','inventory_required'), ('invalid_order','order_required'),
    ('migration_missing','migration_recovery_required')])
def test_unverified_or_incompatible_composition_is_rejected(composition, fault, expected):
    contract, observed, now = composition
    if fault == 'missing_identity': del observed['components']['client']['package_digest']
    elif fault == 'incompatible_version': observed['components']['backend']['version'] = '2.0.0'
    elif fault == 'wrong_branch': observed['components']['client']['branch'] = 'foreign'
    elif fault == 'unknown_store': observed['data']['other'] = {'schema':'v1', 'generation':0}
    elif fault == 'unknown_schema': observed['data']['node']['schema'] = 'v3'
    elif fault == 'unconfirmed_predecessor': observed['components']['backend']['state'] = 'queued'
    elif fault == 'expired': observed['observed_at'] = (now - timedelta(seconds=301)).isoformat()
    elif fault == 'future': observed['observed_at'] = (now + timedelta(seconds=1)).isoformat()
    elif fault.startswith('member_'):
        observed['members'] = [{'policy_revision':1 if fault == 'member_lag' else 4,
            'observed_at':(now - timedelta(seconds=301 if fault == 'member_expired' else 0)).isoformat()}]
    elif fault == 'missing_members': del observed['members']
    elif fault == 'invalid_order': contract['update_order'] = ['core']
    elif fault == 'migration_missing': contract['data']['node']['writes_schema'] = 'v2'
    with pytest.raises(ReleaseCompatibilityError, match=expected):
        verify_composition(contract, observed, policy_revision=4, now=now)


def test_schema_change_requires_exact_generation_and_restore_proof(composition):
    contract, observed, now = composition
    contract['data']['node']['writes_schema'] = 'v2'
    observed['data']['node']['prepared_migration'] = {'from_schema':'v1', 'to_schema':'v2',
        'generation':7, 'status':'prepared', 'checkpoint_digest':'sha256:'+'c'*64,
        'restore_receipt_digest':'sha256:'+'d'*64}
    assert verify_composition(contract, observed, policy_revision=4, now=now)['status'] == 'passed'
    observed['data']['node']['generation'] = 8
    with pytest.raises(ReleaseCompatibilityError, match='migration_recovery_required'):
        verify_composition(contract, observed, policy_revision=4, now=now)
