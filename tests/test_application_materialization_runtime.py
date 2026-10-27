from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

from adaos.domain.application import RuntimeSelection
from adaos.services.applications import materialization
from adaos.services.artifact_pipeline.storage import atomic_write_json


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

    owner_thread = threading.get_ident()
    owner, calls = setup_requests(tmp_path, monkeypatch, total=3)
    rebuilds, metadata_threads = [], []

    class Updates:
        def __init__(self, **_kwargs):
            metadata_threads.append(threading.get_ident())

        def reconcile_local_trials(self, *_args, **_kwargs):
            metadata_threads.append(threading.get_ident())

    async def rebuild(room, **_kwargs):
        rebuilds.append((room, threading.get_ident()))
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
    assert all(thread == owner_thread for _, thread in rebuilds)
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
