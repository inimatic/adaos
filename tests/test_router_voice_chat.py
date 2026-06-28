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
    def __init__(self, doc: _Doc) -> None:
        self.doc = doc

    async def __aenter__(self):
        return self.doc

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _MetaCtx:
    async def __aenter__(self):
        return {}

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _SkillCtx:
    def get(self):
        return None

    def set(self, *_args, **_kwargs):
        return None

    def clear(self):
        return None


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
            skills_repo=None,
            sql=None,
            git=None,
            caps=None,
            settings=None,
        ),
    )
    monkeypatch.delenv("ADAOS_VOICE_CHAT_INTENT_DEMO", raising=False)
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))

    def _run_voice_fallback(_skill, _tool, payload, **_opts):
        meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
        meta["suppress_teacher_bridge"] = True
        calls.append((payload["text"], dict(meta)))
        return {"ok": True, "reply": "ok"}

    monkeypatch.setattr(
        router_service_module,
        "SkillManager",
        lambda **_kwargs: SimpleNamespace(run_tool=_run_voice_fallback),
    )
    monkeypatch.setattr(router_service_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: object())
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    event_payload = {
        "text": "какая погода в москве",
        "reason": "no_intent",
        "_meta": {"route_id": "voice_chat", "webspace_id": "default"},
    }
    bus.publish(Event(type="nlp.intent.not_obtained", source="test", ts=1.0, payload=event_payload))

    await bus.wait_for_idle(timeout=1.0)
    assert calls == [
        (
            "какая погода в москве",
            {"route_id": "voice_chat", "webspace_id": "default", "suppress_teacher_bridge": True},
        )
    ]
    assert "suppress_teacher_bridge" not in event_payload["_meta"]


async def test_voice_chat_not_obtained_prefers_skill_fallback_before_teacher(monkeypatch) -> None:
    bus = LocalEventBus()
    calls: list[dict[str, object]] = []
    teacher_calls: list[object] = []

    class _SkillCtx:
        def get(self):
            return None

        def set(self, *_args, **_kwargs):
            return None

        def clear(self):
            return None

    async def _request_existing_candidate_confirmation(*_args, **_kwargs):
        teacher_calls.append(object())
        return True

    teacher_module = types.SimpleNamespace(
        request_existing_candidate_confirmation=_request_existing_candidate_confirmation,
    )
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.nlu.teacher_confirmation_runtime",
        teacher_module,
    )
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                node_id="member-local",
                root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=True)),
            ),
            paths=SimpleNamespace(skills_workspace_dir=lambda: Path(".")),
            skill_ctx=_SkillCtx(),
            skills_repo=None,
            sql=None,
            git=None,
            caps=None,
            settings=None,
        ),
    )
    monkeypatch.delenv("ADAOS_VOICE_CHAT_INTENT_DEMO", raising=False)
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(
        router_service_module,
        "SkillManager",
        lambda **_kwargs: SimpleNamespace(
            run_tool=lambda _skill, _tool, payload, **opts: calls.append((dict(payload), dict(opts)))
            or {"ok": True, "reply": "ok"}
        ),
    )
    monkeypatch.setattr(router_service_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: object())
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    bus.publish(
        Event(
            type="nlp.intent.not_obtained",
            source="test",
            ts=1.0,
            payload={
                "text": "weather in Berlin",
                "reason": "no_intent_mapping",
                "_meta": {"route_id": "voice_chat", "webspace_id": "desktop"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert calls == [
        (
            {
                "text": "weather in Berlin",
                "webspace_id": "desktop",
                "_meta": {"route_id": "voice_chat", "webspace_id": "desktop"},
            },
            {"bypass_yjs_guard": True},
        )
    ]
    assert teacher_calls == []


async def test_voice_chat_not_obtained_prefers_skill_fallback_during_intent_demo(monkeypatch) -> None:
    bus = LocalEventBus()
    calls: list[dict] = []

    class _SkillCtx:
        def get(self):
            return None

        def set(self, *_args, **_kwargs):
            return None

        def clear(self):
            return None

    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "1")
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
            skills_repo=None,
            sql=None,
            git=None,
            caps=None,
            settings=None,
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(
        router_service_module,
        "SkillManager",
        lambda **_kwargs: SimpleNamespace(
            run_tool=lambda _skill, _tool, payload, **opts: calls.append((dict(payload), dict(opts))) or {"ok": True}
        ),
    )
    monkeypatch.setattr(router_service_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: object())
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
    assert calls == [
        (
            {
                "text": "weather in Moscow",
                "webspace_id": "default",
                "_meta": {"route_id": "voice_chat", "webspace_id": "default"},
            },
            {"bypass_yjs_guard": True},
        )
    ]


async def test_voice_chat_not_obtained_routes_active_dialog_followup(monkeypatch) -> None:
    from adaos.services import dialog_runtime

    bus = LocalEventBus()
    calls: list[tuple[str, str, dict, dict]] = []
    webspace_id = "companion-dialog-ws"

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
                root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=True)),
            ),
            paths=SimpleNamespace(skills_workspace_dir=lambda: Path(".")),
            skill_ctx=_SkillCtx(),
            skills_repo=None,
            sql=None,
            git=None,
            caps=None,
            settings=None,
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))

    def _run_tool(skill, tool, payload, **opts):
        calls.append((skill, tool, dict(payload), dict(opts)))
        return {
            "ok": True,
            "message": "dialog reply",
            "dialog": {
                "dialog_channel_id": "conversational",
                "conversation_id": f"conv.skill.conversation_companions.default.{webspace_id}",
                "owner": "skill:conversation_companions",
                "default_tool": "conversation_companions.talk",
                "active_agent_id": "agent:conversation_companions:arseni",
            },
        }

    monkeypatch.setattr(
        router_service_module,
        "SkillManager",
        lambda **_kwargs: SimpleNamespace(run_tool=_run_tool),
    )
    monkeypatch.setattr(router_service_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: object())
    dialog_runtime.reset_all()
    dialog_runtime.activate_channel(
        webspace_id=webspace_id,
        channel_id="conversational",
        owner="skill:conversation_companions",
        default_skill="conversation_companions",
        default_tool="talk",
        conversation_id=f"conv.skill.conversation_companions.default.{webspace_id}",
        active_agent_id="agent:conversation_companions:arseni",
        route_id="voice_chat",
    )

    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    bus.publish(
        Event(
            type="nlp.intent.not_obtained",
            source="test",
            ts=1.0,
            payload={
                "text": "let us discuss my launch plan",
                "reason": "no_intent_mapping",
                "request_id": "req.dialog-followup",
                "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    turn_trace_id = calls[0][2]["_meta"]["turn_trace_id"]
    assert str(turn_trace_id).startswith("trace.")
    assert calls[0][0:2] == ("conversation_companions", "talk")
    assert calls[0][3] == {"bypass_yjs_guard": True}
    assert calls[0][2]["text"] == "let us discuss my launch plan"
    assert calls[0][2]["webspace_id"] == webspace_id
    assert calls[0][2]["conversation_id"] == f"conv.skill.conversation_companions.default.{webspace_id}"
    assert calls[0][2]["dialog_channel_id"] == "conversational"
    assert calls[0][2]["conversation_context"]["conversation_id"] == calls[0][2]["conversation_id"]
    assert calls[0][2]["_meta"] == {
        "route_id": "voice_chat",
        "webspace_id": webspace_id,
        "turn_trace_id": turn_trace_id,
        "dialog_channel_id": "conversational",
        "conversation_id": f"conv.skill.conversation_companions.default.{webspace_id}",
        "conversation_owner": "skill:conversation_companions",
        "active_agent_id": "agent:conversation_companions:arseni",
    }
    dialog_runtime.reset_all()


async def test_voice_chat_not_obtained_exits_active_dialog(monkeypatch) -> None:
    from adaos.services import dialog_runtime

    bus = LocalEventBus()
    messages: list[dict] = []
    calls: list[object] = []
    webspace_id = "companion-exit-ws"

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
            skills_repo=None,
            sql=None,
            git=None,
            caps=None,
            settings=None,
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(
        router_service_module,
        "SkillManager",
        lambda **_kwargs: SimpleNamespace(run_tool=lambda *_args, **_opts: calls.append(object()) or {"ok": True}),
    )
    monkeypatch.setattr(router_service_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: object())
    dialog_runtime.reset_all()
    dialog_runtime.activate_channel(
        webspace_id=webspace_id,
        channel_id="conversational",
        owner="skill:conversation_companions",
        default_skill="conversation_companions",
        default_tool="talk",
        conversation_id=f"conv.skill.conversation_companions.default.{webspace_id}",
        route_id="voice_chat",
    )

    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()
    bus.subscribe("io.out.chat.append", lambda ev: messages.append(dict(ev.payload or {})))

    bus.publish(
        Event(
            type="nlp.intent.not_obtained",
            source="test",
            ts=1.0,
            payload={
                "text": "\u0432 \u043e\u0431\u0449\u0438\u0439 \u0440\u0435\u0436\u0438\u043c",
                "reason": "no_intent_mapping",
                "request_id": "req.dialog-exit",
                "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert calls == []
    assert dialog_runtime.get_active_channel(webspace_id) is None
    assert any(item.get("from") == "hub" and item.get("_meta", {}).get("route_id") == "voice_chat" for item in messages)
    dialog_runtime.reset_all()


def test_voice_chat_data_path_is_node_scoped() -> None:
    assert node_scope_data_path("data/voice_chat", "member-1") == "data/nodes/member-1/voice_chat"


async def test_voice_chat_open_projects_general_dialog_state(monkeypatch) -> None:
    bus = LocalEventBus()
    doc = _Doc()
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(config=SimpleNamespace(node_id="hub-node")),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc(doc))
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    bus.publish(
        Event(
            type="voice.chat.open",
            source="test",
            ts=1.0,
            payload={
                "webspace_id": "desktop",
                "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    dialog = doc.get_map("data")["dialog"]
    assert dialog["active_channel_id"] == "general"
    assert [item["id"] for item in dialog["channels"][:2]] == ["general", "conversational"]


async def test_dialog_channel_select_conversational_activates_companion(monkeypatch) -> None:
    from adaos.services import dialog_runtime

    bus = LocalEventBus()
    doc = _Doc()
    calls: list[tuple[str, str, dict]] = []
    webspace_id = "dialog-select-ws"
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(node_id="hub-node"),
            paths=SimpleNamespace(skills_workspace_dir=lambda: Path(".")),
            skill_ctx=_SkillCtx(),
            skills_repo=None,
            sql=None,
            git=None,
            caps=None,
            settings=None,
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc(doc))
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())
    monkeypatch.setattr(router_service_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: object())

    def _run_tool(skill, tool, payload, **_opts):
        calls.append((skill, tool, dict(payload)))
        return {
            "ok": True,
            "message": "started",
            "dialog": {
                "dialog_channel_id": "conversational",
                "conversation_id": f"conv.skill.conversation_companions.default.{webspace_id}",
                "owner": "skill:conversation_companions",
                "default_tool": "conversation_companions.talk",
                "active_agent_id": "agent:conversation_companions:arseni",
            },
        }

    monkeypatch.setattr(
        router_service_module,
        "SkillManager",
        lambda **_kwargs: SimpleNamespace(run_tool=_run_tool),
    )
    dialog_runtime.reset_all()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    bus.publish(
        Event(
            type="dialog.channel.select",
            source="test",
            ts=1.0,
            payload={
                "channel_id": "conversational",
                "webspace_id": webspace_id,
                "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    dialog = doc.get_map("data")["dialog"]
    assert calls[0][0:2] == ("conversation_companions", "start")
    assert calls[0][2]["webspace_id"] == webspace_id
    assert dialog["active_channel_id"] == "conversational"
    assert dialog["active_channel"]["default_tool"] == "talk"
    dialog_runtime.reset_all()


async def test_dialog_channel_select_supports_builder_and_persisted_skill_channels(monkeypatch) -> None:
    from adaos.services import conversation_store, dialog_runtime

    bus = LocalEventBus()
    doc = _Doc()
    webspace_id = "dialog-dynamic-ws"
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(config=SimpleNamespace(node_id="hub-node")),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc(doc))
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())
    conversation_store.ensure_schema()
    conversation_store.upsert_dialog_channel(
        webspace_id=webspace_id,
        channel_id="research",
        label="Research",
        owner="skill:research_assistant",
        conversation_id=f"conv.skill.research_assistant.default.{webspace_id}",
        default_skill="research_assistant",
        default_tool="chat",
        route_id="voice_chat",
    )
    dialog_runtime.reset_all()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    bus.publish(
        Event(
            type="dialog.channel.select",
            source="test",
            ts=1.0,
            payload={
                "channel_id": "builder",
                "webspace_id": webspace_id,
                "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
            },
        )
    )
    await bus.wait_for_idle(timeout=1.0)
    builder_state = dialog_runtime.get_active_channel(webspace_id)
    assert builder_state is not None
    assert builder_state.channel_id == "builder"
    assert builder_state.default_skill == "llm_builder"
    assert doc.get_map("data")["dialog"]["active_channel_id"] == "builder"

    bus.publish(
        Event(
            type="dialog.channel.select",
            source="test",
            ts=2.0,
            payload={
                "channel_id": "research",
                "webspace_id": webspace_id,
                "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
            },
        )
    )
    await bus.wait_for_idle(timeout=1.0)
    research_state = dialog_runtime.get_active_channel(webspace_id)
    assert research_state is not None
    assert research_state.channel_id == "research"
    assert research_state.owner == "skill:research_assistant"
    dialog = doc.get_map("data")["dialog"]
    assert dialog["active_channel_id"] == "research"
    assert "builder" in {item["id"] for item in dialog["channels"]}
    assert "research" in {item["id"] for item in dialog["channels"]}
    dialog_runtime.reset_all()


async def test_dialog_channel_select_general_deactivates_companion(monkeypatch) -> None:
    from adaos.services import dialog_runtime

    bus = LocalEventBus()
    doc = _Doc()
    webspace_id = "dialog-general-ws"
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(config=SimpleNamespace(node_id="hub-node")),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc(doc))
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())
    dialog_runtime.reset_all()
    dialog_runtime.activate_channel(
        webspace_id=webspace_id,
        channel_id="conversational",
        owner="skill:conversation_companions",
        default_skill="conversation_companions",
        default_tool="talk",
        conversation_id=f"conv.skill.conversation_companions.default.{webspace_id}",
        active_agent_id="agent:conversation_companions:arseni",
        route_id="voice_chat",
    )
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    bus.publish(
        Event(
            type="dialog.channel.select",
            source="test",
            ts=1.0,
            payload={
                "channel_id": "general",
                "webspace_id": webspace_id,
                "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)
    data = doc.get_map("data")
    assert data["dialog"]["active_channel_id"] == "general"
    assert data["dialog"]["active_agent"]["id"] == "agent:core:general"
    assert data["dialog"]["active_agent"]["label"] == "Ада"
    assert dialog_runtime.get_active_channel(webspace_id) is None
    assert "Ада" in data["voice_chat"]["messages"][-1]["text"]
    dialog_runtime.reset_all()


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
    assert "suppress_teacher_bridge" not in seen_nlu[0].payload["_meta"]


async def test_dialog_user_message_uses_voice_compatibility_route(monkeypatch) -> None:
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
            type="dialog.user_message",
            source="test",
            ts=1.0,
            payload={
                "text": "weather in Moscow",
                "webspace_id": "desktop",
                "_meta": {"route_id": "voice_chat", "dialog_channel_id": "general"},
            },
        )
    )
    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)

    messages = doc.get_map("data")["nodes"]["hub-node"]["voice_chat"]["messages"]
    assert len(messages) == 1
    assert messages[0]["from"] == "user"
    assert messages[0]["text"] == "weather in Moscow"
    assert messages[0]["_meta"]["dialog_event_kind"] == "dialog.user_message"
    assert seen_nlu
    assert seen_nlu[0].payload["_meta"]["route_id"] == "voice_chat"
    assert seen_nlu[0].payload["_meta"]["dialog_channel_id"] == "general"
    assert seen_nlu[0].payload["_meta"]["dialog_event_kind"] == "dialog.user_message"
    assert seen_nlu[0].payload["_meta"]["canonical_event_kind"] == "dialog.user_message"
    assert seen_nlu[0].payload["_meta"]["input_event_kind"] == "dialog.user_message"


async def test_voice_chat_confirmation_answer_is_not_routed_to_nlu(monkeypatch) -> None:
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

    async def _fake_consume_confirmation_answer(webspace_id: str, text: str, **_kwargs) -> bool:
        return webspace_id == "desktop" and text == "yes"

    doc = _Doc()
    bus = LocalEventBus()
    seen_nlu: list[Event] = []
    from adaos.services.nlu import teacher_confirmation_runtime as conf

    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "0")
    monkeypatch.setattr(conf, "should_consume_voice_confirmation_answer", _fake_consume_confirmation_answer)
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
                "text": "yes",
                "webspace_id": "desktop",
                "_meta": {"route_id": "voice_chat"},
            },
        )
    )
    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)

    messages = doc.get_map("data")["nodes"]["hub-node"]["voice_chat"]["messages"]
    assert messages[-1]["from"] == "user"
    assert messages[-1]["text"] == "yes"
    assert seen_nlu == []


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
    assert "suppress_teacher_bridge" not in seen_nlu[0].payload["_meta"]
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


async def test_voice_chat_user_routes_active_dialog_directly_without_nlu(monkeypatch) -> None:
    from adaos.services import dialog_runtime

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

    class _SkillCtx:
        def get(self):
            return None

        def set(self, *_args, **_kwargs):
            return None

        def clear(self):
            return None

    doc = _Doc()
    bus = LocalEventBus()
    calls: list[tuple[str, str, dict, dict]] = []
    seen_nlu: list[Event] = []
    webspace_id = "active-dialog-voice-ws"
    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "0")
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                node_id="hub-node",
                root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=True)),
            ),
            paths=SimpleNamespace(skills_workspace_dir=lambda: Path(".")),
            skill_ctx=_SkillCtx(),
            skills_repo=None,
            sql=None,
            git=None,
            caps=None,
            settings=None,
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc())
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())
    monkeypatch.setattr(router_service_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: object())

    def _run_tool(skill, tool, payload, **opts):
        calls.append((skill, tool, dict(payload), dict(opts)))
        return {
            "ok": True,
            "message": "dialog reply",
            "dialog": {
                "dialog_channel_id": "conversational",
                "conversation_id": f"conv.skill.conversation_companions.default.{webspace_id}",
                "owner": "skill:conversation_companions",
                "default_tool": "conversation_companions.talk",
                "active_agent_id": "agent:conversation_companions:arseni",
            },
        }

    monkeypatch.setattr(
        router_service_module,
        "SkillManager",
        lambda **_kwargs: SimpleNamespace(run_tool=_run_tool),
    )
    dialog_runtime.reset_all()
    dialog_runtime.activate_channel(
        webspace_id=webspace_id,
        channel_id="conversational",
        owner="skill:conversation_companions",
        default_skill="conversation_companions",
        default_tool="talk",
        conversation_id=f"conv.skill.conversation_companions.default.{webspace_id}",
        active_agent_id="agent:conversation_companions:arseni",
        route_id="voice_chat",
    )

    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()
    bus.subscribe("nlp.intent.detect.request", lambda ev: seen_nlu.append(ev))

    bus.publish(
        Event(
            type="voice.chat.user",
            source="test",
            ts=1.0,
            payload={
                "text": "free form companion turn",
                "webspace_id": webspace_id,
                "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
            },
        )
    )
    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)

    assert seen_nlu == []
    turn_trace_id = calls[0][2]["_meta"]["turn_trace_id"]
    assert str(turn_trace_id).startswith("trace.")
    assert calls[0][0:2] == ("conversation_companions", "talk")
    assert calls[0][3] == {"bypass_yjs_guard": True}
    assert calls[0][2]["text"] == "free form companion turn"
    assert calls[0][2]["webspace_id"] == webspace_id
    assert calls[0][2]["conversation_id"] == f"conv.skill.conversation_companions.default.{webspace_id}"
    assert calls[0][2]["dialog_channel_id"] == "conversational"
    assert calls[0][2]["conversation_context"]["conversation_id"] == calls[0][2]["conversation_id"]
    assert calls[0][2]["_meta"] == {
        "route_id": "voice_chat",
        "voice_chat_scope": "shared",
        "webspace_id": webspace_id,
        "turn_trace_id": turn_trace_id,
        "dialog_policy_reason": "active_dialog_followup",
        "dialog_channel_id": "conversational",
        "conversation_id": f"conv.skill.conversation_companions.default.{webspace_id}",
        "conversation_owner": "skill:conversation_companions",
        "active_agent_id": "agent:conversation_companions:arseni",
    }
    assert doc.get_map("data")["voice_chat"]["messages"][0]["text"] == "free form companion turn"
    dialog_runtime.reset_all()


async def test_voice_chat_user_general_agent_address_exits_active_dialog(monkeypatch) -> None:
    from adaos.services import dialog_runtime

    bus = LocalEventBus()
    doc = _Doc()
    calls: list[tuple[str, str, dict, dict]] = []
    seen_nlu: list[Event] = []
    webspace_id = "general-agent-address-ws"
    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "0")
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                node_id="hub-node",
                root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=True)),
            ),
            paths=SimpleNamespace(skills_workspace_dir=lambda: Path(".")),
            skill_ctx=_SkillCtx(),
            skills_repo=None,
            sql=None,
            git=None,
            caps=None,
            settings=None,
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc(doc))
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())
    monkeypatch.setattr(router_service_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        router_service_module,
        "SkillManager",
        lambda **_kwargs: SimpleNamespace(
            run_tool=lambda skill, tool, payload, **opts: calls.append((skill, tool, dict(payload), dict(opts)))
            or {"ok": True}
        ),
    )
    dialog_runtime.reset_all()
    dialog_runtime.activate_channel(
        webspace_id=webspace_id,
        channel_id="conversational",
        owner="skill:conversation_companions",
        default_skill="conversation_companions",
        default_tool="talk",
        conversation_id=f"conv.skill.conversation_companions.default.{webspace_id}",
        active_agent_id="agent:conversation_companions:arseni",
        route_id="voice_chat",
    )
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()
    bus.subscribe("nlp.intent.detect.request", lambda ev: seen_nlu.append(ev))

    bus.publish(
        Event(
            type="voice.chat.user",
            source="test",
            ts=1.0,
            payload={
                "text": "Ada, weather in Paris",
                "webspace_id": webspace_id,
                "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)

    assert calls == []
    assert dialog_runtime.get_active_channel(webspace_id) is None
    assert len(seen_nlu) == 1
    assert seen_nlu[0].payload["text"] == "weather in Paris"
    assert seen_nlu[0].payload["_meta"]["dialog_channel_id"] == "general"
    assert seen_nlu[0].payload["_meta"]["active_agent_id"] == "agent:core:general"
    data = doc.get_map("data")
    assert data["dialog"]["active_channel_id"] == "general"
    assert data["dialog"]["active_agent"]["label"] == "Ада"
    assert "Ада" in data["voice_chat"]["messages"][-1]["text"]
    dialog_runtime.reset_all()


async def test_voice_chat_user_addressed_companion_switches_channel_without_nlu(monkeypatch) -> None:
    from adaos.services import conversation_store, dialog_runtime

    bus = LocalEventBus()
    doc = _Doc()
    calls: list[tuple[str, str, dict, dict]] = []
    seen_nlu: list[Event] = []
    webspace_id = "addressed-companion-ws"
    user_text = "Ника, назови 3 последних русских царей"
    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "0")
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                node_id="hub-node",
                root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=True)),
            ),
            paths=SimpleNamespace(skills_workspace_dir=lambda: Path(".")),
            skill_ctx=_SkillCtx(),
            skills_repo=None,
            sql=None,
            git=None,
            caps=None,
            settings=None,
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc(doc))
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())
    monkeypatch.setattr(router_service_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: object())

    def _run_tool(skill, tool, payload, **opts):
        calls.append((skill, tool, dict(payload), dict(opts)))
        return {
            "ok": True,
            "message": "Ника: ответ",
            "dialog": {
                "dialog_channel_id": "conversational",
                "conversation_id": f"conv.skill.conversation_companions.default.{webspace_id}",
                "owner": "skill:conversation_companions",
                "default_tool": "conversation_companions.talk",
                "active_agent_id": "agent:conversation_companions:nika",
                "active_agent_label": "Ника",
                "active_agent": {
                    "id": "agent:conversation_companions:nika",
                    "label": "Ника",
                    "owner": "skill:conversation_companions",
                    "kind": "skill_agent",
                    "gender": "female",
                    "voice": "ru-female",
                    "icon": "female-outline",
                    "voice_profile": {"gender": "female", "voice": "ru-female", "lang": "ru-RU"},
                },
            },
        }

    monkeypatch.setattr(
        router_service_module,
        "SkillManager",
        lambda **_kwargs: SimpleNamespace(run_tool=_run_tool),
    )
    dialog_runtime.reset_all()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()
    bus.subscribe("nlp.intent.detect.request", lambda ev: seen_nlu.append(ev))

    bus.publish(
        Event(
            type="voice.chat.user",
            source="test",
            ts=1.0,
            payload={
                "text": user_text,
                "webspace_id": webspace_id,
                "_meta": {
                    "route_id": "voice_chat",
                    "voice_chat_scope": "shared",
                    "dialog_channel_id": "general",
                },
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)

    assert seen_nlu == []
    assert calls
    assert calls[0][0:2] == ("conversation_companions", "talk")
    assert calls[0][2]["text"] == user_text
    assert calls[0][2]["_meta"]["dialog_channel_id"] == "conversational"
    assert calls[0][2]["_meta"]["active_agent_id"] == "agent:conversation_companions:nika"
    assert calls[0][2]["_meta"]["active_agent_label"] == "Ника"
    assert calls[0][2]["_meta"]["active_agent_gender"] == "female"
    assert calls[0][2]["_meta"]["voice_gender"] == "female"
    assert calls[0][2]["_meta"]["voice"] == "ru-female"
    assert calls[0][2]["_meta"]["active_agent_icon"] == "female-outline"
    assert calls[0][2]["conversation_context"]["conversation_id"] == f"conv.skill.conversation_companions.default.{webspace_id}"
    state = dialog_runtime.get_active_channel(webspace_id)
    assert state is not None
    assert state.channel_id == "conversational"
    assert state.active_agent_id == "agent:conversation_companions:nika"
    assert state.active_agent_label == "Ника"
    assert state.active_agent_gender == "female"
    assert state.active_agent_voice == "ru-female"
    assert state.active_agent_icon == "female-outline"
    data = doc.get_map("data")
    assert data["dialog"]["active_channel_id"] == "conversational"
    assert data["dialog"]["active_agent"]["id"] == "agent:conversation_companions:nika"
    assert data["dialog"]["active_agent"]["label"] == "Ника"
    assert data["dialog"]["active_agent"]["gender"] == "female"
    assert data["dialog"]["active_agent"]["icon"] == "female-outline"
    assert data["voice_chat"]["messages"][0]["dialog_channel_id"] == "conversational"
    assert data["voice_chat"]["messages"][-1]["dialog_channel_id"] == "conversational"
    turn_trace_id = calls[0][2]["_meta"]["turn_trace_id"]
    trace = conversation_store.get_turn_trace(turn_trace_id)
    assert trace is not None
    assert trace["channel_id"] == "conversational"
    assert trace["agent_id"] == "agent:conversation_companions:nika"
    assert trace["selected_tool"] == "conversation_companions.talk"
    assert trace["status"] == "materialized"
    assert trace["policy_decision"]["reason"] == "addressed_agent"
    assert trace["policy_decision"]["selected_agent_id"] == "agent:conversation_companions:nika"
    assert trace["policy_decision"]["materialization_status"] == "materialized"
    assert trace["renderer"]["projection"] in {"skill_emitted_message", "compact_tail"}
    assert data["dialog"]["last_turn_trace"]["status"] == "materialized"
    assert data["dialog"]["last_turn_trace"]["renderer"]["projection"] in {"skill_emitted_message", "compact_tail"}
    dialog_runtime.reset_all()


async def test_voice_chat_golden_companion_dialog_flow(monkeypatch) -> None:
    from adaos.services import conversation_store, dialog_runtime

    bus = LocalEventBus()
    doc = _Doc()
    calls: list[tuple[str, str, dict, dict]] = []
    nlu_events: list[Event] = []
    webspace_id = "golden-companion-ws"
    conversation_id = f"conv.skill.conversation_companions.default.{webspace_id}"

    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "0")
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                node_id="hub-node",
                root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=False)),
            ),
            paths=SimpleNamespace(skills_workspace_dir=lambda: Path(".")),
            skill_ctx=_SkillCtx(),
            skills_repo=None,
            sql=None,
            git=None,
            caps=None,
            settings=None,
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc(doc))
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())
    monkeypatch.setattr(router_service_module, "SqliteSkillRegistry", lambda *_args, **_kwargs: object())

    def _agent_meta(character: str, meta: dict) -> dict:
        profiles = {
            "arseni": ("agent:conversation_companions:arseni", "Арсений", "male", "ru-male", "male-outline"),
            "nika": ("agent:conversation_companions:nika", "Ника", "female", "ru-female", "female-outline"),
        }
        agent_id, label, gender, voice, icon = profiles[character]
        return {
            **meta,
            "dialog_channel_id": "conversational",
            "conversation_id": conversation_id,
            "conversation_owner": "skill:conversation_companions",
            "active_agent_id": agent_id,
            "active_agent_label": label,
            "active_agent_gender": gender,
            "active_agent_voice": voice,
            "active_agent_icon": icon,
            "voice_gender": gender,
            "voice": voice,
        }

    def _dialog(character: str, meta: dict) -> dict:
        agent_meta = _agent_meta(character, meta)
        return {
            "dialog_channel_id": "conversational",
            "conversation_id": conversation_id,
            "owner": "skill:conversation_companions",
            "default_tool": "conversation_companions.talk",
            "active_agent_id": agent_meta["active_agent_id"],
            "active_agent_label": agent_meta["active_agent_label"],
            "active_agent": {
                "id": agent_meta["active_agent_id"],
                "label": agent_meta["active_agent_label"],
                "owner": "skill:conversation_companions",
                "kind": "skill_agent",
                "gender": agent_meta["active_agent_gender"],
                "voice": agent_meta["active_agent_voice"],
                "icon": agent_meta["active_agent_icon"],
            },
        }

    def _emit_skill_reply(text: str, character: str, meta: dict) -> None:
        agent_meta = _agent_meta(character, meta)
        bus.publish(
            Event(
                type="io.out.chat.append",
                source="test.skill",
                ts=1.0,
                payload={
                    "id": f"golden.{len(calls)}",
                    "from": "hub",
                    "text": text,
                    "ts": 1.0,
                    "_meta": agent_meta,
                    "active_agent_id": agent_meta["active_agent_id"],
                    "active_agent_label": agent_meta["active_agent_label"],
                    "active_agent_gender": agent_meta["active_agent_gender"],
                    "active_agent_voice": agent_meta["active_agent_voice"],
                    "active_agent_icon": agent_meta["active_agent_icon"],
                },
            )
        )

    def _run_tool(skill, tool, payload, **opts):
        meta = dict(payload.get("_meta") or {})
        calls.append((skill, tool, dict(payload), dict(opts)))
        text = str(payload.get("text") or "")
        character = "nika" if meta.get("active_agent_id") == "agent:conversation_companions:nika" else "arseni"
        if "говори короче" in text.lower():
            message = "Обновил профиль Ника: тон - прямее, теплее; длина - коротко, одна-две главные мысли."
        elif character == "nika":
            message = "Ника: проверяю слабые места идеи."
        else:
            message = "Арсений: разложим ситуацию спокойно."
        _emit_skill_reply(message, character, meta)
        return {"ok": True, "message": message, "dialog": _dialog(character, meta)}

    def _on_nlu(ev: Event) -> None:
        nlu_events.append(ev)
        payload = ev.payload or {}
        text = str(payload.get("text") or "").strip().lower()
        if text not in {"поговорим", "pogovorim"}:
            return
        meta = _agent_meta("arseni", dict(payload.get("_meta") or {}))
        dialog_runtime.activate_channel(
            webspace_id=webspace_id,
            channel_id="conversational",
            owner="skill:conversation_companions",
            default_skill="conversation_companions",
            default_tool="talk",
            conversation_id=conversation_id,
            active_agent_id="agent:conversation_companions:arseni",
            active_agent_label="Арсений",
            active_agent_gender="male",
            active_agent_voice="ru-male",
            active_agent_icon="male-outline",
            route_id="voice_chat",
            bus=bus,
            source="test.nlu",
        )
        _emit_skill_reply("Я Арсений. Готов к разговору.", "arseni", meta)

    monkeypatch.setattr(
        router_service_module,
        "SkillManager",
        lambda **_kwargs: SimpleNamespace(run_tool=_run_tool),
    )
    dialog_runtime.reset_all()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()
    bus.subscribe("nlp.intent.detect.request", _on_nlu)

    for text in (
        "Поговорим",
        "Помоги выбрать формат интервью",
        "Ника, проверь идею",
        "говори короче и теплее",
        "Ада, погода в Париже",
    ):
        bus.publish(
            Event(
                type="voice.chat.user",
                source="test",
                ts=1.0,
                payload={
                    "text": text,
                    "webspace_id": webspace_id,
                    "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
                },
            )
        )
        await bus.wait_for_idle(timeout=1.0)
        await _drain_voice_chat_persist(router)

    data = doc.get_map("data")
    messages = conversation_store.list_projection(conversation_id, limit=20)["messages"]
    assert any(item.get("text") == "Я Арсений. Готов к разговору." for item in messages)
    assert any(str(item.get("text") or "").startswith("Ника:") for item in messages)
    assert any("Обновил профиль Ника" in str(item.get("text") or "") for item in messages)
    assert calls[0][0:2] == ("conversation_companions", "talk")
    assert calls[0][2]["text"] == "Помоги выбрать формат интервью"
    assert calls[1][2]["_meta"]["active_agent_id"] == "agent:conversation_companions:nika"
    assert calls[2][2]["text"] == "говори короче и теплее"
    assert dialog_runtime.get_active_channel(webspace_id) is None
    assert data["dialog"]["active_channel_id"] == "general"
    assert data["dialog"]["active_agent"]["id"] == "agent:core:general"
    assert nlu_events[-1].payload["text"] == "погода в Париже"
    assert nlu_events[-1].payload["_meta"]["active_agent_id"] == "agent:core:general"
    trace = conversation_store.latest_turn_trace(webspace_id=webspace_id)
    assert trace is not None
    assert trace["selected_tool"] == "nlp.intent.detect.request"
    assert trace["policy_decision"]["selected_channel"] == "general"
    dialog_runtime.reset_all()


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


async def test_voice_chat_open_restores_active_channel_and_history_from_ledger(monkeypatch) -> None:
    from adaos.services import conversation_store, dialog_runtime
    from adaos.services.agent_context import get_ctx as real_get_ctx

    bus = LocalEventBus()
    doc = _Doc()
    async_get_calls: list[dict[str, object]] = []
    ctx = real_get_ctx()
    webspace_id = "restore-ledger-ws"
    conversation_id = f"conv.skill.conversation_companions.default.{webspace_id}"
    conversation_store.ensure_schema()
    conversation_store.seed_agents(
        [
            {
                "id": "agent:conversation_companions:arseni",
                "label": "Арсений",
                "owner": "skill:conversation_companions",
                "channel_id": "conversational",
                "skill": "conversation_companions",
                "kind": "skill_agent",
                "aliases": ["Арсений"],
                "gender": "male",
                "voice": "ru-male",
                "icon": "male-outline",
                "talk_tool": "talk",
            }
        ],
        source="test",
    )
    conversation_store.upsert_conversation(
        conversation_id=conversation_id,
        webspace_id=webspace_id,
        owner="skill:conversation_companions",
        kind="dialog",
        title="Conversational",
        active_agent_id="agent:conversation_companions:arseni",
    )
    conversation_store.upsert_dialog_channel(
        webspace_id=webspace_id,
        channel_id="conversational",
        label="Conversational",
        owner="skill:conversation_companions",
        conversation_id=conversation_id,
        active_agent_id="agent:conversation_companions:arseni",
        default_skill="conversation_companions",
        default_tool="talk",
        route_id="voice_chat",
        meta={
            "active_agent_label": "Арсений",
            "active_agent_owner": "skill:conversation_companions",
            "active_agent_kind": "skill_agent",
            "active_agent_gender": "male",
            "active_agent_voice": "ru-male",
            "active_agent_icon": "male-outline",
        },
    )
    for index in range(9):
        conversation_store.append_message(
            conversation_id=conversation_id,
            webspace_id=webspace_id,
            channel_id="conversational",
            owner="skill:conversation_companions",
            role="hub",
            text=f"turn {index}",
            payload={"id": f"restore.{index}", "from": "hub", "text": f"turn {index}"},
            actor_id="agent:conversation_companions:arseni",
            actor_label="Арсений",
            actor_icon="male-outline",
            route_id="voice_chat",
            ts=100.0 + index,
        )

    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                node_id="hub-node",
                root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=False)),
            ),
            paths=ctx.paths,
            skill_ctx=_SkillCtx(),
            skills_repo=None,
            sql=ctx.sql,
            git=None,
            caps=None,
            settings=None,
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    def _fake_async_get_ydoc(*_args, **kwargs):
        async_get_calls.append(dict(kwargs))
        return _AsyncDoc(doc)

    monkeypatch.setattr(router_service_module, "async_get_ydoc", _fake_async_get_ydoc)
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())
    dialog_runtime.reset_all()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()
    seen_stream: list[Event] = []
    bus.subscribe("io.out.stream.publish", lambda ev: seen_stream.append(ev))

    bus.publish(
        Event(
            type="voice.chat.open",
            source="test",
            ts=1.0,
            payload={
                "webspace_id": webspace_id,
                "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)

    state = dialog_runtime.get_active_channel(webspace_id)
    assert state is not None
    assert state.channel_id == "conversational"
    assert state.active_agent_id == "agent:conversation_companions:arseni"
    data = doc.get_map("data")
    assert data["dialog"]["active_channel_id"] == "conversational"
    assert data["dialog"]["active_agent"]["label"] == "Арсений"
    assert seen_stream
    stream = seen_stream[-1].payload["data"]
    assert stream["conversation_id"] == conversation_id
    assert stream["dialog_channel_id"] == "conversational"
    assert stream["message_count"] == 8
    assert stream["total_message_count"] == 9
    assert stream["has_more_before"] is True
    assert stream["messages"][0]["text"] == "turn 1"
    assert data["voice_chat"]["conversation_id"] == conversation_id
    assert data["voice_chat"]["dialog_channel_id"] == "conversational"
    assert any(call.get("publish_live_room") is True for call in async_get_calls)
    dialog_runtime.reset_all()


async def test_voice_chat_history_more_publishes_older_window(monkeypatch) -> None:
    bus = LocalEventBus()
    doc = _Doc()
    webspace_id = "history-more-ws"
    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "0")
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                node_id="hub-node",
                root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=False)),
            )
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc(doc))
    monkeypatch.setattr(router_service_module, "ystore_write_metadata", lambda **_kwargs: _MetaCtx())
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()
    seen_stream: list[Event] = []
    bus.subscribe("io.out.stream.publish", lambda ev: seen_stream.append(ev))

    for index in range(10):
        bus.publish(
            Event(
                type="voice.chat.user",
                source="test",
                ts=1.0 + index,
                payload={
                    "text": f"turn {index}",
                    "webspace_id": webspace_id,
                    "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
                },
            )
        )

    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)

    last_tail = seen_stream[-1].payload["data"]
    assert last_tail["message_count"] == 8
    assert last_tail["total_message_count"] == 10
    assert last_tail["has_more_before"] is True
    assert last_tail["before_cursor"] == "2"
    assert last_tail["messages"][0]["text"] == "turn 2"

    bus.publish(
        Event(
            type="conversation.history.more",
            source="test",
            ts=20.0,
            payload={
                "webspace_id": webspace_id,
                "before_cursor": last_tail["before_cursor"],
                "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)

    expanded = seen_stream[-1].payload["data"]
    assert expanded["message_count"] == 10
    assert expanded["total_message_count"] == 10
    assert expanded["has_more_before"] is False
    assert expanded["before_cursor"] == "0"
    assert expanded["messages"][0]["text"] == "turn 0"
    assert expanded["messages"][-1]["text"] == "turn 9"


async def test_voice_chat_user_autocorrects_text_before_nlu(monkeypatch) -> None:
    bus = LocalEventBus()
    doc = _Doc()
    seen_nlu: list[Event] = []
    webspace_id = "autocorrect-ws"
    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "0")
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                node_id="hub-node",
                root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=False)),
            )
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc(doc))
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
                "text": "открой слайшоу",
                "webspace_id": webspace_id,
                "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)

    assert len(seen_nlu) == 1
    assert seen_nlu[0].payload["text"] == "открой слайдшоу"
    assert seen_nlu[0].payload["_meta"]["original_text"] == "открой слайшоу"
    assert seen_nlu[0].payload["_meta"]["autocorrected_text"] == "открой слайдшоу"
    assert doc.get_map("data")["voice_chat"]["messages"][0]["text"] == "открой слайшоу"


async def test_voice_chat_general_agent_describes_available_agents(monkeypatch) -> None:
    bus = LocalEventBus()
    doc = _Doc()
    seen_nlu: list[Event] = []
    webspace_id = "agent-roster-ws"
    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "0")
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                node_id="hub-node",
                root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=False)),
            )
        ),
    )
    monkeypatch.setattr(router_service_module, "load_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(router_service_module, "watch_rules", lambda *_args, **_kwargs: (lambda: None))
    monkeypatch.setattr(router_service_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDoc(doc))
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
                "text": "Ада, расскажи о своих агентах",
                "webspace_id": webspace_id,
                "_meta": {"route_id": "voice_chat", "voice_chat_scope": "shared"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    await _drain_voice_chat_persist(router)

    assert seen_nlu == []
    response = doc.get_map("data")["voice_chat"]["messages"][-1]
    assert response["active_agent_id"] == "agent:core:general"
    assert response["active_agent_icon"] == "sparkles-outline"
    assert "Арсений" in response["text"]
    assert "Ника" in response["text"]
    assert "Мира" in response["text"]


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
