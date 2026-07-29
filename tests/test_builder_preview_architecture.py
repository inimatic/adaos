from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
import threading

import pytest

from adaos.services.builder.preview_reconciler import BuilderPreviewReconciler
from adaos.domain.project_events import (
    BUILDER_CONTEXT_SELECTED,
    PROJECT_CONTENT_CHANGED,
    legacy_project_event_topic,
)
from adaos.services.workspaces.relations import (
    BUILDER_PROJECT_PREVIEW,
    BUILDER_SELF_HOST,
    WebspaceRelationshipRegistry,
)


class _Sql:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self):
        return sqlite3.connect(self.path)


@pytest.mark.parametrize(
    "reason",
    [
        "project_loaded",
        "project_selected",
        "builder_project_created",
        "builder_project_switched",
    ],
)
def test_legacy_selection_reasons_never_map_to_content_reload(reason: str) -> None:
    assert legacy_project_event_topic(reason) == BUILDER_CONTEXT_SELECTED
    assert legacy_project_event_topic("builder_project_updated") == PROJECT_CONTENT_CHANGED


def test_builder_preview_topology_allows_only_one_self_host_level(tmp_path: Path) -> None:
    registry = WebspaceRelationshipRegistry(_Sql(tmp_path / "relations.db"))

    self_host, created = registry.ensure(
        "prod-builder",
        purpose=BUILDER_SELF_HOST,
        scenario_id="builder",
    )
    assert created is True
    assert self_host.target_webspace_id.startswith("preview-")
    assert not self_host.target_webspace_id.endswith("-dev")
    assert registry.resolve_builder_host(self_host.target_webspace_id) == self_host.target_webspace_id

    child, _created = registry.ensure(
        self_host.target_webspace_id,
        purpose=BUILDER_PROJECT_PREVIEW,
        scenario_id="target-scenario",
    )
    assert child.source_webspace_id == self_host.target_webspace_id
    assert child.target_webspace_id == f"{self_host.target_webspace_id}-dev"
    assert registry.resolve_builder_host(child.target_webspace_id) == self_host.target_webspace_id

    with pytest.raises(ValueError, match="cannot own"):
        registry.ensure(
            child.target_webspace_id,
            purpose=BUILDER_PROJECT_PREVIEW,
            scenario_id="forbidden-grandchild",
        )

    demoted, created = registry.ensure(
        "prod-builder",
        purpose=BUILDER_PROJECT_PREVIEW,
        scenario_id="ordinary-project",
    )
    assert created is False
    assert demoted.target_webspace_id == self_host.target_webspace_id
    assert registry.get_outgoing(self_host.target_webspace_id) is None


def test_builder_preview_topology_adopts_legacy_binding_without_parsing_it(tmp_path: Path) -> None:
    registry = WebspaceRelationshipRegistry(_Sql(tmp_path / "relations.db"))

    relation, created = registry.ensure(
        "dev1",
        purpose=BUILDER_PROJECT_PREVIEW,
        scenario_id="shopping",
        legacy_target_webspace_id="dev1-dev",
        metadata={"migrated_from": "test"},
    )

    assert created is True
    assert relation.target_webspace_id == "dev1-dev"
    assert registry.resolve_builder_host("dev1-dev") == "dev1"
    assert registry.resolve_builder_host("unrelated-dev") == "unrelated-dev"


def test_active_builder_claims_legacy_preview_and_owns_one_named_child(tmp_path: Path) -> None:
    registry = WebspaceRelationshipRegistry(_Sql(tmp_path / "relations.db"))
    original, _created = registry.ensure(
        "dev1",
        purpose=BUILDER_PROJECT_PREVIEW,
        scenario_id="previous-project",
        legacy_target_webspace_id="dev1-dev",
    )
    assert original.purpose == BUILDER_PROJECT_PREVIEW

    claimed = registry.claim_builder_self_host("dev1-dev", scenario_id="builder")
    assert claimed == "dev1-dev"
    promoted = registry.get_outgoing("dev1")
    assert promoted is not None
    assert promoted.purpose == BUILDER_SELF_HOST
    assert promoted.target_webspace_id == "dev1-dev"

    child, _created = registry.ensure(
        claimed,
        purpose=BUILDER_PROJECT_PREVIEW,
        scenario_id="test05_recipes",
    )
    assert child.source_webspace_id == "dev1-dev"
    assert child.target_webspace_id == "dev1-dev-dev"

    with pytest.raises(ValueError, match="cannot own"):
        registry.ensure(
            child.target_webspace_id,
            purpose=BUILDER_PROJECT_PREVIEW,
            scenario_id="forbidden-grandchild",
        )


@pytest.mark.asyncio
async def test_preview_reconciler_applies_only_latest_generation(tmp_path: Path) -> None:
    reconciler = BuilderPreviewReconciler(state_dir=tmp_path)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    applied: list[str] = []

    reconciler.request(
        source_webspace_id="dev1",
        preview_webspace_id="preview-one",
        project_kind="scenario",
        project_id="first",
        desired_scenario="first",
    )

    async def _apply(record):
        desired = str(record["desired_scenario"])
        applied.append(desired)
        if desired == "first":
            first_started.set()
            await release_first.wait()
        return {"ok": True, "accepted": True, "scenario_id": desired}

    task = asyncio.create_task(reconciler.reconcile("dev1", _apply, wait=True))
    await first_started.wait()
    second, coalesced = reconciler.request(
        source_webspace_id="dev1",
        preview_webspace_id="preview-one",
        project_kind="scenario",
        project_id="second",
        desired_scenario="second",
    )
    assert coalesced is False
    assert second["generation"] == 2
    release_first.set()

    result = await task
    assert result["status"] == "ready"
    assert result["desired_scenario"] == "second"
    assert result["observed_scenario"] == "second"
    assert applied == ["first", "second"]


def test_preview_reconciler_serializes_materialization_across_event_loops(tmp_path: Path) -> None:
    reconciler = BuilderPreviewReconciler(state_dir=tmp_path)
    first_started = threading.Event()
    release_first = threading.Event()
    counter_lock = threading.Lock()
    results: list[dict[str, object]] = []
    errors: list[BaseException] = []
    applied: list[str] = []
    active = 0
    max_active = 0

    reconciler.request(
        source_webspace_id="dev1",
        preview_webspace_id="preview-one",
        project_kind="scenario",
        project_id="first",
        desired_scenario="first",
    )

    async def _apply(record):
        nonlocal active, max_active
        desired = str(record["desired_scenario"])
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
            applied.append(desired)
        try:
            if desired == "first":
                first_started.set()
                await asyncio.to_thread(release_first.wait)
            return {"ok": True, "accepted": True, "scenario_id": desired}
        finally:
            with counter_lock:
                active -= 1

    def _run_loop() -> None:
        try:
            results.append(asyncio.run(reconciler.reconcile("dev1", _apply, wait=True)))
        except BaseException as exc:
            errors.append(exc)

    first_thread = threading.Thread(target=_run_loop)
    first_thread.start()
    assert first_started.wait(timeout=5.0)

    reconciler.request(
        source_webspace_id="dev1",
        preview_webspace_id="preview-one",
        project_kind="scenario",
        project_id="second",
        desired_scenario="second",
    )
    second_thread = threading.Thread(target=_run_loop)
    second_thread.start()
    assert second_thread.is_alive()
    assert max_active == 1

    release_first.set()
    first_thread.join(timeout=5.0)
    second_thread.join(timeout=5.0)

    assert errors == []
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert max_active == 1
    assert applied == ["first", "second"]
    assert all(result["observed_scenario"] == "second" for result in results)


@pytest.mark.asyncio
async def test_preview_reconciler_coalesces_selection_stress_and_bounds_state(tmp_path: Path) -> None:
    reconciler = BuilderPreviewReconciler(state_dir=tmp_path)
    for _index in range(100):
        _record, coalesced = reconciler.request(
            source_webspace_id="dev1",
            preview_webspace_id="preview-one",
            project_kind="scenario",
            project_id="builder",
            desired_scenario="builder",
        )
    assert coalesced is True

    calls = 0

    async def _apply(_record):
        nonlocal calls
        calls += 1
        return {"ok": True, "accepted": True, "scenario_id": "builder"}

    result = await reconciler.reconcile("dev1", _apply, wait=True)
    assert result["status"] == "ready"
    assert result["generation"] == 1
    assert calls == 1
    assert len(list(reconciler.root.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_preview_reconciler_keeps_background_apply_as_accepted(tmp_path: Path) -> None:
    reconciler = BuilderPreviewReconciler(state_dir=tmp_path)
    reconciler.request(
        source_webspace_id="dev1",
        preview_webspace_id="preview-one",
        project_kind="scenario",
        project_id="builder",
        desired_scenario="builder",
    )

    async def _apply(_record):
        return {"ok": True, "accepted": True, "background_rebuild": True}

    result = await reconciler.reconcile("dev1", _apply, wait=True)
    assert result["status"] == "accepted"
    assert result["observed_scenario"] is None


def test_materialization_worker_default_and_test_override(monkeypatch) -> None:
    from adaos.services.scenario import webspace_runtime

    monkeypatch.delenv("ADAOS_MATERIALIZATION_WORKER", raising=False)
    monkeypatch.delenv("ADAOS_TESTING", raising=False)
    assert webspace_runtime._materialization_worker_enabled() is True

    monkeypatch.setenv("ADAOS_TESTING", "1")
    assert webspace_runtime._materialization_worker_enabled() is False

    monkeypatch.setenv("ADAOS_MATERIALIZATION_WORKER", "1")
    assert webspace_runtime._materialization_worker_enabled() is True


@pytest.mark.asyncio
async def test_preview_reconciler_keeps_one_bounded_record_for_100_distinct_selections(tmp_path: Path) -> None:
    reconciler = BuilderPreviewReconciler(state_dir=tmp_path)
    applied: list[str] = []

    async def _apply(record):
        desired = str(record["desired_scenario"])
        applied.append(desired)
        return {"ok": True, "accepted": True, "scenario_id": desired}

    for index in range(100):
        scenario_id = f"scenario-{index}"
        reconciler.request(
            source_webspace_id="dev1",
            preview_webspace_id="preview-one",
            project_kind="scenario",
            project_id=scenario_id,
            desired_scenario=scenario_id,
        )
        result = await reconciler.reconcile("dev1", _apply, wait=True)

    state_files = list(reconciler.root.glob("*.json"))
    assert result["generation"] == 100
    assert result["observed_scenario"] == "scenario-99"
    assert applied == [f"scenario-{index}" for index in range(100)]
    assert len(state_files) == 1
    assert state_files[0].stat().st_size < 16 * 1024
