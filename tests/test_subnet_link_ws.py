from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.websockets import WebSocketDisconnect

from adaos.services import access_links
from adaos.services.subnet import link_ws


class _FakeWebSocket:
    def __init__(self, *, terminal_error: BaseException | None = None) -> None:
        self.headers = {"x-adaos-token": "dev-local-token"}
        self.query_params = {}
        self.sent: list[dict[str, object]] = []
        self.closed: list[int] = []
        self.accepted = False
        self.receive_calls = 0
        self._terminal_error = terminal_error
        self._messages = [
            {
                "t": "hello",
                "node_id": "member-1",
                "subnet_id": "subnet-1",
                "hostname": "member-host",
                "roles": ["member"],
                "node_names": ["Member 1"],
            }
        ]

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(dict(payload))

    async def receive_json(self) -> dict[str, object]:
        self.receive_calls += 1
        if self._messages:
            return dict(self._messages.pop(0))
        if self._terminal_error is not None:
            raise self._terminal_error
        raise WebSocketDisconnect(code=1000)

    async def close(self, code: int = 1000) -> None:
        self.closed.append(code)


class _FakeLink:
    async def send_json(self, payload: dict[str, object]) -> None:  # noqa: ARG002
        raise AssertionError("hub frames must not be sent before the handshake test completes")


class _FakeManager:
    def __init__(self, websocket: _FakeWebSocket, events: list[str]) -> None:
        self._websocket = websocket
        self._events = events

    async def register(self, node_id: str, *args, **kwargs) -> _FakeLink:  # noqa: ANN002, ANN003, ARG002
        assert self._websocket.sent
        assert self._websocket.sent[0]["t"] == "hello.ack"
        assert self._websocket.sent[0]["ok"] is True
        self._events.append(f"register:{node_id}")
        return _FakeLink()

    async def refresh_member_after_connect(self, node_id: str, *, reason: str) -> dict[str, object]:
        self._events.append(f"refresh:{node_id}:{reason}")
        return {"ok": True}

    async def unregister(self, node_id: str, **kwargs) -> dict[str, object]:  # noqa: ANN003
        assert kwargs.get("expected_link") is not None
        self._events.append(f"unregister:{node_id}")
        return {"ok": True}


@pytest.mark.asyncio
async def test_subnet_ws_sends_hello_ack_before_member_registration(monkeypatch) -> None:
    websocket = _FakeWebSocket()
    events: list[str] = []
    manager = _FakeManager(websocket, events)

    monkeypatch.setattr(
        link_ws,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                role="hub",
                token="dev-local-token",
                node_id="hub-1",
                subnet_id="subnet-1",
            )
        ),
    )
    monkeypatch.setattr(link_ws, "get_hub_link_manager", lambda: manager)
    monkeypatch.setattr(access_links, "authorize_link", lambda kind, node_id: (True, ""))  # noqa: ARG005
    monkeypatch.setattr(
        access_links,
        "touch_member_link",
        lambda node_id, **kwargs: events.append(f"touch:{node_id}:{kwargs.get('connection_state')}"),
    )

    await link_ws.subnet_ws(websocket)  # type: ignore[arg-type]

    assert websocket.accepted is True
    assert websocket.sent == [
        {
            "t": "hello.ack",
            "ok": True,
            "hub_node_id": "hub-1",
            "subnet_id": "subnet-1",
            "server_time": websocket.sent[0]["server_time"],
        }
    ]
    assert events == [
        "register:member-1",
        "touch:member-1:connected",
        "refresh:member-1:member_link_connected",
        "touch:member-1:closed",
        "unregister:member-1",
    ]


@pytest.mark.asyncio
async def test_subnet_ws_stops_after_closed_websocket_runtime_error(monkeypatch) -> None:
    websocket = _FakeWebSocket(
        terminal_error=RuntimeError('Cannot call "receive" once a disconnect message has been received.'),
    )
    events: list[str] = []
    manager = _FakeManager(websocket, events)

    monkeypatch.setattr(
        link_ws,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                role="hub",
                token="dev-local-token",
                node_id="hub-1",
                subnet_id="subnet-1",
            )
        ),
    )
    monkeypatch.setattr(link_ws, "get_hub_link_manager", lambda: manager)
    monkeypatch.setattr(access_links, "authorize_link", lambda kind, node_id: (True, ""))  # noqa: ARG005
    monkeypatch.setattr(access_links, "touch_member_link", lambda *args, **kwargs: None)

    await link_ws.subnet_ws(websocket)  # type: ignore[arg-type]

    assert websocket.receive_calls == 2
    assert events[-1] == "unregister:member-1"
