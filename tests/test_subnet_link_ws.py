from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.websockets import WebSocketDisconnect

from adaos.services import access_links
from adaos.services.subnet import link_ws, member_rpc


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


@pytest.mark.asyncio
async def test_member_rpc_routes_allowlisted_tool_with_member_identity(monkeypatch) -> None:
    sent: list[dict[str, object]] = []
    link = SimpleNamespace(send_json=lambda payload: None)

    async def send_json(payload: dict[str, object]) -> None:
        sent.append(payload)

    link.send_json = send_json
    calls: list[dict[str, object]] = []

    def run_member_tool(**kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)
        return {"response": "Ответ Арсения", "used_llm": True}

    monkeypatch.setattr(member_rpc, "run_member_tool", run_member_tool)

    await link_ws._handle_member_rpc_request(
        node_id="android-1",
        link=link,
        message={
            "t": "rpc.req",
            "id": "request-1",
            "method": "tools.call",
            "params": {
                "tool": "conversation_companions:talk",
                "arguments": {"message": "Привет"},
                "timeout": 40,
            },
        },
    )

    assert calls == [
        {
            "node_id": "android-1",
            "tool": "conversation_companions:talk",
            "arguments": {"message": "Привет"},
            "timeout": 40,
        }
    ]
    assert sent == [
        {
            "t": "rpc.res",
            "id": "request-1",
            "ok": True,
            "result": {"response": "Ответ Арсения", "used_llm": True},
        }
    ]


def test_member_rpc_rejects_non_allowlisted_tools_before_context_access() -> None:
    with pytest.raises(PermissionError, match="member_rpc_tool_not_allowed"):
        member_rpc.run_member_tool(
            node_id="android-1",
            tool="shell:run",
            arguments={"command": "whoami"},
            timeout=40,
        )


def test_member_rpc_allows_canonical_adaos_connect_prepare() -> None:
    assert "adaos_connect:prepare" in member_rpc.MEMBER_RPC_ALLOWED_TOOLS


def test_member_rpc_voice_activation_claim_uses_authenticated_node_id(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def claim(candidate, *, window_ms):  # noqa: ANN001, ANN202
        captured.update({"candidate": candidate, "window_ms": window_ms})
        return {"ok": True, "admitted": True, "winner_device_id": "android-1"}

    monkeypatch.setattr("adaos.services.voice_runtime.claim_voice_activation", claim)

    result = member_rpc.run_member_tool(
        node_id="android-1",
        tool="node.voice.activation.claim",
        arguments={
            "device_id": "spoofed-browser",
            "room_id": "subnet-1",
            "phrase_fingerprint": "phrase:hello",
            "window_ms": 280,
        },
        timeout=5,
    )

    assert result["admitted"] is True
    assert captured["candidate"]["device_id"] == "android-1"
    assert captured["candidate"]["_meta"]["member_rpc"] is True
