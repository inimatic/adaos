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
async def test_voice_confirmation_yes_applies_candidate():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_confirmation_runtime as conf
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-confirmation-yes"
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

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [candidate], "events": []})

    applied: list[dict] = []

    def _capture_apply(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            applied.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.candidate.apply", _capture_apply)

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
    assert applied[-1]["candidate_id"] == candidate["id"]
    assert applied[-1]["_meta"]["nlu_teacher_confirmation_answer"] == "yes"


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
