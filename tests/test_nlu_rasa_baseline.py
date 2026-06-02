from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _examples_for(payload: dict, intent: str) -> str:
    entries = payload.get("nlu") or []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("intent") == intent:
            return str(entry.get("examples") or "")
    return ""


def _lookup_examples_for(payload: dict, lookup: str) -> str:
    entries = payload.get("nlu") or []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("lookup") == lookup:
            return str(entry.get("examples") or "")
    return ""


def test_default_desktop_nlu_sync_exports_modal_intents_to_rasa_project() -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.interpreter.workspace import InterpreterWorkspace
    from adaos.services.nlu.data_registry import sync_from_scenarios_and_skills

    ctx = get_ctx()
    summary = sync_from_scenarios_and_skills(ctx)

    assert summary["scenario_intents"] >= 2
    assert summary["system_action_intents"] >= 4

    ws = InterpreterWorkspace(ctx)
    project = ws.build_rasa_project()
    dataset = yaml.safe_load((project / "data" / "intents_from_config.yml").read_text(encoding="utf-8")) or {}

    open_modal_examples = _examples_for(dataset, "desktop.open_modal")
    open_node_modal_examples = _examples_for(dataset, "desktop.open_node_modal")

    assert "[apps_catalog](modal_id)" in open_modal_examples
    assert "[nlu_teacher_modal](modal_id)" in open_modal_examples
    assert "[member-1](node_ref)" in open_node_modal_examples
    assert "open marketplace" in _examples_for(dataset, "desktop.open_marketplace")
    assert "set timer for [10 minutes](duration)" in _examples_for(dataset, "voice.timer.start")
    assert "reload desktop" in _examples_for(dataset, "desktop.reload_webspace")
    assert "switch to [web_desktop](scenario_id)" in _examples_for(dataset, "desktop.switch_scenario")
    assert "- apps_catalog" in _lookup_examples_for(dataset, "modal_id")
    assert "- nlu_teacher_modal" in _lookup_examples_for(dataset, "modal_id")
    assert "- nlu_teacher_app" in _lookup_examples_for(dataset, "app_id")
    assert "- web_desktop" in _lookup_examples_for(dataset, "scenario_id")
    assert (Path(project) / "data" / "lookup_tables.json").exists()

    rasa_config = yaml.safe_load((Path(project) / "config.yml").read_text(encoding="utf-8")) or {}
    assert "policies" not in rasa_config
    pipeline_names = [item.get("name") for item in rasa_config.get("pipeline", []) if isinstance(item, dict)]
    assert "DIETClassifier" not in pipeline_names
    assert "CRFEntityExtractor" in pipeline_names
    assert "LogisticRegressionClassifier" in pipeline_names


@pytest.mark.anyio
async def test_default_desktop_regex_covers_voice_commands(monkeypatch) -> None:
    from adaos.services.nlu import pipeline as pipeline_module

    async def _current_scenario(_webspace_id: str) -> str:
        return "web_desktop"

    monkeypatch.setattr(pipeline_module, "_resolve_current_scenario_id", _current_scenario)

    marketplace_intent, marketplace_slots, marketplace_via, _ = await pipeline_module._try_regex_intent(
        "\u043e\u0442\u043a\u0440\u043e\u0439 \u043c\u0430\u0440\u043a\u0435\u0442\u043f\u043b\u0435\u0439\u0441",
        webspace_id="desktop",
    )
    time_intent, time_slots, time_via, _ = await pipeline_module._try_regex_intent(
        "\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u0432\u0440\u0435\u043c\u0435\u043d\u0438",
        webspace_id="desktop",
    )
    timer_intent, timer_slots, timer_via, _ = await pipeline_module._try_regex_intent(
        "\u043f\u043e\u0441\u0442\u0430\u0432\u044c \u0442\u0430\u0439\u043c\u0435\u0440 \u043d\u0430 10 \u043c\u0438\u043d\u0443\u0442",
        webspace_id="desktop",
    )
    media_intent, media_slots, media_via, media_raw = await pipeline_module._try_regex_intent(
        "\u043f\u043e\u043a\u0430\u0436\u0438 media indexer",
        webspace_id="desktop",
    )
    indexer_intent, indexer_slots, indexer_via, _ = await pipeline_module._try_regex_intent(
        "\u043f\u043e\u043a\u0430\u0436\u0438 \u0438\u043d\u0434\u0435\u043a\u0441\u0430",
        webspace_id="desktop",
    )
    mediaserver_intent, mediaserver_slots, mediaserver_via, _ = await pipeline_module._try_regex_intent(
        "\u043f\u043e\u043a\u0430\u0436\u0438 \u043c\u0435\u0434\u0438\u0430 \u0441\u0435\u0440\u0432\u0435\u0440",
        webspace_id="desktop",
    )
    mediaserver_compact_intent, mediaserver_compact_slots, mediaserver_compact_via, _ = await pipeline_module._try_regex_intent(
        "\u043f\u043e\u043a\u0430\u0436\u0438 \u043c\u0435\u0434\u0438\u0430\u0441\u0435\u0440\u0432\u0435\u0440",
        webspace_id="desktop",
    )

    assert (marketplace_intent, marketplace_slots, marketplace_via) == (
        "desktop.open_marketplace",
        {},
        "regex",
    )
    assert (time_intent, time_slots, time_via) == ("voice.time.now", {}, "regex")
    assert timer_intent == "voice.timer.start"
    assert timer_slots == {"duration": "10 \u043c\u0438\u043d\u0443\u0442"}
    assert timer_via == "regex"
    assert (media_intent, media_slots, media_via) == (
        "desktop.open_modal",
        {"modal_id": "media_indexer_modal"},
        "regex.lookup",
    )
    assert media_raw["builtin"] == "desktop.open_modal.lookup"
    assert (indexer_intent, indexer_slots, indexer_via) == (
        "desktop.open_modal",
        {"modal_id": "media_indexer_modal"},
        "regex.lookup",
    )
    assert (mediaserver_intent, mediaserver_slots, mediaserver_via) == (
        "desktop.open_modal",
        {"modal_id": "mediaserver_modal"},
        "regex.lookup",
    )
    assert (mediaserver_compact_intent, mediaserver_compact_slots, mediaserver_compact_via) == (
        "desktop.open_modal",
        {"modal_id": "mediaserver_modal"},
        "regex.lookup",
    )


@pytest.mark.anyio
async def test_default_desktop_nlu_dispatches_voice_timer_start(monkeypatch) -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import dispatcher as dispatcher_module

    ctx = get_ctx()
    emitted: list[dict] = []
    ctx.bus.subscribe("voice.chat.timer_start", lambda ev: emitted.append(dict(ev.payload or {})))
    async def _scenario_id(_ctx, _webspace_id: str) -> str:
        return "web_desktop"

    monkeypatch.setattr(dispatcher_module, "_resolve_scenario_id", _scenario_id)

    await dispatcher_module._on_nlp_intent_detected(
        {
            "intent": "voice.timer.start",
            "confidence": 1.0,
            "slots": {"duration": "10 \u043c\u0438\u043d\u0443\u0442"},
            "text": "\u043f\u043e\u0441\u0442\u0430\u0432\u044c \u0442\u0430\u0439\u043c\u0435\u0440 \u043d\u0430 10 \u043c\u0438\u043d\u0443\u0442",
            "webspace_id": "desktop",
            "_meta": {"route_id": "voice_chat"},
        }
    )

    assert emitted == [
        {
            "duration": "10 \u043c\u0438\u043d\u0443\u0442",
            "webspace_id": "desktop",
            "slots": {"duration": "10 \u043c\u0438\u043d\u0443\u0442"},
            "text": "\u043f\u043e\u0441\u0442\u0430\u0432\u044c \u0442\u0430\u0439\u043c\u0435\u0440 \u043d\u0430 10 \u043c\u0438\u043d\u0443\u0442",
            "_meta": {"route_id": "voice_chat", "webspace_id": "desktop", "scenario_id": "web_desktop"},
        }
    ]


@pytest.mark.anyio
async def test_default_desktop_nlu_dispatches_marketplace_open(monkeypatch) -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import dispatcher as dispatcher_module

    ctx = get_ctx()
    emitted: list[dict] = []
    ctx.bus.subscribe("desktop.modal.open", lambda ev: emitted.append(dict(ev.payload or {})))

    async def _scenario_id(_ctx, _webspace_id: str) -> str:
        return "web_desktop"

    monkeypatch.setattr(dispatcher_module, "_resolve_scenario_id", _scenario_id)

    await dispatcher_module._on_nlp_intent_detected(
        {
            "intent": "desktop.open_marketplace",
            "confidence": 1.0,
            "slots": {},
            "text": "\u043e\u0442\u043a\u0440\u043e\u0439 \u043c\u0430\u0440\u043a\u0435\u0442\u043f\u043b\u0435\u0439\u0441",
            "webspace_id": "desktop",
            "_meta": {"route_id": "voice_chat"},
        }
    )

    assert emitted == [
        {
            "modal_id": "apps_catalog",
            "webspace_id": "desktop",
            "text": "\u043e\u0442\u043a\u0440\u043e\u0439 \u043c\u0430\u0440\u043a\u0435\u0442\u043f\u043b\u0435\u0439\u0441",
            "_meta": {"route_id": "voice_chat", "webspace_id": "desktop", "scenario_id": "web_desktop"},
        }
    ]


@pytest.mark.anyio
async def test_default_desktop_nlu_dispatches_modal_open_with_target_node_meta(monkeypatch) -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import dispatcher as dispatcher_module

    ctx = get_ctx()
    emitted: list[dict] = []
    ctx.bus.subscribe("desktop.modal.open", lambda ev: emitted.append(dict(ev.payload or {})))

    async def _scenario_id(_ctx, _webspace_id: str) -> str:
        return "web_desktop"

    monkeypatch.setattr(dispatcher_module, "_resolve_scenario_id", _scenario_id)

    await dispatcher_module._on_nlp_intent_detected(
        {
            "intent": "desktop.open_modal",
            "confidence": 1.0,
            "slots": {"modal_id": "browsers_modal"},
            "text": "\u043f\u043e\u043a\u0430\u0436\u0438 \u0431\u0440\u0430\u0443\u0437\u0435\u0440\u044b",
            "webspace_id": "desktop",
            "_meta": {"route_id": "voice_chat", "target_node_id": "member-1"},
        }
    )

    assert emitted == [
        {
            "modal_id": "browsers_modal",
            "webspace_id": "desktop",
            "slots": {"modal_id": "browsers_modal"},
            "text": "\u043f\u043e\u043a\u0430\u0436\u0438 \u0431\u0440\u0430\u0443\u0437\u0435\u0440\u044b",
            "target_node_id": "member-1",
            "_meta": {
                "route_id": "voice_chat",
                "target_node_id": "member-1",
                "webspace_id": "desktop",
                "scenario_id": "web_desktop",
            },
        }
    ]


@pytest.mark.anyio
async def test_default_desktop_nlu_dispatches_named_node_modal_open() -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.dispatcher import _on_nlp_intent_detected

    ctx = get_ctx()
    emitted: list[dict] = []
    ctx.bus.subscribe("desktop.modal.open", lambda ev: emitted.append(dict(ev.payload or {})))

    await _on_nlp_intent_detected(
        {
            "intent": "desktop.open_node_modal",
            "confidence": 0.95,
            "slots": {"modal_id": "apps_catalog", "node_ref": "member-1"},
            "text": "open apps_catalog on node member-1",
            "webspace_id": "desktop",
        }
    )

    assert emitted == [
        {
            "modal_id": "apps_catalog",
            "node_ref": "member-1",
            "target_node_id": "member-1",
            "webspace_id": "desktop",
            "slots": {"modal_id": "apps_catalog", "node_ref": "member-1"},
            "text": "open apps_catalog on node member-1",
            "_meta": {"webspace_id": "desktop", "scenario_id": "web_desktop"},
        }
    ]


@pytest.mark.anyio
async def test_default_desktop_nlu_dispatches_system_webspace_reload() -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.dispatcher import _on_nlp_intent_detected

    ctx = get_ctx()
    emitted: list[dict] = []
    ctx.bus.subscribe("desktop.webspace.reload", lambda ev: emitted.append(dict(ev.payload or {})))

    await _on_nlp_intent_detected(
        {
            "intent": "desktop.reload_webspace",
            "confidence": 0.95,
            "slots": {},
            "text": "reload desktop",
            "webspace_id": "desktop",
        }
    )

    assert emitted == [
        {
            "webspace_id": "desktop",
            "text": "reload desktop",
            "_meta": {"webspace_id": "desktop", "scenario_id": "web_desktop"},
        }
    ]
