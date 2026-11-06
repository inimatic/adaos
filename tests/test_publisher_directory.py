from types import SimpleNamespace

from adaos.services import publisher_directory as module


def test_local_rename_updates_one_profile_and_all_snapshot_names(monkeypatch):
    records = {}
    name = ['Old name']
    monkeypatch.setattr(module, 'durable_state_get', lambda ns, key: records.get((ns, key)))
    monkeypatch.setattr(module, 'durable_state_put', lambda ns, key, value: records.__setitem__((ns, key), value))
    monkeypatch.setattr(module, 'load_subnet_alias', lambda **kwargs: name[0])
    owner = SimpleNamespace(config=SimpleNamespace(subnet_id='sn_home'))
    directory = module.PublisherDirectory(owner)
    assert directory.refresh_local()['revision'] == 1
    assert directory.refresh_local()['revision'] == 1
    name[0] = 'New name'
    # Reads already reflect authority even if the event hasn't arrived.
    assert directory.resolve('subnet:sn_home', {'display_name': 'Stale app'})['display_name'] == name[0]
    assert directory.refresh_local()['revision'] == 2
    assert len(records) == 1
    assert directory.resolve('subnet:sn_other', {'display_name': 'Other publisher'})['display_name'] == 'Other publisher'


def test_profile_names_never_replace_signing_identity(monkeypatch):
    monkeypatch.setattr(module, 'durable_state_get', lambda *_: {'publisher_ref': 'subnet:sn_wrong', 'display_name': 'Forged'})
    owner = SimpleNamespace(config=SimpleNamespace(subnet_id='sn_home'))
    snapshot = {'display_name': 'Published name', 'release_key_fingerprint': 'exact-key'}
    assert module.PublisherDirectory(owner).resolve('subnet:sn_other', snapshot) == {
        **snapshot, 'publisher_ref': 'subnet:sn_other'}
