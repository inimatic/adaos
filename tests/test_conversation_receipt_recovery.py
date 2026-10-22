"""Receipt recovery boundaries: no real provider or Root calls."""
from types import SimpleNamespace
import time
import requests
from adaos.services import conversation_delivery as delivery, conversation_publication as publication
from adaos.services import conversation_store, durable_delivery
from adaos.services.agent_context import get_ctx


def prepare(monkeypatch):
    conversation_store.upsert_conversation(conversation_id="review:receipts", owner="skill:test",
        webspace_id="test", policy={"output_route_ids": ["route:receipt"]})
    durable_delivery.create_reply_route("review:receipts", transport="telegram", route_id="route:receipt",
        destination_ref={"hub_id": "owned"}, principal_scope=["user:test"])
    result = publication.publish(publication_key="one", conversation_id="review:receipts", owner="skill:test",
        webspace_id="test", text="Receipt test", route_ids=["route:receipt"])
    get_ctx().config = SimpleNamespace(root_settings=SimpleNamespace(base_url="https://receipt-fixture.invalid"), subnet_id="owned")
    sends = []
    monkeypatch.setattr(requests, "post", lambda *a, **kw: sends.append(kw["json"]) or SimpleNamespace(status_code=202))
    return result, sends


def receipt(sends, *, delivered=False, error=None):
    protocol = sends[0]["_protocol"]
    return {"schema": "adaos.telegram.delivery_receipt.v1", "receipt_id": "fixture:receipt",
        "delivery_attempt_id": protocol["delivery_attempt_id"], "operation_key": protocol["operation_key"],
        "delivered": delivered, "transport": "telegram", "external_message_ids": ["provider:1"] if delivered else [],
        "duplicate_suppressed": 0, "error": error, "completed_at": "2026-10-22T18:00:00Z"}


def test_fresh_publication_initializes_receipt_ledger(monkeypatch):
    prepare(monkeypatch)
    assert delivery.reconcile_once() is False


def test_crash_before_transport_record_recovers_one_unsent_intent(monkeypatch):
    result, sends = prepare(monkeypatch)
    first = delivery.claim_next()
    with get_ctx().sql.connect() as con:
        con.execute("UPDATE conversation_output_intents SET lease_until=? WHERE intent_id=?", (time.time()-1, first["intent_id"]))
        con.commit()
    assert delivery.dispatch_once()
    assert len(sends) == 1
    assert publication.output_status(result["publication_id"])[0]["status"] == "accepted_by_root"


def test_permanent_rejection_is_terminal_and_unknown_never_replays(monkeypatch):
    result, sends = prepare(monkeypatch)
    delivery.dispatch_once()
    delivery.apply_receipt(receipt(sends, error="telegram_http_non_ok status=403"))
    assert publication.output_status(result["publication_id"])[0]["status"] == "dead_letter"
    assert not delivery.dispatch_once()
    assert len(sends) == 1
    # Exact late provider proof is monotonic, never reconstructed from equal text.
    delivery.apply_receipt(receipt(sends, delivered=True))
    assert publication.output_status(result["publication_id"])[0]["status"] == "delivered"


def test_ambiguous_provider_outcome_requires_review_but_admits_late_exact_proof(monkeypatch):
    result, sends = prepare(monkeypatch)
    delivery.dispatch_once()
    delivery.apply_receipt(receipt(sends, error="telegram_operation_outcome_unknown"))
    assert publication.output_status(result["publication_id"])[0]["status"] == "needs_review"
    assert not delivery.dispatch_once()
    assert delivery.reconcile_once() is False
    delivery.apply_receipt(receipt(sends, delivered=True))
    assert publication.output_status(result["publication_id"])[0]["status"] == "delivered"
    assert len(sends) == 1


def test_old_root_is_reported_and_negative_cached_without_resend(monkeypatch):
    from adaos.services.root.client import RootHttpClient, RootHttpError
    prepare(monkeypatch)
    delivery._UNSUPPORTED_ROOTS.clear()
    calls = []
    def missing(*a, **kw):
        calls.append((a, kw))
        raise RootHttpError("Cannot POST", status_code=404, payload="Cannot POST")
    monkeypatch.setattr(RootHttpClient, "from_hub_config", lambda *a, **kw: SimpleNamespace(base_url="https://old-root.invalid", request=missing))
    for _ in range(2):
        assert delivery.query_receipt({"hub_id": "owned", "operation_key": "exact"}) == {"ok": False, "status": "root_upgrade_required"}
    assert len(calls) == 1
    delivery._UNSUPPORTED_ROOTS.clear()


def test_local_failure_before_transport_record_is_retryable_not_stranded(monkeypatch):
    from adaos.services import telegram_delivery
    result, sends = prepare(monkeypatch)
    original = telegram_delivery.claim_outbound
    def local_failure(*args, **kwargs):
        raise RuntimeError("local claim failed before external call")
    monkeypatch.setattr(telegram_delivery, "claim_outbound", local_failure)
    assert delivery.dispatch_once()
    assert sends == []
    assert publication.output_status(result["publication_id"])[0]["status"] == "retry_wait"
    monkeypatch.setattr(telegram_delivery, "claim_outbound", original)
    with get_ctx().sql.connect() as con:
        con.execute("UPDATE conversation_output_intents SET next_attempt_at=0")
        con.commit()
    assert delivery.dispatch_once()
    assert len(sends) == 1


def test_receipt_reader_never_crosses_the_current_hub_scope(monkeypatch):
    import pytest
    from adaos.services.root.client import RootHttpClient
    prepare(monkeypatch)
    calls = []
    monkeypatch.setattr(RootHttpClient, "from_hub_config", lambda *a, **kw:
        SimpleNamespace(request=lambda *a, **kw: calls.append((a, kw)) or {"ok": True}))
    with pytest.raises(PermissionError, match="another hub"):
        delivery.query_receipt({"hub_id": "foreign", "operation_key": "exact"})
    assert calls == []
    assert delivery.query_receipt({"hub_id": "owned", "operation_key": "exact"}) == {"ok": True}
    assert calls[0] == (("POST", "/v1/hub/communication/telegram/receipt/status"),
                        {"json": {"operation_key": "exact"}, "timeout": 5})
