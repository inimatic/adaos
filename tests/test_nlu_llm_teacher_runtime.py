import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_llm_teacher_collects_root_mcp_authoring_evidence(monkeypatch):
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.root_mcp import service as root_mcp_service

    calls: list[dict] = []

    def _fake_invoke_tool(tool_id, **kwargs):
        calls.append({"tool_id": tool_id, **kwargs})
        if tool_id == "nlu_authoring.get_context":
            return SimpleNamespace(ok=True, tool_id=tool_id, status="ok", result={"context": {"plane_id": "nlu_authoring"}})
        return SimpleNamespace(
            ok=True,
            tool_id=tool_id,
            status="ok",
            result={"check": {"ok": True, "accepted": False, "text": kwargs["arguments"]["text"]}},
        )

    monkeypatch.setattr(root_mcp_service, "invoke_tool", _fake_invoke_tool)

    evidence = llm._collect_root_mcp_authoring_evidence(
        webspace_id="desktop",
        text="show temperature in Berlin",
        request_id="req.llm",
        request_locale="en",
        preferred_locales=["ru"],
    )

    assert evidence["nlu_authoring_context"]["plane_id"] == "nlu_authoring"
    assert evidence["nlu_authoring_phrase_check"]["check"]["accepted"] is False
    assert [call["tool_id"] for call in calls] == ["nlu_authoring.get_context", "nlu_authoring.check_phrase"]
    assert calls[0]["auth_context"]["capabilities"] == ["development.read.descriptors"]
    assert calls[1]["arguments"]["emit_trace"] is False
    assert calls[1]["dry_run"] is True


@pytest.mark.anyio
async def test_llm_teacher_prompt_includes_mcp_evidence_and_stores_regex_candidate(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-teacher"
    scenario_id = "test_llm_teacher_scenario"
    pattern = r"\btemperature\b(?:\s+in\s+(?P<city>[^?.!,;:]+))?"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {"id": scenario_id, "version": "0.0.1", "nlu": {"intents": {"desktop.open_weather": {"actions": []}}}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", scenario_id)
            data_map.set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})

    captured_messages: list[list[dict[str, str]]] = []

    async def _fake_llm_call(messages, *, request_id=None):
        captured_messages.append(messages)
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": "desktop.open_weather",
                                    "regex_rule": {"intent": "desktop.open_weather", "pattern": pattern},
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": ["show temperature in Berlin"],
                                    "slots": {"city": {"type": "string"}},
                                    "confidence": 0.91,
                                    "notes": "Existing weather intent needs a temperature synonym.",
                                    "candidate": None,
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(
        llm,
        "_collect_root_mcp_authoring_evidence",
        lambda **kwargs: {
            "nlu_authoring_context": {"plane_id": "nlu_authoring", "named_entities": {"items": []}},
            "nlu_authoring_phrase_check": {"check": {"ok": True, "accepted": False, "text": kwargs["text"]}},
        },
    )
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)

    proposed: list[dict] = []

    def _capture_proposed(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            proposed.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.candidate.proposed", _capture_proposed)

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.llm",
                "request_id": "req.llm",
                "text": "show temperature in Berlin",
                "reason": "fallback",
                "via": "rasa",
                "_meta": {"request_locale": "en"},
            },
        }
    )

    assert captured_messages
    user_payload = json.loads(captured_messages[-1][1]["content"])
    assert user_payload["context"]["root_mcp"]["nlu_authoring_context"]["plane_id"] == "nlu_authoring"
    assert user_payload["context"]["root_mcp"]["nlu_authoring_phrase_check"]["check"]["accepted"] is False

    async with async_get_ydoc(webspace_id) as ydoc:
        data_map = ydoc.get_map("data")
        teacher = data_map.get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    assert proposed
    assert candidates
    candidate = candidates[-1]
    assert candidate["kind"] == "regex_rule"
    assert candidate["regex_rule"] == {"intent": "desktop.open_weather", "pattern": pattern}
    assert candidate["target"] == {"type": "scenario", "id": scenario_id}
    assert candidate["status"] == "pending"


@pytest.mark.anyio
async def test_llm_teacher_closed_loop_regex_candidate_can_be_applied_and_replayed(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.nlu.pipeline import _try_regex_intent
    from adaos.services.nlu.regex_rules_runtime import _on_regex_rule_apply
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-closed-loop"
    scenario_id = "test_llm_teacher_closed_loop"
    request_text = "show temperature in Berlin"
    pattern = r"\btemperature\b(?:\s+in\s+(?P<city>[^?.!,;:]+))?"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {"id": scenario_id, "version": "0.0.1", "nlu": {"intents": {"desktop.open_weather": {"actions": []}}}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", scenario_id)
            data_map.set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})
            data_map.set(txn, "nlu", {"regex_rules": []})

    intent, slots, via, _raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
    assert intent is None
    assert slots == {}
    assert via == "regex"

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": "desktop.open_weather",
                                    "regex_rule": {"intent": "desktop.open_weather", "pattern": pattern},
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "slots": {"city": {"type": "string"}},
                                    "confidence": 0.93,
                                    "notes": "Teach temperature synonym for the existing weather intent.",
                                    "candidate": None,
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(
        llm,
        "_collect_root_mcp_authoring_evidence",
        lambda **kwargs: {"nlu_authoring_phrase_check": {"check": {"accepted": False, "text": kwargs["text"]}}},
    )

    acquired: list[dict] = []

    def _capture_acquired(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            acquired.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.regex_rule.apply", _on_regex_rule_apply)
    ctx.bus.subscribe("nlp.teacher.understanding.acquired", _capture_acquired)

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.closed_loop",
                "request_id": "req.closed_loop",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        data_map = ydoc.get_map("data")
        teacher = data_map.get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    assert candidates
    candidate_id = candidates[-1]["id"]

    await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate_id, "_meta": {"webspace_id": webspace_id}})

    for _ in range(50):
        if acquired:
            break
        await asyncio.sleep(0.01)

    assert acquired
    assert acquired[-1]["intent"] == "desktop.open_weather"

    intent, slots, via, _raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
    assert intent == "desktop.open_weather"
    assert via == "regex.dynamic"
    assert slots.get("city") == "Berlin"
