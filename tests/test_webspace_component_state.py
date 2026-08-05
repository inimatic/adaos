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
    caches.resolved_webspaces["fingerprint"] = {"scenario_id": "home"}

    assert tasks.webspace_rebuild_status == {"alpha": {"state": "running"}}
    assert caches.resolved_webspaces == {"fingerprint": {"scenario_id": "home"}}
    assert tasks.workflow_sync_tasks == {}
    assert caches.materialized_webspaces == {}


def test_materialization_executor_owner_reuses_and_replaces_executor() -> None:
    owner = MaterializationExecutorOwner()

    first = owner.get(max_workers=1)
    assert owner.get(max_workers=4) is first

    owner.shutdown()
    second = owner.get(max_workers=1)

    assert second is not first
    owner.shutdown()
