from __future__ import annotations

from adaos.sdk import conversation


def test_builder_change_sdk_forwards_plain_contract(monkeypatch) -> None:
    stored: dict = {}

    def _upsert(**kwargs):
        stored.update(kwargs)
        return {"schema": "adaos.conversation.development_change.v1", **kwargs}

    monkeypatch.setattr(conversation.conversation_store, "upsert_development_change", _upsert)
    monkeypatch.setattr(
        conversation.conversation_store,
        "get_development_change",
        lambda change_id: {"change_id": change_id, "status": "pushed"},
    )
    monkeypatch.setattr(
        conversation.conversation_store,
        "list_development_changes",
        lambda **kwargs: [{"change_id": "change-1", **kwargs}],
    )

    changed = conversation.upsert_development_change(
        change_id="change-1",
        conversation_id="conv.skill.builder_skill.default",
        topic_id="prompt-project:scenario:builder",
        status="pushed",
        artifact_refs=[{"kind": "scenario", "id": "builder"}],
        commit_refs=[{"commit": "abc123"}],
    )
    loaded = conversation.get_development_change("change-1")
    listed = conversation.list_development_changes(artifact_kind="scenario", artifact_id="builder", limit=10)

    assert changed and changed["status"] == "pushed"
    assert stored["commit_refs"] == [{"commit": "abc123"}]
    assert loaded == {"change_id": "change-1", "status": "pushed"}
    assert listed[0]["artifact_id"] == "builder"
    assert listed[0]["limit"] == 10


def test_builder_topic_sdk_uses_canonical_link_contract(monkeypatch) -> None:
    captured: dict = {}

    def _ensure(webspace_id, **kwargs):
        captured.update({"webspace_id": webspace_id, **kwargs})
        return {
            "conversation_id": "conv.skill.builder_skill.default",
            "topic_id": "prompt-project:scenario:builder",
            "thread_id": "prompt-project:scenario:builder",
        }

    monkeypatch.setattr("adaos.services.conversation_links.ensure_builder_topic", _ensure)

    result = conversation.ensure_builder_topic(
        webspace_id="desktop",
        scenario_id="builder",
        project_id="scenario:builder",
        title="Builder",
    )

    assert result["conversation_id"] == "conv.skill.builder_skill.default"
    assert captured["scenario_id"] == "builder"
    assert captured["project_id"] == "scenario:builder"
