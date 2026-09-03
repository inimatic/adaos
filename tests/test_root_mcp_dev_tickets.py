from __future__ import annotations

from adaos.services.root_mcp import service as root_mcp_service


def _invoke(tool_id: str, arguments: dict, capability: str):
    return root_mcp_service.invoke_tool(
        tool_id,
        arguments=arguments,
        actor="codex:test",
        auth_method="mcp_session",
        auth_context={"capabilities": [capability]},
    )


def test_root_mcp_dev_ticket_read_tools(monkeypatch) -> None:
    from adaos.sdk import development_tickets as sdk

    ticket = {"ticket_id": "dticket.test", "summary": "Demo", "status": "accepted"}
    event = {"event_id": "dtevent.test", "ticket_id": "dticket.test"}
    monkeypatch.setattr(sdk, "list_tickets", lambda **filters: [{**ticket, "filters": filters}])
    monkeypatch.setattr(sdk, "get_ticket", lambda ticket_id: ticket if ticket_id == "dticket.test" else None)
    monkeypatch.setattr(
        sdk,
        "list_core_backlog",
        lambda **filters: {"items": [{"ticket_id": "dticket.core", "filters": filters}], "count": 1},
    )
    monkeypatch.setattr(sdk, "list_events", lambda **filters: [{**event, "filters": filters}])
    monkeypatch.setattr(
        sdk,
        "read_feed",
        lambda **filters: {
            "schema": "adaos.dev_ticket.change_feed.v1",
            "snapshot": [],
            "events": [{**event, "filters": filters}],
            "cursor": "dtevent.test",
        },
    )
    monkeypatch.setattr(sdk, "list_artifacts", lambda ticket_id=None: [{"artifact_id": "artifact.test", "ticket_id": ticket_id}])
    monkeypatch.setattr(sdk, "get_artifact", lambda artifact_id: {"artifact_id": artifact_id, "exists": True})

    listed = _invoke(
        "dev_ticket.list",
        {"status_group": "open", "component_ref": "skill:demo", "limit": 5},
        "development.read.tickets",
    )
    shown = _invoke("dev_ticket.show", {"ticket_id": "dticket.test"}, "development.read.tickets")
    backlog = _invoke(
        "dev_ticket.core_backlog",
        {"impact": "blocker", "affected_project_id": "demo"},
        "development.read.tickets",
    )
    events = _invoke(
        "dev_ticket.events",
        {"after": "dtevent.previous", "limit": 5},
        "development.read.tickets",
    )
    artifacts = _invoke(
        "dev_ticket.artifacts",
        {"ticket_id": "dticket.test"},
        "development.read.ticket_artifacts",
    )
    artifact = _invoke(
        "dev_ticket.get_artifact",
        {"artifact_id": "artifact.test"},
        "development.read.ticket_artifacts",
    )

    assert listed.ok is True
    assert listed.result["tickets"][0]["filters"]["status_group"] == "open"
    assert shown.result["ticket"]["ticket_id"] == "dticket.test"
    assert backlog.result["items"][0]["filters"]["impact"] == "blocker"
    assert events.result["cursor"] == "dtevent.test"
    assert artifacts.result["artifacts"][0]["ticket_id"] == "dticket.test"
    assert artifact.result["artifact"]["exists"] is True


def test_root_mcp_dev_ticket_write_tools_and_dry_run(monkeypatch) -> None:
    from adaos.sdk import development_tickets as sdk

    calls: list[tuple[str, dict]] = []

    def _create(summary: str, **request):
        calls.append(("create", {"summary": summary, **request}))
        return {"ok": True, "ticket": {"ticket_id": "dticket.new"}}

    def _operate(ticket_id: str, operation: str, **request):
        calls.append(("operate", {"ticket_id": ticket_id, "operation": operation, **request}))
        return {"ok": True, "ticket": {"ticket_id": ticket_id, "status": "claimed"}}

    monkeypatch.setattr(sdk, "create_ticket", _create)
    monkeypatch.setattr(sdk, "operate_ticket", _operate)

    dry_run = root_mcp_service.invoke_tool(
        "dev_ticket.create",
        arguments={"summary": "Review Demo Metrics", "component_ref": "skill:demo_metrics_skill"},
        actor="codex:test",
        auth_method="mcp_session",
        auth_context={"capabilities": ["development.write.tickets"]},
        dry_run=True,
    )
    created = _invoke(
        "dev_ticket.create",
        {"summary": "Review Demo Metrics", "component_ref": "skill:demo_metrics_skill", "actor": "codex:test"},
        "development.write.tickets",
    )
    operated = _invoke(
        "dev_ticket.operate",
        {
            "ticket_id": "dticket.new",
            "operation": "related",
            "actor": "codex:test",
            "expected_revision": 3,
            "payload": {"related_ticket_id": "dticket.parent", "relation": "blocks"},
        },
        "development.write.tickets",
    )

    assert dry_run.result["would_create"] is True
    assert calls[0][0] == "create"
    assert calls[1][1]["operation"] == "related"
    assert calls[1][1]["expected_revision"] == 3
    assert created.result["ticket"]["ticket_id"] == "dticket.new"
    assert operated.result["ticket"]["status"] == "claimed"


def test_root_mcp_dev_ticket_capabilities_are_explicit() -> None:
    contracts = {item.id: item for item in root_mcp_service.list_tool_contracts()}

    assert contracts["dev_ticket.list"].required_capability == "development.read.tickets"
    assert contracts["dev_ticket.core_backlog"].required_capability == "development.read.tickets"
    assert contracts["dev_ticket.get_artifact"].required_capability == "development.read.ticket_artifacts"
    assert contracts["dev_ticket.operate"].required_capability == "development.write.tickets"
    assert contracts["dev_ticket.operate"].side_effects == "write"


def test_root_mcp_development_feedback_tools(monkeypatch) -> None:
    from adaos.sdk import development_feedback as sdk

    feedback = {
        "feedback_id": "devfeedback.test",
        "category": "ambiguous_contract",
        "status": "observed",
    }
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(sdk, "list_feedback", lambda **filters: [{**feedback, "filters": filters}])
    monkeypatch.setattr(sdk, "get_feedback", lambda feedback_id: feedback if feedback_id == "devfeedback.test" else None)
    monkeypatch.setattr(
        sdk,
        "capture_feedback",
        lambda summary, **request: calls.append(("capture", {"summary": summary, **request}))
        or {"ok": True, "feedback": feedback},
    )
    monkeypatch.setattr(
        sdk,
        "transition_feedback",
        lambda feedback_id, status, **request: calls.append(
            ("transition", {"feedback_id": feedback_id, "status": status, **request})
        )
        or {**feedback, "status": status},
    )

    listed = _invoke(
        "development_feedback.list",
        {"category": "ambiguous_contract", "search": "SDK"},
        "development.read.feedback",
    )
    shown = _invoke(
        "development_feedback.show",
        {"feedback_id": "devfeedback.test"},
        "development.read.feedback",
    )
    captured = _invoke(
        "development_feedback.capture",
        {
            "summary": "SDK behavior is ambiguous",
            "source": "codex",
            "category": "ambiguous_contract",
            "target_refs": ["sdk:resources.query"],
        },
        "development.write.feedback",
    )
    accepted = _invoke(
        "development_feedback.operate",
        {
            "feedback_id": "devfeedback.test",
            "operation": "accept",
            "expected_revision": 2,
        },
        "development.write.feedback",
    )
    contracts = {item.id: item for item in root_mcp_service.list_tool_contracts()}

    assert listed.result["items"][0]["filters"]["search"] == "SDK"
    assert shown.result["feedback"]["feedback_id"] == "devfeedback.test"
    assert captured.result["feedback"]["category"] == "ambiguous_contract"
    assert accepted.result["feedback"]["status"] == "accepted"
    assert calls[1][1]["expected_revision"] == 2
    assert contracts["development_feedback.list"].required_capability == "development.read.feedback"
    assert contracts["development_feedback.operate"].required_capability == "development.write.feedback"


def test_root_mcp_builder_source_recovery_is_typed_and_governed(monkeypatch) -> None:
    from adaos.sdk.builder import source_recovery as sdk
    from adaos.services.root_mcp.policy import list_capability_classes
    from adaos.services.root_mcp.sessions import DEFAULT_CAPABILITY_PROFILES

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        sdk,
        "plan",
        lambda **request: {
            "schema": "adaos.builder.source_recovery_plan.v1",
            "plan_digest": "sha256:" + "a" * 64,
            "request": request,
        },
    )

    def _apply(**request):
        calls.append(("apply", request))
        return {"schema": "adaos.builder.source_recovery_receipt.v1", "status": "applied_to_dev"}

    monkeypatch.setattr(sdk, "apply", _apply)
    planned = _invoke(
        "builder.source_recovery.plan",
        {"kind": "project", "artifact_id": "demo"},
        "development.read.descriptors",
    )
    dry_run = root_mcp_service.invoke_tool(
        "builder.source_recovery.apply",
        arguments={
            "kind": "project",
            "artifact_id": "demo",
            "expected_plan_digest": "sha256:" + "a" * 64,
        },
        actor="codex:test",
        auth_method="mcp_session",
        auth_context={"capabilities": ["development.write.source_recovery"]},
        dry_run=True,
    )
    applied = _invoke(
        "builder.source_recovery.apply",
        {
            "kind": "project",
            "artifact_id": "demo",
            "expected_plan_digest": "sha256:" + "a" * 64,
            "decisions": {"scenario:demo": "keep_dev"},
        },
        "development.write.source_recovery",
    )
    contracts = {item.id: item for item in root_mcp_service.list_tool_contracts()}
    capabilities = {item["capability"] for item in list_capability_classes()}

    assert planned.result["plan"]["request"]["artifact_id"] == "demo"
    assert dry_run.result["would_apply"] is True
    assert calls[0][1]["actor"] == "builder.mcp"
    assert applied.result["receipt"]["status"] == "applied_to_dev"
    assert contracts["builder.source_recovery.apply"].required_capability == (
        "development.write.source_recovery"
    )
    assert contracts["builder.source_recovery.apply"].side_effects == "write"
    assert "development.write.source_recovery" in capabilities
    assert "development.write.source_recovery" in DEFAULT_CAPABILITY_PROFILES["BuilderDeveloper"]
