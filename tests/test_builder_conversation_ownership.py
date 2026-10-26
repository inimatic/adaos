from types import SimpleNamespace

import pytest

from adaos.services import conversation_links as links
from adaos.services import conversation_publication, conversation_store
from adaos.services.builder.automation import BuilderAutomationService


@pytest.mark.parametrize("interaction,expected", [
    ({"owner": "skill:builder_skill", "metadata": {"purpose": "conversation.output_routes"}}, True),
    ({"owner": "skill:other", "metadata": {"purpose": "conversation.output_routes"}}, False),
    ({"owner": "skill:builder_skill", "metadata": {"purpose": "other"}}, False),
    ({"metadata": {"domain": "builder"}}, True),
])
def test_builder_response_dispatch_includes_its_owned_output_selection(interaction, expected):
    from adaos.services.router.service import _is_builder_interaction_response
    assert _is_builder_interaction_response(interaction) is expected


@pytest.mark.parametrize("legacy_owner", ["skill:builder_skill", "core:general_assistant"])
def test_builder_preserves_foreign_legacy_conversation(monkeypatch, legacy_owner):
    writes, merges, dialogs = [], [], []
    monkeypatch.setattr(conversation_store, "ensure_schema", lambda: True)
    monkeypatch.setattr(conversation_store, "get_conversation", lambda _: {"owner": legacy_owner})
    monkeypatch.setattr(conversation_store, "upsert_conversation", lambda **kw: writes.append(kw))
    monkeypatch.setattr(conversation_store, "merge_conversations_by_prefix", lambda **kw: merges.append(kw))
    monkeypatch.setattr(conversation_store, "upsert_dialog_channel", lambda **kw: dialogs.append(kw))
    result = links.ensure_builder_conversation("desktop")
    foreign = legacy_owner != links.BUILDER_OWNER
    expected = links.BUILDER_CONVERSATION_ID + (".desktop" if foreign else "")
    assert result["stored"] and result["conversation_id"] == expected
    assert writes[0]["conversation_id"] == dialogs[0]["conversation_id"] == expected
    assert writes[0]["owner"] == links.BUILDER_OWNER
    assert bool(merges) is not foreign


def test_lifecycle_recovers_only_known_legacy_identity(monkeypatch):
    from adaos.services import agent_context

    owned_id = links.BUILDER_CONVERSATION_ID + ".desktop"
    records = {links.BUILDER_CONVERSATION_ID: {"owner": "core:general_assistant"},
               owned_id: {"owner": links.BUILDER_OWNER, "webspace_id": "desktop", "policy": {}}}
    calls = []
    monkeypatch.setattr(agent_context, "get_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(conversation_store, "get_conversation", lambda cid: records.get(cid))
    monkeypatch.setattr(links, "ensure_builder_conversation", lambda ws: {"conversation_id": owned_id})
    monkeypatch.setattr(conversation_publication, "publish", lambda **kw: calls.append(kw) or {"status": "committed"})
    session = {"conversation_id": links.BUILDER_CONVERSATION_ID, "webspace_id": "desktop",
               "current_task_id": "task-1", "object_type": "scenario", "object_id": "example"}
    assert BuilderAutomationService._publish_lifecycle_notification(
        object(), session, status="in_progress", message="Actual progress")
    assert calls[0]["conversation_id"] == owned_id
    assert calls[0]["route_ids"] == []
    assert session["conversation_id"] == links.BUILDER_CONVERSATION_ID


def test_builder_context_refresh_keeps_owner_output_policy():
    conversation_store.upsert_conversation(conversation_id=links.BUILDER_CONVERSATION_ID,
        owner='core:general_assistant',webspace_id='default')
    record=links.ensure_builder_conversation('desktop')
    cid=record['conversation_id']
    policy={'output_route_ids':['selected-by-human'],'output_policy_revision':7}
    conversation_store.upsert_conversation(conversation_id=cid,owner=links.BUILDER_OWNER,
        webspace_id='desktop',policy=policy)
    links.ensure_builder_conversation('desktop')
    assert conversation_store.get_conversation(cid)['policy']==policy
    # Explicit clearing remains possible, and a new owner/scope inherits no grants.
    conversation_store.upsert_conversation(conversation_id=cid,owner=links.BUILDER_OWNER,
        webspace_id='desktop',policy={})
    assert conversation_store.get_conversation(cid)['policy']=={}
    conversation_store.upsert_conversation(conversation_id=cid,owner=links.BUILDER_OWNER,
        webspace_id='desktop',policy=policy)
    conversation_store.upsert_conversation(conversation_id=cid,owner='skill:another',webspace_id='desktop')
    assert conversation_store.get_conversation(cid)['policy']=={}
