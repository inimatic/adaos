from copy import deepcopy
from dataclasses import replace

import pytest

from adaos.domain.notifications import NotificationConflict, NotificationError, ProducerBinding
from adaos.services.agent_context import get_ctx
from adaos.services.notifications.runtime import NotificationService


@pytest.fixture
def notification_runtime():
    clock = [1794168000.0]  # 2026-11-08 20:00 UTC
    binding = ProducerBinding("reviews", "instance:reviews", "installed", "sha256:release1",
                              "user:alice", "subnet:A", "agent:reviewer", "Reviewer")
    category = {
        "id": "review.completed", "title_key": "review.title", "purpose_key": "review.purpose",
        "source_event": "review.completed.v1",
        "payload_schema": {"type": "object", "properties": {"title": {"type": "string"}},
                           "required": ["title"], "additionalProperties": False},
        "recipient_policy": "current_user", "default_mode": "center_only", "maximum_urgency": "important",
        "sensitivity": "private", "template": {"en": "Completed: {title}", "ru": "Готово: {title}"},
        "lifetime_seconds": 86400, "grouping": {"replacement": "same_source_object"},
    }
    service = NotificationService(clock=lambda: clock[0])
    manifest = {"schema": "adaos.notifications.manifest.v1", "categories": [category]}
    service.admit_catalog(binding, manifest)
    return service, binding, manifest, clock


def emit(service, binding, **patch):
    values = dict(category_id="review.completed", event_id="E1", source_id="R1",
                  source_revision=1, payload={"title": "Document"})
    values.update(patch)
    return service.emit(binding, **values)


def test_source_commit_and_intent_are_atomic_with_restart_and_retry(notification_runtime):
    service, binding, _, clock = notification_runtime
    with service.store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        receipt = emit(service, binding, connection=con)
        con.rollback()
    assert service.get("user:alice", receipt["notification_id"]) is None
    receipt = emit(service, binding)
    clock[0] += 100
    restarted = NotificationService(clock=lambda: clock[0])
    assert emit(restarted, binding) == receipt
    assert len(restarted.list("user:alice")["items"]) == 1
    with pytest.raises(NotificationConflict):
        emit(restarted, binding, payload={"title": "Substituted"})


def test_private_queries_and_foreign_recipient_denial(notification_runtime):
    service, binding, _, _ = notification_runtime
    receipt = emit(service, binding)
    assert service.get("user:bob", receipt["notification_id"]) is None
    assert service.list("user:bob")["items"] == []
    with pytest.raises(PermissionError):
        emit(service, binding, recipient_ref="user:bob")
    with pytest.raises(PermissionError):
        service.recipient_command("user:bob", receipt["notification_id"],
            operation_key="foreign", expected_revision=0, action="read", value=1)
    assert "producer_binding" not in service.list("user:alice")["items"][0]
    # No notification projection is written to any shared desktop.
    assert get_ctx().kv.list("webspaces/") == []


def test_read_archive_snooze_have_their_own_revision_and_no_source_effect(notification_runtime):
    service, binding, _, clock = notification_runtime
    receipt = emit(service, binding)
    result = service.recipient_command("user:alice", receipt["notification_id"],
        operation_key="read1", expected_revision=0, action="read", value=1)
    assert result["read_revision"] == 1 and result["source_state"] == "active"
    assert service.recipient_command("user:alice", receipt["notification_id"],
        operation_key="read1", expected_revision=0, action="read", value=1) == result
    with pytest.raises(NotificationConflict):
        service.recipient_command("user:alice", receipt["notification_id"],
            operation_key="stale", expected_revision=0, action="archive", value=True)
    archived = service.recipient_command("user:alice", receipt["notification_id"],
        operation_key="archive", expected_revision=1, action="archive", value=True)
    assert archived["archived"] and archived["source_state"] == "active"
    with pytest.raises(NotificationError):
        service.recipient_command("user:alice", receipt["notification_id"],
            operation_key="long-snooze", expected_revision=2, action="snooze",
            value=archived["expires_at"] + 1)
    emit(service, binding, event_id="E2", source_revision=2, payload={"title": "Updated"})
    updated = service.get("user:alice", receipt["notification_id"])
    assert updated["archived"] and updated["read_revision"] == 2
    assert updated["source_revision"] == 2


def test_immutable_catalogs_and_governed_upgrade_preserve_mute(notification_runtime):
    service, binding, manifest, _ = notification_runtime
    preferences = service.preferences.read("user:alice", "subnet:A")
    preferences["rules"] = [{"scope": {"category_id": "review.completed"},
                           "choice": {"mode": "off", "endpoint_ids": []}}]
    service.preferences.write("user:alice", "subnet:A", preferences, expected_revision=0)
    manifest["categories"][0]["title_key"] = "renamed.title"
    with pytest.raises(NotificationConflict):
        service.admit_catalog(binding, manifest)
    beta = replace(binding, release_digest="sha256:beta1")
    service.admit_catalog(beta, manifest)
    result = emit(service, beta)
    assert result["status"] == "suppressed"
    assert service.list("user:alice")["items"] == []
    assert service.get("user:alice", result["notification_id"])["text"] == ""


def test_source_withdrawal_and_late_progress_cannot_resurrect_notice(notification_runtime):
    service, binding, _, _ = notification_runtime
    receipt = emit(service, binding)
    assert service.withdraw(binding, "R1") == 1
    with pytest.raises(NotificationConflict):
        emit(service, binding, event_id="E2", source_revision=2)
    assert service.get("user:alice", receipt["notification_id"])["source_state"] == "withdrawn"


def test_expiry_uses_source_time_and_pagination_is_bounded(notification_runtime):
    service, binding, _, clock = notification_runtime
    receipt = emit(service, binding, occurred_at=clock[0] - 90000)
    assert service.get("user:alice", receipt["notification_id"])["decision"]["reason"] == "expired"
    for i in range(5):
        clock[0] += 1
        emit(service, binding, source_id=f"R{i+2}", event_id=f"E{i+2}")
    page = service.list("user:alice", limit=2)
    second = service.list("user:alice", limit=2, cursor=page["next_cursor"])
    assert len(page["items"]) == len(second["items"]) == 2
    assert {x["notification_id"] for x in page["items"]}.isdisjoint(
        x["notification_id"] for x in second["items"])
    with pytest.raises(NotificationError):
        service.list("user:alice", limit=1000)
