from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys
import types

import pytest

from adaos.domain import Event
from adaos.services.eventbus import LocalEventBus
from adaos.services.scenario.node_data_scope import node_scope_data_path

if "y_py" not in sys.modules:
    sys.modules["y_py"] = types.SimpleNamespace(YDoc=object)
if "ypy_websocket" not in sys.modules:
    ystore_mod = types.SimpleNamespace(BaseYStore=object, YDocNotFound=RuntimeError)
    sys.modules["ypy_websocket"] = types.SimpleNamespace(ystore=ystore_mod)
    sys.modules["ypy_websocket.ystore"] = ystore_mod

from adaos.services.router import service as router_service_module
from adaos.services.router.service import RouterService


pytestmark = pytest.mark.anyio


async def test_voice_chat_user_ignores_other_target_node(monkeypatch) -> None:
    bus = LocalEventBus()
    monkeypatch.setattr(router_service_module, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(node_id="member-local")))
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    seen: list[object] = []
    bus.subscribe("nlp.intent.detect.request", lambda ev: seen.append(ev))
    bus.publish(
        Event(
            type="voice.chat.user",
            source="test",
            ts=1.0,
            payload={
                "text": "weather in Berlin",
                "webspace_id": "default",
                "target_node_id": "member-remote",
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert seen == []


async def test_voice_chat_not_obtained_uses_skill_fallback(monkeypatch) -> None:
    bus = LocalEventBus()
    calls: list[tuple[str, dict[str, object]]] = []
    class _SkillCtx:
        def get(self):
            return None
        def set(self, *_args, **_kwargs):
            return None
        def clear(self):
            return None
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                node_id="member-local",
                root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=False)),
            ),
            paths=SimpleNamespace(skills_workspace_dir=lambda: Path(".")),
            skill_ctx=_SkillCtx(),
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(
        router_service_module,
        "execute_tool",
        lambda *_args, **kwargs: calls.append((kwargs["payload"]["text"], dict(kwargs["payload"].get("_meta") or {}))) or {"ok": True, "reply": "ok"},
    )
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    bus.publish(
        Event(
            type="nlp.intent.not_obtained",
            source="test",
            ts=1.0,
            payload={
                "text": "какая погода в москве",
                "reason": "no_intent",
                "_meta": {"route_id": "voice_chat", "webspace_id": "default"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert calls == [("какая погода в москве", {"route_id": "voice_chat", "webspace_id": "default"})]
def test_voice_chat_data_path_is_node_scoped() -> None:
    assert node_scope_data_path("data/voice_chat", "member-1") == "data/nodes/member-1/voice_chat"


async def test_io_out_chat_append_writes_node_scoped_history_without_crashing(monkeypatch) -> None:
    class _Txn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    class _Map(dict):
        def set(self, txn, key, value):  # noqa: ARG002
            self[key] = value

        def to_json(self):
            return dict(self)

    class _Doc:
        def __init__(self) -> None:
            self._maps = {"data": _Map()}

        def get_map(self, name: str):
            return self._maps.setdefault(name, _Map())

        def begin_transaction(self):
            return _Txn()

    doc = _Doc()

    class _AsyncDoc:
        async def __aenter__(self):
            return doc

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    async def _fake_async_get_ydoc(*_args, **_kwargs):
        return _AsyncDoc()

    class _MetaCtx:
        async def __aenter__(self):
            return {}

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    bus = LocalEventBus()
    monkeypatch.setattr(router_service_module, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(node_id="hub-node")))
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc())
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())

    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    bus.publish(
        Event(
            type="io.out.chat.append",
            source="test",
            ts=1.0,
            payload={
                "text": "hello",
                "_meta": {"webspace_id": "desktop", "target_node_id": "member-3"},
            },
        )
    )
    await bus.wait_for_idle(timeout=1.0)

    assert doc.get_map("data")["nodes"]["member-3"]["voice_chat"]["messages"][0]["text"] == "hello"
    assert float(doc.get_map("data")["nodes"]["member-3"]["voice_chat"]["last_refresh_ts"]) > 0
