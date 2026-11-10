from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.domain import Event
from adaos.services import dialog_runtime
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import LocalEventBus
from adaos.services.router import service as router_module
from test_router_voice_chat import _Doc, _AsyncDoc, _MetaCtx, _drain_voice_chat_persist


def metadata(transport="max", **changes):
    return {
        "io_type": transport,
        "route_id": transport,
        "hub_id": "home",
        "bot_id": "bot",
        "chat_id": "42",
        "user_id": "7",
        "binding_id": "pairing",
        **changes,
    }


def select(meta=None, persona="arseni"):
    return dialog_runtime.activate_channel(
        webspace_id="desktop",
        channel_id="conversational",
        owner="skill:conversation_companions",
        default_skill="conversation_companions",
        default_tool="talk",
        conversation_id="conv.companions",
        active_agent_id="agent:conversation_companions:" + persona,
        active_agent_label=persona,
        route_id=(meta or {}).get("route_id", "voice_chat"),
        scope_meta=meta,
    )


@pytest.mark.parametrize("transport", ["max", "telegram"])
def test_selected_agent_survives_desktop_changes_and_runtime_restart(transport):
    dialog_runtime.reset_all()
    meta = metadata(transport)
    selected = select(meta)
    select(persona="mira")  # UI tab changes cannot retarget this chat
    action = dialog_runtime.resolve_followup_action(
        webspace_id="desktop", text="Next question", route_id=transport, meta=meta
    )
    assert action["payload"]["_meta"]["active_agent_id"] == selected.active_agent_id
    dialog_runtime.reset_all()  # model process loss, not a new recipient grant
    assert (
        dialog_runtime.get_active_channel(
            "desktop", scope_meta={**meta, "request_id": "new"}
        )
        == selected
    )
    for field in (
        "hub_id",
        "bot_id",
        "chat_id",
        "user_id",
        "binding_id",
        "transport_thread_id",
    ):
        assert (
            dialog_runtime.get_active_channel(
                "desktop", scope_meta={**meta, field: "another"}
            )
            is None
        )
    assert dialog_runtime.get_active_channel("another-desktop", scope_meta=meta) is None


def test_exit_and_tool_state_only_affect_the_originating_chat():
    dialog_runtime.reset_all()
    meta = metadata()
    select(meta)
    ui = select(persona="mira")
    result = {
        "ok": True,
        "active_character": "nika",
        "dialog": {
            "dialog_channel_id": "conversational",
            "default_tool": "conversation_companions.talk",
            "active_agent_id": "agent:conversation_companions:nika",
            "owner": "skill:conversation_companions",
        },
    }
    dialog_runtime.apply_tool_result(
        result,
        webspace_id="desktop",
        target="conversation_companions.talk",
        raw_meta=meta,
    )
    assert dialog_runtime.get_active_channel(
        "desktop", scope_meta=meta
    ).active_agent_id.endswith(":nika")
    assert dialog_runtime.get_active_channel("desktop") == ui
    dialog_runtime.deactivate_channel(webspace_id="desktop", scope_meta=meta)
    dialog_runtime.reset_all()
    assert dialog_runtime.get_active_channel("desktop", scope_meta=meta) is None


def test_incomplete_transport_identity_cannot_borrow_desktop_selection():
    select(persona="mira")
    assert (
        dialog_runtime.get_active_channel("desktop", scope_meta={"io_type": "max"})
        is None
    )
    with pytest.raises(ValueError, match="identity"):
        select({"io_type": "max"})


def test_transport_followup_cannot_read_or_cancel_desktop_frame():
    meta = metadata()
    select(meta)
    frame = dialog_runtime.set_active_frame(
        webspace_id="desktop",
        frame_id="private-desktop-form",
        slots={"secret": "desktop-only"},
        required_slots=["answer"],
    )
    for text in ("Next question", "cancel"):
        action = dialog_runtime.resolve_followup_action(
            webspace_id="desktop", text=text, route_id="max", meta=meta
        )
        assert action is not None
        assert action.get("frame") is None
        assert "dialog_frame_id" not in action.get("payload", {}).get("_meta", {})
        assert dialog_runtime.get_active_frame("desktop") == frame


@pytest.mark.anyio
@pytest.mark.parametrize("transport", ["max", "telegram"])
async def test_address_then_unnamed_followup_after_ui_change_and_restart(
    monkeypatch, transport
):
    ctx = get_ctx()
    bus = LocalEventBus()
    doc = _Doc()
    calls = []
    nlu = []
    monkeypatch.setenv("ADAOS_VOICE_CHAT_INTENT_DEMO", "0")
    monkeypatch.setattr(
        router_module,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(node_id="hub"),
            paths=ctx.paths,
            sql=ctx.sql,
            skill_ctx=ctx.skill_ctx,
            skills_repo=None,
            git=None,
            caps=None,
            settings=None,
        ),
    )
    monkeypatch.setattr(router_module, "load_rules", lambda *_a, **_k: [])
    monkeypatch.setattr(router_module, "watch_rules", lambda *_a, **_k: lambda: None)
    monkeypatch.setattr(
        router_module, "async_get_ydoc", lambda *_a, **_k: _AsyncDoc(doc)
    )
    monkeypatch.setattr(router_module, "ystore_write_metadata", lambda **_k: _MetaCtx())

    def run(skill, tool, payload, **opts):
        calls.append((skill, tool, payload))
        character = payload.get("character_id", "arseni")
        return {
            "ok": True,
            "message": "Arseni response",
            "dialog": {
                "dialog_channel_id": "conversational",
                "conversation_id": "conv.companions",
                "owner": "skill:conversation_companions",
                "default_tool": "conversation_companions.talk",
                "active_agent_id": "agent:conversation_companions:" + character,
                "active_agent_label": character,
            },
        }

    monkeypatch.setattr(
        router_module, "SkillManager", lambda **_kw: SimpleNamespace(run_tool=run)
    )
    dialog_runtime.reset_all()
    router = router_module.RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()
    bus.subscribe("nlp.intent.detect.request", lambda ev: nlu.append(ev))

    async def send(text, request):
        bus.publish(
            Event(
                type="dialog.user_message",
                source="chat_io." + transport,
                ts=1,
                payload={
                    "text": text,
                    "webspace_id": "desktop",
                    "_meta": metadata(transport, request_id=request),
                },
            )
        )
        assert await bus.wait_for_idle(timeout=8)
        await _drain_voice_chat_persist(router)

    await send("Арсений, первый вопрос", "one")
    assert calls[-1][2]["character_id"] == "arseni"
    select(persona="mira")
    dialog_runtime.reset_all()
    await send("А следующий вопрос?", "two")
    assert len(calls) == 2 and nlu == []
    assert calls[-1][0:2] == ("conversation_companions", "talk")
    assert calls[-1][2]["character_id"] == "arseni"
    assert calls[-1][2]["_meta"]["route_id"] == transport
    await send("Ника, теперь вопрос тебе", "switch")
    await send("Ещё один вопрос", "followup")
    assert calls[-1][2]["character_id"] == "nika" and nlu == []
    await send("Assistant, в общий режим", "three")
    assert (
        dialog_runtime.get_active_channel("desktop", scope_meta=metadata(transport))
        is None
    )
