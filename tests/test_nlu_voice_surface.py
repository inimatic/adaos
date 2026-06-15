import json


def test_voice_surface_match_expands_nested_affordance_activation_plan():
    from adaos.services.nlu.voice_surface import find_voice_surface_match

    context = {
        "root_mcp": {
            "nlu_authoring_context": {
                "action_surface": {
                    "voice_capabilities": [
                        {
                            "id": "infrastate.inventory.installed_skills.query",
                            "title": "Installed skills",
                            "labels": {
                                "ru": [
                                    "\u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043d\u044b\u0435 \u043d\u0430\u0432\u044b\u043a\u0438"
                                ]
                            },
                            "side_effect_class": "read_only",
                            "activation": [
                                {"type": "desktop.open_modal", "params": {"modal_id": "infrastate_modal"}},
                                {"type": "ui.state.set", "params": {"key": "infrastateTab", "value": "inventory"}},
                                {
                                    "type": "ui.affordance.activate",
                                    "params": {"affordance_id": "infrastate.inventory.installed_skills"},
                                },
                            ],
                        }
                    ],
                    "voice_affordances": [
                        {
                            "id": "infrastate.inventory.installed_skills",
                            "title": "Installed skills section",
                            "activation": [
                                {"type": "desktop.open_modal", "params": {"modal_id": "infrastate_modal"}},
                                {"type": "ui.state.set", "params": {"key": "infrastateTab", "value": "inventory"}},
                                {"type": "ui.focus_widget", "params": {"widget_id": "infrastate-skills"}},
                            ],
                        }
                    ],
                }
            }
        }
    }

    match = find_voice_surface_match(
        context,
        "\u041f\u043e\u043a\u0430\u0436\u0438 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043d\u044b\u0435 \u043d\u0430\u0432\u044b\u043a\u0438",
    )

    assert match is not None
    assert match["capability_id"] == "infrastate.inventory.installed_skills.query"
    assert match["affordance_id"] == "infrastate.inventory.installed_skills"
    assert [step["type"] for step in match["activation_plan"]] == [
        "desktop.open_modal",
        "ui.state.set",
        "ui.focus_widget",
    ]


def test_voice_capability_binding_validation_accepts_json_activation_plan():
    from adaos.services.nlu.teacher_validation import validate_candidate_apply
    from adaos.services.nlu.voice_surface import VOICE_CAPABILITY_BINDING_INTENT, exact_phrase_pattern

    plan = [
        {"type": "desktop.open_modal", "params": {"modal_id": "infrastate_modal"}},
        {"type": "ui.state.set", "params": {"key": "infrastateTab", "value": "inventory"}},
        {"type": "ui.focus_widget", "params": {"widget_id": "infrastate-skills"}},
    ]
    candidate = {
        "id": "cand.voice-surface",
        "kind": "voice_capability_binding",
        "status": "pending",
        "text": "show installed skills",
        "target": {"type": "scenario", "id": "web_desktop"},
        "regex_rule": {"intent": VOICE_CAPABILITY_BINDING_INTENT, "pattern": exact_phrase_pattern("show installed skills")},
        "action_candidate": {
            "class": "interface_action",
            "intent": VOICE_CAPABILITY_BINDING_INTENT,
            "action_id": "host.voice_capability.activate",
            "side_effect_class": "read_only",
            "slots": {"activation_plan": json.dumps(plan)},
        },
    }

    validation = validate_candidate_apply(webspace_id="desktop", candidate=candidate)

    assert validation["ok"] is True
    assert validation["kind"] == "voice_capability_binding"
    assert any(check["name"] == "activation_step[2].widget_id" and check["ok"] for check in validation["checks"])


def test_voice_capability_dispatcher_emits_activation_steps():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import dispatcher

    ctx = get_ctx()
    captured: list[tuple[str, dict]] = []

    def _capture(kind: str):
        def _inner(ev):
            payload = getattr(ev, "payload", None) or {}
            if isinstance(payload, dict):
                captured.append((kind, dict(payload)))

        return _inner

    for topic in ("desktop.modal.open", "ui.state.set", "ui.focus_widget", "nlu.action.dispatched"):
        ctx.bus.subscribe(topic, _capture(topic))

    plan = [
        {"type": "desktop.open_modal", "params": {"modal_id": "infrastate_modal"}},
        {"type": "ui.state.set", "params": {"key": "infrastateTab", "value": "inventory"}},
        {"type": "ui.focus_widget", "params": {"widget_id": "infrastate-skills"}},
    ]

    dispatcher._on_voice_capability_activate(
        {
            "webspace_id": "desktop",
            "capability_id": "infrastate.inventory.installed_skills.query",
            "affordance_id": "infrastate.inventory.installed_skills",
            "activation_plan": json.dumps(plan),
            "_meta": {"webspace_id": "desktop", "route_id": "voice_chat"},
        }
    )

    kinds = [kind for kind, _payload in captured]
    assert kinds[:3] == ["desktop.modal.open", "ui.state.set", "ui.focus_widget"]
    assert "nlu.action.dispatched" in kinds
    modal_payload = captured[0][1]
    assert modal_payload["modal_id"] == "infrastate_modal"
    assert modal_payload["_meta"]["voice_capability_activation"] is True
    state_payload = captured[1][1]
    assert state_payload["key"] == "infrastateTab"
    assert state_payload["value"] == "inventory"
    focus_payload = captured[2][1]
    assert focus_payload["widget_id"] == "infrastate-skills"


def test_voice_capability_dispatcher_fails_empty_activation_plan_without_ack():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import dispatcher

    ctx = get_ctx()
    captured: list[tuple[str, dict]] = []

    def _capture(kind: str):
        def _inner(ev):
            payload = getattr(ev, "payload", None) or {}
            if isinstance(payload, dict):
                captured.append((kind, dict(payload)))

        return _inner

    for topic in ("io.out.chat.append", "nlu.action.dispatch_failed"):
        ctx.bus.subscribe(topic, _capture(topic))

    dispatcher._on_voice_capability_activate(
        {
            "webspace_id": "desktop",
            "capability_id": "infrastate.inventory.installed_skills.query",
            "activation_plan": "[]",
            "_meta": {"webspace_id": "desktop", "route_id": "voice_chat"},
        }
    )

    kinds = [kind for kind, _payload in captured]
    assert "io.out.chat.append" not in kinds
    assert "nlu.action.dispatch_failed" in kinds
    failure = next(payload for kind, payload in captured if kind == "nlu.action.dispatch_failed")
    assert failure["reason"] == "activation_plan_empty"
    assert failure["action_payload"]["activation_steps"] == 0
