from __future__ import annotations

from adaos.services.scenario.webspace_components import (
    MaterializationExecutorOwner,
    WebspaceCacheState,
    WebspaceTaskState,
)


def test_webspace_state_owners_are_isolated() -> None:
    tasks = WebspaceTaskState()
    caches = WebspaceCacheState()

    tasks.webspace_rebuild_status["alpha"] = {"state": "running"}
    caches.put_resolved_webspace(
        "fingerprint",
        {"scenario_id": "home", "_cache_size_bytes": 10},
        max_entries=2,
        max_bytes=100,
    )

    assert tasks.webspace_rebuild_status == {"alpha": {"state": "running"}}
    assert caches.get_resolved_webspace("fingerprint") == {
        "scenario_id": "home",
        "_cache_size_bytes": 10,
    }
    assert tasks.workflow_sync_tasks == {}
    assert caches.clear_materialized_webspaces() == 0


def test_webspace_cache_owner_applies_lru_and_byte_limits() -> None:
    caches = WebspaceCacheState()

    caches.put_resolved_webspace(
        "first",
        {"_cache_size_bytes": 10},
        max_entries=2,
        max_bytes=25,
    )
    caches.put_resolved_webspace(
        "second",
        {"_cache_size_bytes": 10},
        max_entries=2,
        max_bytes=25,
    )
    assert caches.get_resolved_webspace("first") is not None

    caches.put_resolved_webspace(
        "third",
        {"_cache_size_bytes": 10},
        max_entries=2,
        max_bytes=25,
    )

    assert caches.get_resolved_webspace("second") is None
    assert caches.get_resolved_webspace("first") is not None
    assert caches.get_resolved_webspace("third") is not None


def test_webspace_cache_owner_invalidates_materialized_entries_by_identity() -> None:
    caches = WebspaceCacheState()
    for key, webspace_id in (("one", "alpha"), ("two", "beta"), ("three", "alpha")):
        caches.put_materialized_webspace(
            key,
            {"identity": {"webspace_id": webspace_id}, "_cache_size_bytes": 1},
            max_entries=4,
            max_bytes=100,
        )

    removed = caches.discard_materialized_webspaces(
        lambda _key, value: value.get("identity", {}).get("webspace_id") == "alpha"
    )

    assert removed == 2
    assert caches.get_materialized_webspace("one") is None
    assert caches.get_materialized_webspace("two") is not None
    assert caches.get_materialized_webspace("three") is None


def test_materialization_executor_owner_reuses_and_replaces_executor() -> None:
    owner = MaterializationExecutorOwner()

    first = owner.get(max_workers=1)
    assert owner.get(max_workers=4) is first

    owner.shutdown()
    second = owner.get(max_workers=1)

    assert second is not first
    owner.shutdown()
