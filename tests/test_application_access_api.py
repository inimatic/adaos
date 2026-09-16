from __future__ import annotations

import asyncio
from types import SimpleNamespace

from adaos.apps.api import application_access


class _Decision:
    def to_dict(self):
        return {
            "decision": "allow",
            "reason_code": "allowed",
            "application_id": "family_tasks",
            "subject_ref": "user:masha",
        }


class _Grant:
    application_id = "family_tasks"
    subject_ref = "user:masha"
    grant_id = "appgrant.1"
    reviewed_permission_profile_digest = "sha256:" + "a" * 64

    def to_dict(self):
        return {"grant_id": self.grant_id, "status": "revoked"}


class _Access:
    def decide(self, *args, **kwargs):
        return _Decision()

    def revoke_access(self, *args, **kwargs):
        return _Grant()


class _Store:
    def get_application_access_grant(self, grant_id):
        assert grant_id == "appgrant.1"
        return _Grant()


class _Management:
    access = _Access()
    store = _Store()

    def application_detail(self, application_id, **kwargs):
        return {"application_id": application_id, "sections": {"access": []}}

    def users_access(self):
        return {"people": [], "guests": [], "children": []}


def test_conversational_access_reads_explains_and_routes_sensitive_revoke(monkeypatch) -> None:
    published: list[dict] = []

    async def publish(**kwargs):
        published.append(kwargs)
        return {"id": "pending.revoke.1", "status": "pending", "metadata": kwargs["metadata"]}

    async def pending(*, include_terminal):
        assert include_terminal is True
        return {
            "by_id": {
                "pending.revoke.1": {
                    "kind": "application.access.revoke",
                    "domain_ref": {"grant_id": "appgrant.1"},
                    "status": "responded",
                    "response": {"response_action_id": "approve"},
                }
            }
        }

    monkeypatch.setattr(application_access, "_management", lambda _ctx: _Management())
    monkeypatch.setattr(application_access, "_issuer", lambda _ctx: "user:owner")
    monkeypatch.setattr(application_access, "publish_pending_action_async", publish)
    monkeypatch.setattr(application_access, "list_pending_actions_async", pending)
    ctx = SimpleNamespace()

    listed = asyncio.run(
        application_access.conversation(
            application_access.ConversationRequest(
                intent="list", application_id="family_tasks"
            ),
            ctx,
        )
    )
    explained = asyncio.run(
        application_access.conversation(
            application_access.ConversationRequest(
                intent="explain",
                application_id="family_tasks",
                release_digest="sha256:" + "b" * 64,
                subject_ref="user:masha",
                permission_id="workspace.read",
                component_capabilities=["workspace.read"],
            ),
            ctx,
        )
    )
    requested = asyncio.run(
        application_access.conversation(
            application_access.ConversationRequest(
                intent="revoke", grant_id="appgrant.1", expected_revision=1
            ),
            ctx,
        )
    )
    approved = asyncio.run(
        application_access.conversation(
            application_access.ConversationRequest(
                intent="revoke",
                grant_id="appgrant.1",
                expected_revision=1,
                pending_action_id="pending.revoke.1",
            ),
            ctx,
        )
    )

    assert listed["result"]["application_id"] == "family_tasks"
    assert explained["decision"]["reason_code"] == "allowed"
    assert requested["status"] == "approval_required"
    assert published[0]["response_route"] == {
        "type": "event",
        "topic": "application.access.conversation",
    }
    assert published[0]["domain_ref"]["expected_revision"] == 1
    assert published[0]["metadata"]["channel_affordances"] == {
        "chat": "keyboard",
        "telegram": "inline_keyboard",
        "voice": "trusted_device_handoff",
        "voice_text": "Confirm this access change on a trusted device.",
        "voice_text_i18n_key": "runtime.application_approval.voice_handoff",
    }
    assert approved["grant"]["status"] == "revoked"
    assert approved["approval_id"] == "pending.revoke.1"
