from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from adaos.apps.api import subnet_api
from adaos.services.subnet_heartbeat_runtime import HeartbeatPersistenceRuntime


class _FakeRepo:
    def __init__(self, directory: "_FakeDirectory") -> None:
        self._directory = directory

    def get_node(self, node_id: str):
        return {"node_id": node_id} if self._directory.known else None


class _FakeDirectory:
    def __init__(self, *, known: bool = False, online: bool = False) -> None:
        self.known = known
        self.online = online
        self.repo = _FakeRepo(self)
        self.register_threads: list[int] = []
        self.heartbeat_threads: list[int] = []
        self.persisted: list[tuple[str, dict | None]] = []
        self.persist_started = threading.Event()
        self.persist_release = threading.Event()
        self.persist_release.set()
        self.persist_error: Exception | None = None

    def is_online(self, _node_id: str) -> bool:
        return self.online

    def on_register(self, _node_info) -> None:
        self.register_threads.append(threading.get_ident())
        self.known = True
        self.online = True

    def accept_heartbeat(self, _node_id: str) -> bool:
        if not self.known:
            return False
        self.online = True
        return True

    def persist_heartbeat(self, node_id: str, capacity, **_kwargs) -> None:
        self.heartbeat_threads.append(threading.get_ident())
        self.persist_started.set()
        self.persist_release.wait(timeout=2.0)
        if self.persist_error is not None:
            raise self.persist_error
        self.persisted.append((node_id, capacity))


def _hub_context():
    return SimpleNamespace(config=SimpleNamespace(role="hub", subnet_id="sn-test"))


@pytest.mark.asyncio
async def test_register_offloads_directory_io_and_emits_node_up_only_on_edge(monkeypatch) -> None:
    directory = _FakeDirectory()
    emitted: list[str] = []

    async def _emit(event_type: str, *_args, **_kwargs) -> None:
        emitted.append(event_type)

    monkeypatch.setattr(subnet_api, "get_ctx", _hub_context)
    monkeypatch.setattr(subnet_api, "get_directory", lambda: directory)
    monkeypatch.setattr(subnet_api.bus, "emit", _emit)

    request = subnet_api.RegisterRequest(node_id="member-1", subnet_id="sn-test")
    main_thread = threading.get_ident()

    first = await subnet_api.register(request)
    second = await subnet_api.register(request)

    assert first.ok is True
    assert second.ok is True
    assert emitted == ["net.subnet.node.up"]
    assert directory.register_threads
    assert all(thread_id != main_thread for thread_id in directory.register_threads)


@pytest.mark.asyncio
async def test_heartbeat_accepts_before_durable_io_and_preserves_unknown_404(monkeypatch) -> None:
    directory = _FakeDirectory(known=True, online=True)
    directory.persist_release.clear()
    runtime = HeartbeatPersistenceRuntime(idle_exit_s=0.05)
    monkeypatch.setattr(subnet_api, "get_ctx", _hub_context)
    monkeypatch.setattr(subnet_api, "get_directory", lambda: directory)
    monkeypatch.setattr(subnet_api, "get_heartbeat_persistence_runtime", lambda: runtime)

    main_thread = threading.get_ident()
    response = await subnet_api.heartbeat(subnet_api.HeartbeatRequest(node_id="member-1"))

    assert response.ok is True
    await asyncio.to_thread(directory.persist_started.wait, 1.0)
    assert directory.heartbeat_threads
    assert directory.heartbeat_threads[0] != main_thread
    assert directory.persisted == []

    directory.persist_release.set()
    await runtime.wait_idle()
    assert directory.persisted == [("member-1", None)]

    directory.known = False
    with pytest.raises(HTTPException) as exc_info:
        await subnet_api.heartbeat(subnet_api.HeartbeatRequest(node_id="unknown"))
    assert exc_info.value.status_code == 404
    await runtime.close()


@pytest.mark.asyncio
async def test_heartbeat_persistence_coalesces_pending_state_and_reports_failures() -> None:
    directory = _FakeDirectory(known=True, online=True)
    directory.persist_release.clear()
    runtime = HeartbeatPersistenceRuntime(idle_exit_s=0.05)

    runtime.submit(directory, node_id="member-1", capacity={"revision": 1}, node_state="ready", base_url=None)
    await asyncio.to_thread(directory.persist_started.wait, 1.0)
    runtime.submit(directory, node_id="member-1", capacity={"revision": 2}, node_state="ready", base_url=None)
    runtime.submit(directory, node_id="member-1", capacity={"revision": 3}, node_state="ready", base_url=None)
    directory.persist_release.set()
    await runtime.wait_idle()

    assert directory.persisted == [
        ("member-1", {"revision": 1}),
        ("member-1", {"revision": 3}),
    ]
    assert runtime.snapshot()["coalesced_total"] == 1

    directory.persist_error = RuntimeError("disk unavailable")
    runtime.submit(directory, node_id="member-1", capacity=None, node_state="ready", base_url=None)
    await runtime.wait_idle()
    snapshot = runtime.snapshot()
    assert snapshot["status"] == "degraded"
    assert snapshot["failed_total"] == 1
    assert snapshot["last_error"] == "RuntimeError: disk unavailable"
    await runtime.close()
