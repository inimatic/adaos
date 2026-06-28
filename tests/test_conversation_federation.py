from __future__ import annotations

from adaos.services import conversation_federation, conversation_store


def test_federated_retrieval_returns_fragments_refs_scores_without_remote_sql() -> None:
    conversation_store.upsert_conversation(
        conversation_id="conv.skill.builder_skill.default.fed",
        webspace_id="fed",
        owner="skill:builder_skill",
        kind="builder",
        title="Builder",
    )
    msg = conversation_store.append_message(
        conversation_id="conv.skill.builder_skill.default.fed",
        webspace_id="fed",
        channel_id="builder",
        owner="skill:builder_skill",
        role="user",
        text="shopping list prototype needs a category filter",
    )
    memory_id = conversation_store.remember(
        scope="skill_user",
        owner="skill:builder_skill",
        subject_id="agent:builder_skill:builder",
        key="preference.prototype",
        text="User prefers compact shopping list prototypes",
        consent_state="granted",
    )

    response = conversation_federation.execute_local_request(
        {
            "requester": {"owner": "skill:builder_skill", "actor_id": "agent:builder_skill:builder"},
            "query": "shopping list",
            "scopes": {
                "webspace_id": "fed",
                "owners": ["skill:builder_skill"],
                "memory_scopes": ["skill_user"],
                "conversation_ids": ["conv.skill.builder_skill.default.fed"],
            },
            "limits": {"max_fragments": 5, "timeout_ms": 250, "per_node_timeout_ms": 250},
        },
        node_id="node-a",
    )

    assert response["schema"] == conversation_federation.RESPONSE_SCHEMA
    assert response["status"] == "ok"
    assert response["diagnostics"]["remote_sql"] is False
    assert response["fragments"]
    refs = [item["source_ref"] for item in response["fragments"]]
    assert {"type": "memory", "memory_id": memory_id, "scope": "skill_user", "owner": "skill:builder_skill", "subject_id": "agent:builder_skill:builder"} in refs
    assert any(ref.get("message_id") == msg["id"] and ref.get("conversation_id") == "conv.skill.builder_skill.default.fed" for ref in refs)
    assert all("score" in item and "source_ref" in item and "summary" in item for item in response["fragments"])
    assert all("payload_json" not in item and "meta_json" not in item for item in response["fragments"])


def test_federated_retrieval_denies_cross_owner_by_default() -> None:
    conversation_store.remember(
        scope="skill_user",
        owner="skill:conversation_companions",
        subject_id="agent:conversation_companions:nika",
        key="private.note",
        text="Private companion note",
        consent_state="granted",
    )

    response = conversation_federation.execute_local_request(
        {
            "requester": {"owner": "skill:builder_skill"},
            "query": "Private",
            "scopes": {
                "owners": ["skill:builder_skill", "skill:conversation_companions"],
                "memory_scopes": ["skill_user"],
            },
            "policy": {"allow_cross_owner": False},
            "limits": {"max_fragments": 5, "timeout_ms": 250},
        }
    )

    assert response["status"] in {"ok", "denied"}
    assert any(item["reason"] == "cross_owner_denied" for item in response["denials"])
    assert all(item["source_ref"].get("owner") != "skill:conversation_companions" for item in response["fragments"])


def test_federated_retrieval_is_timeout_bound_and_partial() -> None:
    response = conversation_federation.execute_local_request(
        {
            "requester": {"owner": "skill:builder_skill"},
            "query": "anything",
            "scopes": {"owners": ["skill:builder_skill"], "memory_scopes": ["skill_user"]},
            "limits": {"timeout_ms": 0, "per_node_timeout_ms": 0, "max_fragments": 5},
        }
    )

    assert response["status"] == "partial"
    assert response["partial"] is True
    assert response["fragments"] == []
    assert response["denials"][0]["reason"] == "timeout_before_retrieval"
