import json

import pytest
from types import SimpleNamespace


@pytest.mark.anyio
async def test_teacher_bridge_skips_explicitly_suppressed_voice_fallback(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_bridge

    ctx = get_ctx()
    monkeypatch.setattr(teacher_bridge, "_ENABLED", True)

    requests: list[dict] = []
    ctx.bus.subscribe("nlp.teacher.request", lambda ev: requests.append(dict(getattr(ev, "payload", None) or {})))

    await teacher_bridge._on_not_obtained(
        {
            "text": "weather in Berlin",
            "webspace_id": "desktop",
            "request_id": "req.voice.suppressed",
            "via": "neuro_lite",
            "reason": "below_margin_threshold",
            "_meta": {"route_id": "voice_chat", "webspace_id": "desktop", "suppress_teacher_bridge": True},
        }
    )

    assert requests == []


@pytest.mark.anyio
async def test_teacher_bridge_skips_teacher_dispatch_miss(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_bridge

    ctx = get_ctx()
    monkeypatch.setattr(teacher_bridge, "_ENABLED", True)

    requests: list[dict] = []
    ctx.bus.subscribe("nlp.teacher.request", lambda ev: requests.append(dict(getattr(ev, "payload", None) or {})))

    await teacher_bridge._on_not_obtained(
        {
            "text": "Напишем заметку",
            "webspace_id": "desktop",
            "request_id": "req.teacher.test.no_mapping",
            "via": "nlu_teacher.test",
            "reason": "no_intent_mapping",
            "_meta": {
                "route_id": "api",
                "webspace_id": "desktop",
                "nlu_teacher_dispatch": True,
                "nlu_teacher_test": True,
                "nlu_teacher_candidate_id": "cand.teacher.test.no_mapping",
            },
        }
    )

    assert requests == []


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
async def test_teacher_bridge_keeps_transient_provider_failure_teachable_when_other_engines_miss(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_bridge
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-transient-provider-warning"
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
            "text": "show infrastructure risks",
            "webspace_id": webspace_id,
            "request_id": "req.transient.provider.warning",
            "via": "rasa",
            "reason": "rasa_timeout",
            "_meta": {
                "webspace_id": webspace_id,
                "route_id": "voice_chat",
                "neuro_lite_fallback": True,
                "neuro_lite_fallback_reason": "below_margin_threshold",
                "nlu_pipeline": {
                    "active_stages": {
                        "regex": True,
                        "neuro_lite": True,
                        "rasa": True,
                    }
                },
            },
        }
    )

    assert requests
    assert not skipped
    classification = requests[-1]["request"]["classification"]
    assert classification["class"] == "nlu_gap"
    assert classification["teachable"] is True
    assert classification["provider_issue"]["reason"] == "rasa_timeout"
    assert classification["provider_issue"]["fallbacks"]["neuro_lite_fallback_reason"] == "below_margin_threshold"

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        items = list((teacher or {}).get("items") or [])
        events = list((teacher or {}).get("events") or [])

    assert items[-1]["status"] == "pending"
    assert items[-1]["classification"]["provider_issue"]["pipeline"]["active_stages"]["rasa"] is True
    assert events[-1]["kind"] == "not_obtained"


@pytest.mark.anyio
async def test_teacher_bridge_allows_low_confidence_as_nlu_gap(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services import conversation_links, conversation_store
    from adaos.services.nlu import teacher_bridge, teacher_events
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
    assert items[-1]["conversation_ref"]["conversation_id"] == conversation_links.teacher_conversation_id(webspace_id)
    projection = conversation_store.list_projection(
        conversation_links.teacher_conversation_id(webspace_id),
        thread_id=items[-1]["conversation_ref"]["thread_id"],
        limit=5,
    )
    source_messages = [item for item in projection["messages"] if item.get("id") == items[-1]["source_message_id"]]
    assert source_messages
    assert source_messages[-1]["text"] == "bring up the operations console"
    assert source_messages[-1]["thread_id"] == items[-1]["conversation_ref"]["thread_id"]
    assert events[-1]["kind"] == "not_obtained"

    rebuilt = teacher_events.rebuild_teacher_projection_from_ledger(webspace_id)
    rebuilt_items = [item for item in rebuilt.get("items") or [] if item.get("text") == "bring up the operations console"]
    assert len(rebuilt_items) == 1
    assert rebuilt_items[0]["source_message_id"] == items[-1]["source_message_id"]
    assert any(event.get("kind") == "not_obtained" for event in rebuilt.get("events") or [])

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {})

    projected = await teacher_events.write_teacher_projection_from_ledger(webspace_id)
    assert projected["projection_source"]["kind"] == "conversation_ledger"
    assert projected["items"][-1]["text"] == "bring up the operations console"

    async with async_get_ydoc(webspace_id) as ydoc:
        restored = ydoc.get_map("data").get("nlu_teacher") or {}
    assert restored["items"][-1]["text"] == "bring up the operations console"
    assert restored["threads_by_request"]


@pytest.mark.anyio
async def test_teacher_bridge_uses_last_active_ladder_stage_for_display_reason(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_bridge
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-teacher-ladder-status"
    monkeypatch.setattr(teacher_bridge, "_ENABLED", True)

    requests: list[dict] = []
    ctx.bus.subscribe("nlp.teacher.request", lambda ev: requests.append(dict(getattr(ev, "payload", None) or {})))

    await teacher_bridge._on_not_obtained(
        {
            "text": "open teacher app",
            "webspace_id": webspace_id,
            "request_id": "req.ladder.status",
            "via": "rasa",
            "reason": "rasa_low_confidence",
            "_meta": {
                "webspace_id": webspace_id,
                "route_id": "voice_chat",
                "nlu_pipeline": {
                    "active_stages": {
                        "regex": True,
                        "neuro_lite": True,
                        "neural": True,
                        "rasa": False,
                    }
                },
            },
        }
    )

    assert requests
    assert requests[-1]["request"]["reason"] == "neural_not_obtained"
    assert requests[-1]["request"]["raw_reason"] == "rasa_low_confidence"
    assert requests[-1]["request"]["via"] == "neural"

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        items = list((teacher or {}).get("items") or [])

    assert items[-1]["effective_status"]["last_active_stage"] == "neural"
    assert items[-1]["classification"]["reason"] == "neural_not_obtained"


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


@pytest.mark.anyio
async def test_teacher_store_runtime_removes_legacy_event_projection_on_ready(monkeypatch):
    from adaos.services.nlu import teacher_store_runtime

    writes: list[dict] = []
    saves: list[dict] = []

    async def _read(_webspace_id: str) -> dict:
        return {
            "events": [],
            "events_by_candidate": {"candidate-1": [{"id": "event-1"}]},
            "threads_by_request": {},
        }

    async def _write(_webspace_id: str, teacher: dict) -> None:
        writes.append(teacher)

    monkeypatch.setattr(teacher_store_runtime, "load_teacher_state", lambda **_kwargs: {})
    monkeypatch.setattr(teacher_store_runtime, "_read_teacher_from_ydoc", _read)
    monkeypatch.setattr(teacher_store_runtime, "_write_teacher_to_ydoc", _write)
    monkeypatch.setattr(
        teacher_store_runtime,
        "save_teacher_state",
        lambda **kwargs: saves.append(kwargs["teacher"]),
    )

    await teacher_store_runtime._on_sys_ready({"webspace_id": "ws-teacher-store"})  # type: ignore[attr-defined]

    assert len(writes) == 1
    assert len(saves) == 1
    assert "events_by_candidate" not in writes[0]
    assert "events_by_candidate" not in saves[0]


def test_teacher_durable_projection_is_bounded_and_keeps_newest_threads():
    from adaos.services.nlu import teacher_events

    teacher = {
        "events": [
            {
                "id": f"evt-{index}",
                "ts": float(index),
                "request_id": f"req-{index:03d}",
                "request_text": f"request {index}",
                "kind": "candidate.proposed",
                "raw": {"id": f"cand-{index}", "payload": "x" * 2000},
            }
            for index in range(140)
        ],
        "llm_logs": [
            {
                "id": f"log-{index}",
                "ts": float(index),
                "request_id": f"req-{index:03d}",
                "response": {"raw": "y" * 4000},
            }
            for index in range(140)
        ],
        "candidates": [
            {
                "id": f"cand-{index}",
                "ts": float(index),
                "request_id": f"req-{index:03d}",
                "kind": "skill",
                "status": "pending",
                "candidate": {"name": f"candidate {index}"},
            }
            for index in range(140)
        ],
        "projection_window": {
            "ledger_backfill": {
                "schema": "adaos.nlu_teacher.ledger_backfill.v1",
                "completed": True,
            }
        },
    }

    teacher_events.rebuild_teacher_derived_views(teacher)
    limits = teacher_events.teacher_projection_limits()

    assert len(teacher["events"]) == limits["events"]
    assert len(teacher["llm_logs"]) == limits["llm_logs"]
    assert len(teacher["candidates"]) == limits["candidates"]
    assert len(teacher["threads_by_request"]) == limits["threads_by_request"]
    assert len(teacher["threads_by_candidate"]) == limits["threads_by_candidate"]
    assert teacher["threads_by_request"][-1]["request_id"] == "req-139"
    assert all(len(str(item.get("details") or "")) <= 2000 for item in teacher["threads_by_candidate"])
    assert teacher["projection_window"]["source_of_truth"] == "conversation_ledger"
    assert teacher["projection_window"]["truncated"]["events"] is True
    assert teacher["projection_window"]["truncated"]["llm_logs"] is True
    assert teacher["projection_window"]["ledger_backfill"]["completed"] is True
    assert teacher["projection_window"]["byte_budget"]["over_budget"] is False
    assert (
        len(json.dumps(teacher, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        <= limits["projection_bytes"]
    )


def test_teacher_history_backfill_is_idempotent_and_preserves_llm_logs():
    from adaos.services import conversation_links, conversation_store
    from adaos.services.nlu import teacher_events

    webspace_id = "ws-teacher-ledger-backfill"
    event = {
        "id": "evt-backfill-1",
        "ts": 10.0,
        "webspace_id": webspace_id,
        "request_id": "req-backfill-1",
        "request_text": "backfill request",
        "kind": "not_obtained",
        "title": "Intent not obtained",
        "raw": {"id": "item-backfill-1", "request_id": "req-backfill-1", "text": "backfill request"},
    }
    llm_log = {
        "id": "log-backfill-1",
        "ts": 11.0,
        "request_id": "req-backfill-1",
        "status": "complete",
        "response": {"decision": "propose"},
    }
    teacher = {"events": [event], "llm_logs": [llm_log]}

    marker = teacher_events.backfill_teacher_history_to_ledger(webspace_id, teacher)
    teacher["projection_window"] = {"ledger_backfill": marker}
    repeated = teacher_events.backfill_teacher_history_to_ledger(webspace_id, teacher)

    conversation_id = conversation_links.teacher_conversation_id(webspace_id)
    messages = conversation_store.list_messages(conversation_id)
    page = teacher_events.read_teacher_history_page(webspace_id, request_id="req-backfill-1", limit=32)
    assert marker["completed"] is True
    assert repeated == marker
    assert len(messages) == 2
    assert [item["id"] for item in page["events"]] == ["evt-backfill-1"]
    assert [item["id"] for item in page["llm_logs"]] == ["log-backfill-1"]
    assert page["total_message_count"] == 2


def test_teacher_history_backfill_batches_a_full_legacy_window():
    from adaos.services import conversation_links, conversation_store
    from adaos.services.nlu import teacher_events

    webspace_id = "ws-teacher-ledger-batch-window"
    teacher = {
        "events": [
            {
                "id": f"evt-batch-{index}",
                "ts": float(index),
                "request_id": f"req-batch-{index}",
                "request_text": f"batch request {index}",
                "kind": "not_obtained",
                "raw": {"id": f"item-batch-{index}"},
            }
            for index in range(96)
        ],
        "llm_logs": [
            {
                "id": f"log-batch-{index}",
                "ts": float(index),
                "request_id": f"req-batch-{index}",
                "status": "complete",
            }
            for index in range(48)
        ],
    }

    marker = teacher_events.backfill_teacher_history_to_ledger(webspace_id, teacher)
    messages = conversation_store.list_messages(conversation_links.teacher_conversation_id(webspace_id), limit=500)

    assert marker["records_ensured"] == 144
    assert marker["elapsed_ms"] < 5000.0
    assert len(messages) == 144


@pytest.mark.anyio
async def test_live_llm_log_create_and_update_are_mirrored_to_ledger(monkeypatch):
    from adaos.services.nlu import llm_teacher_runtime
    from adaos.services.yjs.doc import async_get_ydoc

    webspace_id = "ws-teacher-live-llm-ledger"
    mirrored: list[dict] = []

    def _mirror(_webspace_id: str, log: dict) -> dict:
        assert _webspace_id == webspace_id
        mirrored.append(dict(log))
        return {"id": f"message-{len(mirrored)}"}

    monkeypatch.setattr(llm_teacher_runtime, "append_llm_log_to_ledger", _mirror)
    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"events": [], "llm_logs": []})

    await llm_teacher_runtime._append_llm_log(  # type: ignore[attr-defined]
        webspace_id,
        {"id": "log-live-1", "request_id": "req-live-1", "status": "pending"},
    )
    await llm_teacher_runtime._patch_llm_log(  # type: ignore[attr-defined]
        webspace_id,
        log_id="log-live-1",
        patch={"status": "complete", "response": {"decision": "propose"}},
    )

    assert [item["status"] for item in mirrored] == ["pending", "complete"]
    assert mirrored[-1]["response"] == {"decision": "propose"}


def test_teacher_history_page_reads_canonical_ledger_on_demand():
    from adaos.services import conversation_links
    from adaos.services.nlu import teacher_events

    webspace_id = "ws-teacher-history-page"
    request_id = "req-history"
    for index in range(3):
        event = {
            "id": f"evt-history-{index}",
            "ts": float(index + 1),
            "webspace_id": webspace_id,
            "request_id": request_id,
            "request_text": f"history request {index}",
            "kind": "not_obtained",
            "title": "Intent not obtained",
            "raw": {"id": f"item-{index}", "request_id": request_id, "text": f"history request {index}"},
        }
        stored = conversation_links.append_teacher_event_message(
            webspace_id=webspace_id,
            text=event["request_text"],
            request_id=request_id,
            kind="event.not_obtained",
            payload={"event": event},
        )
        assert stored is not None

    page = teacher_events.read_teacher_history_page(webspace_id, request_id=request_id, limit=2)

    assert page["source"] == "conversation_ledger"
    assert page["thread_id"] == conversation_links.teacher_thread_id(
        webspace_id=webspace_id,
        request_id=request_id,
    )
    assert len(page["messages"]) == 2
    assert len(page["events"]) == 2
    assert page["total_message_count"] == 3
    assert page["has_more_before"] is True
    assert page["before_cursor"]
    assert page["threads_by_request"][0]["details_truncated"] is False


@pytest.mark.anyio
async def test_teacher_store_runtime_compacts_oversized_projection_without_saved_state(monkeypatch):
    from adaos.services.nlu import teacher_events, teacher_store_runtime

    limits = teacher_events.teacher_projection_limits()
    writes: list[dict] = []

    async def _read(_webspace_id: str) -> dict:
        return {
            "events": [
                {"id": f"evt-{index}", "ts": float(index), "request_id": f"req-{index}"}
                for index in range(limits["events"] + 10)
            ],
            "llm_logs": [],
        }

    async def _write(_webspace_id: str, teacher: dict) -> None:
        writes.append(teacher)

    monkeypatch.setattr(teacher_store_runtime, "load_teacher_state", lambda **_kwargs: {})
    monkeypatch.setattr(teacher_store_runtime, "_read_teacher_from_ydoc", _read)
    monkeypatch.setattr(teacher_store_runtime, "_write_teacher_to_ydoc", _write)
    monkeypatch.setattr(teacher_store_runtime, "save_teacher_state", lambda **_kwargs: None)

    await teacher_store_runtime._on_sys_ready({"webspace_id": "ws-teacher-compact"})  # type: ignore[attr-defined]

    assert len(writes) == 1
    assert len(writes[0]["events"]) == limits["events"]


@pytest.mark.anyio
async def test_teacher_store_runtime_backfills_before_projection_is_bounded(monkeypatch):
    from adaos.services.nlu import teacher_events, teacher_store_runtime

    webspace_id = "ws-teacher-backfill-before-bound"
    limits = teacher_events.teacher_projection_limits()
    source_count = limits["events"] + 9
    seen_counts: list[int] = []
    writes: list[dict] = []

    async def _read(_webspace_id: str) -> dict:
        return {
            "events": [
                {"id": f"evt-before-bound-{index}", "ts": float(index), "request_id": f"req-{index}"}
                for index in range(source_count)
            ],
            "llm_logs": [],
        }

    def _backfill(_webspace_id: str, teacher: dict) -> dict:
        seen_counts.append(len(teacher["events"]))
        return {
            "schema": "adaos.nlu_teacher.ledger_backfill.v1",
            "completed": True,
            "events_total": len(teacher["events"]),
            "llm_logs_total": 0,
            "records_ensured": len(teacher["events"]),
            "already_present": 0,
            "elapsed_ms": 1.0,
        }

    async def _write(_webspace_id: str, teacher: dict) -> None:
        writes.append(teacher)

    monkeypatch.setattr(teacher_store_runtime, "load_teacher_state", lambda **_kwargs: {})
    monkeypatch.setattr(teacher_store_runtime, "_read_teacher_from_ydoc", _read)
    monkeypatch.setattr(teacher_store_runtime, "_write_teacher_to_ydoc", _write)
    monkeypatch.setattr(teacher_store_runtime, "save_teacher_state", lambda **_kwargs: None)
    monkeypatch.setattr(teacher_store_runtime, "backfill_teacher_history_to_ledger", _backfill)

    await teacher_store_runtime._on_sys_ready({"webspace_id": webspace_id})  # type: ignore[attr-defined]

    assert seen_counts == [source_count]
    assert len(writes) == 1
    assert len(writes[0]["events"]) == limits["events"]
    assert writes[0]["projection_window"]["ledger_backfill"]["events_total"] == source_count


@pytest.mark.anyio
async def test_teacher_store_runtime_preserves_projection_when_ledger_backfill_fails(monkeypatch):
    from adaos.services.nlu import teacher_events, teacher_store_runtime

    webspace_id = "ws-teacher-backfill-failure"
    limits = teacher_events.teacher_projection_limits()
    writes: list[dict] = []
    saves: list[dict] = []

    async def _read(_webspace_id: str) -> dict:
        return {
            "events": [
                {"id": f"evt-failure-{index}", "ts": float(index)}
                for index in range(limits["events"] + 1)
            ],
            "llm_logs": [],
        }

    def _fail_backfill(_webspace_id: str, _teacher: dict) -> dict:
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(teacher_store_runtime, "load_teacher_state", lambda **_kwargs: {})
    monkeypatch.setattr(teacher_store_runtime, "_read_teacher_from_ydoc", _read)
    monkeypatch.setattr(teacher_store_runtime, "_write_teacher_to_ydoc", lambda *_args: writes.append({}))
    monkeypatch.setattr(teacher_store_runtime, "save_teacher_state", lambda **kwargs: saves.append(kwargs))
    monkeypatch.setattr(teacher_store_runtime, "backfill_teacher_history_to_ledger", _fail_backfill)

    await teacher_store_runtime._on_sys_ready({"webspace_id": webspace_id})  # type: ignore[attr-defined]

    assert writes == []
    assert saves == []


@pytest.mark.anyio
async def test_teacher_store_runtime_does_not_rewrite_identical_projection(monkeypatch):
    from adaos.services.nlu import teacher_store_runtime

    webspace_id = "ws-teacher-identical-projection"
    current = teacher_store_runtime._merge_teacher(  # type: ignore[attr-defined]
        current={"events": [], "llm_logs": []},
        saved={},
    )
    writes: list[dict] = []
    saves: list[dict] = []

    async def _read(_webspace_id: str) -> dict:
        return current

    monkeypatch.setattr(teacher_store_runtime, "load_teacher_state", lambda **_kwargs: current)
    monkeypatch.setattr(teacher_store_runtime, "_read_teacher_from_ydoc", _read)
    monkeypatch.setattr(teacher_store_runtime, "_write_teacher_to_ydoc", lambda *_args: writes.append({}))
    monkeypatch.setattr(teacher_store_runtime, "save_teacher_state", lambda **kwargs: saves.append(kwargs))

    await teacher_store_runtime._on_scenarios_synced({"webspace_id": webspace_id})  # type: ignore[attr-defined]

    assert writes == []
    assert saves == []
