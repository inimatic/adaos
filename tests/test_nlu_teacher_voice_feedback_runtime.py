import re

import pytest


@pytest.mark.anyio
async def test_voice_feedback_reports_duplicate_template_anomaly():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_voice_feedback_runtime as feedback

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-voice-feedback-duplicate"
    messages: list[dict] = []

    def _capture_chat(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            messages.append(dict(payload))

    ctx.bus.subscribe("io.out.chat.append", _capture_chat)

    await feedback._on_candidate_duplicate_suppressed(
        {
            "webspace_id": webspace_id,
            "request_id": "req.duplicate",
            "suppressed": {
                "preview": {"ok": False, "status": "source_text_miss"},
                "pattern": r"\bInfra\s*State\b",
            },
            "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
        }
    )

    assert messages
    assert "не совпал" in messages[-1]["text"]
    assert "source_text_miss" in messages[-1]["text"]


@pytest.mark.anyio
async def test_voice_feedback_reports_verified_understanding():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_voice_feedback_runtime as feedback

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-voice-feedback-acquired"
    messages: list[dict] = []

    def _capture_chat(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            messages.append(dict(payload))

    ctx.bus.subscribe("io.out.chat.append", _capture_chat)

    await feedback._on_understanding_acquired(
        {
            "webspace_id": webspace_id,
            "request_id": "req.acquired",
            "intent": "desktop.open_modal",
            "_meta": {
                "route_id": "voice_chat",
                "webspace_id": webspace_id,
                "nlu_teacher_confirmation_answer": "yes",
            },
        }
    )

    assert messages
    assert "Новое понимание установлено" in messages[-1]["text"]
    assert "desktop.open_modal" in messages[-1]["text"]


@pytest.mark.anyio
async def test_voice_feedback_reports_deferred_llm():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_voice_feedback_runtime as feedback

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-voice-feedback-deferred"
    messages: list[dict] = []

    def _capture_chat(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            messages.append(dict(payload))

    ctx.bus.subscribe("io.out.chat.append", _capture_chat)

    await feedback._on_llm_deferred(
        {
            "webspace_id": webspace_id,
            "request_id": "req.deferred",
            "reason": "root_llm_unavailable",
            "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
        }
    )

    assert messages
    assert "не смог завершить анализ" in messages[-1]["text"]
    assert "root_llm_unavailable" in messages[-1]["text"]


def test_open_modal_repair_matches_alias_inside_user_phrase():
    from adaos.services.nlu import llm_teacher_runtime as llm

    text = "Покажи состояние инфраструктуры"
    repair = llm._infer_open_modal_repair(
        text=text,
        context={
            "root_mcp": {
                "desktop_registry_lookup": {
                    "lookups": {
                        "modal_id": [
                            {
                                "value": "infrastate_modal",
                                "labels": ["Infra State", "infrastate", "инфраструктура"],
                            }
                        ]
                    }
                }
            }
        },
    )

    assert repair
    assert repair["modal_id"] == "infrastate_modal"
    assert re.search(repair["pattern"], text, re.IGNORECASE | re.UNICODE)
