import pytest


async def _seed_teacher(webspace_id: str, candidate: dict) -> None:
    from adaos.services.yjs.doc import async_get_ydoc

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [candidate], "events": []})


async def _read_teacher(webspace_id: str) -> dict:
    from adaos.services.yjs.doc import async_get_ydoc

    async with async_get_ydoc(webspace_id, read_only=True) as ydoc:
        return dict(ydoc.get_map("data").get("nlu_teacher") or {})


def _safe_modal_candidate(candidate_id: str = "cand.teacher.dispatch") -> dict:
    return {
        "id": candidate_id,
        "kind": "regex_rule",
        "status": "intent_matched",
        "text": "show browsers",
        "request_id": "req.teacher.dispatch",
        "regex_rule": {"intent": "desktop.open_modal", "pattern": r"\bshow\s+browsers\b"},
        "preview": {"ok": True, "slots": {"modal_id": "browsers_modal"}},
        "validation": {
            "ok": True,
            "status": "passed",
            "side_effect_policy": {
                "side_effect_class": "ui_navigation",
                "approval": "operator_apply_allowed",
            },
        },
    }


@pytest.mark.anyio
async def test_voice_confirmed_understanding_dispatches_normal_intent() -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_dispatch_runtime as dispatch

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-dispatch-voice"
    candidate = _safe_modal_candidate()
    await _seed_teacher(webspace_id, candidate)

    detected: list[dict] = []

    def _capture_detected(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            detected.append(dict(payload))

    ctx.bus.subscribe("nlp.intent.detected", _capture_detected)

    await dispatch._on_understanding_acquired(
        {
            "webspace_id": webspace_id,
            "candidate_id": candidate["id"],
            "request_id": "req.teacher.dispatch",
            "intent": "desktop.open_modal",
            "text": "show browsers",
            "verification": {
                "status": "intent_matched",
                "probe": {
                    "accepted": True,
                    "intent": "desktop.open_modal",
                    "slots": {"modal_id": "browsers_modal"},
                },
            },
            "_meta": {
                "route_id": "voice_chat",
                "webspace_id": webspace_id,
                "nlu_teacher_confirmation_answer": "yes",
            },
        }
    )

    assert detected
    assert detected[-1]["intent"] == "desktop.open_modal"
    assert detected[-1]["slots"] == {"modal_id": "browsers_modal"}
    assert detected[-1]["via"] == "nlu_teacher.verified"
    assert detected[-1]["_meta"]["nlu_teacher_dispatch"] is True

    teacher = await _read_teacher(webspace_id)
    saved = list(teacher.get("candidates") or [])[0]
    assert saved["dispatch_status"] == "requested"
    assert saved["dispatch"]["path"] == "nlp.intent.detected"
    assert any(item.get("kind") == "dispatch.requested" for item in teacher.get("events") or [])


@pytest.mark.anyio
async def test_operator_apply_understanding_does_not_auto_dispatch() -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_dispatch_runtime as dispatch

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-dispatch-operator"
    candidate = _safe_modal_candidate("cand.teacher.dispatch.operator")
    await _seed_teacher(webspace_id, candidate)

    detected: list[dict] = []
    ctx.bus.subscribe("nlp.intent.detected", lambda ev: detected.append(dict(getattr(ev, "payload", None) or {})))

    await dispatch._on_understanding_acquired(
        {
            "webspace_id": webspace_id,
            "candidate_id": candidate["id"],
            "request_id": "req.teacher.dispatch.operator",
            "intent": "desktop.open_modal",
            "text": "show browsers",
            "_meta": {"route_id": "api", "webspace_id": webspace_id},
        }
    )

    assert detected == []
    teacher = await _read_teacher(webspace_id)
    saved = list(teacher.get("candidates") or [])[0]
    assert "dispatch_status" not in saved


@pytest.mark.anyio
async def test_voice_confirmed_unsafe_candidate_dispatch_is_blocked() -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_dispatch_runtime as dispatch

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-dispatch-blocked"
    candidate = _safe_modal_candidate("cand.teacher.dispatch.blocked")
    candidate["validation"]["side_effect_policy"] = {
        "side_effect_class": "external_io",
        "approval": "blocked",
        "reason": "high_risk_side_effect",
    }
    await _seed_teacher(webspace_id, candidate)

    detected: list[dict] = []
    ctx.bus.subscribe("nlp.intent.detected", lambda ev: detected.append(dict(getattr(ev, "payload", None) or {})))

    await dispatch._on_understanding_acquired(
        {
            "webspace_id": webspace_id,
            "candidate_id": candidate["id"],
            "request_id": "req.teacher.dispatch.blocked",
            "intent": "desktop.open_modal",
            "text": "show browsers",
            "_meta": {
                "route_id": "voice_chat",
                "webspace_id": webspace_id,
                "nlu_teacher_confirmation_answer": "yes",
            },
        }
    )

    assert detected == []
    teacher = await _read_teacher(webspace_id)
    saved = list(teacher.get("candidates") or [])[0]
    assert saved["dispatch_status"] == "blocked"
    assert saved["dispatch"]["reason"] == "side_effect_policy"
    assert any(item.get("kind") == "dispatch.blocked" for item in teacher.get("events") or [])
