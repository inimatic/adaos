from adaos.services.router.builder_chat_notifications import builder_chat_notifications, claim_builder_notification


def test_user_brief_is_complete_and_chunked_for_telegram():
    text = "Требование сохранить фактуру.\n" * 260
    identity, items = builder_chat_notifications({
        "id": "echo-1", "from": "user", "text": text,
        "_meta": {"dialog_channel_id": "builder", "source_message_id": "turn-1", "skip_voice_chat": True,
                  "thread_id": "prompt-project:scenario:natural_retouch"},
    })
    assert identity == "turn-1"
    assert len(items) > 1
    assert all(len(item["text"]) < 3500 for item in items)
    assert "\n".join(item["text"].split("\n", 1)[1] for item in items) == text.strip()
    assert all("route_id" not in item["_meta"] for item in items)
    assert all("natural_retouch" in item["text"] for item in items)


def test_builder_response_uses_durable_identity():
    identity, items = builder_chat_notifications({
        "id": "output-1", "from": "hub", "text": "Проверки завершены.",
        "_meta": {"conversation_owner": "skill:builder_skill", "response_idempotency_key": "response-1"},
    })
    assert identity == "response-1"
    assert items[0]["text"] == "Builder · Ответ\nПроверки завершены."


def test_other_channels_and_telegram_are_not_broadcast_twice():
    for meta in ({"dialog_channel_id": "general"},
                 {"dialog_channel_id": "builder", "io_type": "telegram"}):
        assert builder_chat_notifications({"id": "m-1", "text": "Сообщение", "_meta": meta}) is None


def test_worker_lifecycle_transcript_is_delivered_without_direct_notify():
    identity, items = builder_chat_notifications({
        "id": "message.completed", "from": "hub", "text": "Builder завершил проверку Beta.",
        "_meta": {"schema": "adaos.builder.automation_notification.v1", "dialog_channel_id": "builder",
                  "response_idempotency_key": "builder-automation:completed:task.19"},
    })
    assert identity == "builder-automation:completed:task.19"
    assert len(items) == 1 and "Builder завершил проверку Beta." in items[0]["text"]


def test_transcript_and_direct_lifecycle_share_one_delivery_in_either_order():
    for order in [("transcript", "direct"), ("direct", "transcript")]:
        seen = {}
        delivered = [source for source in order if claim_builder_notification(seen, "builder-automation:completed:task.19")]
        assert delivered == [order[0]]
        assert claim_builder_notification(seen, "builder-automation:failed:task.20")


def test_notification_deduplication_retains_a_bounded_recent_window():
    seen = {}
    for index in range(1025):
        assert claim_builder_notification(seen, str(index))
    assert len(seen) == 1024 and "0" not in seen
    assert not claim_builder_notification(seen, "1024")


def test_delivery_receipt_tracks_transport_without_text_or_unsafe_paths(tmp_path):
    import json
    from adaos.services.router.builder_chat_notifications import record_builder_delivery
    payload = {"id": "../turn/1", "text": "Private portrait brief", "_meta": {"schema": "adaos.builder.chat_notification.v1"}}
    record_builder_delivery(tmp_path, payload, phase="started")
    record_builder_delivery(tmp_path, payload, phase="sent", http_status=200)
    paths = list((tmp_path / "state/router/builder-deliveries").glob("*.json"))
    assert len(paths) == 1
    stored = paths[0].read_text(encoding="utf-8")
    assert "Private portrait brief" not in stored
    assert json.loads(stored)["http_status"] == 200 and json.loads(stored)["phase"] == "sent"
