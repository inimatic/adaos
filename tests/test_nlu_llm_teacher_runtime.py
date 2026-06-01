import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


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
                            "text": "```json\n"
                            + json.dumps(
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
                            )
                            + "\n```",
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
    assert candidate["preview"]["status"] == "regex_matched"
    assert candidate["preview"]["slots"]["city"] == "Berlin"


@pytest.mark.anyio
async def test_llm_teacher_quarantines_regex_candidate_that_misses_source_text(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-teacher-quarantine"
    scenario_id = "test_llm_teacher_quarantine"
    request_text = "open the operations console"
    pattern = r"\bweather\b"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        "demo.interface_action.open_ops_console": {
                            "actions": [{"type": "callHost", "target": "desktop.modal.open"}]
                        }
                    }
                },
            },
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
                                    "intent": "demo.interface_action.open_ops_console",
                                    "regex_rule": {
                                        "intent": "demo.interface_action.open_ops_console",
                                        "pattern": pattern,
                                    },
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "slots": {},
                                    "confidence": 0.77,
                                    "notes": "Bad candidate for quarantine test.",
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
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})

    rejected: list[dict] = []

    def _capture_rejected(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            rejected.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.candidate.apply.rejected", _capture_rejected)

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.quarantine",
                "request_id": "req.quarantine",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    assert candidates
    candidate = candidates[-1]
    assert candidate["status"] == "quarantined"
    assert candidate["preview"]["status"] == "source_text_miss"

    await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate["id"]})
    assert rejected
    assert rejected[-1]["reason"] == "candidate_quarantined"


@pytest.mark.anyio
async def test_llm_teacher_records_prompt_hashes_and_suppresses_duplicate_regex(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-teacher-dedupe"
    scenario_id = "test_llm_teacher_dedupe"
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
                                    "confidence": 0.91,
                                    "notes": "Duplicate suppression smoke.",
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
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})

    duplicates: list[dict] = []

    def _capture_duplicate(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            duplicates.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.candidate.duplicate_suppressed", _capture_duplicate)

    for suffix in ("one", "two"):
        await llm._on_teacher_request(
            {
                "webspace_id": webspace_id,
                "request": {
                    "id": f"teach.dedupe.{suffix}",
                    "request_id": f"req.dedupe.{suffix}",
                    "text": request_text,
                    "reason": "fallback",
                    "via": "rasa",
                },
            }
        )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])
        logs = list((teacher or {}).get("llm_logs") or [])

    assert len(candidates) == 1
    assert duplicates
    assert duplicates[-1]["duplicate_of"] == candidates[0]["id"]
    audit = candidates[0]["llm"]["audit"]
    assert audit["prompt_hash"].startswith("sha256:")
    assert audit["context_hash"].startswith("sha256:")
    assert audit["request_hash"].startswith("sha256:")
    assert logs[-1]["audit"]["prompt_hash"].startswith("sha256:")


@pytest.mark.anyio
async def test_llm_teacher_includes_correction_thread_context(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-teacher-correction"
    scenario_id = "test_llm_teacher_correction"
    request_text = "no, open the operations console instead"
    intent_name = "demo.interface_action.open_ops_console"
    pattern = r"\boperations\s+console\b"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        intent_name: {"actions": [{"type": "callHost", "target": "desktop.modal.open"}]}
                    }
                },
            },
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
            data_map.set(
                txn,
                "nlu_teacher",
                {
                    "candidates": [
                        {
                            "id": "cand.previous",
                            "ts": 10.0,
                            "kind": "regex_rule",
                            "text": "open weather",
                            "request_id": "req.previous",
                            "regex_rule": {"intent": "desktop.open_weather", "pattern": r"\bweather\b"},
                            "target": {"type": "scenario", "id": scenario_id},
                            "status": "intent_matched",
                        }
                    ],
                    "llm_logs": [],
                },
            )

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
                                    "intent": intent_name,
                                    "regex_rule": {"intent": intent_name, "pattern": pattern},
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "slots": {},
                                    "confidence": 0.87,
                                    "notes": "Correction of the previous weather candidate.",
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
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.correction",
                "request_id": "req.correction",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    user_payload = json.loads(captured_messages[-1][1]["content"])
    correction = user_payload["context"]["correction_thread"]
    assert correction["active"] is True
    assert correction["previous_candidate"]["candidate_id"] == "cand.previous"
    assert correction["previous_candidate"]["regex_rule"]["intent"] == "desktop.open_weather"

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    candidate = candidates[-1]
    assert candidate["correction_of"]["candidate_id"] == "cand.previous"
    assert candidate["thread_id"] == "thread.req.previous"


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


@pytest.mark.anyio
async def test_llm_teacher_trains_skill_action_regex_and_rolls_back(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.nlu.pipeline import _try_regex_intent
    from adaos.services.nlu.regex_rules_runtime import _on_regex_rule_apply, _on_regex_rule_rollback
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-skill-action-teacher"
    scenario_id = "test_skill_action_teacher"
    skill_id = "test_weather_action_skill"
    request_text = "skillprobeweather in Oslo"
    intent_name = "demo.skill_action.weather"
    pattern = r"\bskillprobeweather\b(?:\s+in\s+(?P<city>[^?.!,;:]+))?"

    skill_root = Path(ctx.paths.skills_dir()) / skill_id
    skill_root.mkdir(parents=True, exist_ok=True)
    skill_yaml = skill_root / "skill.yaml"
    skill_yaml.write_text(
        yaml.safe_dump(
            {
                "name": skill_id,
                "version": "0.0.1",
                "events": {"subscribe": ["demo.weather.fetch"]},
                "nlu": {"regex_rules": []},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        intent_name: {
                            "actions": [{"type": "callSkill", "target": "demo.weather.fetch", "params": {"city": "$slot.city"}}]
                        }
                    }
                },
            },
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
            data_map.set(txn, "catalog", {"apps": [{"id": "weather", "origin": f"skill:{skill_id}"}]})
            data_map.set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})
            data_map.set(txn, "nlu", {"regex_rules": []})

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
                                    "intent": intent_name,
                                    "regex_rule": {"intent": intent_name, "pattern": pattern},
                                    "target": None,
                                    "examples": [request_text],
                                    "slots": {"city": {"type": "string"}},
                                    "confidence": 0.9,
                                    "notes": "Skill action should route to the weather skill owner.",
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
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})
    ctx.bus.subscribe("nlp.teacher.regex_rule.apply", _on_regex_rule_apply)

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {"id": "teach.skill", "request_id": "req.skill", "text": request_text, "reason": "fallback", "via": "rasa"},
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])
    assert candidates[-1]["target"] == {"type": "skill", "id": skill_id}
    candidate_id = candidates[-1]["id"]

    await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate_id, "_meta": {"webspace_id": webspace_id}})
    for _ in range(50):
        intent, slots, via, _raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
        if intent == intent_name:
            break
        await asyncio.sleep(0.01)

    assert intent == intent_name
    assert via == "regex.dynamic"
    assert slots.get("city") == "Oslo"
    saved_skill = yaml.safe_load(skill_yaml.read_text(encoding="utf-8")) or {}
    assert (saved_skill.get("nlu") or {}).get("regex_rules")

    await _on_regex_rule_rollback({"webspace_id": webspace_id, "candidate_id": candidate_id, "_meta": {"webspace_id": webspace_id}})
    intent, slots, via, _raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
    assert intent is None
    assert slots == {}
    saved_skill = yaml.safe_load(skill_yaml.read_text(encoding="utf-8")) or {}
    assert not ((saved_skill.get("nlu") or {}).get("regex_rules") or [])


@pytest.mark.anyio
async def test_llm_teacher_trains_interface_action_regex_and_rolls_back(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.nlu.pipeline import _try_regex_intent
    from adaos.services.nlu.regex_rules_runtime import _on_regex_rule_apply, _on_regex_rule_rollback
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-interface-action-teacher"
    scenario_id = "test_interface_action_teacher"
    request_text = "launch opsconsole"
    intent_name = "demo.interface_action.open_ops_console"
    pattern = r"\bopsconsole\b"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    scenario_json = scenario_root / "scenario.json"
    scenario_json.write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        intent_name: {
                            "actions": [
                                {"type": "callHost", "target": "desktop.modal.open", "params": {"modal_id": "ops_console"}}
                            ]
                        }
                    }
                },
            },
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
                                    "intent": intent_name,
                                    "regex_rule": {"intent": intent_name, "pattern": pattern},
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "slots": {},
                                    "confidence": 0.88,
                                    "notes": "Interface action should stay scenario-owned.",
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
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})
    ctx.bus.subscribe("nlp.teacher.regex_rule.apply", _on_regex_rule_apply)

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {"id": "teach.interface", "request_id": "req.interface", "text": request_text, "reason": "fallback", "via": "rasa"},
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])
    assert candidates[-1]["target"] == {"type": "scenario", "id": scenario_id}
    candidate_id = candidates[-1]["id"]

    await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate_id, "_meta": {"webspace_id": webspace_id}})
    for _ in range(50):
        intent, slots, via, _raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
        if intent == intent_name:
            break
        await asyncio.sleep(0.01)

    assert intent == intent_name
    assert via == "regex.dynamic"
    saved_scenario = json.loads(scenario_json.read_text(encoding="utf-8"))
    assert (saved_scenario.get("nlu") or {}).get("regex_rules")

    await _on_regex_rule_rollback({"webspace_id": webspace_id, "candidate_id": candidate_id, "_meta": {"webspace_id": webspace_id}})
    intent, slots, via, _raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
    assert intent is None
    assert slots == {}
    saved_scenario = json.loads(scenario_json.read_text(encoding="utf-8"))
    assert not ((saved_scenario.get("nlu") or {}).get("regex_rules") or [])
