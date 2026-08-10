from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from adaos.apps.api import subnet_api


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

    def is_online(self, _node_id: str) -> bool:
        return self.online

    def on_register(self, _node_info) -> None:
        self.register_threads.append(threading.get_ident())
        self.known = True
        self.online = True

    def on_heartbeat(self, _node_id: str, _capacity, **_kwargs) -> None:
        self.heartbeat_threads.append(threading.get_ident())
        self.online = True


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
async def test_heartbeat_offloads_directory_io_and_preserves_unknown_404(monkeypatch) -> None:
    directory = _FakeDirectory(known=True, online=True)
    monkeypatch.setattr(subnet_api, "get_ctx", _hub_context)
    monkeypatch.setattr(subnet_api, "get_directory", lambda: directory)

    main_thread = threading.get_ident()
    response = await subnet_api.heartbeat(subnet_api.HeartbeatRequest(node_id="member-1"))

    assert response.ok is True
    assert directory.heartbeat_threads
    assert directory.heartbeat_threads[0] != main_thread

    directory.known = False
    with pytest.raises(HTTPException) as exc_info:
        await subnet_api.heartbeat(subnet_api.HeartbeatRequest(node_id="unknown"))
    assert exc_info.value.status_code == 404
