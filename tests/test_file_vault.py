from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from adaos.adapters.secrets.file_vault import FileVault


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
