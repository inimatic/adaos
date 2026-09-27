from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from adaos.adapters.secrets.file_vault import FileVault
from adaos.services.crypto.vault import (
    ENV_MASTER,
    load_or_create_local_master,
    load_or_create_master,
    local_master_key_path,
)


def vault(tmp_path):
    key = Fernet.generate_key()
    policy = SimpleNamespace(require_read=lambda _: None, require_write=lambda _: None)
    return FileVault(base_dir=tmp_path, fs=policy, key_get=lambda: key, key_set=lambda _: None)


def test_concurrent_vault_mutations_do_not_lose_keys_or_expose_plaintext(tmp_path):
    store = vault(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda number: store.put(f"key{number}", f"synthetic-secret-{number}"), range(24)))
    assert len(store.list()) == 24
    assert store.get("key13") == "synthetic-secret-13"
    assert "synthetic-secret" not in store.vault_path.read_text(encoding="utf-8")
    store.import_items([{"key": "imported", "value": "synthetic-import"}])
    store.delete("key13")
    assert store.get("key13") is None and store.get("imported") == "synthetic-import"
    assert len(store.list()) == 24


@pytest.mark.parametrize("contents", ["{", "[]", '{"profile": []}'])
def test_corrupt_vault_is_not_replaced_with_empty_store(tmp_path, contents):
    store = vault(tmp_path)
    store.vault_path.parent.mkdir(parents=True)
    store.vault_path.write_text(contents, encoding="utf-8")
    with pytest.raises(PermissionError, match="refusing to replace"):
        store.put("new", "synthetic")
    assert store.vault_path.read_text(encoding="utf-8") == contents


def test_headless_local_master_is_persistent_and_concurrency_safe(tmp_path):
    path = local_master_key_path(tmp_path, "production")
    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(lambda _index: load_or_create_local_master(path), range(16)))

    assert len(set(keys)) == 1
    assert path.read_bytes() == keys[0]
    assert not list(path.parent.glob("*.tmp"))
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_environment_fernet_key_seeds_local_master(monkeypatch, tmp_path):
    expected = Fernet.generate_key()
    monkeypatch.setenv(ENV_MASTER, expected.decode("ascii"))
    path = local_master_key_path(tmp_path, "seeded")

    assert load_or_create_local_master(path) == expected
    assert load_or_create_master(lambda: None, lambda _value: None) == expected


def test_invalid_environment_master_key_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_MASTER, "not-a-fernet-key")

    with pytest.raises(ValueError, match=ENV_MASTER):
        load_or_create_local_master(local_master_key_path(tmp_path, "invalid"))


def test_local_master_rejects_symbolic_link(tmp_path):
    target = tmp_path / "actual.key"
    target.write_bytes(Fernet.generate_key())
    link = tmp_path / "linked.key"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(PermissionError, match="symbolic link"):
        load_or_create_local_master(link)
