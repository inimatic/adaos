from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.sdk import development_tickets as sdk
from adaos.services.development_tickets import DevelopmentTicketService


def _ticket(service: DevelopmentTicketService, summary: str, project_id: str) -> dict:
    signal = service.capture_signal(
        kind="development_request",
        summary=summary,
        target_scope={
            "type": "skill",
            "id": f"{project_id}_skill",
            "project_id": project_id,
            "project_ref": f"project:{project_id}",
        },
        source="test",
    )["signal"]
    return service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="accepted",
    )["ticket"]


def test_sdk_operates_relations_with_optimistic_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    monkeypatch.setattr(sdk, "_service", lambda: service)
    source = _ticket(service, "Project symptom", "demo")
    blocker = _ticket(service, "Core blocker", "core")

    related = sdk.operate_ticket(
        source["ticket_id"],
        "related",
        actor="builder:test",
        expected_revision=source["revision"],
        payload={"related_ticket_id": blocker["ticket_id"], "relation": "blocked_by"},
    )["ticket"]

    assert related["revision"] == source["revision"] + 1
    assert related["relation_refs"][0]["ticket_id"] == blocker["ticket_id"]
    with pytest.raises(ValueError, match="revision conflict"):
        sdk.operate_ticket(
            source["ticket_id"],
            "duplicate",
            actor="builder:test",
            expected_revision=source["revision"],
            payload={"duplicate_of": blocker["ticket_id"]},
        )


def test_sdk_reads_relevant_snapshot_and_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    monkeypatch.setattr(sdk, "_service", lambda: service)
    ticket = _ticket(service, "Demo change", "demo")

    initial = sdk.read_feed(project_id="demo")
    service.comment_ticket(ticket["ticket_id"], body="Builder started", actor="builder:test")
    changed = sdk.read_feed(project_id="demo", after=initial["cursor"])

    assert [item["ticket_id"] for item in initial["snapshot"]] == [ticket["ticket_id"]]
    assert changed["snapshot"] == []
    assert changed["events"][-1]["ticket_id"] == ticket["ticket_id"]


def test_legacy_ticket_without_revision_is_migrated_on_read(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    ticket = _ticket(service, "Legacy ticket", "legacy")
    state = json.loads(service.state_path.read_text(encoding="utf-8"))
    state["tickets"][ticket["ticket_id"]].pop("revision")
    service.state_path.write_text(json.dumps(state), encoding="utf-8")

    migrated = service.get_ticket(ticket["ticket_id"])
    updated = service.comment_ticket(
        ticket["ticket_id"],
        body="Revision migration works",
        actor="test",
        expected_revision=1,
    )

    assert migrated["revision"] == 1
    assert updated["revision"] == 2
