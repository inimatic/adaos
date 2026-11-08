from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from adaos.domain.application import RuntimeSelection
from adaos.services.applications import materialization
from adaos.services.artifact_pipeline.storage import atomic_write_json


def test_live_projection_readiness_requires_exact_post_commit_core_ack(monkeypatch):
    from adaos.services.yjs import projection_convergence
    observed = {'state': 'applied', 'published_at': 1793117290,
        'identity': {'application_id': 'chat', 'application_release_digest': 'sha256:exact'}}
    monkeypatch.setattr(projection_convergence, 'snapshot', lambda _room: observed)
    query = lambda: materialization.live_projection_ready('chat', 'sha256:exact', 'room', since='2026-10-27T16:02:13Z')
    assert query()
    observed['identity']['application_release_digest'] = 'sha256:old'
    assert not query()
    observed['identity']['application_release_digest'] = 'sha256:exact'
    observed['state'] = 'awaiting_client'
    assert not query()
    observed['state'] = 'applied'
    observed['published_at'] = 1
    assert not query()


def setup_requests(tmp_path, monkeypatch, *, completed=False, total=2):
    owner = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path))
    selections = [RuntimeSelection(webspace_id=f"isolated-{index}", application_id=f"app{index}",
        source="stable_installation", release_digest="sha256:" + "a" * 64,
        runtime_root_ref="workspace", revision=1) for index in range(total)]
    calls = []

    class Store:
        def __init__(self, _state):
            pass

        def list_runtime_selections(self):
            calls.append(threading.get_ident())
            time.sleep(0.1)
            return selections

        def list_application_runtime_selections(self, application_id):
            return tuple(item for item in selections if item.application_id == application_id)

    monkeypatch.setattr(materialization, "ApplicationStore", Store)
    for selection in selections:
        materialization.request_materialization(owner, selection.application_id)
        if completed:
            path = materialization.request_path(tmp_path, selection.application_id)
            request = json.loads(path.read_text())
            atomic_write_json(path, {**request, "status": "completed"})
    calls.clear()
    return owner, calls


def test_completed_requests_use_one_off_loop_catalog_snapshot(tmp_path, monkeypatch):
    owner_thread = threading.get_ident()
    owner, calls = setup_requests(tmp_path, monkeypatch, completed=True)
    for path in (tmp_path / "applications/materialization_requests").glob("*.json"):
        legacy = json.loads(path.read_text())
        legacy.pop("priority_webspace_ids", None)
        atomic_write_json(path, legacy)

    async def exercise():
        task = asyncio.create_task(materialization.reconcile_materializations(owner))
        beats = 0
        while not task.done():
            await asyncio.sleep(0.01)
            beats += 1
        assert await task == []
        assert beats >= 5

    asyncio.run(exercise())
    assert len(calls) == 1 and calls[0] != owner_thread


def test_pending_rebuilds_keep_owner_affinity_and_bound_work(tmp_path, monkeypatch):
    from adaos.services import component_updates
    from adaos.services.scenario import webspace_runtime
    from adaos.services.yjs import projection_convergence

    owner_thread = threading.get_ident()
    owner, calls = setup_requests(tmp_path, monkeypatch, total=3)
    rebuilds, metadata_threads = [], []
    monkeypatch.setattr(
        projection_convergence,
        "current_navigation_projection",
        lambda room: {
            "ready": True,
            "reason": "projected_navigation",
            "request_id": f"nav:{room}:current",
            "scenario_id": f"scenario:{room}",
        },
    )

    class Updates:
        def __init__(self, **_kwargs):
            metadata_threads.append(threading.get_ident())

        def reconcile_local_trials(self, *_args, **_kwargs):
            metadata_threads.append(threading.get_ident())

    async def rebuild(room, **_kwargs):
        rebuilds.append((room, threading.get_ident(), _kwargs.get("request_id")))
        return {"ok": True}

    original_write = materialization.atomic_write_json

    def record_write(*args, **kwargs):
        metadata_threads.append(threading.get_ident())
        return original_write(*args, **kwargs)

    monkeypatch.setattr(component_updates, "ComponentUpdateService", Updates)
    monkeypatch.setattr(webspace_runtime, "rebuild_webspace_from_sources", rebuild)
    monkeypatch.setattr(materialization, "atomic_write_json", record_write)
    results = asyncio.run(materialization.reconcile_materializations(owner, limit=2))
    assert len(results) == len(rebuilds) == 2
    assert all(item["status"] == "completed" for item in results)
    assert len(calls) == 1 and calls[0] != owner_thread
    assert all(thread == owner_thread for _, thread, _fence in rebuilds)
    assert all(fence == f"nav:{room}:current" for room, _thread, fence in rebuilds)
    assert metadata_threads and all(thread != owner_thread for thread in metadata_threads)


def test_old_completion_cannot_overwrite_new_request(tmp_path, monkeypatch):
    from adaos.services import component_updates
    from adaos.services.scenario import webspace_runtime

    owner, _calls = setup_requests(tmp_path, monkeypatch, total=1)
    path = materialization.request_path(tmp_path, "app0")
    monkeypatch.setattr(component_updates.ComponentUpdateService, "reconcile_local_trials", lambda *_args, **_kwargs: None)

    async def rebuild(_room, **_kwargs):
        request = json.loads(path.read_text())
        atomic_write_json(path, {**request, "request_digest": "newer-request", "status": "pending"})
        return {"ok": True}

    monkeypatch.setattr(webspace_runtime, "rebuild_webspace_from_sources", rebuild)
    assert asyncio.run(materialization.reconcile_materializations(owner)) == []
    assert json.loads(path.read_text())["request_digest"] == "newer-request"
    assert json.loads(path.read_text())["status"] == "pending"


def test_target_is_first_and_partial_success_is_durable_and_not_rebuilt(tmp_path, monkeypatch):
    from adaos.services import component_updates
    from adaos.services.scenario import webspace_runtime

    owner, _ = setup_requests(tmp_path, monkeypatch, total=1)
    materialization.request_materialization(owner, "app0", webspace_ids=("target",))
    path = materialization.request_path(tmp_path, "app0")
    calls = []
    fail = True
    monkeypatch.setattr(component_updates.ComponentUpdateService, "reconcile_local_trials", lambda *_args, **_kwargs: None)

    async def rebuild(room, **_kwargs):
        calls.append(room)
        if room == "isolated-0":
            progress = json.loads(path.read_text())
            assert progress["status"] == "pending"
            assert progress["observations"][0] == {"webspace_id": "target", "ok": True}
            if fail:
                raise RuntimeError("temporary offline projection")
        return {"ok": True}

    monkeypatch.setattr(webspace_runtime, "rebuild_webspace_from_sources", rebuild)
    assert asyncio.run(materialization.reconcile_materializations(owner))[0]["status"] == "pending"
    assert calls == ["target", "isolated-0"]
    fail = False
    request = json.loads(path.read_text())
    retry_at = datetime.fromisoformat(request["observations"][1]["next_retry_at"])
    assert asyncio.run(materialization.reconcile_materializations(
        owner, now=retry_at + timedelta(microseconds=1)
    ))[0]["status"] == "completed"
    assert calls == ["target", "isolated-0", "isolated-0"]
    assert json.loads(path.read_text())["attempts"] == 2


def test_navigation_in_progress_defers_background_projection(tmp_path, monkeypatch):
    from adaos.services import component_updates
    from adaos.services.scenario import webspace_runtime
    from adaos.services.yjs import projection_convergence

    owner, _ = setup_requests(tmp_path, monkeypatch, total=1)
    rebuilds = []
    monkeypatch.setattr(
        projection_convergence,
        "current_navigation_projection",
        lambda _room: {
            "ready": False,
            "reason": "navigation_in_progress",
            "request_id": "nav:new",
            "scenario_id": None,
        },
    )
    monkeypatch.setattr(
        component_updates.ComponentUpdateService,
        "reconcile_local_trials",
        lambda *_args, **_kwargs: None,
    )

    async def rebuild(*args, **kwargs):
        rebuilds.append((args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(webspace_runtime, "rebuild_webspace_from_sources", rebuild)

    result = asyncio.run(materialization.reconcile_materializations(owner))

    assert result[0]["status"] == "pending"
    assert rebuilds == []
    request = json.loads(materialization.request_path(tmp_path, "app0").read_text())
    assert request["observations"][0]["reason"] == "navigation_in_progress"
