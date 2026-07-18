from __future__ import annotations

import json
import io
import zipfile
from uuid import uuid4

from adaos.services import conversation_store
from adaos.services.root.service import (
    RootDeveloperService,
    RootServiceError,
    _extract_zip_bytes,
    _normalize_draft_metadata,
    _parse_draft_commit_metadata,
)


def test_draft_metadata_is_allowlisted_and_round_trips_from_git_trailers() -> None:
    normalized = _normalize_draft_metadata(
        {
            "change_id": "builder_change_123",
            "conversation_id": "conv.skill.builder_skill.default",
            "topic_id": "prompt-project:scenario:recipes",
            "revision": "007",
            "model": "gpt-5",
            "source_message_ids": ["m.request", "m.result"],
            "transcript": "must not be copied to Git",
        }
    )

    assert "transcript" not in normalized
    assert normalized["source_message_ids"] == ["m.request", "m.result"]
    parsed = _parse_draft_commit_metadata(
        "Update recipes\n\n"
        "AdaOS-Change-Id: builder_change_123\n"
        "AdaOS-Conversation-Id: conv.skill.builder_skill.default\n"
        "AdaOS-Topic-Id: prompt-project:scenario:recipes\n"
        "AdaOS-Revision: 007\n"
        "AdaOS-Model: gpt-5\n"
        "AdaOS-Source-Messages: m.request,m.result\n"
    )

    assert parsed == normalized


def test_forge_reconciliation_recovers_builder_chat_only_once(tmp_path) -> None:
    suffix = uuid4().hex[:10]
    conversation_id = f"conv.builder.recovery.{suffix}"
    topic_id = f"prompt-project:scenario:recipes_{suffix}"
    change_id = f"builder_change_{suffix}"
    target = tmp_path / f"recipes_{suffix}"
    revision_dir = target / "ui_revisions"
    revision_dir.mkdir(parents=True)
    (revision_dir / "007.json").write_text(
        json.dumps(
            {
                "request": {"text": "Add a favorites filter"},
                "llm": {"comment": "Added the favorites filter."},
            }
        ),
        encoding="utf-8",
    )
    service = object.__new__(RootDeveloperService)
    metadata = {
        "change_id": change_id,
        "conversation_id": conversation_id,
        "topic_id": topic_id,
        "thread_id": topic_id,
        "revision": "007",
        "model": "gpt-5",
    }

    first = service._reconcile_builder_change_from_forge(
        kind="scenarios",
        name=f"recipes_{suffix}",
        target=target,
        commit="abc123",
        message="Added the favorites filter.",
        metadata=metadata,
    )
    second = service._reconcile_builder_change_from_forge(
        kind="scenarios",
        name=f"recipes_{suffix}",
        target=target,
        commit="abc123",
        message="Added the favorites filter.",
        metadata=metadata,
    )

    projection = conversation_store.list_projection(conversation_id, thread_id=topic_id, limit=10)
    change = conversation_store.get_development_change(change_id)
    assert first and first["messages_recovered"] == 2
    assert second and second["messages_recovered"] == 0
    assert [item["text"] for item in projection["messages"]] == [
        "Add a favorites filter",
        "Added the favorites filter.",
    ]
    assert change and change["status"] == "recovered"
    assert change["meta"]["synthetic_chat"] is True


def test_root_draft_archive_extraction_rejects_path_traversal(tmp_path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../outside.txt", "unsafe")

    try:
        _extract_zip_bytes(buffer.getvalue(), tmp_path / "artifact")
    except RootServiceError as exc:
        assert "escapes artifact root" in str(exc)
    else:
        raise AssertionError("path traversal archive must be rejected")
