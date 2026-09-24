from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from adaos.domain.personalization_access import Grant, ScopeRef, SessionKey, SubjectRef
from adaos.sdk import access
from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore
from adaos.services.policy.caller import current_caller, verified_caller
from adaos.services.policy.application import bind_application, clear_application


OWNER = SubjectRef("user", "owner")
READER = SubjectRef("user", "reader")


@pytest.fixture
def caller_access(monkeypatch, tmp_path):
    path = tmp_path / "access.json"
    service = PersonalizationAccessService(PersonalizationAccessStore(path), owner=OWNER)
    service.put_grant(Grant(
        grant_id="reader-data", subject=READER, scope=ScopeRef("skill", "sample"),
        capabilities=("workspace.read",), issued_by=OWNER,
    ), actor=OWNER)
    ctx = SimpleNamespace(skill_ctx=SimpleNamespace(get=lambda: SimpleNamespace(name="sample")))
    monkeypatch.setattr(access, "require_ctx", lambda _name: ctx)
    monkeypatch.setattr(access, "personalization_access_service", lambda _ctx:
                        PersonalizationAccessService(PersonalizationAccessStore(path), owner=OWNER))
    return ctx, path


def test_absent_caller_is_not_implicitly_owner(caller_access):
    with verified_caller(None):
        assert access.caller() is None
        with pytest.raises(PermissionError, match="caller_not_authenticated"):
            access.require("workspace.write")


def test_local_owner_and_scoped_reader_are_distinct(caller_access):
    with verified_caller(OWNER):
        assert access.caller() == {"kind": "user", "id": "owner"}
        assert access.require("workspace.write")["decision"] == "allow"
    with verified_caller(READER):
        assert access.require("workspace.read")["decision"] == "allow"
        with pytest.raises(PermissionError, match="missing_capability"):
            access.require("workspace.write")


def test_grants_do_not_extend_to_another_skill(caller_access):
    ctx, _ = caller_access
    ctx.skill_ctx.get = lambda: SimpleNamespace(name="another")
    with verified_caller(READER), pytest.raises(PermissionError, match="missing_capability"):
        access.require("workspace.read")


def test_scope_cannot_be_missing(caller_access):
    ctx, _ = caller_access
    ctx.skill_ctx.get = lambda: None
    with verified_caller(OWNER), pytest.raises(PermissionError, match="caller_skill_scope_missing"):
        access.require("workspace.write")


def test_credential_scope_cannot_be_broadened_by_the_executing_skill(caller_access):
    ctx, _ = caller_access
    with verified_caller(OWNER, ScopeRef("skill", "sample")):
        assert access.require("workspace.write")["decision"] == "allow"
        ctx.skill_ctx.get = lambda: SimpleNamespace(name="another")
        with pytest.raises(PermissionError, match="credential_scope_mismatch"):
            access.require("workspace.write")


def test_revocation_is_read_again_on_next_check(caller_access):
    _, path = caller_access
    with verified_caller(READER):
        access.require("workspace.read")
        service = PersonalizationAccessService(PersonalizationAccessStore(path), owner=OWNER)
        service.revoke_grant("reader-data", actor=OWNER)
        with pytest.raises(PermissionError, match="missing_capability"):
            access.require("workspace.read")


def test_exact_application_ingress_permission_is_not_reaudited(caller_access, monkeypatch):
    ctx, _ = caller_access
    calls = []
    monkeypatch.setattr(
        access,
        "personalization_access_service",
        lambda _ctx: calls.append(_ctx) or pytest.fail("ingress decision was repeated"),
    )
    bind_application({
        "application_id": "mail_client",
        "release_digest": "sha256:" + "a" * 64,
        "subject_ref": OWNER.ref(),
        "_ingress_authorized_permission_id": "providers.google.gmail",
    })
    try:
        with verified_caller(OWNER):
            decision = access.require("providers.google.gmail")
            assert decision["decision"] == "allow"
            assert decision["reason_code"] == "application_ingress_authorized"
    finally:
        clear_application()
    assert calls == []


def test_successful_reads_do_not_rewrite_facts_but_denials_and_writes_remain_audited(caller_access):
    _, path = caller_access
    original = path.read_bytes()
    with verified_caller(READER):
        assert access.require("workspace.read")["decision"] == "allow"
    assert path.read_bytes() == original
    with verified_caller(OWNER):
        access.require("workspace.write")
    after_write = path.read_bytes()
    assert after_write != original
    with verified_caller(SubjectRef("user", "unknown")):
        with pytest.raises(PermissionError):
            access.require("workspace.read")
    assert path.read_bytes() != after_write
    audit = PersonalizationAccessStore(path).list_audit()
    assert any(item["event_type"] == "policy.deny" for item in audit)


@pytest.mark.parametrize("status,expires", [("revoked", 60), ("active", -60)])
def test_stale_sessions_fail_closed(caller_access, status, expires):
    _, path = caller_access
    service = PersonalizationAccessService(PersonalizationAccessStore(path), owner=OWNER)
    service.put_session(SessionKey(
        session_id="reader-session", key_id="key", subject=READER,
        status=status, expires_at=time.time() + expires,
    ))
    with verified_caller(SubjectRef("session", "reader-session")):
        with pytest.raises(PermissionError, match="inactive_session"):
            access.require("workspace.read")


def test_context_follows_thread_calls_and_is_restored_after_failure():
    async def run():
        assert current_caller() is None
        with verified_caller(READER):
            assert await asyncio.to_thread(current_caller) == READER
            with pytest.raises(RuntimeError):
                with verified_caller(OWNER):
                    raise RuntimeError("failed handler")
            assert current_caller() == READER
        assert current_caller() is None

    asyncio.run(run())
