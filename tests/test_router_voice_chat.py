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

try:
    import y_py  # noqa: F401
except ImportError:
    sys.modules["y_py"] = types.SimpleNamespace(YDoc=object)
try:
    import ypy_websocket  # noqa: F401
except ImportError:
    ystore_mod = types.SimpleNamespace(BaseYStore=object, YDocNotFound=RuntimeError)
    sys.modules["ypy_websocket"] = types.SimpleNamespace(ystore=ystore_mod)
    sys.modules["ypy_websocket.ystore"] = ystore_mod

from adaos.services.router import service as router_service_module
from adaos.services.router.service import RouterService


pytestmark = pytest.mark.anyio


async def _drain_voice_chat_persist(router: RouterService) -> None:
    pending = list(getattr(router, "_voice_chat_persist_tasks", set()))
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


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
    monkeypatch.delenv("ADAOS_VOICE_CHAT_INTENT_DEMO", raising=False)
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
async def test_voice_chat_not_obtained_skips_skill_fallback_during_intent_demo(monkeypatch) -> None:
    bus = LocalEventBus()
    calls: list[object] = []

    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "1")
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                node_id="member-local",
                root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=False)),
            ),
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(
        router_service_module,
        "execute_tool",
        lambda *_args, **_kwargs: calls.append(object()) or {"ok": True},
    )
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    bus.publish(
        Event(
            type="nlp.intent.not_obtained",
            source="test",
            ts=1.0,
            payload={
                "text": "weather in Moscow",
                "reason": "no_intent_mapping",
                "_meta": {"route_id": "voice_chat", "webspace_id": "default"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert calls == []


def test_voice_chat_data_path_is_node_scoped() -> None:
    assert node_scope_data_path("data/voice_chat", "member-1") == "data/nodes/member-1/voice_chat"


async def test_voice_chat_user_defaults_history_to_local_node_when_target_missing(monkeypatch) -> None:
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

    class _AsyncDoc:
        async def __aenter__(self):
            return doc

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class _MetaCtx:
        async def __aenter__(self):
            return {}

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    doc = _Doc()
    bus = LocalEventBus()
    seen_nlu: list[Event] = []
    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "0")
    monkeypatch.setattr(router_service_module, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(node_id="hub-node")))
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc())
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())

    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()
    bus.subscribe("nlp.intent.detect.request", lambda ev: seen_nlu.append(ev))

    bus.publish(
        Event(
            type="voice.chat.user",
            source="test",
            ts=1.0,
            payload={
                "text": "weather in Moscow",
                "webspace_id": "desktop",
            },
        )
    )
    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)

    messages = doc.get_map("data")["nodes"]["hub-node"]["voice_chat"]["messages"]
    assert len(messages) == 1
    assert messages[0]["from"] == "user"
    assert messages[0]["text"] == "weather in Moscow"
    assert seen_nlu
    assert seen_nlu[0].payload["_meta"]["target_node_id"] == "hub-node"


async def test_voice_chat_user_shared_scope_uses_shared_history(monkeypatch) -> None:
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

    class _AsyncDoc:
        async def __aenter__(self):
            return doc

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class _MetaCtx:
        async def __aenter__(self):
            return {}

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    doc = _Doc()
    bus = LocalEventBus()
    seen_nlu: list[Event] = []
    seen_stream: list[Event] = []
    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "0")
    monkeypatch.setattr(router_service_module, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(node_id="hub-node")))
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc())
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())

    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()
    bus.subscribe("nlp.intent.detect.request", lambda ev: seen_nlu.append(ev))
    bus.subscribe("io.out.stream.publish", lambda ev: seen_stream.append(ev))

    bus.publish(
        Event(
            type="voice.chat.user",
            source="test",
            ts=1.0,
            payload={
                "text": "weather in Moscow",
                "webspace_id": "desktop",
                "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
            },
        )
    )
    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)

    data = doc.get_map("data")
    messages = data["voice_chat"]["messages"]
    assert len(messages) == 1
    assert messages[0]["from"] == "user"
    assert messages[0]["text"] == "weather in Moscow"
    assert "nodes" not in data
    assert seen_nlu
    assert "target_node_id" not in seen_nlu[0].payload["_meta"]
    assert seen_nlu[0].payload["_meta"]["voice_chat_scope"] == "shared"
    assert seen_stream
    assert seen_stream[0].payload["receiver"] == "voice_chat.messages"
    assert seen_stream[0].payload["data"]["messages"][0]["text"] == "weather in Moscow"
    assert seen_stream[0].payload["data"]["message_count"] == 1

    seen_stream.clear()
    bus.publish(
        Event(
            type="webio.stream.snapshot.requested",
            source="test",
            ts=2.0,
            payload={
                "receiver": "voice_chat.messages",
                "webspace_id": "desktop",
            },
        )
    )
    await bus.wait_for_idle(timeout=1.0)

    assert seen_stream
    assert seen_stream[0].payload["receiver"] == "voice_chat.messages"
    assert seen_stream[0].payload["data"]["messages"][0]["text"] == "weather in Moscow"
    assert seen_stream[0].payload["data"]["message_count"] == 1


async def test_voice_chat_snapshot_request_does_not_publish_uncached_empty_history(monkeypatch) -> None:
    bus = LocalEventBus()
    monkeypatch.setattr(router_service_module, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(node_id="hub-node")))
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    seen_stream: list[Event] = []
    bus.subscribe("io.out.stream.publish", lambda ev: seen_stream.append(ev))
    bus.publish(
        Event(
            type="webio.stream.snapshot.requested",
            source="test",
            ts=1.0,
            payload={
                "receiver": "voice_chat.messages",
                "webspace_id": "desktop",
            },
        )
    )
    await bus.wait_for_idle(timeout=1.0)

    assert seen_stream == []


async def test_voice_chat_user_continues_when_yjs_history_write_times_out(monkeypatch) -> None:
    class _SlowAsyncDoc:
        async def __aenter__(self):
            await asyncio.sleep(10)
            return object()

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class _MetaCtx:
        async def __aenter__(self):
            return {}

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    bus = LocalEventBus()
    seen_nlu: list[Event] = []
    seen_stream: list[Event] = []
    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "0")
    monkeypatch.setenv("ADAOS_VOICE_CHAT_YJS_TIMEOUT_S", "0.05")
    monkeypatch.setattr(router_service_module, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(node_id="hub-node")))
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "mutate_live_room", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _SlowAsyncDoc())
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())

    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()
    bus.subscribe("nlp.intent.detect.request", lambda ev: seen_nlu.append(ev))
    bus.subscribe("io.out.stream.publish", lambda ev: seen_stream.append(ev))

    bus.publish(
        Event(
            type="voice.chat.user",
            source="test",
            ts=1.0,
            payload={
                "text": "Покажи браузеры",
                "webspace_id": "desktop",
                "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
            },
        )
    )
    await bus.wait_for_idle(timeout=1.0)

    assert seen_nlu
    assert seen_nlu[0].payload["text"] == "Покажи браузеры"
    assert seen_stream
    assert seen_stream[0].payload["receiver"] == "voice_chat.messages"
    assert seen_stream[0].payload["data"]["messages"][0]["text"] == "Покажи браузеры"


async def test_voice_chat_user_appends_neural_intent_demo(monkeypatch) -> None:
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

    class _AsyncDoc:
        async def __aenter__(self):
            return doc

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class _MetaCtx:
        async def __aenter__(self):
            return {}

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    async def _fake_parse_text(text: str, **kwargs):
        calls.append((text, kwargs))
        return {
            "ok": True,
            "accepted": True,
            "intent": "weather.get",
            "via": "neural",
            "confidence": 0.91,
            "slots": {"city": "Moscow"},
        }

    doc = _Doc()
    bus = LocalEventBus()
    calls: list[tuple[str, dict[str, object]]] = []
    from adaos.services.nlu import neural_service_bridge

    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "1")
    monkeypatch.setattr(neural_service_bridge, "parse_text", _fake_parse_text)
    monkeypatch.setattr(router_service_module, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(node_id="hub-node")))
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc())
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())

    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    bus.publish(
        Event(
            type="voice.chat.user",
            source="test",
            ts=1.0,
            payload={
                "text": "weather in Moscow",
                "webspace_id": "desktop",
                "_meta": {"route_id": "voice_chat"},
            },
        )
    )
    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)

    messages = doc.get_map("data")["nodes"]["hub-node"]["voice_chat"]["messages"]
    assert messages[0]["from"] == "user"
    assert messages[1]["from"] == "hub"
    assert "Intent detector: weather.get" in messages[1]["text"]
    assert "via=neural" in messages[1]["text"]
    assert calls
    assert calls[0][0] == "weather in Moscow"
    assert calls[0][1]["webspace_id"] == "desktop"
    assert calls[0][1]["meta"]["voice_chat_intent_demo"] is True


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
    await _drain_voice_chat_persist(router)

    assert doc.get_map("data")["nodes"]["member-3"]["voice_chat"]["messages"][0]["text"] == "hello"
    assert float(doc.get_map("data")["nodes"]["member-3"]["voice_chat"]["last_refresh_ts"]) > 0
