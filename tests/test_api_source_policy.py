import asyncio

import pytest
from fastapi import HTTPException

from adaos.apps.api import server
from adaos.services import source_policy


@pytest.mark.parametrize('branch,commit,confirmed,accepted', [
    ('main', 'a' * 40, True, True),
    ('dev2027', 'a' * 40, True, False),
    ('main', 'abc123', True, False),
    ('main', 'a' * 40, False, False),
])
def test_production_update_authorizes_source_before_forwarding_to_supervisor(monkeypatch, branch, commit, confirmed, accepted):
    monkeypatch.setenv('ENV_TYPE', 'prod')
    monkeypatch.setattr(server, 'core_update_reactions_disabled_reason', lambda: None)
    monkeypatch.setattr(source_policy, 'source_policy', lambda conf=None: {
        'branch': 'main', 'confirmed': confirmed, 'authority': 'hub', 'revision': 1, 'hub_id': 'sn_test'})
    forwarded = []
    def forward(body):
        forwarded.append(body)
        return {'ok': True, 'accepted': True, '_served_by': 'supervisor'}
    monkeypatch.setattr(server, '_try_forward_update_start_to_supervisor', forward)
    body = server.CoreUpdateStartRequest(target_rev=branch, target_version=commit)
    if accepted:
        assert asyncio.run(server.admin_update_start(body))['_served_by'] == 'supervisor'
        assert len(forwarded) == 1
    else:
        with pytest.raises(HTTPException) as error:
            asyncio.run(server.admin_update_start(body))
        assert error.value.status_code == 409
        assert not forwarded
