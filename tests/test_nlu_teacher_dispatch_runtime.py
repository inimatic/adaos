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
async def test_action_dispatched_marks_teacher_dispatch_emitted() -> None:
    from adaos.services.nlu import teacher_dispatch_runtime as dispatch

    webspace_id = "ws-test-teacher-dispatch-emitted"
    candidate = _safe_modal_candidate("cand.teacher.dispatch.emitted")
    candidate["dispatch_status"] = "requested"
    candidate["dispatch"] = {
        "id": "tdispatch.test.emitted",
        "status": "requested",
        "path": "nlp.intent.detected",
        "intent": "desktop.open_modal",
    }
    await _seed_teacher(webspace_id, candidate)

    await dispatch._on_action_dispatched(
        {
            "webspace_id": webspace_id,
            "intent": "desktop.open_modal",
            "action_type": "callHost",
            "target": "desktop.modal.open",
            "status": "emitted",
            "action_payload": {"modal_id": "browsers_modal", "webspace_id": webspace_id},
            "request_id": "req.teacher.dispatch.teacher_dispatch",
            "text": "show browsers",
            "_meta": {
                "webspace_id": webspace_id,
                "route_id": "voice_chat",
                "nlu_teacher_dispatch": True,
                "nlu_teacher_candidate_id": candidate["id"],
                "nlu_teacher_dispatch_id": "tdispatch.test.emitted",
            },
        }
    )

    teacher = await _read_teacher(webspace_id)
    saved = list(teacher.get("candidates") or [])[0]
    assert saved["dispatch_status"] == "emitted"
    assert saved["dispatch"]["status"] == "emitted"
    assert saved["dispatch"]["outcome"]["target"] == "desktop.modal.open"
    assert saved["dispatch"]["outcome"]["action_payload"]["modal_id"] == "browsers_modal"
    assert any(item.get("kind") == "dispatch.emitted" for item in teacher.get("events") or [])


@pytest.mark.anyio
async def test_action_dispatch_failed_marks_teacher_dispatch_failed() -> None:
    from adaos.services.nlu import teacher_dispatch_runtime as dispatch

    webspace_id = "ws-test-teacher-dispatch-failed"
    candidate = _safe_modal_candidate("cand.teacher.dispatch.failed")
    candidate["dispatch_status"] = "requested"
    candidate["dispatch"] = {
        "id": "tdispatch.test.failed",
        "status": "requested",
        "path": "nlp.intent.detected",
        "intent": "desktop.open_modal",
    }
    await _seed_teacher(webspace_id, candidate)

    await dispatch._on_action_dispatch_failed(
        {
            "webspace_id": webspace_id,
            "intent": "desktop.open_modal",
            "action_type": "callHost",
            "target": "desktop.modal.open",
            "status": "failed",
            "reason": "bus_emit_failed",
            "request_id": "req.teacher.dispatch.teacher_dispatch",
            "text": "show browsers",
            "_meta": {
                "webspace_id": webspace_id,
                "route_id": "voice_chat",
                "nlu_teacher_dispatch": True,
                "nlu_teacher_candidate_id": candidate["id"],
                "nlu_teacher_dispatch_id": "tdispatch.test.failed",
            },
        }
    )

    teacher = await _read_teacher(webspace_id)
    saved = list(teacher.get("candidates") or [])[0]
    assert saved["dispatch_status"] == "failed"
    assert saved["dispatch"]["status"] == "failed"
    assert saved["dispatch"]["outcome"]["reason"] == "bus_emit_failed"
    assert any(item.get("kind") == "dispatch.failed" for item in teacher.get("events") or [])


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
