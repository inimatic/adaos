from copy import deepcopy
from dataclasses import replace
import json

import pytest

from adaos.domain.notifications import NotificationConflict, NotificationError
from adaos.services import conversation_store, conversation_interactions
from adaos.services.notifications.audiences import emit_to_source
from adaos.services.notifications.runtime import NotificationService
from test_notification_runtime import notification_runtime


def audience(service, binding, manifest, *, policy="authorized_assignees", refs=("user:bob", "user:carol"), owner=None):
    declaration = deepcopy(manifest)
    category = declaration["categories"][0]
    category["recipient_policy"] = policy
    category["source_binding"] = {"kind": "interaction", "payload_field": "interaction_id"}
    category["payload_schema"]["properties"]["interaction_id"] = {"type": "string"}
    category["payload_schema"]["required"].append("interaction_id")
    bound = replace(binding, release_digest="sha256:source-audience")
    service.admit_catalog(bound, declaration)
    conversation_store.upsert_conversation(conversation_id="audience:review", owner=owner or binding.author_ref, webspace_id="test")
    source = conversation_interactions.create_interaction(conversation_id="audience:review", owner=owner or binding.author_ref,
        prompt="Review document", interaction_id="interaction:source-audience",
        actions=[{"action_id": "review", "label": "Review", "command": "review", "risk": "read", "principal_scope": list(refs)}],
        metadata={"participants": [binding.principal_ref, *refs]})
    request = dict(category_id=category["id"], event_id="fanout:E1", source_id="source:document", source_revision=1,
        occurred_at=service.clock(), payload={"title": "Document", "interaction_id": source["interaction_id"]})
    return bound, source, request


def update_source(service, source):
    with service.store.connect() as con:
        con.execute("UPDATE conversation_interactions SET payload_json=? WHERE interaction_id=?", (json.dumps(source), source["interaction_id"]))
        con.commit()


def test_owned_source_pages_freeze_audience_and_restart_replay_exact_receipts(notification_runtime):
    service, binding, manifest, _ = notification_runtime
    binding, source, request = audience(service, binding, manifest)
    first = emit_to_source(service, binding, limit=1, **request)
    from adaos.domain.notifications import validate
    validate("audience_receipt", first)
    assert len(first["receipts"]) == 1 and first["next_cursor"]
    restarted = NotificationService(clock=service.clock)
    assert emit_to_source(restarted, binding, limit=1, **request) == first
    source["actions"][0]["principal_scope"].append("user:dave")
    source["metadata"]["participants"].append("user:dave")
    update_source(service, source)
    second = emit_to_source(restarted, binding, limit=1, cursor=first["next_cursor"], **request)
    assert len(second["receipts"]) == 1 and second["next_cursor"] is None
    assert len(service.list("user:bob")["items"]) == len(service.list("user:carol")["items"]) == 1
    assert service.list("user:dave")["items"] == []
    assert service.list(binding.principal_ref)["items"] == []
    with pytest.raises(NotificationConflict):
        emit_to_source(service, binding, **{**request, "payload": {**request["payload"], "title": "Changed"}})
    with pytest.raises(NotificationError):
        emit_to_source(service, binding, **{**request, "event_id": "E2"}, cursor=first["next_cursor"])


def test_revoked_source_access_disappears_from_private_queries_and_later_fanout(notification_runtime):
    service, binding, manifest, _ = notification_runtime
    binding, source, request = audience(service, binding, manifest)
    first = emit_to_source(service, binding, limit=1, **request)
    read_request = dict(operation_key="source-read", expected_revision=0, action="read", value=1)
    service.recipient_command("user:bob", first["receipts"][0]["notification_id"], **read_request)
    source["actions"][0]["principal_scope"] = ["user:dave"]
    source["metadata"]["participants"] = [binding.principal_ref, "user:dave"]
    update_source(service, source)
    assert service.get("user:bob", first["receipts"][0]["notification_id"]) is None
    assert service.list("user:bob")["items"] == []
    assert service.list("user:bob", _export=True)["items"] == []
    with pytest.raises(PermissionError):
        service.recipient_command("user:bob", first["receipts"][0]["notification_id"], **read_request)
    second = emit_to_source(service, binding, limit=1, cursor=first["next_cursor"], **request)
    assert second["skipped_count"] == 1 and second["receipts"] == []
    assert service.list("user:dave")["items"] == []


def test_foreign_source_and_raw_recipient_identifiers_do_not_grant_authority(notification_runtime):
    service, binding, manifest, _ = notification_runtime
    bound, _, request = audience(service, binding, manifest, owner="skill:foreign")
    with pytest.raises(PermissionError):
        emit_to_source(service, bound, **request)
    with pytest.raises(PermissionError):
        service.emit(replace(bound, admitted_recipients=("user:bob",)), recipient_ref="user:bob", **request)


def test_erasure_cannot_reintroduce_recipient_through_source_outbox_replay(notification_runtime):
    service, binding, manifest, _ = notification_runtime
    bound, _, request = audience(service, binding, manifest)
    first = emit_to_source(service, bound, limit=1, **request)
    service.forget_user("user:bob")
    assert emit_to_source(service, bound, limit=1, **request)["receipts"] == []
    second = emit_to_source(service, bound, limit=1, cursor=first["next_cursor"], **request)
    assert len(second["receipts"]) == 1
    assert len(service.list("user:carol")["items"]) == 1
    assert service.list("user:bob")["items"] == []


def test_shared_source_means_private_recipient_cards_and_not_group_export(notification_runtime):
    service, binding, manifest, _ = notification_runtime
    bound, _, request = audience(service, binding, manifest, policy="shared_audience")
    result = emit_to_source(service, bound, **request)
    assert len(result["receipts"]) == 3
    with service.store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM conversation_output_intents").fetchone()[0] == 0
