from adaos.services.router.builder_chat_notifications import builder_chat_notifications


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


def test_other_channels_telegram_and_lifecycle_are_not_broadcast_twice():
    for meta in ({"dialog_channel_id": "general"},
                 {"dialog_channel_id": "builder", "io_type": "telegram"},
                 {"dialog_channel_id": "builder", "schema": "adaos.builder.automation_notification.v1"}):
        assert builder_chat_notifications({"id": "m-1", "text": "Сообщение", "_meta": meta}) is None
