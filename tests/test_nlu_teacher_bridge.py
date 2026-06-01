import pytest
from types import SimpleNamespace


@pytest.mark.anyio
async def test_teacher_bridge_uses_root_policy_when_env_unset(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_bridge
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-root-policy"
    monkeypatch.setattr(teacher_bridge, "_ENABLED", None)
    monkeypatch.setattr(
        teacher_bridge,
        "get_ctx",
        lambda: SimpleNamespace(
            bus=ctx.bus,
            config=SimpleNamespace(root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=True))),
        ),
    )

    requests: list[dict] = []

    def _capture_request(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            requests.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.request", _capture_request)

    await teacher_bridge._on_not_obtained(
        {
            "text": "Покажи Infrascope",
            "webspace_id": webspace_id,
            "request_id": "req.root.policy",
            "via": "neuro_lite",
            "reason": "below_margin_threshold",
            "_meta": {"route_id": "voice_chat", "webspace_id": webspace_id},
        }
    )

    assert requests

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}

    assert list(teacher.get("items") or [])[-1]["text"] == "Покажи Infrascope"
    assert list(teacher.get("threads_by_request") or [])


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


@pytest.mark.anyio
async def test_teacher_bridge_builds_ui_projection_from_primary_miss(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_bridge
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-primary-miss-ui"
    monkeypatch.setattr(teacher_bridge, "_ENABLED", True)

    requests: list[dict] = []

    def _capture_request(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            requests.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.request", _capture_request)

    await teacher_bridge._on_not_obtained(
        {
            "text": "Покажи Infrascope",
            "webspace_id": webspace_id,
            "request_id": "req.infrascope.miss",
            "via": "neuro_lite",
            "reason": "below_margin_threshold",
        }
    )

    assert requests
    assert requests[-1]["request"]["classification"]["class"] == "nlu_gap"

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}

    items = list(teacher.get("items") or [])
    events = list(teacher.get("events") or [])
    threads = list(teacher.get("threads_by_request") or [])
    signals = list(teacher.get("workbench_signals") or [])

    assert items[-1]["text"] == "Покажи Infrascope"
    assert items[-1]["status"] == "pending"
    assert events[-1]["kind"] == "not_obtained"
    assert threads
    assert threads[-1]["request_id"] == "req.infrascope.miss"
    assert threads[-1]["title"] == "Покажи Infrascope"
    assert any(signal.get("id") == "teacher.queue" for signal in signals)


@pytest.mark.anyio
async def test_teacher_store_runtime_persists_primary_teacher_events(monkeypatch):
    from adaos.services.nlu import teacher_store_runtime

    scheduled: list[str] = []
    monkeypatch.setattr(teacher_store_runtime, "_schedule_persist", lambda webspace_id: scheduled.append(webspace_id))

    await teacher_store_runtime._on_teacher_request({"webspace_id": "ws-teacher-store"})  # type: ignore[attr-defined]
    await teacher_store_runtime._on_teacher_skipped({"webspace_id": "ws-teacher-store"})  # type: ignore[attr-defined]

    assert scheduled == ["ws-teacher-store", "ws-teacher-store"]
