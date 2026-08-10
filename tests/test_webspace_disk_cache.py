from __future__ import annotations

from adaos.services.scenario.webspace_components import MaterializedWebspaceDiskCache


def test_materialized_disk_cache_owns_storage_and_invalidation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_MATERIALIZATION_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_WEBSPACE_MATERIALIZATION_DISK_CACHE_LIMIT", "2")
    cache = MaterializedWebspaceDiskCache()

    assert cache.store_record("first", {"identity": {"webspace_id": "desktop"}}) is True
    assert cache.store_record("second", {"identity": {"webspace_id": "research"}}) is True
    assert cache.load_record("first") == {
        "schema": cache.schema,
        "cache_key": "first",
        "identity": {"webspace_id": "desktop"},
    }

    removed = cache.discard_records(
        lambda record: record.get("identity", {}).get("webspace_id") == "desktop"
    )

    assert removed == 1
    assert cache.load_record("first") is None
    assert cache.load_record("second") is not None
