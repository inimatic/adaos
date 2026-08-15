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
        self.registrations: list[dict] = []
        self.registration_started = threading.Event()
        self.registration_release = threading.Event()
        self.registration_release.set()
        self.registration_error: Exception | None = None
        self.heartbeat_threads: list[int] = []
        self.persisted: list[tuple[str, dict | None]] = []
        self.persist_started = threading.Event()
        self.persist_release = threading.Event()
        self.persist_release.set()
        self.persist_error: Exception | None = None

    def is_online(self, _node_id: str) -> bool:
        return self.online

    def accept_registration(self, _node_info) -> bool:
        became_online = not self.known or not self.online
        self.known = True
        self.online = True
        return became_online

    def persist_registration(self, node_info) -> None:
        self.register_threads.append(threading.get_ident())
        self.registration_started.set()
        self.registration_release.wait(timeout=2.0)
        if self.registration_error is not None:
            raise self.registration_error
        self.registrations.append(dict(node_info))

    def accept_heartbeat(self, _node_id: str, **_kwargs) -> bool:
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
async def test_register_accepts_before_durable_io_and_emits_node_up_only_on_edge(monkeypatch) -> None:
    directory = _FakeDirectory()
    directory.registration_release.clear()
    runtime = HeartbeatPersistenceRuntime(idle_exit_s=0.05)
    emitted: list[str] = []

    async def _emit(event_type: str, *_args, **_kwargs) -> None:
        emitted.append(event_type)

    monkeypatch.setattr(subnet_api, "get_ctx", _hub_context)
    monkeypatch.setattr(subnet_api, "get_directory", lambda: directory)
    monkeypatch.setattr(subnet_api, "get_heartbeat_persistence_runtime", lambda: runtime)
    monkeypatch.setattr(subnet_api.bus, "emit", _emit)

    request = subnet_api.RegisterRequest(node_id="member-1", subnet_id="sn-test")
    main_thread = threading.get_ident()

    first = await subnet_api.register(request)
    second = await subnet_api.register(request)
    await asyncio.sleep(0)

    assert first.ok is True
    assert second.ok is True
    assert emitted == ["net.subnet.node.up"]
    await asyncio.to_thread(directory.registration_started.wait, 1.0)
    assert directory.register_threads
    assert all(thread_id != main_thread for thread_id in directory.register_threads)
    assert directory.registrations == []
    directory.registration_release.set()
    await runtime.wait_idle()
    assert directory.registrations
    snapshot = runtime.snapshot()
    assert snapshot["registration_accepted_total"] == 2
    assert snapshot["registration_persisted_total"] >= 1
    assert snapshot["executor"] == "dedicated_single_worker"
    await runtime.close()


@pytest.mark.asyncio
async def test_register_response_does_not_wait_for_node_up_handlers(monkeypatch) -> None:
    directory = _FakeDirectory()
    runtime = HeartbeatPersistenceRuntime(idle_exit_s=0.05)
    emission_started = asyncio.Event()
    emission_release = asyncio.Event()

    async def _emit(*_args, **_kwargs) -> None:
        emission_started.set()
        await emission_release.wait()

    monkeypatch.setattr(subnet_api, "get_ctx", _hub_context)
    monkeypatch.setattr(subnet_api, "get_directory", lambda: directory)
    monkeypatch.setattr(subnet_api, "get_heartbeat_persistence_runtime", lambda: runtime)
    monkeypatch.setattr(subnet_api.bus, "emit", _emit)

    response = await asyncio.wait_for(
        subnet_api.register(subnet_api.RegisterRequest(node_id="member-1", subnet_id="sn-test")),
        timeout=0.1,
    )

    assert response.ok is True
    await asyncio.wait_for(emission_started.wait(), timeout=0.1)
    assert len(subnet_api._BACKGROUND_TASKS) == 1
    background_tasks = list(subnet_api._BACKGROUND_TASKS)
    emission_release.set()
    await asyncio.gather(*background_tasks)
    await asyncio.sleep(0)
    assert not subnet_api._BACKGROUND_TASKS
    await runtime.wait_idle()
    await runtime.close()


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


@pytest.mark.asyncio
async def test_registration_persistence_retries_failure_and_recovers() -> None:
    directory = _FakeDirectory()
    directory.registration_error = RuntimeError("database locked")
    runtime = HeartbeatPersistenceRuntime(idle_exit_s=0.05)

    runtime.submit_registration(directory, node_info={"node_id": "member-1", "subnet_id": "sn-test"})
    await asyncio.to_thread(directory.registration_started.wait, 1.0)
    deadline = asyncio.get_running_loop().time() + 2.0
    while runtime.snapshot()["registration_failed_total"] < 1:
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.01)
    snapshot = runtime.snapshot()
    assert snapshot["status"] == "degraded"
    assert snapshot["pending_registration_total"] == 1

    directory.registration_error = None
    await runtime.wait_idle(timeout_s=3.0)
    snapshot = runtime.snapshot()
    assert snapshot["status"] == "ready"
    assert snapshot["registration_persisted_total"] == 1
    assert snapshot["pending_registration_total"] == 0
    await runtime.close()
