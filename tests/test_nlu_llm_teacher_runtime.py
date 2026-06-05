import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


def test_llm_teacher_collects_root_mcp_authoring_evidence(monkeypatch):
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.root_mcp import service as root_mcp_service

    llm._clear_root_mcp_descriptor_cache()
    calls: list[dict] = []

    def _fake_invoke_tool(tool_id, **kwargs):
        calls.append({"tool_id": tool_id, **kwargs})
        if tool_id == "nlu_authoring.get_context":
            return SimpleNamespace(ok=True, tool_id=tool_id, status="ok", result={"context": {"plane_id": "nlu_authoring"}})
        if tool_id == "desktop.registry.lookup":
            return SimpleNamespace(
                ok=True,
                tool_id=tool_id,
                status="ok",
                result={
                    "ok": True,
                    "webspace_id": "desktop",
                    "lookups": {
                        "modal_id": [{"value": "weather_modal", "labels": ["Weather"], "sources": ["test"]}],
                        "app_id": [],
                        "scenario_id": [],
                    },
                    "summary": [],
                    "fingerprint": "fp.test",
                },
            )
        if tool_id == "nlu_authoring.check_phrase":
            return SimpleNamespace(
                ok=True,
                tool_id=tool_id,
                status="ok",
                result={"check": {"ok": True, "accepted": False, "text": kwargs["arguments"]["text"]}},
            )
        if tool_id == "nlu_authoring.get_dialog_context":
            return SimpleNamespace(ok=True, tool_id=tool_id, status="ok", result={"request_id": "req.llm", "events": []})
        if tool_id == "nlu_authoring.list_training_targets":
            return SimpleNamespace(ok=True, tool_id=tool_id, status="ok", result={"summary": {"count": 1}, "targets": [{"id": "weather_skill"}]})
        if tool_id == "nlu_authoring.list_templates":
            return SimpleNamespace(ok=True, tool_id=tool_id, status="ok", result={"summary": {"count": 1}, "templates": [{"id": "tpl.weather"}]})
        if tool_id == "sdk.describe_surface":
            return SimpleNamespace(ok=True, tool_id=tool_id, status="ok", result={"surface_id": "adaos.sdk.describe_surface.v1"})
        return SimpleNamespace(
            ok=False,
            tool_id=tool_id,
            status="error",
            error=SimpleNamespace(code="unexpected_tool"),
        )

    monkeypatch.setattr(root_mcp_service, "invoke_tool", _fake_invoke_tool)

    evidence = llm._collect_root_mcp_authoring_evidence(
        webspace_id="desktop",
        text="show temperature in Berlin",
        request_id="req.llm",
        request_locale="en",
        preferred_locales=["ru"],
    )

    assert evidence["nlu_authoring_context"]["plane_id"] == "nlu_authoring"
    assert evidence["desktop_registry_lookup"]["lookups"]["modal_id"][0]["value"] == "weather_modal"
    assert evidence["nlu_authoring_phrase_check"]["check"]["accepted"] is False
    assert evidence["nlu_dialog_context"]["request_id"] == "req.llm"
    assert evidence["nlu_training_targets"]["targets"][0]["id"] == "weather_skill"
    assert evidence["nlu_templates"]["templates"][0]["id"] == "tpl.weather"
    assert evidence["sdk_surface"]["surface_id"] == "adaos.sdk.describe_surface.v1"
    assert [call["tool_id"] for call in calls] == [
        "nlu_authoring.get_context",
        "desktop.registry.lookup",
        "nlu_authoring.check_phrase",
        "nlu_authoring.get_dialog_context",
        "nlu_authoring.list_training_targets",
        "nlu_authoring.list_templates",
        "sdk.describe_surface",
    ]
    assert calls[0]["auth_context"]["capabilities"] == ["development.read.descriptors"]
    assert calls[2]["arguments"]["emit_trace"] is False
    assert calls[2]["dry_run"] is True


def test_llm_teacher_caches_root_mcp_descriptor_evidence(monkeypatch):
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.root_mcp import service as root_mcp_service

    llm._clear_root_mcp_descriptor_cache()
    monkeypatch.setattr(llm, "_MCP_EVIDENCE_CACHE_TTL_S", 60.0)
    calls: list[dict] = []

    def _fake_invoke_tool(tool_id, **kwargs):
        calls.append({"tool_id": tool_id, **kwargs})
        if tool_id == "nlu_authoring.get_context":
            return SimpleNamespace(ok=True, tool_id=tool_id, status="ok", result={"context": {"plane_id": "nlu_authoring"}})
        if tool_id == "desktop.registry.lookup":
            return SimpleNamespace(
                ok=True,
                tool_id=tool_id,
                status="ok",
                result={
                    "ok": True,
                    "webspace_id": "desktop",
                    "lookups": {"modal_id": [], "app_id": [], "scenario_id": []},
                    "summary": [],
                    "fingerprint": "fp.cached",
                },
            )
        if tool_id == "nlu_authoring.check_phrase":
            return SimpleNamespace(
                ok=True,
                tool_id=tool_id,
                status="ok",
                result={"check": {"ok": True, "accepted": False, "text": kwargs["arguments"]["text"]}},
            )
        if tool_id == "nlu_authoring.get_dialog_context":
            return SimpleNamespace(ok=True, tool_id=tool_id, status="ok", result={"request_id": kwargs["arguments"]["request_id"], "events": []})
        if tool_id == "nlu_authoring.list_training_targets":
            return SimpleNamespace(ok=True, tool_id=tool_id, status="ok", result={"summary": {"count": 0}, "targets": []})
        if tool_id == "nlu_authoring.list_templates":
            return SimpleNamespace(ok=True, tool_id=tool_id, status="ok", result={"summary": {"count": 0}, "templates": []})
        if tool_id == "sdk.describe_surface":
            return SimpleNamespace(ok=True, tool_id=tool_id, status="ok", result={"surface_id": "adaos.sdk.describe_surface.v1"})
        return SimpleNamespace(ok=False, tool_id=tool_id, status="error", error=SimpleNamespace(code="unexpected_tool"))

    monkeypatch.setattr(root_mcp_service, "invoke_tool", _fake_invoke_tool)

    first = llm._collect_root_mcp_authoring_evidence(
        webspace_id="desktop",
        text="show media server",
        request_id="req.cached.1",
        request_locale="ru",
    )
    second = llm._collect_root_mcp_authoring_evidence(
        webspace_id="desktop",
        text="show media indexer",
        request_id="req.cached.2",
        request_locale="ru",
    )

    tool_counts = {tool_id: [call["tool_id"] for call in calls].count(tool_id) for tool_id in {call["tool_id"] for call in calls}}
    assert first["_meta"]["descriptor_cache"]["stores"] == 5
    assert second["_meta"]["descriptor_cache"]["hits"] == 5
    assert second["nlu_authoring_phrase_check"]["check"]["text"] == "show media indexer"
    assert tool_counts["nlu_authoring.get_context"] == 1
    assert tool_counts["desktop.registry.lookup"] == 1
    assert tool_counts["nlu_authoring.list_training_targets"] == 1
    assert tool_counts["nlu_authoring.list_templates"] == 1
    assert tool_counts["sdk.describe_surface"] == 1
    assert tool_counts["nlu_authoring.check_phrase"] == 2
    assert tool_counts["nlu_authoring.get_dialog_context"] == 2
    llm._clear_root_mcp_descriptor_cache()


def test_llm_teacher_prepares_openai_mcp_tool_from_env_bearer(monkeypatch):
    from adaos.services.nlu import llm_teacher_runtime as llm

    llm._clear_llm_mcp_descriptor_cache()
    monkeypatch.setenv("ADAOS_NLU_LLM_MCP_BEARER", "secret-mcp-token")
    monkeypatch.setenv("ADAOS_NLU_LLM_MCP_SERVER_URL", "https://mcp.example.test/v1/root/mcp")
    monkeypatch.setattr(llm, "_LLM_MCP_MODE", "hybrid")
    monkeypatch.setattr(llm, "_LLM_MCP_ALLOWED_TOOLS", ("hub.get_subnet_info", "nlu_authoring.get_context"))

    plan = llm._prepare_llm_mcp_plan(request_id="req.mcp.env")

    assert plan["status"] == "ready"
    assert plan["source"] == "env_bearer"
    assert plan["tools"][0]["type"] == "mcp"
    assert plan["tools"][0]["server_url"] == "https://mcp.example.test/v1/root/mcp"
    assert plan["tools"][0]["authorization"] == "secret-mcp-token"
    assert plan["tools"][0]["allowed_tools"] == ["hub_get_subnet_info", "nlu_authoring_get_context"]
    assert llm._redact_llm_mcp_plan(plan)["tools"][0]["authorization"] == "<redacted>"
    llm._clear_llm_mcp_descriptor_cache()


@pytest.mark.anyio
async def test_llm_teacher_forwards_mcp_tools_to_root_llm_proxy(monkeypatch):
    from adaos.services.nlu import llm_teacher_runtime as llm

    captured: dict[str, object] = {}

    class _FakeRootHttp:
        def request(self, method, path, **kwargs):
            captured["method"] = method
            captured["path"] = path
            captured["kwargs"] = kwargs
            return {
                "output": [{"content": [{"type": "output_text", "text": "{\"decision\":\"ignore\"}"}]}],
                "_protocol": {"mcp": {"used_mcp": True, "item_count": 2}},
            }

    fake_http = _FakeRootHttp()
    monkeypatch.setattr(llm.RootHttpClient, "from_settings", classmethod(lambda cls, settings: fake_http))

    tool = {
        "type": "mcp",
        "server_label": "adaos_nlu",
        "server_url": "https://mcp.example.test/v1/root/mcp",
        "authorization": "secret-mcp-token",
        "require_approval": "never",
        "allowed_tools": ["hub_get_subnet_info"],
    }
    result = await llm._llm_call(
        [{"role": "user", "content": "Return JSON."}],
        request_id="req.mcp.forward",
        tools=[tool],
        tool_choice="auto",
        max_tool_calls=3,
    )

    assert result["_protocol"]["mcp"]["used_mcp"] is True
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/llm/response"
    body = captured["kwargs"]["json"]  # type: ignore[index]
    assert body["request_id"] == "req.mcp.forward"
    assert body["tools"] == [tool]
    assert body["tool_choice"] == "auto"
    assert body["max_tool_calls"] == 3


@pytest.mark.anyio
async def test_llm_teacher_uses_root_policy_when_env_unset(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-root-policy"
    fake_ctx = SimpleNamespace(
        bus=ctx.bus,
        paths=ctx.paths,
        settings=getattr(ctx, "settings", None),
        config=SimpleNamespace(root_settings=SimpleNamespace(llm=SimpleNamespace(allow_nlu_teacher=True))),
    )
    monkeypatch.setattr(llm, "get_ctx", lambda: fake_ctx)
    monkeypatch.setattr(llm, "_TEACHER_ENABLED", None)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", None)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})

    calls: list[dict] = []

    async def _fake_llm_call(messages, *, request_id=None):
        calls.append({"messages": messages, "request_id": request_id})
        return {"output": [{"content": [{"type": "output_text", "text": json.dumps({"decision": "ignore", "confidence": 0.1, "notes": "test"})}]}]}

    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {"id": "teach.policy", "request_id": "req.policy", "text": "show test panel", "reason": "fallback", "via": "rasa"},
        }
    )

    assert calls
    assert calls[-1]["request_id"] == "req.policy"

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        events = list(teacher.get("events") or [])
        logs = list(teacher.get("llm_logs") or [])

    assert any(item.get("kind") == "llm.request" for item in events)
    assert any(item.get("kind") == "llm.response" for item in events)
    assert logs[-1]["status"] == "response"


@pytest.mark.anyio
async def test_llm_teacher_records_disabled_llm_skip(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-disabled-skip"
    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", False)

    calls: list[dict] = []

    async def _fake_llm_call(messages, *, request_id=None):
        calls.append({"messages": messages, "request_id": request_id})
        return {}

    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {"id": "teach.skip", "request_id": "req.skip", "text": "show test panel", "reason": "fallback", "via": "rasa"},
        }
    )

    assert not calls

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        events = list(teacher.get("events") or [])
        logs = list(teacher.get("llm_logs") or [])

    assert events[-1]["kind"] == "llm.skipped"
    assert events[-1]["raw"]["reason"] == "llm_teacher_disabled"
    assert logs[-1]["status"] == "skipped"
    assert logs[-1]["skip_reason"] == "llm_teacher_disabled"
    assert teacher["budget"]["counters"]["skipped"] >= 1


@pytest.mark.anyio
async def test_llm_teacher_defers_miss_when_root_llm_unavailable(monkeypatch):
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    webspace_id = "ws-test-llm-deferred-unavailable"
    request_text = "show offline only panel"

    async def _failing_llm_call(messages, *, request_id=None, **kwargs):
        raise RuntimeError("root proxy unavailable")

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})
    monkeypatch.setattr(llm, "_llm_call", _failing_llm_call)

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [], "llm_logs": [], "events": []})

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.deferred",
                "request_id": "req.deferred",
                "text": request_text,
                "reason": "below_margin_threshold",
                "via": "rasa",
                "_meta": {"route_id": "voice_chat"},
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        queue = list(teacher.get("deferred_enrichment_queue") or [])
        events = list(teacher.get("events") or [])
        logs = list(teacher.get("llm_logs") or [])

    assert queue
    assert queue[-1]["request_id"] == "req.deferred"
    assert queue[-1]["reason"] == "root_llm_unavailable"
    assert "root proxy unavailable" in queue[-1]["error"]
    assert any(event.get("kind") == "llm.deferred" for event in events)
    assert logs[-1]["status"] == "error"
    assert teacher["budget"]["counters"]["deferred"] >= 1
    assert teacher["budget"]["by_reason"]["root_llm_unavailable"] >= 1


@pytest.mark.anyio
async def test_llm_teacher_suppresses_repeated_phrase_before_llm(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-rate-repeat"
    request_text = "show repeated media"
    calls: list[str] = []
    skipped: list[dict] = []

    async def _fake_llm_call(messages, *, request_id=None):
        calls.append(str(request_id or ""))
        return {"output": [{"content": [{"type": "output_text", "text": json.dumps({"decision": "ignore", "confidence": 0.0})}]}]}

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})
    monkeypatch.setattr(llm, "_REPEATED_PHRASE_TTL_S", 60.0)
    monkeypatch.setattr(llm, "_RATE_LIMIT_MAX_PER_WINDOW", 10)
    llm._RATE_LIMIT_BUCKETS.clear()
    llm._RECENT_PHRASE_HASHES.clear()

    ctx.bus.subscribe("nlp.teacher.llm.skipped", lambda ev: skipped.append(dict(ev.payload or {})))

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [], "llm_logs": [], "events": []})

    for idx in range(2):
        await llm._on_teacher_request(
            {
                "webspace_id": webspace_id,
                "request": {
                    "id": f"teach.repeat.{idx}",
                    "request_id": f"req.repeat.{idx}",
                    "text": request_text,
                    "reason": "below_margin_threshold",
                    "via": "rasa",
                    "_meta": {"route_id": "voice_chat"},
                },
            }
        )

    assert len(calls) == 1
    assert skipped
    assert skipped[-1]["reason"] == "repeated_phrase_suppressed"
    async with async_get_ydoc(webspace_id) as ydoc:
        logs = list(((ydoc.get_map("data").get("nlu_teacher") or {}).get("llm_logs") or []))
    assert any(item.get("status") == "skipped" and item.get("skip_reason") == "repeated_phrase_suppressed" for item in logs)


@pytest.mark.anyio
async def test_llm_teacher_rate_gate_exempts_correction_retry(monkeypatch):
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    webspace_id = "ws-test-llm-rate-correction"
    request_text = "show correction media"
    calls: list[str] = []

    async def _fake_llm_call(messages, *, request_id=None):
        calls.append(str(request_id or ""))
        return {"output": [{"content": [{"type": "output_text", "text": json.dumps({"decision": "ignore", "confidence": 0.0})}]}]}

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})
    monkeypatch.setattr(llm, "_REPEATED_PHRASE_TTL_S", 60.0)
    monkeypatch.setattr(llm, "_RATE_LIMIT_MAX_PER_WINDOW", 1)
    llm._RATE_LIMIT_BUCKETS.clear()
    llm._RECENT_PHRASE_HASHES.clear()

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [], "llm_logs": [], "events": []})

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.correction.1",
                "request_id": "req.correction.1",
                "text": request_text,
                "reason": "below_margin_threshold",
                "via": "rasa",
                "_meta": {"route_id": "voice_chat"},
            },
        }
    )
    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.correction.2",
                "request_id": "req.correction.2",
                "text": request_text,
                "reason": "below_margin_threshold",
                "via": "rasa",
                "_meta": {
                    "route_id": "voice_chat",
                    "rejected_candidate_id": "cand.previous",
                    "previous_request_id": "req.correction.1",
                },
            },
        }
    )

    assert calls == ["req.correction.1", "req.correction.2"]


@pytest.mark.anyio
async def test_teacher_append_event_persists_to_store():
    from adaos.services.nlu.teacher_events import append_event, make_event
    from adaos.services.nlu.teacher_store import load_teacher_state
    from adaos.services.yjs.doc import async_get_ydoc

    webspace_id = "ws-test-teacher-event-persistence"
    request_id = "req.persist.event"

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"events": []})

    await append_event(
        webspace_id,
        make_event(
            webspace_id=webspace_id,
            request_id=request_id,
            request_text="show persisted event",
            kind="llm.request",
            title="LLM request",
        ),
    )

    saved = load_teacher_state(webspace_id=webspace_id)
    events = list(saved.get("events") or [])
    assert events
    assert events[-1]["request_id"] == request_id
    assert events[-1]["kind"] == "llm.request"


@pytest.mark.anyio
async def test_llm_teacher_prompt_includes_mcp_evidence_and_stores_regex_candidate(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-teacher"
    scenario_id = "test_llm_teacher_scenario"
    pattern = r"\btemperature\b(?:\s+in\s+(?P<city>[^?.!,;:]+))?"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {"id": scenario_id, "version": "0.0.1", "nlu": {"intents": {"desktop.open_weather": {"actions": []}}}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", scenario_id)
            data_map.set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})

    captured_messages: list[list[dict[str, str]]] = []

    async def _fake_llm_call(messages, *, request_id=None):
        captured_messages.append(messages)
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": "```json\n"
                            + json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": "desktop.open_weather",
                                    "regex_rule": {"intent": "desktop.open_weather", "pattern": pattern},
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": ["show temperature in Berlin"],
                                    "slots": {"city": {"type": "string"}},
                                    "confidence": 0.91,
                                    "notes": "Existing weather intent needs a temperature synonym.",
                                    "candidate": None,
                                }
                            )
                            + "\n```",
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(
        llm,
        "_collect_root_mcp_authoring_evidence",
        lambda **kwargs: {
            "nlu_authoring_context": {"plane_id": "nlu_authoring", "named_entities": {"items": []}},
            "nlu_authoring_phrase_check": {"check": {"ok": True, "accepted": False, "text": kwargs["text"]}},
        },
    )
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)

    proposed: list[dict] = []

    def _capture_proposed(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            proposed.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.candidate.proposed", _capture_proposed)

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.llm",
                "request_id": "req.llm",
                "text": "show temperature in Berlin",
                "reason": "fallback",
                "via": "rasa",
                "_meta": {"request_locale": "en"},
            },
        }
    )

    assert captured_messages
    user_payload = json.loads(captured_messages[-1][1]["content"])
    assert user_payload["context"]["root_mcp"]["nlu_authoring_context"]["plane_id"] == "nlu_authoring"
    assert user_payload["context"]["root_mcp"]["nlu_authoring_phrase_check"]["check"]["accepted"] is False

    async with async_get_ydoc(webspace_id) as ydoc:
        data_map = ydoc.get_map("data")
        teacher = data_map.get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    assert proposed
    assert candidates
    candidate = candidates[-1]
    assert candidate["kind"] == "regex_rule"
    assert candidate["regex_rule"] == {"intent": "desktop.open_weather", "pattern": pattern}
    assert candidate["target"] == {"type": "scenario", "id": scenario_id}
    assert candidate["status"] == "pending"
    assert candidate["preview"]["status"] == "regex_matched"
    assert candidate["preview"]["slots"]["city"] == "Berlin"
    assert candidate["training_strategy"] == {
        "primary": "regex",
        "source": "adaos.default",
        "why_not_regex": None,
    }
    assert candidate["action_candidate"]["intent"] == "desktop.open_weather"
    assert candidate["action_candidate"]["side_effect_class"] == "read_only"
    assert candidate["action_candidate"]["phrase_preview"]["ok"] is True
    assert candidate["action_candidate"]["action_preview"]["status"] == "not_run"
    assert candidate["template_candidate"]["engine"] == "regex"
    assert candidate["template_candidate"]["operation"] == "add_regex_rule"
    assert candidate["template_candidate"]["training_strategy"]["primary"] == "regex"
    assert candidate["promotion"]["state"] == "local_learned"
    assert candidate["promotion"]["public_export_allowed"] is False
    assert candidate["privacy"]["public_promotion_requires_review"] is True
    assert candidate["provenance"]["request_id"] == "req.llm"
    assert candidate["provenance"]["mcp_bearer_embedded"] is False
    assert candidate["provenance"]["mcp"]["enabled"] in {True, False}
    assert "authorization" not in json.dumps(candidate["provenance"], ensure_ascii=False).lower()
    assert teacher["policies"]["retention"]["version"] == "nlu.teacher.retention.v1"
    assert teacher["policies"]["promotion_privacy"]["default_state"] == "local_learned"
    assert teacher["budget"]["policy"]["fallback_behavior"] == "store_miss_for_later_batch_enrichment"
    assert teacher["budget"]["counters"]["request"] >= 1
    assert teacher["budget"]["counters"]["response"] >= 1


@pytest.mark.anyio
async def test_llm_teacher_can_base_candidate_on_mcp_only_fact(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-mcp-only-fact"
    scenario_id = "test_llm_mcp_only_fact"
    request_text = "show mcp only violet panel 77"
    modal_id = "mcp_only_modal_77"
    modal_label = "MCP Only Violet Panel 77"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        "desktop.open_modal": {
                            "actions": [
                                {
                                    "type": "callHost",
                                    "target": "desktop.modal.open",
                                    "params": {"modal_id": "$slot.modal_id", "webspace_id": "$ctx.webspace_id"},
                                }
                            ]
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

    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", scenario_id)
            data_map.set(txn, "nlu_teacher", {"candidates": [], "llm_logs": [], "events": []})

    captured: dict[str, bool] = {"saw_mcp_only_fact": False}

    async def _fake_llm_call(messages, *, request_id=None):
        user_payload = json.loads(messages[-1]["content"])
        context = dict(user_payload["context"])
        root_mcp = context.pop("root_mcp")
        root_blob = json.dumps(root_mcp, ensure_ascii=False)
        non_mcp_blob = json.dumps(context, ensure_ascii=False)

        assert modal_id in root_blob
        assert modal_label in root_blob
        assert modal_id not in non_mcp_blob
        captured["saw_mcp_only_fact"] = True

        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": "desktop.open_app",
                                    "regex_rule": {
                                        "intent": "desktop.open_app",
                                        "pattern": r"\bshow\s+(?P<app_id>mcp only violet panel 77)\b",
                                    },
                                    "target": {"type": "skill", "id": "mcp_only_skill"},
                                    "examples": [request_text],
                                    "slots": {"app_id": {"type": "string"}},
                                    "confidence": 0.88,
                                    "notes": f"Resolved {modal_id} from MCP desktop registry lookup.",
                                    "candidate": None,
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(
        llm,
        "_collect_root_mcp_authoring_evidence",
        lambda **kwargs: {
            "nlu_authoring_context": {
                "plane_id": "nlu_authoring",
                "action_surface": {
                    "available_actions": [
                        {
                            "class": "interface_action",
                            "intent": "desktop.open_modal",
                            "owner": {"type": "scenario", "id": scenario_id},
                            "required_slots": ["modal_id"],
                            "side_effect_class": "ui_navigation",
                        }
                    ]
                },
            },
            "desktop_registry_lookup": {
                "ok": True,
                "webspace_id": webspace_id,
                "lookups": {
                    "modal_id": [
                        {
                            "value": modal_id,
                            "labels": [modal_label],
                            "sources": ["test.mcp.sentinel"],
                        }
                    ],
                    "app_id": [],
                    "scenario_id": [],
                },
                "summary": [],
                "fingerprint": "fp.mcp-only-sentinel",
            },
            "nlu_authoring_phrase_check": {"check": {"ok": True, "accepted": False, "text": kwargs["text"]}},
        },
    )

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.mcp-only-fact",
                "request_id": "req.mcp-only-fact",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
                "_meta": {"request_locale": "en"},
            },
        }
    )

    assert captured["saw_mcp_only_fact"] is True

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    assert candidates
    candidate = candidates[-1]
    assert candidate["status"] == "pending"
    assert candidate["regex_rule"]["intent"] == "desktop.open_modal"
    assert candidate["normalization"]["llm_proposal_repair"]["modal_id"] == modal_id
    assert candidate["target"] == {"type": "scenario", "id": scenario_id}
    assert candidate["action_candidate"]["intent"] == "desktop.open_modal"
    assert candidate["action_candidate"]["slots"]["modal_id"] == "mcp only violet panel 77"
    assert candidate["template_candidate"]["patch"]["intent"] == "desktop.open_modal"


@pytest.mark.anyio
async def test_llm_teacher_need_clarification_creates_session(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-clarification"
    request_text = "show media"

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("data").set(txn, "nlu_teacher", {"clarification_sessions": [], "events": [], "llm_logs": []})

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "ignore",
                                    "need_clarification": True,
                                    "clarification_question": "Open Media Indexer or Media Server?",
                                    "options": [
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
                                    "confidence": 0.56,
                                    "training_strategy": {
                                        "primary": "clarification",
                                        "rationale": "Two similarly named media actions are available.",
                                    },
                                    "why_not_regex": "The entity is ambiguous.",
                                    "risk_notes": "Needs user disambiguation.",
                                    "notes": "Ask before teaching a template.",
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})

    requested: list[dict] = []
    messages: list[dict] = []

    def _capture_requested(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            requested.append(dict(payload))

    def _capture_chat(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            messages.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.clarification.requested", _capture_requested)
    ctx.bus.subscribe("io.out.chat.append", _capture_chat)

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.clarify",
                "request_id": "req.clarify",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
                "_meta": {"route_id": "voice_chat"},
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        clarifications = list(teacher.get("clarification_sessions") or [])
        candidates = list(teacher.get("candidates") or [])
        events = list(teacher.get("events") or [])

    assert not candidates
    assert clarifications
    session = clarifications[-1]
    assert session["status"] == "awaiting_user"
    assert session["kind"] == "llm_clarification"
    assert session["uncertainty_kind"] == "llm_ambiguity"
    assert session["request_id"] == "req.clarify"
    assert session["request_text"] == request_text
    assert session["question"] == "Open Media Indexer or Media Server?"
    assert session["allowed_answers"][0]["id"] == "media_indexer"
    assert session["allowed_answers"][0]["action_candidate"]["slots"]["modal_id"] == "media_indexer_modal"
    assert session["training_strategy"]["primary"] == "clarification"
    assert session["risk_notes"] == "Needs user disambiguation."
    assert any(item.get("kind") == "clarification.requested" for item in events)
    assert requested
    assert requested[-1]["session"]["id"] == session["id"]
    assert messages
    assert "1. Media Indexer" in messages[-1]["text"]
    assert "2. Media Server" in messages[-1]["text"]


@pytest.mark.anyio
async def test_llm_teacher_quarantines_regex_candidate_that_misses_source_text(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-teacher-quarantine"
    scenario_id = "test_llm_teacher_quarantine"
    request_text = "open the operations console"
    pattern = r"\bweather\b"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        "demo.interface_action.open_ops_console": {
                            "actions": [{"type": "callHost", "target": "desktop.modal.open"}]
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

    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", scenario_id)
            data_map.set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": "demo.interface_action.open_ops_console",
                                    "regex_rule": {
                                        "intent": "demo.interface_action.open_ops_console",
                                        "pattern": pattern,
                                    },
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "slots": {},
                                    "confidence": 0.77,
                                    "notes": "Bad candidate for quarantine test.",
                                    "candidate": None,
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})

    rejected: list[dict] = []

    def _capture_rejected(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            rejected.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.candidate.apply.rejected", _capture_rejected)

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.quarantine",
                "request_id": "req.quarantine",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    assert candidates
    candidate = candidates[-1]
    assert candidate["status"] == "quarantined"
    assert candidate["preview"]["status"] == "source_text_miss"

    await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate["id"]})
    assert rejected
    assert rejected[-1]["reason"] == "candidate_quarantined"


@pytest.mark.anyio
async def test_llm_teacher_rejects_regex_when_strategy_prefers_rasa_example(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.nlu.teacher_runtime import _on_example_save
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-m3-rasa-strategy"
    scenario_id = "test_llm_m3_rasa_strategy"
    request_text = "show all recent indexing failures"
    intent_name = "media_indexer.show_failures"
    pattern = r".*failures.*"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {"id": scenario_id, "version": "0.0.1", "nlu": {"intents": {intent_name: {"actions": []}}}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("ui").set(txn, "current_scenario", scenario_id)
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [], "events": [], "dataset": [], "llm_logs": []})

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": intent_name,
                                    "regex_rule": {"intent": intent_name, "pattern": pattern},
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "slots": {},
                                    "training_strategy": {
                                        "primary": "rasa_example",
                                        "rationale": "The phrase is semantic and process-state dependent.",
                                    },
                                    "why_not_regex": "Regex would overfit a broad process query.",
                                    "confidence": 0.74,
                                    "notes": "Prefer curated Rasa example.",
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})
    ctx.bus.subscribe("nlp.teacher.example.save", _on_example_save)

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.m3.rasa",
                "request_id": "req.m3.rasa",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    assert candidates
    candidate = candidates[-1]
    assert candidate["kind"] == "training_example"
    assert "regex_rule" not in candidate
    assert candidate["training_strategy"]["primary"] == "rasa_example"
    assert candidate["strategy_candidate"]["template_candidate"]["operation"] == "save_example"
    assert candidate["strategy_candidate"]["regex_rejection"]["reason"] == "strategy_not_regex"
    assert candidate["rejected_regex_rule"]["regex_rule"] == {"intent": intent_name, "pattern": pattern}

    await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate["id"]})
    await ctx.bus.wait_for_idle(timeout=2.0)

    saved = json.loads((scenario_root / "scenario.json").read_text(encoding="utf-8"))
    assert request_text in saved["nlu"]["intents"][intent_name]["examples"]
    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        dataset = list((teacher or {}).get("dataset") or [])
    assert dataset
    assert dataset[-1]["status"] == "positive_feedback"


@pytest.mark.anyio
async def test_llm_teacher_quarantines_overbroad_regex_as_example_strategy(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-m3-overbroad"
    scenario_id = "test_llm_m3_overbroad"
    request_text = "open dangerous maintenance panel"
    intent_name = "desktop.open_modal"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {"id": scenario_id, "version": "0.0.1", "nlu": {"intents": {intent_name: {"actions": []}}}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("ui").set(txn, "current_scenario", scenario_id)
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": intent_name,
                                    "regex_rule": {"intent": intent_name, "pattern": ".*"},
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "confidence": 0.91,
                                    "notes": "Bad broad regex.",
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.m3.overbroad",
                "request_id": "req.m3.overbroad",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    assert candidates
    candidate = candidates[-1]
    assert candidate["kind"] == "training_example"
    assert candidate["training_strategy"]["source"] == "adaos.policy"
    assert candidate["training_strategy"]["primary"] == "rasa_example"
    assert candidate["strategy_candidate"]["regex_rejection"]["reason"] == "overbroad_regex"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("strategy", "expected_kind"),
    [
        ("entity_alias", "entity_alias"),
        ("descriptor_fix", "descriptor_fix"),
        ("development_task", "development_task"),
    ],
)
async def test_llm_teacher_persists_first_class_non_regex_strategy_candidates(monkeypatch, strategy, expected_kind):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = f"ws-test-llm-m3-{strategy}"
    scenario_id = f"test_llm_m3_{strategy}"
    request_text = f"teach {strategy}"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps({"id": scenario_id, "version": "0.0.1", "nlu": {"intents": {}}}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("ui").set(txn, "current_scenario", scenario_id)
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [], "plan": [], "llm_logs": []})

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "revise_nlu",
                                    "intent": "demo.intent" if strategy != "development_task" else None,
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "training_strategy": {"primary": strategy, "rationale": "M3 test"},
                                    "candidate": {
                                        "name": f"{strategy} candidate",
                                        "description": "First-class M3 candidate.",
                                        "alias": "медиа индекс" if strategy == "entity_alias" else None,
                                        "missing_surface": "llm_hints" if strategy == "descriptor_fix" else None,
                                        "requested_behavior": "new capability" if strategy == "development_task" else None,
                                    },
                                    "why_not_regex": "Not a deterministic command template.",
                                    "confidence": 0.66,
                                    "notes": f"Persist {strategy}.",
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": f"teach.m3.{strategy}",
                "request_id": f"req.m3.{strategy}",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    assert candidates
    candidate = candidates[-1]
    assert candidate["kind"] == expected_kind
    assert candidate["training_strategy"]["primary"] == strategy
    assert candidate["strategy_candidate"]["class"] in {
        "entity_correction",
        "descriptor_fix",
        "development_task",
    }
    if strategy in {"descriptor_fix", "development_task"}:
        builder_task = candidate["builder_task"]
        assert builder_task["kind"] == strategy
        assert builder_task["status"] == "proposed"
        assert builder_task["source"]["type"] == "nlu_teacher"
        assert builder_task["source"]["candidate_id"] == candidate["id"]
        assert isinstance(builder_task["context_snapshot"], dict)
        assert candidate["strategy_candidate"]["builder_task"]["task_id"] == builder_task["task_id"]
    else:
        assert "builder_task" not in candidate

    await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate["id"]})
    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        plan = list((teacher or {}).get("plan") or [])
    assert plan
    assert plan[-1]["kind"] == expected_kind
    assert plan[-1]["training_strategy"]["primary"] == strategy


@pytest.mark.anyio
async def test_llm_teacher_records_prompt_hashes_and_suppresses_duplicate_regex(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-teacher-dedupe"
    scenario_id = "test_llm_teacher_dedupe"
    request_text = "show temperature in Berlin"
    pattern = r"\btemperature\b(?:\s+in\s+(?P<city>[^?.!,;:]+))?"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {"id": scenario_id, "version": "0.0.1", "nlu": {"intents": {"desktop.open_weather": {"actions": []}}}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", scenario_id)
            data_map.set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": "desktop.open_weather",
                                    "regex_rule": {"intent": "desktop.open_weather", "pattern": pattern},
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "slots": {"city": {"type": "string"}},
                                    "confidence": 0.91,
                                    "notes": "Duplicate suppression smoke.",
                                    "candidate": None,
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})

    duplicates: list[dict] = []

    def _capture_duplicate(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            duplicates.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.candidate.duplicate_suppressed", _capture_duplicate)

    for suffix in ("one", "two"):
        await llm._on_teacher_request(
            {
                "webspace_id": webspace_id,
                "request": {
                    "id": f"teach.dedupe.{suffix}",
                    "request_id": f"req.dedupe.{suffix}",
                    "text": request_text,
                    "reason": "fallback",
                    "via": "rasa",
                },
            }
        )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])
        logs = list((teacher or {}).get("llm_logs") or [])

    assert len(candidates) == 1
    assert duplicates
    assert duplicates[-1]["duplicate_of"] == candidates[0]["id"]
    audit = candidates[0]["llm"]["audit"]
    assert audit["prompt_hash"].startswith("sha256:")
    assert audit["context_hash"].startswith("sha256:")
    assert audit["request_hash"].startswith("sha256:")
    assert logs[-1]["audit"]["prompt_hash"].startswith("sha256:")


@pytest.mark.anyio
async def test_llm_teacher_includes_correction_thread_context(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-teacher-correction"
    scenario_id = "test_llm_teacher_correction"
    request_text = "no, open the operations console instead"
    intent_name = "demo.interface_action.open_ops_console"
    pattern = r"\boperations\s+console\b"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        intent_name: {"actions": [{"type": "callHost", "target": "desktop.modal.open"}]}
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", scenario_id)
            data_map.set(
                txn,
                "nlu_teacher",
                {
                    "candidates": [
                        {
                            "id": "cand.previous",
                            "ts": 10.0,
                            "kind": "regex_rule",
                            "text": "open weather",
                            "request_id": "req.previous",
                            "regex_rule": {"intent": "desktop.open_weather", "pattern": r"\bweather\b"},
                            "target": {"type": "scenario", "id": scenario_id},
                            "status": "intent_matched",
                        }
                    ],
                    "llm_logs": [],
                },
            )

    captured_messages: list[list[dict[str, str]]] = []

    async def _fake_llm_call(messages, *, request_id=None):
        captured_messages.append(messages)
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": intent_name,
                                    "regex_rule": {"intent": intent_name, "pattern": pattern},
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "slots": {},
                                    "confidence": 0.87,
                                    "notes": "Correction of the previous weather candidate.",
                                    "candidate": None,
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.correction",
                "request_id": "req.correction",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    user_payload = json.loads(captured_messages[-1][1]["content"])
    correction = user_payload["context"]["correction_thread"]
    assert correction["active"] is True
    assert correction["previous_candidate"]["candidate_id"] == "cand.previous"
    assert correction["previous_candidate"]["regex_rule"]["intent"] == "desktop.open_weather"

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    candidate = candidates[-1]
    assert candidate["correction_of"]["candidate_id"] == "cand.previous"
    assert candidate["thread_id"] == "thread.req.previous"


@pytest.mark.anyio
async def test_llm_teacher_closed_loop_regex_candidate_can_be_applied_and_replayed(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.nlu.pipeline import _try_regex_intent
    from adaos.services.nlu.regex_rules_runtime import _on_regex_rule_apply
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-llm-closed-loop"
    scenario_id = "test_llm_teacher_closed_loop"
    request_text = "show temperature in Berlin"
    pattern = r"\btemperature\b(?:\s+in\s+(?P<city>[^?.!,;:]+))?"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {"id": scenario_id, "version": "0.0.1", "nlu": {"intents": {"desktop.open_weather": {"actions": []}}}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", scenario_id)
            data_map.set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})
            data_map.set(txn, "nlu", {"regex_rules": []})

    intent, slots, via, _raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
    assert intent is None
    assert slots == {}
    assert via == "regex"

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": "desktop.open_weather",
                                    "regex_rule": {"intent": "desktop.open_weather", "pattern": pattern},
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "slots": {"city": {"type": "string"}},
                                    "confidence": 0.93,
                                    "notes": "Teach temperature synonym for the existing weather intent.",
                                    "candidate": None,
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(
        llm,
        "_collect_root_mcp_authoring_evidence",
        lambda **kwargs: {"nlu_authoring_phrase_check": {"check": {"accepted": False, "text": kwargs["text"]}}},
    )

    acquired: list[dict] = []

    def _capture_acquired(ev):
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            acquired.append(dict(payload))

    ctx.bus.subscribe("nlp.teacher.regex_rule.apply", _on_regex_rule_apply)
    ctx.bus.subscribe("nlp.teacher.understanding.acquired", _capture_acquired)

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.closed_loop",
                "request_id": "req.closed_loop",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        data_map = ydoc.get_map("data")
        teacher = data_map.get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    assert candidates
    candidate_id = candidates[-1]["id"]

    await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate_id, "_meta": {"webspace_id": webspace_id}})

    for _ in range(50):
        if acquired:
            break
        await asyncio.sleep(0.01)

    assert acquired
    assert acquired[-1]["intent"] == "desktop.open_weather"

    intent, slots, via, _raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
    assert intent == "desktop.open_weather"
    assert via == "regex.dynamic"
    assert slots.get("city") == "Berlin"


@pytest.mark.anyio
async def test_llm_teacher_trains_skill_action_regex_and_rolls_back(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.nlu.pipeline import _try_regex_intent
    from adaos.services.nlu.regex_rules_runtime import _on_regex_rule_apply, _on_regex_rule_rollback
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-skill-action-teacher"
    scenario_id = "test_skill_action_teacher"
    skill_id = "test_weather_action_skill"
    request_text = "skillprobeweather in Oslo"
    intent_name = "demo.skill_action.weather"
    pattern = r"\bskillprobeweather\b(?:\s+in\s+(?P<city>[^?.!,;:]+))?"

    skill_root = Path(ctx.paths.skills_dir()) / skill_id
    skill_root.mkdir(parents=True, exist_ok=True)
    skill_yaml = skill_root / "skill.yaml"
    skill_yaml.write_text(
        yaml.safe_dump(
            {
                "name": skill_id,
                "version": "0.0.1",
                "events": {"subscribe": ["demo.weather.fetch"]},
                "nlu": {"regex_rules": []},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        intent_name: {
                            "actions": [{"type": "callSkill", "target": "demo.weather.fetch", "params": {"city": "$slot.city"}}]
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

    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", scenario_id)
            data_map.set(txn, "catalog", {"apps": [{"id": "weather", "origin": f"skill:{skill_id}"}]})
            data_map.set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})
            data_map.set(txn, "nlu", {"regex_rules": []})

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": intent_name,
                                    "regex_rule": {"intent": intent_name, "pattern": pattern},
                                    "target": None,
                                    "examples": [request_text],
                                    "slots": {"city": {"type": "string"}},
                                    "confidence": 0.9,
                                    "notes": "Skill action should route to the weather skill owner.",
                                    "candidate": None,
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})
    ctx.bus.subscribe("nlp.teacher.regex_rule.apply", _on_regex_rule_apply)

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {"id": "teach.skill", "request_id": "req.skill", "text": request_text, "reason": "fallback", "via": "rasa"},
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])
    assert candidates[-1]["target"] == {"type": "skill", "id": skill_id}
    candidate_id = candidates[-1]["id"]

    await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate_id, "_meta": {"webspace_id": webspace_id}})
    for _ in range(50):
        intent, slots, via, _raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
        if intent == intent_name:
            break
        await asyncio.sleep(0.01)

    assert intent == intent_name
    assert via == "regex.dynamic"
    assert slots.get("city") == "Oslo"
    saved_skill = yaml.safe_load(skill_yaml.read_text(encoding="utf-8")) or {}
    assert (saved_skill.get("nlu") or {}).get("regex_rules")

    await _on_regex_rule_rollback({"webspace_id": webspace_id, "candidate_id": candidate_id, "_meta": {"webspace_id": webspace_id}})
    intent, slots, via, _raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
    assert intent is None
    assert slots == {}
    saved_skill = yaml.safe_load(skill_yaml.read_text(encoding="utf-8")) or {}
    assert not ((saved_skill.get("nlu") or {}).get("regex_rules") or [])


@pytest.mark.anyio
async def test_llm_teacher_trains_interface_action_regex_and_rolls_back(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.nlu.candidates_runtime import _on_candidate_apply
    from adaos.services.nlu.pipeline import _try_regex_intent
    from adaos.services.nlu.regex_rules_runtime import _on_regex_rule_apply, _on_regex_rule_rollback
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-interface-action-teacher"
    scenario_id = "test_interface_action_teacher"
    request_text = "launch opsconsole"
    intent_name = "demo.interface_action.open_ops_console"
    pattern = r"\bopsconsole\b"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    scenario_json = scenario_root / "scenario.json"
    scenario_json.write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        intent_name: {
                            "actions": [
                                {"type": "callHost", "target": "desktop.modal.open", "params": {"modal_id": "ops_console"}}
                            ]
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

    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", scenario_id)
            data_map.set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})
            data_map.set(txn, "nlu", {"regex_rules": []})

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": intent_name,
                                    "regex_rule": {"intent": intent_name, "pattern": pattern},
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "slots": {},
                                    "confidence": 0.88,
                                    "notes": "Interface action should stay scenario-owned.",
                                    "candidate": None,
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})
    ctx.bus.subscribe("nlp.teacher.regex_rule.apply", _on_regex_rule_apply)

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {"id": "teach.interface", "request_id": "req.interface", "text": request_text, "reason": "fallback", "via": "rasa"},
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])
    assert candidates[-1]["target"] == {"type": "scenario", "id": scenario_id}
    candidate_id = candidates[-1]["id"]

    await _on_candidate_apply({"webspace_id": webspace_id, "candidate_id": candidate_id, "_meta": {"webspace_id": webspace_id}})
    for _ in range(50):
        intent, slots, via, _raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
        if intent == intent_name:
            break
        await asyncio.sleep(0.01)

    assert intent == intent_name
    assert via == "regex.dynamic"
    saved_scenario = json.loads(scenario_json.read_text(encoding="utf-8"))
    assert (saved_scenario.get("nlu") or {}).get("regex_rules")

    await _on_regex_rule_rollback({"webspace_id": webspace_id, "candidate_id": candidate_id, "_meta": {"webspace_id": webspace_id}})
    intent, slots, via, _raw = await _try_regex_intent(request_text, webspace_id=webspace_id)
    assert intent is None
    assert slots == {}
    saved_scenario = json.loads(scenario_json.read_text(encoding="utf-8"))
    assert not ((saved_scenario.get("nlu") or {}).get("regex_rules") or [])


@pytest.mark.anyio
async def test_llm_teacher_repairs_app_open_to_modal_alias_candidate(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-open-modal-alias-repair"
    scenario_id = "test_open_modal_alias_repair"
    request_text = "Покажи браузеры"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        "desktop.open_modal": {
                            "actions": [
                                {
                                    "type": "callHost",
                                    "target": "desktop.modal.open",
                                    "params": {"modal_id": "$slot.modal_id", "webspace_id": "$ctx.webspace_id"},
                                }
                            ]
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

    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", scenario_id)
            data_map.set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": "desktop.open_app",
                                    "regex_rule": {"intent": "desktop.open_app", "pattern": r"\bпокажи\s+(?P<app_id>browsers)\b"},
                                    "target": {"type": "skill", "id": "browsers_skill"},
                                    "examples": [request_text],
                                    "slots": {},
                                    "confidence": 0.81,
                                    "notes": "LLM picked app id, AdaOS should repair this to modal open.",
                                    "candidate": None,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(
        llm,
        "_collect_root_mcp_authoring_evidence",
        lambda **kwargs: {
            "desktop_registry_lookup": {
                "ok": True,
                "webspace_id": webspace_id,
                "lookups": {
                    "modal_id": [
                        {
                            "value": "browsers_modal",
                            "labels": ["Browsers", "браузеры", "браузер"],
                            "sources": ["test"],
                        }
                    ],
                    "app_id": [],
                    "scenario_id": [],
                },
                "summary": [],
                "fingerprint": "fp.test",
            }
        },
    )

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.open-modal",
                "request_id": "req.open-modal",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    assert candidates
    candidate = candidates[-1]
    assert candidate["status"] == "pending"
    assert candidate["target"] == {"type": "scenario", "id": scenario_id}
    assert candidate["regex_rule"]["intent"] == "desktop.open_modal"
    assert candidate["preview"]["ok"] is True
    assert candidate["preview"]["slots"] == {"modal_id": "браузеры"}
    assert candidate["normalization"]["llm_proposal_repair"]["modal_id"] == "browsers_modal"
    assert candidate["action_candidate"]["class"] == "interface_action"
    assert candidate["action_candidate"]["intent"] == "desktop.open_modal"
    assert candidate["action_candidate"]["side_effect_class"] == "ui_navigation"
    assert candidate["action_candidate"]["owner"] == {"type": "scenario", "id": scenario_id}
    assert candidate["template_candidate"]["patch"]["intent"] == "desktop.open_modal"
    assert candidate["template_candidate"]["phrase_preview"]["ok"] is True


@pytest.mark.anyio
async def test_llm_teacher_repairs_open_modal_without_required_slot(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-open-modal-missing-slot-repair"
    scenario_id = "test_open_modal_missing_slot_repair"
    request_text = "Покажи браузеры"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        "desktop.open_modal": {
                            "actions": [
                                {
                                    "type": "callHost",
                                    "target": "desktop.modal.open",
                                    "params": {"modal_id": "$slot.modal_id", "webspace_id": "$ctx.webspace_id"},
                                }
                            ]
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

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("ui").set(txn, "current_scenario", scenario_id)
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": "desktop.open_modal",
                                    "regex_rule": {"intent": "desktop.open_modal", "pattern": r"\b(?:покажи|открой)\s+(?:браузеры)\b"},
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "slots": {},
                                    "confidence": 0.86,
                                    "notes": "LLM omitted the required modal_id capture.",
                                    "candidate": None,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(
        llm,
        "_collect_root_mcp_authoring_evidence",
        lambda **kwargs: {
            "desktop_registry_lookup": {
                "ok": True,
                "webspace_id": webspace_id,
                "lookups": {
                    "modal_id": [
                        {
                            "value": "browsers_modal",
                            "labels": ["Browsers", "браузеры"],
                            "sources": ["test"],
                        }
                    ],
                    "app_id": [],
                    "scenario_id": [],
                },
                "summary": [],
                "fingerprint": "fp.test",
            }
        },
    )

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.open-modal.missing-slot",
                "request_id": "req.open-modal.missing-slot",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    candidate = candidates[-1]
    assert candidate["status"] == "pending"
    assert candidate["regex_rule"]["intent"] == "desktop.open_modal"
    assert "(?P<modal_id>" in candidate["regex_rule"]["pattern"]
    assert candidate["preview"]["slots"] == {"modal_id": "браузеры"}
    assert candidate["normalization"]["llm_proposal_repair"]["from_preview"]["slots"] == {}


@pytest.mark.anyio
async def test_llm_teacher_repairs_open_modal_label_slot_to_canonical_modal(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-open-modal-canonical-slot-repair"
    scenario_id = "test_open_modal_canonical_slot_repair"
    request_text = "show subnet environment variables"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        "desktop.open_modal": {
                            "actions": [
                                {
                                    "type": "callHost",
                                    "target": "desktop.modal.open",
                                    "params": {"modal_id": "$slot.modal_id", "webspace_id": "$ctx.webspace_id"},
                                }
                            ]
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

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("ui").set(txn, "current_scenario", scenario_id)
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": "desktop.open_modal",
                                    "regex_rule": {
                                        "intent": "desktop.open_modal",
                                        "pattern": r"\b(?:show|open)\s+(?P<modal_id>subnet\s+environment\s+variables)\b",
                                    },
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "slots": {},
                                    "confidence": 0.7,
                                    "notes": "LLM captured a display label; AdaOS should preserve the canonical modal evidence.",
                                    "candidate": None,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(
        llm,
        "_collect_root_mcp_authoring_evidence",
        lambda **kwargs: {
            "desktop_registry_lookup": {
                "ok": True,
                "webspace_id": webspace_id,
                "lookups": {
                    "modal_id": [
                        {
                            "value": "subnet_env_modal",
                            "labels": ["Subnet Env", "subnet environment variables"],
                            "sources": ["test"],
                        }
                    ],
                    "app_id": [],
                    "scenario_id": [],
                },
                "summary": [],
                "fingerprint": "fp.subnet-env",
            }
        },
    )

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.subnet-env",
                "request_id": "req.subnet-env",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    candidate = candidates[-1]
    assert candidate["status"] == "pending"
    assert candidate["regex_rule"]["intent"] == "desktop.open_modal"
    assert candidate["preview"]["slots"] == {"modal_id": "subnet environment variables"}
    assert candidate["normalization"]["llm_proposal_repair"]["modal_id"] == "subnet_env_modal"


@pytest.mark.anyio
async def test_llm_teacher_repairs_open_modal_from_catalog_app_alias_when_registry_is_empty(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-open-modal-catalog-alias-repair"
    scenario_id = "test_open_modal_catalog_alias_repair"
    request_text = "show infrastructure state"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        "desktop.open_modal": {
                            "actions": [
                                {
                                    "type": "callHost",
                                    "target": "desktop.modal.open",
                                    "params": {"modal_id": "$slot.modal_id", "webspace_id": "$ctx.webspace_id"},
                                }
                            ]
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

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("ui").set(txn, "current_scenario", scenario_id)
            ydoc.get_map("data").set(
                txn,
                "catalog",
                {
                    "apps": [
                        {
                            "id": "infrastate_app",
                            "title": "Infra State",
                            "launchModal": "infrastate_modal",
                            "aliases": ["infrastructure"],
                        }
                    ]
                },
            )
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": "desktop.open_modal",
                                    "regex_rule": {
                                        "intent": "desktop.open_modal",
                                        "pattern": r"\b(?:show|open)\s+(?P<modal_id>Infra\s*State)\b",
                                    },
                                    "target": {"type": "scenario", "id": scenario_id},
                                    "examples": [request_text],
                                    "slots": {},
                                    "confidence": 0.8,
                                    "notes": "LLM selected the right modal but used a display label that does not match the phrase.",
                                    "candidate": None,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(
        llm,
        "_collect_root_mcp_authoring_evidence",
        lambda **kwargs: {
            "desktop_registry_lookup": {
                "ok": True,
                "webspace_id": webspace_id,
                "lookups": {"modal_id": [], "app_id": [], "scenario_id": []},
                "summary": [],
                "fingerprint": "fp.empty",
            }
        },
    )

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.infrastructure-state",
                "request_id": "req.infrastructure-state",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    candidate = candidates[-1]
    assert candidate["status"] == "pending"
    assert candidate["regex_rule"]["intent"] == "desktop.open_modal"
    assert candidate["preview"]["ok"] is True
    assert candidate["preview"]["slots"] == {"modal_id": "infrastructure state"}
    assert candidate["normalization"]["llm_proposal_repair"]["modal_id"] == "infrastate_modal"
    assert candidate["normalization"]["llm_proposal_repair"]["matched"] == "catalog.app.launchModal"


@pytest.mark.anyio
async def test_llm_teacher_repairs_open_modal_from_llm_action_params_when_phrase_is_localized(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-open-modal-action-param-repair"
    scenario_id = "test_open_modal_action_param_repair"
    entity = "\u0441\u043b\u0430\u0439\u0434\u0448\u043e\u0443"
    request_text = f"\u041f\u043e\u043a\u0430\u0436\u0438 {entity}"
    pattern = rf"\b(?:\u043f\u043e\u043a\u0430\u0436\u0438|\u043e\u0442\u043a\u0440\u043e\u0439|show|launch)\s+(?P<modal_id>{entity})\b"

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        "desktop.open_modal": {
                            "actions": [
                                {
                                    "type": "callHost",
                                    "target": "desktop.modal.open",
                                    "params": {"modal_id": "$slot.modal_id", "webspace_id": "$ctx.webspace_id"},
                                }
                            ]
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

    async with async_get_ydoc(webspace_id) as ydoc:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("ui").set(txn, "current_scenario", scenario_id)
            ydoc.get_map("data").set(
                txn,
                "catalog",
                {
                    "apps": [
                        {
                            "id": "slideshow_skill_app",
                            "title": "Slideshow",
                            "launchModal": "slideshow_modal",
                            "action": {"openModal": "slideshow_modal"},
                        }
                    ]
                },
            )
            ydoc.get_map("data").set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": "desktop.open_modal",
                                    "regex_rule": {"intent": "desktop.open_modal", "pattern": pattern},
                                    "target": {"type": "skill", "id": "slideshow_skill"},
                                    "action_candidate": {
                                        "type": "callHost",
                                        "target": "desktop.modal.open",
                                        "params": {"modal_id": "slideshow", "webspace_id": webspace_id},
                                    },
                                    "examples": [request_text],
                                    "slots": {},
                                    "confidence": 0.6,
                                    "notes": "LLM selected the slideshow app and provided an English action parameter.",
                                    "candidate": None,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(
        llm,
        "_collect_root_mcp_authoring_evidence",
        lambda **kwargs: {
            "desktop_registry_lookup": {
                "ok": True,
                "webspace_id": webspace_id,
                "lookups": {"modal_id": [], "app_id": [], "scenario_id": []},
                "summary": [],
                "fingerprint": "fp.empty",
            }
        },
    )

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.slideshow-localized",
                "request_id": "req.slideshow-localized",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    candidate = candidates[-1]
    assert candidate["status"] == "pending"
    assert candidate["regex_rule"]["intent"] == "desktop.open_modal"
    assert candidate["preview"]["ok"] is True
    assert candidate["preview"]["slots"] == {"modal_id": entity}
    repair = candidate["normalization"]["llm_proposal_repair"]
    assert repair["modal_id"] == "slideshow_modal"
    assert repair["matched"] == "llm.action_candidate.params.modal_id"


@pytest.mark.anyio
async def test_llm_teacher_repairs_scenario_switch_target_and_slot_alias(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import llm_teacher_runtime as llm
    from adaos.services.yjs.doc import async_get_ydoc

    ctx = get_ctx()
    webspace_id = "ws-test-scenario-switch-alias"
    owner_scenario_id = "test_scenario_switch_alias_owner"
    opened_scenario_id = "infrascope"
    request_text = "show Infrascope"
    intent_name = "desktop.open_scenario"
    pattern = r"\b(?:show|open)\s+(?P<scenario>infrascope)\b"

    owner_root = Path(ctx.paths.scenarios_dir()) / owner_scenario_id
    owner_root.mkdir(parents=True, exist_ok=True)
    (owner_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": owner_scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        intent_name: {
                            "actions": [
                                {
                                    "type": "callHost",
                                    "target": "desktop.scenario.set",
                                    "params": {"scenario_id": "$slot.scenario_id"},
                                }
                            ]
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

    async with async_get_ydoc(webspace_id) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        with ydoc.begin_transaction() as txn:
            ui_map.set(txn, "current_scenario", owner_scenario_id)
            data_map.set(txn, "nlu_teacher", {"candidates": [], "llm_logs": []})
            data_map.set(
                txn,
                "catalog",
                {
                    "apps": [
                        {
                            "id": f"scenario:{opened_scenario_id}",
                            "title": "Infrascope",
                            "scenario_id": opened_scenario_id,
                        }
                    ]
                },
            )

    async def _fake_llm_call(messages, *, request_id=None):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "propose_regex_rule",
                                    "intent": intent_name,
                                    "regex_rule": {"intent": intent_name, "pattern": pattern},
                                    "target": {"type": "scenario", "id": opened_scenario_id},
                                    "examples": [request_text],
                                    "slots": {"scenario": {"type": "string"}},
                                    "confidence": 0.82,
                                    "notes": "Scenario switch target/slot alias repair.",
                                    "candidate": None,
                                }
                            ),
                        }
                    ]
                }
            ]
        }

    monkeypatch.setattr(llm, "_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_LLM_TEACHER_ENABLED", True)
    monkeypatch.setattr(llm, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(llm, "_collect_root_mcp_authoring_evidence", lambda **kwargs: {})

    await llm._on_teacher_request(
        {
            "webspace_id": webspace_id,
            "request": {
                "id": "teach.alias",
                "request_id": "req.alias",
                "text": request_text,
                "reason": "fallback",
                "via": "rasa",
            },
        }
    )

    async with async_get_ydoc(webspace_id) as ydoc:
        teacher = ydoc.get_map("data").get("nlu_teacher") or {}
        candidates = list((teacher or {}).get("candidates") or [])

    assert candidates
    candidate = candidates[-1]
    assert candidate["target"] == {"type": "scenario", "id": owner_scenario_id}
    assert "(?P<scenario_id>" in candidate["regex_rule"]["pattern"]
    assert candidate["slots"] == {"scenario_id": {"type": "string"}}
    assert candidate["normalization"]["slot_aliases"] == {"scenario": "scenario_id"}
    assert candidate["preview"]["ok"] is True
    assert candidate["preview"]["slots"] == {"scenario_id": "Infrascope"}
