from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.ai.legacy_state_retirement import retire_adopted_legacy_state


def seed(tmp_path, monkeypatch):
    from adaos.services import personalization_runtime
    monkeypatch.setattr(personalization_runtime, 'current_user_id', lambda _ctx: 'owner')
    state = tmp_path / 'state'
    account = state / 'ai' / hashlib.sha256(b'user:owner').hexdigest()
    stable = tmp_path / 'stable'
    for path, values in [(account / 'ai.sqlite3', {'host_id': 'same-host',
            'legacy_adoption': {'source': 'context_chat', 'status': 'completed'}}),
            (stable / 'db/context_chat.db', {'host_id': 'same-host'})]:
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)')
            for key, value in values.items():
                db.execute('INSERT INTO settings VALUES (?,?)', (key, json.dumps(value)))
    (account / 'connection.enc').write_bytes(b'new-active-core-connection-must-not-change')
    source = stable / 'files/chatgpt-session.enc'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'old-ciphertext-not-a-provider-payload')
    component = SimpleNamespace(component_ref='skill:context_chat_service', stable_root=stable,
        beta_root=tmp_path / 'beta', target_manifest={'capabilities': ['ai.inference']})
    lifecycle = SimpleNamespace(components=[component], application_id='context_chat',
        candidate_id='candidate', release_digest='sha256:release', recovery=tmp_path / 'recovery',
        state_file_contracts={component.component_ref: {'files/chatgpt-session.enc': object()}})
    owner = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: state))
    return owner, lifecycle, source, account


def test_retirement_preserves_ciphertext_and_active_core_account_and_is_idempotent(tmp_path, monkeypatch):
    owner, lifecycle, source, account = seed(tmp_path, monkeypatch)
    before = (account / 'connection.enc').read_bytes()
    original = source.read_bytes()
    result = retire_adopted_legacy_state(owner, lifecycle, operation_key='exact-step')
    assert result['core_connection_unchanged'] is True
    assert not source.exists()
    backup = lifecycle.recovery / 'core-owned-state-retirement/chatgpt-session.enc'
    assert backup.read_bytes() == original
    assert (account / 'connection.enc').read_bytes() == before
    assert retire_adopted_legacy_state(owner, lifecycle, operation_key='exact-step') == result
    with pytest.raises(ValueError, match='identity changed'):
        retire_adopted_legacy_state(owner, lifecycle, operation_key='another-intent')


@pytest.mark.parametrize('difference', ['host', 'adoption', 'vault'])
def test_retirement_requires_verified_same_host_core_ownership(tmp_path, monkeypatch, difference):
    owner, lifecycle, source, account = seed(tmp_path, monkeypatch)
    if difference == 'vault':
        (account / 'connection.enc').unlink()
    else:
        with sqlite3.connect(account / 'ai.sqlite3') as db:
            key, value = ('host_id', 'other-host') if difference == 'host' else ('legacy_adoption', {'status': 'copied'})
            db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(value), key))
    with pytest.raises(ValueError, match='verified Core adoption'):
        retire_adopted_legacy_state(owner, lifecycle, operation_key='exact-step')
    assert source.exists()
    assert not lifecycle.recovery.exists()


def test_live_beta_file_is_not_a_core_retirement_request(tmp_path, monkeypatch):
    owner, lifecycle, source, _account = seed(tmp_path, monkeypatch)
    beta = lifecycle.components[0].beta_root / 'files/chatgpt-session.enc'
    beta.parent.mkdir(parents=True)
    beta.write_bytes(b'not-migrated')
    assert retire_adopted_legacy_state(owner, lifecycle, operation_key='exact-step')['retired_components'] == []
    assert source.exists()
