from __future__ import annotations

import keyring
import pytest

from adaos.adapters.secrets.keyring_vault import KeyringVault


class _KV:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get_json(self, key: str):
        return self.values.get(key)

    def set_json(self, key: str, value) -> None:
        self.values[key] = value


def _patch_keyring(monkeypatch):
    values: dict[tuple[str, str], str] = {}

    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, key, value: values.__setitem__((service, key), value),
    )
    monkeypatch.setattr(
        keyring,
        "get_password",
        lambda service, key: values.get((service, key)),
    )

    def delete(service, key):
        if (service, key) not in values:
            raise keyring.errors.PasswordDeleteError("missing")
        values.pop((service, key))

    monkeypatch.setattr(keyring, "delete_password", delete)
    return values


def test_keyring_vault_round_trips_large_values_in_bounded_chunks(monkeypatch) -> None:
    values = _patch_keyring(monkeypatch)
    vault = KeyringVault(profile="test", kv=_KV())
    payload = "oauth-correlation:" + "x" * 6000

    vault.put("integration:attempt", payload, meta={"authority": "ingress"})

    assert vault.get("integration:attempt") == payload
    assert len(values) > 2
    assert max(len(value) for value in values.values()) <= vault._CHUNK_CHARS
    assert vault.list() == [
        {"key": "integration:attempt", "meta": {"authority": "ingress"}}
    ]


def test_keyring_vault_replaces_chunked_value_and_removes_old_chunks(monkeypatch) -> None:
    values = _patch_keyring(monkeypatch)
    vault = KeyringVault(profile="test", kv=_KV())
    vault.put("connection", "a" * 5000)
    old_chunk_keys = {
        key for (_service, key) in values if key.startswith("adaos-chunk:")
    }

    vault.put("connection", "small")

    assert vault.get("connection") == "small"
    assert not any(key in values for key in (("adaos:profile:test", item) for item in old_chunk_keys))


def test_keyring_vault_fails_closed_when_chunk_is_missing(monkeypatch) -> None:
    values = _patch_keyring(monkeypatch)
    vault = KeyringVault(profile="test", kv=_KV())
    vault.put("connection", "a" * 5000)
    chunk = next(key for (_service, key) in values if key.startswith("adaos-chunk:"))
    values.pop(("adaos:profile:test", chunk))

    with pytest.raises(PermissionError, match="chunk is missing"):
        vault.get("connection")
