import pytest


@pytest.mark.anyio
async def test_teacher_bridge_skips_provider_outage_before_llm(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_bridge
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-provider-outage"
    monkeypatch.setattr(teacher_bridge, "_ENABLED", True)

    requests: list[dict] = []
    skipped: list[dict] = []

    def _capture_request(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            requests.append(dict(payload))

    def _capture_skipped(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            skipped.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.request", _capture_request)
    ctx.bus.subscribe("nlp.teacher.skipped", _capture_skipped)

    await teacher_bridge._on_not_obtained(
        {
            "text": "open weather",
            "webspace_id": webspace_id,
            "request_id": "req.provider.outage",
            "via": "rasa",
            "reason": "rasa_timeout",
        }
    )

    assert not requests
    assert skipped
    assert skipped[-1]["classification"]["class"] == "provider_state"
    assert skipped[-1]["classification"]["teachable"] is False

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        items = list((teacher or {}).get("items") or [])
        events = list((teacher or {}).get("events") or [])

    assert items[-1]["status"] == "skipped"
    assert items[-1]["classification"]["skip_reason"] == "provider_or_stage_unavailable"
    assert events[-1]["kind"] == "not_obtained.skipped"


@pytest.mark.anyio
async def test_teacher_bridge_allows_low_confidence_as_nlu_gap(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_bridge
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-low-confidence"
    monkeypatch.setattr(teacher_bridge, "_ENABLED", True)

    requests: list[dict] = []
    skipped: list[dict] = []

    def _capture_request(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            requests.append(dict(payload))

    def _capture_skipped(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            skipped.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.request", _capture_request)
    ctx.bus.subscribe("nlp.teacher.skipped", _capture_skipped)

    await teacher_bridge._on_not_obtained(
        {
            "text": "bring up the operations console",
            "webspace_id": webspace_id,
            "request_id": "req.low.confidence",
            "via": "rasa",
            "reason": "rasa_low_confidence",
        }
    )

    assert requests
    assert not skipped
    assert requests[-1]["request"]["classification"]["class"] == "nlu_gap"
    assert requests[-1]["request"]["classification"]["teachable"] is True

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        items = list((teacher or {}).get("items") or [])
        events = list((teacher or {}).get("events") or [])

    assert items[-1]["status"] == "pending"
    assert events[-1]["kind"] == "not_obtained"
