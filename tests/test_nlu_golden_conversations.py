import asyncio
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml


def _fixture(name: str) -> dict:
    path = Path(__file__).parent / "fixtures" / "nlu" / name
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.anyio
async def test_gate1_golden_conversation_learn_replay_rollback():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import dispatcher
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.nlu.pipeline import _try_regex_intent
    from adaos.services.nlu.regex_rules_runtime import _on_regex_rule_apply, _on_regex_rule_rollback
    from adaos.services.nlu.voice_surface import exact_phrase_pattern
    from adaos.services.yjs.doc import async_get_ydoc

    golden = _fixture("gate1_existing_action_golden.json")
    ctx = get_ctx()
    webspace_id = "ws-test-gate1-golden"
    scenario_id = "test_gate1_golden_existing_action"
    candidate_id = "cand.gate1.golden"
    utterance = golden["utterance"]
    expected_intent = golden["expected_intent"]
    expected_target = golden["expected_target"]
    expected_slots = golden["expected_slots"]

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    if scenario_root.exists():
        shutil.rmtree(scenario_root)
    scenario_root.mkdir(parents=True, exist_ok=True)
    scenario_json = scenario_root / "scenario.json"
    scenario_json.write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        expected_intent: {
                            "scope": "scenario",
                            "actions": [
                                {
                                    "type": "callHost",
                                    "target": expected_target,
                                    "params": {"modal_id": "$slot.modal_id"},
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
    (scenario_root / "scenario.yaml").write_text(
        yaml.safe_dump(json.loads(scenario_json.read_text(encoding="utf-8")), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    try:
        ctx.bus.subscribe("nlp.teacher.regex_rule.apply", _on_regex_rule_apply)

        async with async_get_ydoc(webspace_id) as ydoc:
            with ydoc.begin_transaction() as txn:
                ydoc.get_map("ui").set(txn, "current_scenario", scenario_id)
                ydoc.get_map("ui").set(
                    txn,
                    "application",
                    {"modals": {expected_slots["modal_id"]: {"title": "Infra State"}}},
                )
                ydoc.get_map("data").set(
                    txn,
                    "catalog",
                    {
                        "apps": [
                            {
                                "id": "infra_state_app",
                                "title": "Infra State",
                                "launchModal": expected_slots["modal_id"],
                            }
                        ]
                    },
                )
                ydoc.get_map("data").set(
                    txn,
                    "nlu_teacher",
                    {
                        "candidates": [
                            {
                                "id": candidate_id,
                                "kind": "regex_rule",
                                "text": utterance,
                                "request_id": "req.gate1.golden",
                                "target": {"type": "scenario", "id": scenario_id},
                                "regex_rule": {
                                    "intent": expected_intent,
                                    "pattern": exact_phrase_pattern(utterance),
                                },
                                "status": "pending",
                                "preview": {"ok": True, "status": "regex_matched", "slots": dict(expected_slots)},
                                "slots": dict(expected_slots),
                                "action_candidate": {
                                    "class": "interface_action",
                                    "intent": expected_intent,
                                    "side_effect_class": "ui_navigation",
                                    "slots": dict(expected_slots),
                                },
                            }
                        ],
                        "events": [],
                    },
                )

        intent, slots, via, _raw = await _try_regex_intent(utterance, webspace_id=webspace_id)
        assert (intent, slots, via) == (None, {}, "regex")

        await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate_id})

        for _ in range(100):
            async with async_get_ydoc(webspace_id) as ydoc:
                teacher = ydoc.get_map("data").get("nlu_teacher") or {}
                candidate = next(
                    item for item in list(teacher.get("candidates") or []) if item.get("id") == candidate_id
                )
            if candidate.get("status") == "intent_matched":
                break
            await asyncio.sleep(0.01)

        assert candidate["status"] == "intent_matched", (candidate.get("validation") or {}).get("failed_checks")
        assert candidate["verification"]["status"] == "intent_matched"
        assert candidate["promotion"]["state"] == "local_learned"
        rollback_pointer = candidate["provenance"]["rollback_pointer"]
        assert rollback_pointer["target"] == {"type": "scenario", "id": scenario_id}

        saved = json.loads(scenario_json.read_text(encoding="utf-8"))
        rules = (saved.get("nlu") or {}).get("regex_rules") or []
        saved_rule = next(item for item in rules if item.get("candidate_id") == candidate_id)
        assert re.match(r"^rx\.[0-9a-f-]{36}$", saved_rule["id"])

        intent, slots, via, raw = await _try_regex_intent(utterance, webspace_id=webspace_id)
        assert intent == expected_intent
        assert slots == expected_slots
        assert via == "regex.dynamic"
        assert raw["rule_id"] == saved_rule["id"]

        dispatched: list[dict] = []

        def _capture_dispatch(ev):
            payload = getattr(ev, "payload", None) or {}
            if isinstance(payload, dict):
                dispatched.append(dict(payload))

        ctx.bus.subscribe(expected_target, _capture_dispatch)

        await dispatcher._on_nlp_intent_detected(
            {
                "webspace_id": webspace_id,
                "intent": intent,
                "slots": slots,
                "text": utterance,
                "confidence": 1.0,
                "_meta": {"webspace_id": webspace_id, "route_id": "golden_conversation"},
            }
        )

        assert dispatched
        assert dispatched[-1]["modal_id"] == expected_slots["modal_id"]
        assert dispatched[-1]["_meta"]["webspace_id"] == webspace_id

        await _on_regex_rule_rollback(
            {
                "webspace_id": webspace_id,
                "candidate_id": candidate_id,
                "rule_id": saved_rule["id"],
                "target": {"type": "scenario", "id": scenario_id},
            }
        )

        async with async_get_ydoc(webspace_id) as ydoc:
            teacher = ydoc.get_map("data").get("nlu_teacher") or {}
            candidate = next(item for item in list(teacher.get("candidates") or []) if item.get("id") == candidate_id)
            events = list(teacher.get("events") or [])

        assert candidate["status"] == "rolled_back"
        assert candidate["rollback"]["removed_owner"] == 1
        assert any(item.get("kind") == "regex_rule.rolled_back" for item in events)

        intent, slots, via, _raw = await _try_regex_intent(utterance, webspace_id=webspace_id)
        assert (intent, slots, via) == (None, {}, "regex")
    finally:
        shutil.rmtree(scenario_root, ignore_errors=True)
