import time

import pytest


@pytest.mark.anyio
async def test_voice_candidate_proposal_requests_confirmation():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_confirmation_runtime as conf
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-confirmation-request"
    candidate = {
        "id": "cand.confirm.request",
        "ts": 10.0,
        "kind": "regex_rule",
        "status": "pending",
        "text": "Покажи Infrascope",
        "request_id": "req.confirm.request",
        "candidate": {"name": "Regex rule for desktop.open_scenario"},
        "regex_rule": {"intent": "desktop.open_scenario", "pattern": r"\b(?P<scenario_id>Infrascope)\b"},
        "target": {"type": "scenario", "id": "homepoint"},
        "preview": {"ok": True, "slots": {"scenario_id": "Infrascope"}},
    }
    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [candidate], "events": []})

    messages: list[dict] = []

    def _capture_chat(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            messages.append(dict(payload))

    ctx.bus.subscribe("io.out.chat.append", _capture_chat)

    await conf._on_candidate_proposed(
        {
            "webspace_id": webspace_id,
            "candidate": candidate,
            "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        confirmations = list(teacher.get("pending_confirmations") or [])
        clarifications = list(teacher.get("clarification_sessions") or [])
        events = list(teacher.get("events") or [])

    assert confirmations
    assert confirmations[-1]["status"] == "awaiting_user"
    assert confirmations[-1]["candidate_id"] == candidate["id"]
    assert confirmations[-1]["question"] == "Открыть Infrascope?"
    assert clarifications
    assert clarifications[-1]["status"] == "awaiting_user"
    assert clarifications[-1]["kind"] == "voice_confirmation"
    assert clarifications[-1]["uncertainty_kind"] == "candidate_confirmation"
    assert clarifications[-1]["candidate_id"] == candidate["id"]
    assert clarifications[-1]["allowed_answers"][0]["effect"] == "apply_candidate"
    assert events[-1]["kind"] == "confirmation.requested"
    assert messages
    assert "Открыть Infrascope?" in messages[-1]["text"]


@pytest.mark.anyio
async def test_voice_candidate_proposal_skips_recent_accepted_duplicate():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_confirmation_runtime as conf
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-confirmation-accepted-duplicate"
    request_text = "show infrastructure state"
    candidate = {
        "id": "cand.confirm.duplicate.new",
        "ts": time.time(),
        "kind": "regex_rule",
        "status": "pending",
        "text": request_text,
        "request_id": "req.confirm.duplicate.new",
        "regex_rule": {"intent": "desktop.open_modal", "pattern": r"\bshow infrastructure state\b"},
        "preview": {"ok": True, "slots": {"modal_id": "infrastate_modal"}},
    }
    accepted_confirmation = {
        "id": "confirm.duplicate.old",
        "ts": time.time(),
        "status": "accepted",
        "candidate_id": "cand.confirm.duplicate.old",
        "request_id": "req.confirm.duplicate.old",
        "request_text": request_text,
        "question": "Open infrastructure state?",
        "answer": "yes",
        "answered_at": time.time(),
        "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
    }

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(
                txn,
                "nlu_teacher",
                {
                    "pending_confirmations": [accepted_confirmation],
                    "candidates": [candidate],
                    "events": [],
                },
            )

    messages: list[dict] = []

    def _capture_chat(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            messages.append(dict(payload))

    ctx.bus.subscribe("io.out.chat.append", _capture_chat)

    await conf._on_candidate_proposed(
        {
            "webspace_id": webspace_id,
            "candidate": candidate,
            "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        confirmations = list(teacher.get("pending_confirmations") or [])
        events = list(teacher.get("events") or [])

    assert confirmations == [accepted_confirmation]
    assert not events
    assert not messages


@pytest.mark.anyio
async def test_voice_existing_candidate_reasks_confirmation():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_confirmation_runtime as conf
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-existing-confirmation"
    candidate = {
        "id": "cand.confirm.existing",
        "ts": 10.0,
        "updated_at": 11.0,
        "kind": "regex_rule",
        "status": "validation_failed",
        "text": "show subnet env",
        "request_id": "req.confirm.original",
        "regex_rule": {"intent": "desktop.open_modal", "pattern": r"\bshow\s+(?P<modal_id>subnet\s+env)\b"},
        "target": {"type": "skill", "id": "subnet_env"},
        "preview": {"ok": True, "slots": {"modal_id": "subnet env"}},
    }

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [candidate], "events": []})

    messages: list[dict] = []

    def _capture_chat(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            messages.append(dict(payload))

    ctx.bus.subscribe("io.out.chat.append", _capture_chat)

    handled = await conf.request_existing_candidate_confirmation(
        webspace_id,
        "show subnet env",
        request_id="req.confirm.repeat",
        meta={"route_id": "voice_chat", "webspace_id": webspace_id},
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        confirmations = list(teacher.get("pending_confirmations") or [])
        clarifications = list(teacher.get("clarification_sessions") or [])
        events = list(teacher.get("events") or [])

    assert handled is True
    assert confirmations[-1]["status"] == "awaiting_user"
    assert confirmations[-1]["candidate_id"] == candidate["id"]
    assert confirmations[-1]["request_id"] == "req.confirm.repeat"
    assert confirmations[-1]["candidate_request_id"] == "req.confirm.original"
    assert confirmations[-1]["reused_candidate"] is True
    assert clarifications[-1]["kind"] == "voice_confirmation"
    assert events[-1]["kind"] == "confirmation.requested"
    assert messages


@pytest.mark.anyio
async def test_voice_existing_apply_requested_candidate_is_not_reconfirmed():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_confirmation_runtime as conf
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-existing-apply-requested"
    candidate = {
        "id": "cand.confirm.apply-requested",
        "ts": time.time(),
        "updated_at": time.time(),
        "apply_requested_at": time.time(),
        "kind": "regex_rule",
        "status": "apply_requested",
        "text": "show infrastructure state",
        "request_id": "req.confirm.apply-requested",
        "regex_rule": {"intent": "desktop.open_modal", "pattern": r"\bshow infrastructure state\b"},
        "preview": {"ok": True, "slots": {"modal_id": "infrastate_modal"}},
    }

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [candidate], "events": []})

    messages: list[dict] = []

    def _capture_chat(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            messages.append(dict(payload))

    ctx.bus.subscribe("io.out.chat.append", _capture_chat)

    handled = await conf.request_existing_candidate_confirmation(
        webspace_id,
        "show infrastructure state",
        request_id="req.confirm.apply-repeat",
        meta={"route_id": "voice_chat", "webspace_id": webspace_id},
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        confirmations = list(teacher.get("pending_confirmations") or [])
        events = list(teacher.get("events") or [])
        candidates = list(teacher.get("candidates") or [])

    assert handled is True
    assert confirmations == []
    assert events == []
    assert candidates[-1]["status"] == "apply_requested"
    assert messages
    assert "NLU" in messages[-1]["text"]


@pytest.mark.anyio
async def test_voice_confirmation_yes_applies_candidate():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_confirmation_runtime as conf
    from adaos.services.nlu.probe import probe_phrase
    from adaos.services.yjs.doc import async_get_ydoc
    import asyncio
    import json
    from pathlib import Path

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-confirmation-yes"
    scenario_id = "test_teacher_confirmation_yes_scenario"
    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    scenario_json = scenario_root / "scenario.json"
    scenario_json.write_text(
        json.dumps(
            {"id": scenario_id, "version": "0.0.1", "nlu": {"intents": {"demo.open_panel": {"actions": []}}}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    candidate = {
        "id": "cand.confirm.yes",
        "ts": 10.0,
        "kind": "regex_rule",
        "status": "pending",
        "text": "Покажи Infrascope",
        "request_id": "req.confirm.yes",
        "regex_rule": {"intent": "desktop.open_scenario", "pattern": r"\b(?P<scenario_id>Infrascope)\b"},
        "target": {"type": "scenario", "id": "homepoint"},
        "preview": {"ok": True, "slots": {"scenario_id": "Infrascope"}},
    }
    candidate.update(
        {
            "text": "open raw panel",
            "regex_rule": {"intent": "demo.open_panel", "pattern": r"\bopen\s+(?P<modal_id>raw\s+panel)\b"},
            "target": {"type": "scenario", "id": scenario_id},
            "preview": {"ok": True, "slots": {"modal_id": "raw panel"}},
            "normalization": {"llm_proposal_repair": {"modal_id": "canonical_panel"}},
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("ui").set(txn, "current_scenario", scenario_id)
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [candidate], "events": []})

    applied: list[dict] = []

    def _capture_apply(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            applied.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.regex_rule.applied", _capture_apply)

    await conf._on_candidate_proposed(
        {
            "webspace_id": webspace_id,
            "candidate": candidate,
            "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
        }
    )
    await conf._on_voice_chat_user(
        {
            "webspace_id": webspace_id,
            "text": "да",
            "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
        }
    )

    acquired: list[dict] = []

    def _capture_acquired(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            acquired.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.understanding.acquired", _capture_acquired)

    for _ in range(100):
        if applied:
            break
        await asyncio.sleep(0.01)

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        confirmations = list(teacher.get("pending_confirmations") or [])
        clarifications = list(teacher.get("clarification_sessions") or [])
        events = list(teacher.get("events") or [])

    assert confirmations[-1]["status"] == "accepted"
    assert clarifications[-1]["status"] == "accepted"
    assert clarifications[-1]["answer"] == "да"
    assert any(item.get("kind") == "confirmation.accepted" for item in events)
    assert applied

    saved = json.loads(scenario_json.read_text(encoding="utf-8"))
    rules = (saved.get("nlu") or {}).get("regex_rules") or []
    saved_rule = next(item for item in rules if item.get("candidate_id") == candidate["id"])
    assert saved_rule["slots"] == {"modal_id": "canonical_panel"}

    probe = await probe_phrase("open raw panel", webspace_id=webspace_id, use_rasa=False, emit_trace=False)
    assert probe["accepted"] is True
    assert probe["intent"] == "demo.open_panel"
    assert probe["slots"]["modal_id"] == "canonical_panel"


@pytest.mark.anyio
async def test_voice_confirmation_suppresses_short_stt_tail():
    from adaos.services.nlu import teacher_confirmation_runtime as conf
    from adaos.services.yjs.doc import async_get_ydoc

    webspace_id = "ws-test-teacher-confirmation-stt-tail"
    confirmation = {
        "id": "confirm.stt.tail",
        "ts": time.time(),
        "status": "awaiting_user",
        "candidate_id": "cand.stt.tail",
        "request_id": "req.stt.tail",
        "request_text": "Покажи переменные окружения подсети",
        "question": "Открыть переменные окружения подсети?",
        "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
    }

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"pending_confirmations": [confirmation]})

    assert await conf.should_suppress_voice_text_for_confirmation(webspace_id, "от сети")
    assert not await conf.should_suppress_voice_text_for_confirmation(webspace_id, "да")
    assert not await conf.should_suppress_voice_text_for_confirmation(webspace_id, "покажи браузеры")


@pytest.mark.anyio
async def test_voice_confirmation_answer_consumed_after_teacher_accepts():
    from adaos.services.nlu import teacher_confirmation_runtime as conf
    from adaos.services.yjs.doc import async_get_ydoc

    webspace_id = "ws-test-teacher-confirmation-answer-consumed"
    confirmation = {
        "id": "confirm.answer.consumed",
        "ts": time.time(),
        "status": "awaiting_user",
        "candidate_id": "cand.answer.consumed",
        "request_id": "req.answer.consumed",
        "request_text": "show infrastructure state",
        "question": "Open infrastructure state?",
        "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
    }

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"pending_confirmations": [confirmation], "events": []})

    assert await conf.should_consume_voice_confirmation_answer(webspace_id, "yes")

    await conf._on_voice_chat_user(
        {
            "webspace_id": webspace_id,
            "text": "yes",
            "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        confirmations = list(teacher.get("pending_confirmations") or [])

    assert confirmations[-1]["status"] == "accepted"
    assert await conf.should_consume_voice_confirmation_answer(webspace_id, "yes")


@pytest.mark.anyio
async def test_voice_confirmation_apply_timeout_marks_candidate_failed(monkeypatch):
    import asyncio

    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import candidates_runtime
    from adaos.services.nlu import teacher_confirmation_runtime as conf
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-confirmation-apply-timeout"
    candidate = {
        "id": "cand.confirm.apply-timeout",
        "ts": time.time(),
        "kind": "regex_rule",
        "status": "pending",
        "text": "show infrastructure state",
        "request_id": "req.confirm.apply-timeout",
        "regex_rule": {"intent": "desktop.open_modal", "pattern": r"\bshow infrastructure state\b"},
        "preview": {"ok": True, "slots": {"modal_id": "infrastate_modal"}},
    }
    confirmation = {
        "id": "confirm.apply-timeout",
        "ts": time.time(),
        "status": "awaiting_user",
        "candidate_id": candidate["id"],
        "request_id": candidate["request_id"],
        "request_text": candidate["text"],
        "question": "Open infrastructure state?",
        "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
    }

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(
                txn,
                "nlu_teacher",
                {"candidates": [candidate], "pending_confirmations": [confirmation], "events": []},
            )

    async def _slow_apply(_evt):
        await asyncio.sleep(1.0)

    monkeypatch.setenv("ADAOS_NLU_TEACHER_CONFIRM_APPLY_TIMEOUT_S", "0.01")
    monkeypatch.setattr(candidates_runtime, "_on_candidate_apply", _slow_apply)

    messages: list[dict] = []

    def _capture_chat(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            messages.append(dict(payload))

    ctx.bus.subscribe("io.out.chat.append", _capture_chat)

    await conf._on_voice_chat_user(
        {
            "webspace_id": webspace_id,
            "text": "yes",
            "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        confirmations = list(teacher.get("pending_confirmations") or [])
        events = list(teacher.get("events") or [])
        candidates = list(teacher.get("candidates") or [])

    assert confirmations[-1]["status"] == "accepted"
    assert candidates[-1]["status"] == "apply_failed"
    assert candidates[-1]["status_reason"] == "voice_confirmation_apply_timeout"
    assert candidates[-1]["validation"]["failed_checks"][0]["reason"] == "voice_confirmation_apply_timeout"
    assert any(item.get("kind") == "confirmation.accepted" for item in events)
    assert any(item.get("kind") == "candidate.apply_rejected" for item in events)
    assert messages


@pytest.mark.anyio
async def test_voice_confirmation_no_retries_once_with_rejected_candidate_context():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_confirmation_runtime as conf
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-confirmation-no"
    candidate = {
        "id": "cand.confirm.no",
        "ts": 10.0,
        "kind": "regex_rule",
        "status": "pending",
        "text": "Покажи Infrascope",
        "request_id": "req.confirm.no",
        "regex_rule": {"intent": "desktop.open_scenario", "pattern": r"\b(?P<scenario_id>Infrascope)\b"},
        "target": {"type": "scenario", "id": "homepoint"},
        "preview": {"ok": True, "slots": {"scenario_id": "Infrascope"}},
    }

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [candidate], "events": []})

    retries: list[dict] = []

    def _capture_retry(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            retries.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.request", _capture_retry)

    await conf._on_candidate_proposed(
        {
            "webspace_id": webspace_id,
            "candidate": candidate,
            "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
        }
    )
    await conf._on_voice_chat_user(
        {
            "webspace_id": webspace_id,
            "text": "нет, нужно открыть Infra State",
            "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        confirmations = list(teacher.get("pending_confirmations") or [])
        clarifications = list(teacher.get("clarification_sessions") or [])
        candidates = list(teacher.get("candidates") or [])
        events = list(teacher.get("events") or [])

    assert confirmations[-1]["status"] == "rejected"
    assert clarifications[-1]["status"] == "rejected"
    assert clarifications[-1]["rejected_candidates"] == [candidate["id"]]
    assert candidates[-1]["status"] == "rejected"
    assert any(item.get("kind") == "confirmation.rejected" for item in events)
    assert retries
    retry = retries[-1]["request"]
    assert retry["text"] == "нет, нужно открыть Infra State"
    assert retry["_meta"]["nlu_teacher_confirmation_attempt"] == 1
    assert retry["_meta"]["rejected_candidate_id"] == candidate["id"]


@pytest.mark.anyio
async def test_voice_clarification_short_answer_resolves_session():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_confirmation_runtime as conf
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-clarification-answer"
    session = {
        "id": "clarify.short.answer",
        "ts": time.time(),
        "status": "awaiting_user",
        "kind": "llm_clarification",
        "uncertainty_kind": "llm_ambiguity",
        "request_id": "req.clarify.short",
        "request_text": "show media",
        "question": "Open Media Indexer or Media Server?",
        "allowed_answers": [
            {
                "id": "media_indexer",
                "label": "Media Indexer",
                "effect": "answer",
                "action_candidate": {
                    "intent": "desktop.open_modal",
                    "slots": {"modal_id": "media_indexer_modal"},
                },
            },
            {
                "id": "media_server",
                "label": "Media Server",
                "effect": "answer",
                "action_candidate": {
                    "intent": "desktop.open_modal",
                    "slots": {"modal_id": "mediaserver_modal"},
                },
            },
        ],
        "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
    }

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"clarification_sessions": [session], "events": []})

    answered: list[dict] = []

    def _capture_answered(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            answered.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.clarification.answered", _capture_answered)

    assert conf.is_confirmation_answer("first")
    assert await conf.has_recent_voice_confirmation(webspace_id, within_s=3600)

    await conf._on_voice_chat_user(
        {
            "webspace_id": webspace_id,
            "text": "first",
            "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        clarifications = list(teacher.get("clarification_sessions") or [])
        events = list(teacher.get("events") or [])

    assert clarifications[-1]["status"] == "answered"
    assert clarifications[-1]["answer"] == "first"
    assert clarifications[-1]["answer_kind"] == "first"
    assert clarifications[-1]["selected_answer"]["id"] == "media_indexer"
    assert any(item.get("kind") == "clarification.answered" for item in events)
    assert answered
    assert answered[-1]["selected_answer"]["id"] == "media_indexer"
