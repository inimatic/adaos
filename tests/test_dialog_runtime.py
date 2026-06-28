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
                "active_agent": {
                    "id": "agent:conversation_companions:arseni",
                    "label": "Арсений",
                    "gender": "male",
                    "icon": "male-outline",
                    "voice_profile": {"voice": "ru-male"},
                },
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
    assert state.active_agent_gender == "male"
    assert state.active_agent_voice == "ru-male"
    assert state.active_agent_icon == "male-outline"
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
        active_agent_gender="female",
        active_agent_voice="ru-female",
        active_agent_icon="female-outline",
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
    assert action["payload"]["_meta"]["active_agent_gender"] == "female"
    assert action["payload"]["_meta"]["active_agent_voice"] == "ru-female"
    assert action["payload"]["_meta"]["active_agent_icon"] == "female-outline"


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


def test_repair_state_and_frame_metadata_are_attached_to_followup() -> None:
    dialog_runtime.activate_channel(
        webspace_id="desktop",
        channel_id="builder",
        owner="skill:llm_builder",
        default_skill="llm_builder",
        default_tool="chat",
        conversation_id="conv.skill.llm_builder.default.desktop",
        route_id="voice_chat",
    )
    frame = dialog_runtime.set_active_frame(
        webspace_id="desktop",
        frame_id="frame.create_skill",
        owner="skill:llm_builder",
        conversation_id="conv.skill.llm_builder.default.desktop",
        required_slots=("skill_name", "purpose"),
    )
    assert frame.state == "collecting"

    updated = dialog_runtime.apply_frame_input(webspace_id="desktop", text="weather helper")
    assert updated is not None
    assert updated.slots == {"skill_name": "weather helper"}
    assert updated.validation == {"missing_slots": ["purpose"]}

    action = dialog_runtime.resolve_followup_action(
        webspace_id="desktop",
        text="actually change it to calendar helper",
        route_id="voice_chat",
        meta={"route_id": "voice_chat"},
    )

    assert action is not None
    assert action["repair_state"] == "correction"
    assert action["frame"]["frame_id"] == "frame.create_skill"
    assert action["payload"]["_meta"]["dialog_repair_state"] == "correction"
    assert action["payload"]["_meta"]["dialog_frame_id"] == "frame.create_skill"


def test_cancel_repair_clears_active_frame() -> None:
    dialog_runtime.activate_channel(
        webspace_id="desktop",
        channel_id="builder",
        owner="skill:llm_builder",
        default_skill="llm_builder",
        default_tool="chat",
        conversation_id="conv.skill.llm_builder.default.desktop",
        route_id="voice_chat",
    )
    dialog_runtime.set_active_frame(
        webspace_id="desktop",
        frame_id="frame.create_skill",
        required_slots=("skill_name",),
    )

    action = dialog_runtime.resolve_followup_action(
        webspace_id="desktop",
        text="cancel",
        route_id="voice_chat",
        meta={"route_id": "voice_chat"},
    )

    assert action is not None
    assert action["repair_state"] == "cancel"
    assert dialog_runtime.get_active_frame("desktop") is None


def test_active_frame_restores_from_node_store_after_process_cache_clear() -> None:
    from adaos.services import conversation_store

    conversation_store.ensure_schema()
    frame = dialog_runtime.set_active_frame(
        webspace_id="desktop",
        frame_id="frame.restartable",
        owner="skill:builder_skill",
        conversation_id="conv.skill.builder_skill.default.desktop",
        required_slots=("scenario_name", "purpose"),
        slots={"scenario_name": "shopping list"},
        validation={"missing_slots": ["purpose"]},
    )
    assert frame.frame_id == "frame.restartable"

    dialog_runtime._FRAMES_BY_WEBSPACE.clear()  # type: ignore[attr-defined]

    restored = dialog_runtime.get_active_frame("desktop")
    assert restored is not None
    assert restored.frame_id == "frame.restartable"
    assert restored.owner == "skill:builder_skill"
    assert restored.required_slots == ("scenario_name", "purpose")
    assert restored.slots == {"scenario_name": "shopping list"}
    assert restored.validation == {"missing_slots": ["purpose"]}

    removed = dialog_runtime.clear_active_frame("desktop")
    assert removed is not None
    assert removed.frame_id == "frame.restartable"
    assert conversation_store.get_dialog_frame("desktop") is None
