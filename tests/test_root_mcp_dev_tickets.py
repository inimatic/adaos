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
    monkeypatch.setattr(sdk, "list_events", lambda **filters: [{**event, "filters": filters}])
    monkeypatch.setattr(sdk, "list_artifacts", lambda ticket_id=None: [{"artifact_id": "artifact.test", "ticket_id": ticket_id}])
    monkeypatch.setattr(sdk, "get_artifact", lambda artifact_id: {"artifact_id": artifact_id, "exists": True})

    listed = _invoke(
        "dev_ticket.list",
        {"status_group": "open", "component_ref": "skill:demo", "limit": 5},
        "development.read.tickets",
    )
    shown = _invoke("dev_ticket.show", {"ticket_id": "dticket.test"}, "development.read.tickets")
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
        {"ticket_id": "dticket.new", "operation": "claim", "actor": "codex:test"},
        "development.write.tickets",
    )

    assert dry_run.result["would_create"] is True
    assert calls[0][0] == "create"
    assert calls[1][1]["operation"] == "claim"
    assert created.result["ticket"]["ticket_id"] == "dticket.new"
    assert operated.result["ticket"]["status"] == "claimed"


def test_root_mcp_dev_ticket_capabilities_are_explicit() -> None:
    contracts = {item.id: item for item in root_mcp_service.list_tool_contracts()}

    assert contracts["dev_ticket.list"].required_capability == "development.read.tickets"
    assert contracts["dev_ticket.get_artifact"].required_capability == "development.read.ticket_artifacts"
    assert contracts["dev_ticket.operate"].required_capability == "development.write.tickets"
    assert contracts["dev_ticket.operate"].side_effects == "write"
