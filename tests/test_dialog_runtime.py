from __future__ import annotations

from adaos.services import dialog_runtime


def setup_function(_function=None) -> None:
    dialog_runtime.reset_all()


def teardown_function(_function=None) -> None:
    dialog_runtime.reset_all()


def test_apply_tool_result_activates_companion_channel() -> None:
    state = dialog_runtime.apply_tool_result(
        {
            "ok": True,
            "active_character": "arseni",
            "dialog": {
                "dialog_channel_id": "conversational",
                "conversation_id": "conv.skill.conversation_companions.default.desktop",
                "owner": "skill:conversation_companions",
                "default_tool": "conversation_companions.talk",
                "active_agent_id": "agent:conversation_companions:arseni",
                "active_agent_label": "Арсений",
            },
        },
        webspace_id="desktop",
        target="conversation_companions.start",
        raw_meta={"route_id": "voice_chat", "request_id": "req-1"},
    )

    assert state is not None
    assert state.channel_id == "conversational"
    assert state.default_skill == "conversation_companions"
    assert state.default_tool == "talk"
    assert state.active_agent_id == "agent:conversation_companions:arseni"
    assert state.active_agent_label == "Арсений"
    assert dialog_runtime.get_active_channel("desktop") == state


def test_resolve_followup_routes_to_owner_default_tool() -> None:
    dialog_runtime.activate_channel(
        webspace_id="desktop",
        channel_id="conversational",
        owner="skill:conversation_companions",
        default_skill="conversation_companions",
        default_tool="talk",
        conversation_id="conv.skill.conversation_companions.default.desktop",
        active_agent_id="agent:conversation_companions:nika",
        active_agent_label="Ника",
        route_id="voice_chat",
    )

    action = dialog_runtime.resolve_followup_action(
        webspace_id="desktop",
        text="let us discuss the launch plan",
        route_id="voice_chat",
        meta={"route_id": "voice_chat", "webspace_id": "desktop"},
    )

    assert action is not None
    assert action["kind"] == "skill_tool"
    assert action["skill"] == "conversation_companions"
    assert action["tool"] == "talk"
    assert action["payload"]["text"] == "let us discuss the launch plan"
    assert action["payload"]["_meta"]["dialog_channel_id"] == "conversational"
    assert action["payload"]["_meta"]["active_agent_id"] == "agent:conversation_companions:nika"
    assert action["payload"]["_meta"]["active_agent_label"] == "Ника"


def test_resolve_followup_exit_deactivates_channel() -> None:
    dialog_runtime.activate_channel(
        webspace_id="desktop",
        channel_id="conversational",
        owner="skill:conversation_companions",
        default_skill="conversation_companions",
        default_tool="talk",
        conversation_id="conv.skill.conversation_companions.default.desktop",
        route_id="voice_chat",
    )

    action = dialog_runtime.resolve_followup_action(
        webspace_id="desktop",
        text="\u0432 \u043e\u0431\u0449\u0438\u0439 \u0440\u0435\u0436\u0438\u043c",
        route_id="voice_chat",
        meta={"route_id": "voice_chat"},
    )

    assert action is not None
    assert action["kind"] == "exit"
    removed = dialog_runtime.deactivate_channel(webspace_id="desktop", channel_id="conversational")
    assert removed is not None
    assert dialog_runtime.get_active_channel("desktop") is None
