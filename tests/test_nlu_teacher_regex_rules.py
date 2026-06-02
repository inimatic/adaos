# tests/test_nlu_teacher_regex_rules.py
import json
from pathlib import Path
import re
import asyncio

import pytest


@pytest.mark.anyio
async def test_teacher_regex_rule_applies_to_scenario_and_pipeline_picks_it_up():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.pipeline import _try_regex_intent
    from adaos.services.nlu.regex_rules_runtime import _on_regex_rule_apply
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    scenario_id = "web_desktop"
    webspace_id = "ws-test-regex"

    # Minimal scenario that owns the intent mapping (scope=scenario).
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
                        "desktop.open_weather": {
                            "scope": "scenario",
                            "actions": [{"type": "callSkill", "target": "nlp.intent.weather.get", "params": {"city": "$slot.city"}}],
                            "examples": ["погода в Москве"],
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

    # Bind webspace -> scenario.
    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", scenario_id)

    # Baseline: built-in regex stage should not match "температура" (it only matches "погода/weather").
    intent, slots, via, _raw = await _try_regex_intent("Покажи температуру в Берлине", webspace_id=webspace_id)
    assert intent is None
    assert via == "regex"
    assert slots == {}

    # Apply a teacher regex-rule into scenario (NLU Teacher flow).
    pattern = r"\b(?:температур\w*|градус\w*)\b(?:\s+(?:в|во)\s+(?P<city>[^?.!,;:]+))?"
    await _on_regex_rule_apply(
        {
            "webspace_id": webspace_id,
            "intent": "desktop.open_weather",
            "pattern": pattern,
        }
    )

    # Now the same utterance must be recognized via dynamic regex.
    intent, slots, via, _raw = await _try_regex_intent("Покажи температуру в Берлине", webspace_id=webspace_id)
    assert intent == "desktop.open_weather"
    assert via == "regex.dynamic"
    assert slots.get("city") == "Берлине"

    # Verify it was persisted into scenario.json (workspace scope).
    saved = json.loads(scenario_json.read_text(encoding="utf-8"))
    rules = (saved.get("nlu") or {}).get("regex_rules") or []
    matching = [r for r in rules if isinstance(r, dict) and r.get("intent") == "desktop.open_weather" and r.get("pattern") == pattern]
    assert matching
    assert any(isinstance(r.get("id"), str) and re.match(r"^rx\.[0-9a-f-]{36}$", r.get("id")) for r in matching)

    # Also mirrored into per-webspace state as runtime cache.
    async with async_get_ydoc(webspace_id) as ydoc:
        data_map = ydoc.get_map("data")
        nlu_obj = data_map.get("nlu") or {}
        stored = []
        try:
            stored = list((nlu_obj or {}).get("regex_rules") or [])
        except Exception:
            stored = []
        assert any(isinstance(r, dict) and r.get("intent") == "desktop.open_weather" and r.get("pattern") == pattern for r in stored)

    # Regex usage journal should record dynamic hits (JSONL).
    usage_path = Path(ctx.paths.state_dir()) / "nlu" / "regex_usage.jsonl"
    assert usage_path.exists()
    last_lines = usage_path.read_text(encoding="utf-8").splitlines()[-50:]
    rule_id = str((_raw or {}).get("rule_id") or "")
    assert rule_id
    assert any(rule_id in line for line in last_lines)


@pytest.mark.anyio
async def test_teacher_regex_rule_verifies_candidate_after_apply():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.regex_rules_runtime import _on_regex_rule_apply
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    scenario_id = "web_desktop"
    webspace_id = "ws-test-regex-verify"
    candidate_id = "cand.verify"
    request_text = "show temperature in Berlin"
    pattern = r"\btemperature\b(?:\s+in\s+(?P<city>[^?.!,;:]+))?"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    scenario_json = scenario_root / "scenario.json"
    scenario_json.write_text(
        json.dumps(
            {"id": scenario_id, "version": "0.0.1", "nlu": {"intents": {"desktop.open_weather": {"actions": []}}}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    acquired: list[dict] = []

    def _capture_acquired(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            acquired.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.understanding.acquired", _capture_acquired)

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
                            "id": candidate_id,
                            "kind": "regex_rule",
                            "text": request_text,
                            "request_id": "req.verify",
                            "regex_rule": {"intent": "desktop.open_weather", "pattern": pattern},
                            "status": "pending",
                        }
                    ]
                },
            )

    await _on_regex_rule_apply(
        {
            "webspace_id": webspace_id,
            "candidate_id": candidate_id,
            "intent": "desktop.open_weather",
            "pattern": pattern,
            "target": {"type": "scenario", "id": scenario_id},
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        data_map = ydoc.get_map("data")
        teacher = data_map.get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    candidate = next(item for item in candidates if item.get("id") == candidate_id)
    assert candidate["status"] == "intent_matched"
    assert candidate["verification"]["status"] == "intent_matched"
    assert candidate["verification"]["expected_intent"] == "desktop.open_weather"
    assert candidate["verification"]["probe"]["intent"] == "desktop.open_weather"
    assert acquired
    assert acquired[-1]["intent"] == "desktop.open_weather"


@pytest.mark.anyio
async def test_dynamic_regex_canonicalizes_lookup_slots_for_scenario_switch():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.pipeline import _try_regex_intent
    from adaos.services.nlu.regex_rules_runtime import _on_regex_rule_apply
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    owner_scenario_id = "test_infrascope_switch_teacher"
    target_scenario_id = "infrascope"
    webspace_id = "ws-test-infrascope-slot-normalization"
    request_text = "\u041f\u043e\u043a\u0430\u0436\u0438 Infrascope"
    pattern = r"\b(?:\u043f\u043e\u043a\u0430\u0436\u0438|\u043e\u0442\u043a\u0440\u043e\u0439|show|open)\s+(?P<scenario_id>infrascope)\b"

    target_root = Path(ctx.paths.scenarios_dir()) / target_scenario_id
    target_root.mkdir(parents=True, exist_ok=True)
    target_json = target_root / "scenario.json"
    if not target_json.exists():
        target_json.write_text(
            json.dumps({"id": target_scenario_id, "version": "0.0.1"}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    owner_root = Path(ctx.paths.scenarios_dir()) / owner_scenario_id
    owner_root.mkdir(parents=True, exist_ok=True)
    owner_json = owner_root / "scenario.json"
    owner_json.write_text(
        json.dumps(
            {
                "id": owner_scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        "desktop.open_scenario": {
                            "scope": "scenario",
                            "actions": [
                                {
                                    "type": "callHost",
                                    "target": "desktop.scenario.set",
                                    "params": {"scenario_id": "$slot.scenario_id", "webspace_id": "$ctx.webspace_id"},
                                }
                            ],
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
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("ui").set(txn, "current_scenario", owner_scenario_id)

    intent, slots, via, _raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
    assert intent is None
    assert slots == {}
    assert via == "regex"

    await _on_regex_rule_apply(
        {
            "webspace_id": webspace_id,
            "intent": "desktop.open_scenario",
            "pattern": pattern,
            "target": {"type": "scenario", "id": owner_scenario_id},
        }
    )

    intent, slots, via, raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
    assert intent == "desktop.open_scenario"
    assert via == "regex.dynamic"
    assert slots == {"scenario_id": target_scenario_id}
    assert raw["slot_normalization"]["scenario_id"]["from"] == "Infrascope"
    assert raw["slot_normalization"]["scenario_id"]["to"] == target_scenario_id


@pytest.mark.anyio
async def test_lookup_slot_normalization_falls_back_when_live_lookup_times_out(monkeypatch):
    from adaos.services.nlu import pipeline
    import adaos.services.nlu_lookup_tables as lookup_tables

    calls: list[bool] = []

    async def fake_collect(_ctx, *, webspace_id=None, include_live=True):
        calls.append(include_live)
        if include_live:
            await asyncio.sleep(0.2)
            return {}
        return {
            "lookups": {
                "modal_id": [
                    {
                        "value": "browsers_modal",
                        "labels": ["\u0431\u0440\u0430\u0443\u0437\u0435\u0440\u044b"],
                    }
                ]
            }
        }

    monkeypatch.setattr(lookup_tables, "collect_desktop_lookup_tables_async", fake_collect)
    monkeypatch.setattr(pipeline, "_LOOKUP_NORMALIZE_TIMEOUT_S", 0.05)

    normalized, evidence = await pipeline._normalize_lookup_slots(
        {"modal_id": "\u0431\u0440\u0430\u0443\u0437\u0435\u0440\u044b"},
        webspace_id="desktop",
    )

    assert calls == [True, False]
    assert normalized["modal_id"] == "browsers_modal"
    assert evidence["modal_id"] == {
        "from": "\u0431\u0440\u0430\u0443\u0437\u0435\u0440\u044b",
        "to": "browsers_modal",
        "lookup": "modal_id",
        "matched": "label",
    }
