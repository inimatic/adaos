# tests/test_nlu_teacher_candidate_apply.py
import asyncio
import json
from pathlib import Path

import pytest


@pytest.mark.anyio
async def test_candidate_apply_persists_rule_and_notifies():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.nlu.regex_rules_runtime import _on_regex_rule_apply
    from adaos.services.nlu.runtime_flags import get_runtime_flags
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    scenario_id = "web_desktop"
    webspace_id = "ws-test-cand"

    # Minimal scenario that owns the intent mapping.
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

    # Wire apply handler to the local bus for this test.
    ctx.bus.subscribe("nlp.teacher.regex_rule.apply", _on_regex_rule_apply)

    notified: list[str] = []
    acquired: list[dict] = []

    def _capture_notify(ev):
        try:
            payload = getattr(ev, "payload", None) or {}
            text = payload.get("text")
            if isinstance(text, str):
                notified.append(text)
        except Exception:
            pass

    def _capture_acquired(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            acquired.append(dict(payload))

    ctx.bus.subscribe("ui.notify", _capture_notify)
    ctx.bus.subscribe("nlp.teacher.understanding.acquired", _capture_acquired)

    candidate_id = "cand.test"
    pattern = r"\btemperature\b(?:\s+in\s+(?P<city>[^?.!,;:]+))?"

    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", scenario_id)
            data_map.set(
                txn,
                "nlu_runtime",
                {
                    "flags": {
                        "regex_enabled": False,
                        "neuro_lite_enabled": True,
                        "neural_enabled": False,
                        "rasa_enabled": False,
                    }
                },
            )
            data_map.set(
                txn,
                "nlu_teacher",
                {
                    "candidates": [
                        {
                            "id": candidate_id,
                            "kind": "regex_rule",
                            "text": "show temperature in Berlin",
                            "request_id": "nlu.test",
                            "regex_rule": {"intent": "desktop.open_weather", "pattern": pattern},
                            "status": "pending",
                        }
                    ]
                },
            )

    await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate_id, "_meta": {"webspace_id": webspace_id}})

    # Give the LocalEventBus time to run async subscribers scheduled via create_task.
    for _ in range(50):
        if notified and acquired:
            break
        await asyncio.sleep(0.01)

    saved = json.loads(scenario_json.read_text(encoding="utf-8"))
    rules = (saved.get("nlu") or {}).get("regex_rules") or []
    saved_rule = next(
        r
        for r in rules
        if isinstance(r, dict) and r.get("intent") == "desktop.open_weather" and r.get("pattern") == pattern
    )
    assert saved_rule["promotion"]["state"] == "local_learned"
    assert saved_rule["promotion"]["public_export_allowed"] is False
    assert saved_rule["provenance"]["candidate_id"] == candidate_id
    assert saved_rule["provenance"]["mcp_bearer_embedded"] is False
    assert saved_rule["privacy"]["public_promotion_requires_review"] is True

    assert any("NLU Teacher acquired a new understanding" in t for t in notified)
    assert acquired
    assert acquired[-1]["intent"] == "desktop.open_weather"
    flags = await get_runtime_flags(webspace_id)
    assert flags["regex_enabled"] is True
    candidates = []
    for _ in range(100):
        async with async_get_ydoc(webspace_id) as ydoc:
            teacher = ydoc.get_map("data").get("nlu_teacher") or {}
            candidates = list((teacher or {}).get("candidates") or [])
        latest = candidates[-1] if candidates else {}
        promotion = latest.get("promotion") if isinstance(latest, dict) else {}
        if isinstance(promotion, dict) and isinstance(promotion.get("applied_artifact"), dict):
            break
        await asyncio.sleep(0.01)
    assert candidates[-1]["validation"]["status"] == "passed"
    assert candidates[-1]["promotion"]["applied_artifact"]["rule_id"] == saved_rule["id"]
    assert candidates[-1]["provenance"]["rollback_pointer"]["rule_id"] == saved_rule["id"]
    assert candidates[-1]["provenance"]["verification_result"]["status"] == "intent_matched"


@pytest.mark.anyio
async def test_candidate_apply_is_idempotent_after_verified_understanding():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-cand-apply-idempotent-verified"
    candidate_id = "cand.verified"
    candidate = {
        "id": candidate_id,
        "kind": "regex_rule",
        "status": "validation_failed",
        "text": "show NLU Teacher",
        "request_id": "req.verified",
        "regex_rule": {"intent": "desktop.open_modal", "pattern": r"\bshow NLU Teacher\b"},
        "verification": {
            "status": "intent_matched",
            "expected_intent": "desktop.open_modal",
            "probe": {"accepted": True, "intent": "desktop.open_modal"},
        },
        "promotion": {
            "state": "local_learned",
            "applied_artifact": {
                "type": "regex_rule",
                "rule_id": "rx.verified",
                "target": {"type": "scenario", "id": "web_desktop"},
            },
        },
    }

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [candidate], "events": []})

    rejected: list[dict] = []
    ctx.bus.subscribe("nlp.teacher.candidate.apply.rejected", lambda ev: rejected.append(dict((ev.payload or {}))))

    await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate_id})

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        events = list(teacher.get("events") or [])

    assert not rejected
    assert not any(item.get("kind") == "candidate.apply_rejected" for item in events)


@pytest.mark.anyio
async def test_candidate_apply_reject_suppresses_stale_failure_after_success():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.candidates_runtime import reject_candidate_apply
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-cand-stale-reject-suppressed"
    candidate_id = "cand.stale-reject"
    candidate = {
        "id": candidate_id,
        "kind": "regex_rule",
        "status": "intent_matched",
        "text": "show NLU Teacher",
        "request_id": "req.stale-reject",
        "regex_rule": {"intent": "desktop.open_modal", "pattern": r"\bshow NLU Teacher\b"},
        "verification": {
            "status": "intent_matched",
            "expected_intent": "desktop.open_modal",
            "probe": {"accepted": True, "intent": "desktop.open_modal"},
        },
        "promotion": {
            "state": "local_learned",
            "applied_artifact": {
                "type": "regex_rule",
                "rule_id": "rx.stale-reject",
                "target": {"type": "scenario", "id": "web_desktop"},
            },
        },
    }

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [candidate], "events": []})

    rejected: list[dict] = []
    messages: list[dict] = []
    ctx.bus.subscribe("nlp.teacher.candidate.apply.rejected", lambda ev: rejected.append(dict((ev.payload or {}))))
    ctx.bus.subscribe("io.out.chat.append", lambda ev: messages.append(dict((ev.payload or {}))))

    await reject_candidate_apply(
        webspace_id=webspace_id,
        candidate_id=candidate_id,
        reason="voice_confirmation_apply_timeout",
        meta={"route_id": "voice_chat", "webspace_id": webspace_id},
        request_id="req.stale-reject",
        request_text="show NLU Teacher",
        candidate_patch={"status": "apply_failed", "status_reason": "voice_confirmation_apply_timeout"},
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list(teacher.get("candidates") or [])
        events = list(teacher.get("events") or [])

    assert not rejected
    assert not messages
    assert candidates[-1]["status"] == "intent_matched"
    assert not any(item.get("kind") == "candidate.apply_rejected" for item in events)


@pytest.mark.anyio
async def test_candidate_apply_rejects_duplicate_regex_before_mutation():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    scenario_id = "test_m4_duplicate_regex"
    webspace_id = "ws-test-m4-duplicate-regex"
    pattern = r"\bopen panel\b"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    scenario_json = scenario_root / "scenario.json"
    scenario_json.write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {"demo.open_panel": {"actions": []}},
                    "regex_rules": [{"intent": "demo.open_panel", "pattern": pattern, "source": "test"}],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    rejected: list[dict] = []
    applied: list[dict] = []

    ctx.bus.subscribe("nlp.teacher.candidate.apply.rejected", lambda ev: rejected.append(dict((ev.payload or {}))))
    ctx.bus.subscribe("nlp.teacher.regex_rule.apply", lambda ev: applied.append(dict((ev.payload or {}))))

    candidate_id = "cand.m4.duplicate"
    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(
                txn,
                "nlu_teacher",
                {
                    "candidates": [
                        {
                            "id": candidate_id,
                            "kind": "regex_rule",
                            "text": "open panel",
                            "request_id": "nlu.m4.duplicate",
                            "target": {"type": "scenario", "id": scenario_id},
                            "regex_rule": {"intent": "demo.open_panel", "pattern": pattern},
                            "status": "pending",
                            "preview": {"ok": True, "status": "regex_matched", "slots": {}},
                        }
                    ]
                },
            )

    await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate_id})

    assert rejected
    assert rejected[-1]["reason"] == "m4_validation_failed"
    assert not applied
    saved = json.loads(scenario_json.read_text(encoding="utf-8"))
    assert len(saved["nlu"]["regex_rules"]) == 1
    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidate = list((teacher or {}).get("candidates") or [])[-1]
    assert candidate["status"] == "validation_failed"
    assert any(item["name"] == "template_preview" for item in candidate["validation"]["failed_checks"])


def test_m4_validation_blocks_builtin_ui_action_without_required_slot():
    from adaos.services.nlu.teacher_validation import validate_candidate_apply

    candidate = {
        "id": "cand.m4.missing-slot",
        "kind": "regex_rule",
        "text": "show media",
        "request_id": "nlu.m4.missing-slot",
        "regex_rule": {"intent": "desktop.open_modal", "pattern": r"\bshow media\b"},
        "status": "pending",
        "preview": {"ok": True, "status": "regex_matched", "slots": {}},
        "action_candidate": {
            "class": "interface_action",
            "intent": "desktop.open_modal",
            "side_effect_class": "ui_navigation",
            "slots": {},
        },
    }

    validation = validate_candidate_apply(webspace_id="ws-test-m4-missing-slot", candidate=candidate)

    assert validation["status"] == "blocked"
    assert any(item["name"] == "action_preview" and item["status"] == "blocked" for item in validation["failed_checks"])
    assert validation["action_preview"]["status"] == "blocked"


def test_m4_validation_accepts_lookup_label_for_open_modal(monkeypatch):
    from adaos.services.nlu import teacher_read_model
    from adaos.services.nlu.teacher_validation import validate_candidate_apply

    monkeypatch.setattr(
        teacher_read_model,
        "get_desktop_registry_lookup",
        lambda **kwargs: {
            "fingerprint": "fp.test.subnet-env",
            "lookups": {
                "modal_id": [
                    {
                        "value": "subnet_env_modal",
                        "labels": ["Subnet Env", "переменные окружения подсети"],
                    }
                ]
            },
        },
    )
    candidate = {
        "id": "cand.m4.modal-label",
        "kind": "regex_rule",
        "text": "Покажи переменные окружения подсети",
        "request_id": "nlu.m4.modal-label",
        "regex_rule": {
            "intent": "desktop.open_modal",
            "pattern": r"\b(?:покажи|открой)\s+(?P<modal_id>переменные\s+окружения\s+подсети)\b",
        },
        "status": "pending",
        "preview": {"ok": True, "status": "regex_matched", "slots": {"modal_id": "переменные окружения подсети"}},
    }

    validation = validate_candidate_apply(webspace_id="ws-test-m4-modal-label", candidate=candidate)

    assert validation["status"] == "passed"
    assert validation["action_preview"]["status"] == "ready"
    assert any(
        item["name"] == "lookup.modal_id" and item["status"] == "found"
        for item in validation["action_preview"]["checks"]
    )


def test_m4_validation_blocks_overbroad_non_read_only_regex():
    from adaos.services.nlu.teacher_validation import validate_candidate_apply

    candidate = {
        "id": "cand.m4.overbroad",
        "kind": "regex_rule",
        "text": "open maintenance",
        "request_id": "nlu.m4.overbroad",
        "regex_rule": {"intent": "desktop.open_modal", "pattern": r".*"},
        "status": "pending",
        "preview": {"ok": True, "status": "regex_matched", "slots": {"modal_id": "maintenance_modal"}},
        "action_candidate": {
            "class": "interface_action",
            "intent": "desktop.open_modal",
            "side_effect_class": "ui_navigation",
            "slots": {"modal_id": "maintenance_modal"},
        },
    }

    validation = validate_candidate_apply(webspace_id="ws-test-m4-overbroad", candidate=candidate)

    assert validation["status"] == "blocked"
    assert any(item["name"] == "regex_scope" and item["status"] == "overbroad_non_read_only" for item in validation["failed_checks"])


def test_m4_validation_blocks_action_intent_mismatch():
    from adaos.services.nlu.teacher_validation import validate_candidate_apply

    candidate = {
        "id": "cand.m4.mismatch",
        "kind": "regex_rule",
        "text": "show weather",
        "request_id": "nlu.m4.mismatch",
        "regex_rule": {"intent": "desktop.open_weather", "pattern": r"\bshow weather\b"},
        "status": "pending",
        "preview": {"ok": True, "status": "regex_matched", "slots": {}},
        "action_candidate": {
            "class": "interface_action",
            "intent": "desktop.open_modal",
            "side_effect_class": "ui_navigation",
            "slots": {"modal_id": "weather_modal"},
        },
    }

    validation = validate_candidate_apply(webspace_id="ws-test-m4-mismatch", candidate=candidate)

    assert validation["status"] == "blocked"
    assert any(item["name"] == "action_intent_match" and item["status"] == "mismatch" for item in validation["failed_checks"])
