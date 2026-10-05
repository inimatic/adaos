from adaos.services.router.voice_chat_stream import (
    _bound_voice_chat_stream_messages,
    _compact_voice_chat_stream_message,
    _voice_chat_stream_json_bytes,
)


def test_shared_message_keeps_identity_and_bounded_references_not_file_bytes():
    source = {"id": "m1", "from": "agent", "text": "", "sender_id": "agent:a",
              "recipient_ids": ["agent:b", "agent:a", "agent:b"],
              "attachments": [{"ref": "/api/tools/app/read/attachments/one", "name": "report.txt",
                               "mime": "text/plain", "size_bytes": 12, "bytes": "PRIVATE FILE DATA"},
                              {"ref": "data:text/plain,not-a-reference"}]}
    result = _compact_voice_chat_stream_message(source)
    assert result["id"] == "m1"
    assert result["recipient_ids"] == ["agent:b", "agent:a"]
    assert len(result["attachments"]) == 1
    assert "bytes" not in result["attachments"][0]


def test_attachment_metadata_does_not_bypass_stream_budget():
    source = {"id": "m1", "from": "agent", "text": "report", "sender_id": "agent:a",
              "recipient_ids": ["a" * 256] * 32,
              "attachments": [{"ref": "/api/tools/app/read/attachments/" + "x" * 900,
                               "name": "n" * 256}] * 20}
    result, _ = _bound_voice_chat_stream_messages([_compact_voice_chat_stream_message(source)], max_bytes=4096)
    assert _voice_chat_stream_json_bytes(result) <= 4096
    assert result[0]["id"] == "m1"
    assert result[0]["details_omitted"] is True
