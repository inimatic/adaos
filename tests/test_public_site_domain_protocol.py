import json

import pytest

from adaos.services.public_site_root import PublicSiteRootService, PublicationRootError
from adaos.services.public_site_root_client import RootPublicationDelivery
from test_public_site_root import POLICY, Provider, prepare


class DomainProvider(Provider):
    def status_domain(self, hostname):
        return {'hostname': hostname, 'status': 'active', 'ssl_status': 'active'}


@pytest.fixture
def adopted_platform_domain(tmp_path):
    service = PublicSiteRootService(tmp_path / 'root', DomainProvider())
    client, args = prepare(service, tmp_path)
    client.publish(**args)
    hostname = 'beta.adaos.io'
    with service._db() as db:
        # Same existing operator-owned record as the native migration imports.
        db.execute('INSERT INTO domains VALUES(?,?,?,?,?)',
                   (hostname, 'owner', 'first', 'active', json.dumps({'hostname': hostname, 'status': 'active'})))
    return service, hostname


def test_operator_adopted_platform_domain_can_be_observed_by_its_owner(adopted_platform_domain):
    service, hostname = adopted_platform_domain
    result = service.domain('owner', {'site_id': 'first', 'hostname': hostname, 'command': 'status'}, {})
    assert result['status'] == result['ssl_status'] == 'active'


def test_platform_domain_is_not_readable_by_another_subnet(adopted_platform_domain):
    service, hostname = adopted_platform_domain
    with pytest.raises(PublicationRootError) as error:
        service.domain('other', {'site_id': 'first', 'hostname': hostname, 'command': 'status'}, {})
    assert error.value.status == 403


@pytest.mark.parametrize('hostname', ['adaos.io', 'p.adaos.io', 'another.adaos.io'])
def test_unadmitted_platform_domain_cannot_be_claimed(adopted_platform_domain, hostname):
    service, _ = adopted_platform_domain
    with pytest.raises(PublicationRootError) as error:
        service.domain('owner', {'site_id': 'first', 'hostname': hostname, 'command': 'request'},
                       {**POLICY, 'custom_domains': True})
    assert error.value.code == 'domain_reserved'
    with service._db() as db:
        assert db.execute('SELECT 1 FROM domains WHERE hostname=?', (hostname,)).fetchone() is None


def test_domain_observation_has_a_read_budget_and_write_has_no_retry(monkeypatch):
    calls = []
    class Http:
        base_url = 'https://domain-protocol.invalid'
        def request(self, *args, **kwargs):
            calls.append(kwargs)
            return {'ok': True, 'result': {'status': 'pending'}}
    delivery = RootPublicationDelivery(Http())
    delivery.authoring = {'site_id': 'first'}
    delivery.status_domain('example.test')
    delivery.request_domain('example.test')
    assert 0 < calls[0]['timeout'] <= 5
    assert 0 < calls[1]['timeout'] <= 45
