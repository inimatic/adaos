from dataclasses import replace
import json

import pytest

from adaos.domain.notifications import NotificationConflict
from adaos.services import conversation_delivery, max_delivery
from adaos.services.notifications import delivery, retention
from adaos.services.notifications.runtime import NotificationService
from test_notification_runtime import notification_runtime, emit
from test_notification_delivery import configure, Adapter
from test_notification_digest_and_device import digest_mode
from test_sdk_notifications import sdk_notification_scope


def drain(service):
    # Every page is independently committed and can resume in a fresh owner.
    for _ in range(100):
        service = NotificationService(service.ctx, clock=service.clock)
        if not retention.scrub_pending(service, limit=1):
            return
    pytest.fail("bounded retention fixture did not drain")


def test_retention_read_fence_precedes_gc_and_idle_work_stays_indexed(notification_runtime):
    service, binding, _, clock = notification_runtime
    first = emit(service, binding, payload={"title": "PRIVATE_RETIRED"})
    record = service.get(binding.principal_ref, first["notification_id"])
    clock[0] = record["expires_at"] + retention.CONTENT_RETENTION_SECONDS - 1
    assert service.get(binding.principal_ref, first["notification_id"])
    clock[0] += 1
    assert service.get(binding.principal_ref, first["notification_id"]) is None
    assert service.list(binding.principal_ref)["items"] == []
    assert service.list(binding.principal_ref, _export=True)["items"] == []
    second = emit(service, replace(binding, principal_ref="user:bob"), event_id="bob", source_id="bob")
    assert retention.retire_due(service, limit=1) == 1
    assert service.get("user:bob", second["notification_id"])
    drain(service)
    assert emit(service, binding, payload={"title": "PRIVATE_RETIRED"}) == first  # exact outbox retry cannot restore text
    with service.store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM notification_record_tombstones").fetchone()[0] == 1
        plan = con.execute("EXPLAIN QUERY PLAN SELECT notification_id FROM notification_records WHERE expires_at<=? ORDER BY expires_at,notification_id LIMIT 25", (clock[0],)).fetchall()
        assert any("idx_notification_content_retention" in row[3] for row in plan)
    traced = []
    connect = service.store.connect
    def tracing(**kwargs):
        con = connect(**kwargs)
        con.set_trace_callback(traced.append)
        return con
    service.store.connect = tracing
    assert retention.retire_due(service) == retention.scrub_pending(service) == 0
    assert not any(sql.startswith(("BEGIN", "UPDATE", "INSERT", "DELETE", "CREATE")) for sql in traced)


def test_old_revisions_stay_retired_but_new_source_work_does_not_lose_its_snapshot(notification_runtime):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding, transports=("telegram",))
    first = emit(service, binding, payload={"title": "OLD_PRIVATE_TEXT"})
    expires = service.get(binding.principal_ref, first["notification_id"])["expires_at"]
    service.recipient_command(binding.principal_ref, first["notification_id"], operation_key="old-read",
                              expected_revision=0, action="read", value=1)
    clock[0] = expires + retention.CONTENT_RETENTION_SECONDS
    assert retention.retire_due(service) == 1
    assert emit(service, binding, event_id="old-copy", occurred_at=expires - 86400)["status"] == "stale"
    assert emit(service, binding, event_id="late-update", source_revision=2, occurred_at=expires - 1)["status"] == "suppressed"
    assert service.get(binding.principal_ref, first["notification_id"]) is None
    fresh = emit(service, binding, event_id="fresh", source_revision=3, payload={"title": "NEW_WORK"})
    assert fresh["notification_id"] == first["notification_id"] and fresh["content_revision"] == 2
    # The earlier cached command must not restore its retired response even
    # while a new version of the same card already exists.
    with pytest.raises(PermissionError, match="expired"):
        service.recipient_command(binding.principal_ref, first["notification_id"], operation_key="old-read",
                                  expected_revision=0, action="read", value=1)
    drain(service)
    with service.store.connect() as con:
        bodies = [json.loads(row[0])["payload"]["text"] for row in con.execute("SELECT payload_json FROM conversation_response_outbox ORDER BY created_at")]
        assert len(bodies) == 2 and bodies[0] == "" and "NEW_WORK" in bodies[1]
        assert all("OLD_PRIVATE_TEXT" not in row[0] for row in con.execute("SELECT receipt_json FROM notification_user_operations"))
    assert "NEW_WORK" in service.get(binding.principal_ref, fresh["notification_id"])["text"]


def test_withdrawn_source_and_withdrawal_after_retirement_remain_fenced(notification_runtime):
    service, binding, _, clock = notification_runtime
    first = emit(service, binding)
    service.withdraw(binding, "R1")
    expires = service.get(binding.principal_ref, first["notification_id"])["expires_at"]
    clock[0] = expires + retention.CONTENT_RETENTION_SECONDS
    retention.retire_due(service)
    with pytest.raises(NotificationConflict, match="withdrawn"):
        emit(service, binding, event_id="late", source_revision=2)
    second = emit(service, binding, event_id="other", source_id="R2")
    clock[0] = service.get(binding.principal_ref, second["notification_id"])["expires_at"] + retention.CONTENT_RETENTION_SECONDS
    retention.retire_due(service)
    service.withdraw(binding, "R2")
    with pytest.raises(NotificationConflict, match="withdrawn"):
        emit(service, binding, event_id="late2", source_id="R2", source_revision=2)


def test_cleanup_page_rolls_back_atomically_and_cannot_consume_all_progress_history(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding, transports=("telegram",))
    for revision in range(1, 5):
        result = emit(service, binding, event_id=f"progress:{revision}", source_revision=revision,
                      payload={"title": f"PRIVATE_REVISION_{revision}"})
    clock[0] = service.get(binding.principal_ref, result["notification_id"])["expires_at"] + retention.CONTENT_RETENTION_SECONDS
    retention.retire_due(service)
    scrub = retention._scrub_output
    def crash(con, intent_id):
        scrub(con, intent_id)
        raise RuntimeError("crash after snapshot scrub, before page checkpoint")
    with monkeypatch.context() as scope:
        scope.setattr(retention, "_scrub_output", crash)
        with pytest.raises(RuntimeError, match="before page checkpoint"):
            retention.scrub_pending(service, limit=1)
    with service.store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM conversation_response_outbox WHERE json_extract(payload_json,'$.payload.text')<>''").fetchone()[0] == 4
        assert con.execute("SELECT after_id FROM notification_retention_work").fetchone()[0] == ""
    restarted = NotificationService(service.ctx, clock=service.clock)
    assert retention.scrub_pending(restarted, limit=1) == 1
    with service.store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM conversation_response_outbox WHERE json_extract(payload_json,'$.payload.text')<>''").fetchone()[0] == 3
    drain(restarted)


def test_non_leader_digest_member_retirement_scrubs_the_immutable_summary(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding, transports=("max",))
    monkeypatch.setattr(delivery, "live_application_authority", lambda *args: True)
    digest_mode(service, binding)
    first = emit(service, binding, payload={"title": "LEADER_REMAINS"})
    second = emit(service, binding, event_id="second", source_id="R2", payload={"title": "RETIRED_NON_LEADER"})
    # Give the leader a later retention deadline without changing summary order.
    with service.store.connect() as con:
        con.execute("UPDATE notification_records SET expires_at=expires_at+86400 WHERE notification_id=?", (first["notification_id"],))
        con.commit()
    clock[0] += 61
    assert delivery.flush_digests(service) == 1
    with service.store.connect() as con:
        leader = con.execute("SELECT notification_id FROM notification_outputs").fetchone()[0]
    # Pick the member that did not supply the output link; both must be handled.
    retired = second if leader == first["notification_id"] else first
    survivor = first if retired is second else second
    with service.store.connect() as con:
        con.execute("UPDATE notification_records SET expires_at=? WHERE notification_id=?", (clock[0]-retention.CONTENT_RETENTION_SECONDS, retired["notification_id"]))
        # The current roster is mutable; GC must use the old output's frozen
        # membership, including a removed non-leader.
        con.execute("DELETE FROM notification_digest_members WHERE notification_id=?", (retired["notification_id"],))
        con.commit()
    assert retention.retire_due(service) == 1
    drain(service)
    with service.store.connect() as con:
        assert json.loads(con.execute("SELECT payload_json FROM conversation_response_outbox").fetchone()[0])["payload"]["text"] == ""
        assert con.execute("SELECT content_json FROM notification_digest_windows").fetchone()[0] is None
    assert service.get(binding.principal_ref, survivor["notification_id"])
    adapter = Adapter()
    assert not conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert adapter.sent == []


def test_retiring_member_preserves_a_rebuilt_summary_that_no_longer_contains_it(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding, transports=("max",))
    monkeypatch.setattr(delivery, "live_application_authority", lambda *args: True)
    digest_mode(service, binding)
    first = emit(service, binding, payload={"title": "SURVIVING_SOURCE"})
    second = emit(service, binding, event_id="second", source_id="R2", payload={"title": "RETIRED_SOURCE"})
    with service.store.connect() as con:
        con.execute("UPDATE notification_records SET expires_at=expires_at+100*86400 WHERE notification_id=?", (first["notification_id"],))
        con.commit()
    clock[0] += 61
    delivery.flush_digests(service)
    service.withdraw(binding, "R2")
    adapter = Adapter()
    assert conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert adapter.sent == []
    assert delivery.flush_digests(service) == 1  # same window, new immutable summary
    clock[0] = service.get(binding.principal_ref, second["notification_id"])["expires_at"] + retention.CONTENT_RETENTION_SECONDS
    assert retention.retire_due(service) == 1
    drain(service)
    with service.store.connect() as con:
        texts = [json.loads(row[0])["payload"]["text"] for row in con.execute("SELECT payload_json FROM conversation_response_outbox")]
    assert len(texts) == 2 and texts.count("") == 1
    remaining = next(text for text in texts if text)
    assert "SURVIVING_SOURCE" in remaining and "RETIRED_SOURCE" not in remaining


def test_v8_upgrade_recovers_immutable_digest_members_from_the_original_envelope(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, _, _ = configure(service, binding, transports=("max",))
    monkeypatch.setattr(delivery, "live_application_authority", lambda *args: True)
    digest_mode(service, binding)
    emit(service, binding)
    emit(service, binding, event_id="second", source_id="R2")
    clock[0] += 61
    delivery.flush_digests(service)
    with service.store.connect() as con:
        con.execute("DROP TABLE notification_output_members")
        con.execute("DELETE FROM notification_schema_version WHERE version=9")
        con.execute("INSERT OR IGNORE INTO notification_schema_version VALUES(8)")
        con.execute("DELETE FROM notification_digest_members")
        con.commit()
    upgraded = NotificationService(service.ctx, clock=service.clock)
    with upgraded.store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM notification_output_members").fetchone()[0] == 2
        assert con.execute("SELECT MAX(version) FROM notification_schema_version").fetchone()[0] == 9


def test_retention_scrubs_secondary_max_copy_and_preserves_late_delivery_fact(notification_runtime, monkeypatch):
    from test_max_delivery import receipt
    service, binding, _, clock = notification_runtime
    monkeypatch.setattr(max_delivery.time, "time", lambda: clock[0])
    monkeypatch.setattr(delivery, "live_application_authority", lambda *args: True)
    binding, _, _ = configure(service, binding, transports=("max",))
    result = emit(service, binding, payload={"title": "PRIVATE_QUEUED_COPY"})
    assert conversation_delivery.dispatch_once(now=clock[0])
    with service.store.connect() as con:
        attempt = dict(con.execute("SELECT * FROM max_outbound_attempts").fetchone())
    parts = len(json.loads(attempt["request_json"])["messages"])
    clock[0] = service.get(binding.principal_ref, result["notification_id"])["expires_at"] + retention.CONTENT_RETENTION_SECONDS
    assert delivery.tick(now=clock[0])  # existing production worker owns GC
    drain(service)
    max_delivery.complete(receipt(attempt, external_message_ids=["exact:late"]*parts))
    with service.store.connect() as con:
        assert "PRIVATE_QUEUED_COPY" not in con.execute("SELECT request_json FROM max_outbound_attempts").fetchone()[0]
        assert "PRIVATE_QUEUED_COPY" not in con.execute("SELECT payload_json FROM conversation_response_outbox").fetchone()[0]
        assert con.execute("SELECT status FROM conversation_output_intents").fetchone()[0] == "delivered"
    assert service.get(binding.principal_ref, result["notification_id"]) is None


def test_retired_audience_roster_does_not_resolve_or_erase_live_source_obligations(notification_runtime):
    from test_notification_source_audiences import audience
    from adaos.services.notifications.audiences import emit_to_source
    service, binding, manifest, clock = notification_runtime
    binding, source, request = audience(service, binding, manifest)
    admitted = emit_to_source(service, binding, limit=1, **request)
    clock[0] += 86400 + retention.CONTENT_RETENTION_SECONDS
    assert retention.retire_authority_metadata(service) == 1
    repeated = emit_to_source(service, binding, limit=1, cursor=admitted["next_cursor"], **request)
    assert repeated["status"] == "expired_or_erased" and repeated["receipts"] == []
    with service.store.connect() as con:
        row = dict(con.execute("SELECT * FROM notification_audience_snapshots").fetchone())
        assert row["recipients_json"] == "[]" and row["source_ref"] == row["source_id"] == ""
        assert row["principal_ref"].startswith("retired:")
        original = json.loads(con.execute("SELECT payload_json FROM conversation_interactions WHERE interaction_id=?", (source["interaction_id"],)).fetchone()[0])
        assert original == source  # notification GC owns no business resolution


def test_expired_sdk_capture_loses_private_binding_without_becoming_reusable(sdk_notification_scope):
    from adaos.sdk import notifications
    captured = notifications.capture_publisher(lifetime_seconds=60)
    service = NotificationService(clock=lambda: captured["expires_at"] + retention.CONTENT_RETENTION_SECONDS)
    assert retention.retire_authority_metadata(service) == 1
    with service.store.connect() as con:
        row = con.execute("SELECT binding_json,status FROM notification_publisher_grants").fetchone()
        assert tuple(row) == ("{}", "retired")
    with pytest.raises(PermissionError, match="unavailable"):
        notifications.emit_captured(captured["publisher_ref"], category_id="review.completed", event_id="expired",
                                    source_id="source", source_revision=1, occurred_at=service.clock(), payload={"title": "Disallowed"})
