from __future__ import annotations

import pytest

from adaos.services.root_mcp import users_access_plane as plane


def _context() -> dict:
    return {
        "actor": "user:owner",
        "scope": {"subnet_id": "sn_test"},
        "auth_context": {"actor": "user:owner", "subnet_id": "sn_test"},
    }


class _Store:
    def __init__(self) -> None:
        self.invites: dict[str, dict] = {}
        self.devices = {"phone": {"device_id": "phone", "status": "active"}}
        self.sessions = {"browser": {"session_id": "browser", "status": "active"}}
        self.grants: list[dict] = []

    def iter_grants(self, *, status: str = "active") -> list[dict]:
        return [item for item in self.grants if item.get("status", "active") == status]

    def iter_invites(self, *, status: str = "pending") -> list[dict]:
        return [item for item in self.invites.values() if item.get("status") == status]

    def update_invite(self, invite_id: str, patch: dict) -> dict:
        self.invites[invite_id].update(patch)
        return dict(self.invites[invite_id])

    def get_invite(self, invite_id: str) -> dict | None:
        value = self.invites.get(invite_id)
        return dict(value) if value else None

    def get_device_key(self, device_id: str) -> dict | None:
        value = self.devices.get(device_id)
        return dict(value) if value else None

    def get_session(self, session_id: str) -> dict | None:
        value = self.sessions.get(session_id)
        return dict(value) if value else None


class _Service:
    def __init__(self) -> None:
        self.store = _Store()
        self.calls: list[tuple] = []

    def admin_summary(self, *, actor, audit_limit: int) -> dict:
        self.calls.append(("summary", actor.ref(), audit_limit))
        return {
            "users": [{"user_id": "owner", "subject": {"kind": "user", "id": "owner"}}],
            "profiles": [{"user_id": "owner", "display_name": "Owner"}],
            "devices": list(self.store.devices.values()),
            "sessions": list(self.store.sessions.values()),
            "memberships": [],
            "grants": list(self.store.grants),
            "invites": list(self.store.invites.values()),
            "recovery_actions": [],
            "audit": [],
        }

    def grant_role_preset(
        self, *, subject, scope, role, actor, expires_at=None
    ) -> dict:
        grant = {
            "grant_id": "grant-1",
            "subject": subject.to_dict(),
            "scope": scope.to_dict(),
            "role": role,
            "status": "active",
        }
        self.store.grants.append(grant)
        return {"grant": grant, "membership": {"role": role}}

    def create_guest_join_link(
        self, *, invite_id, scope, issued_by, expires_at, max_sessions
    ):
        invite = {
            "invite_id": invite_id,
            "kind": "guest_join_link",
            "role": "guest",
            "scope": scope.to_dict(),
            "status": "pending",
            "expires_at": expires_at,
            "max_sessions": max_sessions,
        }
        self.store.invites[invite_id] = invite
        return dict(invite)

    def create_targeted_invite_link(
        self,
        *,
        invite_id,
        scope,
        role,
        issued_by,
        profile_hint,
        expires_at,
        constraints,
    ):
        invite = {
            "invite_id": invite_id,
            "kind": "targeted_invite_link",
            "role": role,
            "scope": scope.to_dict(),
            "status": "pending",
            "profile_hint": profile_hint,
            "expires_at": expires_at,
        }
        self.store.invites[invite_id] = invite
        return dict(invite)

    def revoke_invite(self, invite_id: str, *, actor, reason=None):
        return self.store.update_invite(
            invite_id, {"status": "revoked", "reason": reason}
        )

    def revoke_device(self, device_id: str, *, actor, reason=None):
        self.store.devices[device_id].update({"status": "revoked", "reason": reason})
        return dict(self.store.devices[device_id])

    def revoke_session(self, session_id: str, *, actor, reason=None):
        self.store.sessions[session_id].update({"status": "revoked", "reason": reason})
        return dict(self.store.sessions[session_id])


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> _Service:
    value = _Service()
    monkeypatch.setattr(plane, "_service", lambda: value)
    monkeypatch.setattr(
        plane,
        "_claim_url",
        lambda invite_id: f"https://app.test/?adaos_invite={invite_id}",
    )
    monkeypatch.setattr(
        plane.applications_sdk,
        "get_users_access_surface",
        lambda directory: {
            "schema": "adaos.users_access.surface.v1",
            "people": directory["users"],
        },
    )
    return value


def test_contracts_publish_owner_governed_read_and_write_tools() -> None:
    items = {item.id: item for item in plane.contracts()}
    assert items["users_access.summary"].required_capability == "users_access.read"
    assert (
        items["users_access.create_invite"].required_capability == "users_access.invite"
    )
    assert (
        items["users_access.revoke_device"].required_capability == "users_access.manage"
    )
    assert items["users_access.revoke_device"].side_effects == "write"


def test_summary_combines_personalization_and_application_access(
    service: _Service,
) -> None:
    result = plane.handlers()["users_access.summary"](
        {"audit_limit": 20, "_mcp_context": _context()},
        dry_run=False,
    )
    assert result["users_access"]["people"][0]["user_id"] == "owner"
    assert result["administration"]["invites"] == []
    assert service.calls == [("summary", "user:owner", 20)]


def test_summary_projects_requested_compact_sections(
    service: _Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        plane.applications_sdk,
        "get_users_access_surface",
        lambda directory: {
            "schema": "adaos.users_access.surface.v1",
            "people": [
                {
                    "subject_ref": "user:owner",
                    "kind": "user",
                    "profile": {"display_name": "Owner"},
                    "memberships": [
                        {"role": "owner", "scope": {"kind": "subnet", "id": "home"}},
                        {
                            "role": "owner",
                            "scope": {"kind": "workspace", "id": "desktop"},
                        },
                    ],
                    "application_access": [{"grant_id": "grant-1"}],
                }
            ],
            "devices": [{"device_id": "phone"}],
            "diagnostics": {"content_redacted": True},
        },
    )

    result = plane.handlers()["users_access.summary"](
        {
            "sections": ["people"],
            "detail": "compact",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert set(result["users_access"]) == {"schema", "people", "diagnostics"}
    assert result["users_access"]["people"] == [
        {
            "subject_ref": "user:owner",
            "kind": "user",
            "profile": {"display_name": "Owner"},
            "memberships": [
                {"role": "owner", "scope": {"kind": "subnet", "id": "home"}},
                {"role": "owner", "scope": {"kind": "workspace", "id": "desktop"}},
            ],
            "membership_summary": "owner",
            "application_access_count": 1,
        }
    ]
    assert result["administration"] == {}


def test_summary_redacts_and_normalizes_access_audit(
    service: _Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = service.admin_summary

    def summary(*, actor, audit_limit: int) -> dict:
        payload = original(actor=actor, audit_limit=audit_limit)
        payload["audit"] = [
            {
                "audit_id": "audit-1",
                "event_type": "policy.allow",
                "actor": {"kind": "user", "id": "owner"},
                "scope": {"kind": "skill", "id": "notes"},
                "decision": {
                    "decision": "allow",
                    "reason_code": "owner",
                    "action": "workspace.read",
                    "resource": "skill:notes",
                    "grant_ids": ["secret-grant"],
                },
                "metadata": {"resource": "skill:notes", "secret": "hidden"},
                "ts": 1_800_000_000,
                "source": "personalization_access",
            }
        ]
        return payload

    monkeypatch.setattr(service, "admin_summary", summary)
    result = plane.handlers()["users_access.summary"](
        {"sections": ["audit"], "detail": "compact", "_mcp_context": _context()},
        dry_run=False,
    )

    assert result["administration"]["audit"] == [
        {
            "audit_id": "audit-1",
            "event_type": "policy.allow",
            "actor": {"kind": "user", "id": "owner"},
            "actor_ref": "user:owner",
            "scope": {"kind": "skill", "id": "notes"},
            "scope_ref": "skill:notes",
            "decision": {
                "decision": "allow",
                "reason_code": "owner",
                "action": "workspace.read",
            },
            "resource": "skill:notes",
            "occurred_at": "2027-01-15T08:00:00+00:00",
            "source": "personalization_access",
        }
    ]


def test_grant_role_is_state_idempotent(service: _Service) -> None:
    arguments = {
        "subject_id": "member",
        "role": "member",
        "scope_kind": "subnet",
        "scope_id": "sn_test",
        "idempotency_key": "grant-member-1",
        "_mcp_context": _context(),
    }
    first = plane.handlers()["users_access.grant_role"](arguments, dry_run=False)
    second = plane.handlers()["users_access.grant_role"](arguments, dry_run=False)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert len(service.store.grants) == 1


def test_grant_role_accepts_typed_user_reference_from_people_projection(
    service: _Service,
) -> None:
    result = plane.handlers()["users_access.grant_role"](
        {
            "subject_id": "user:member",
            "role": "member",
            "scope_kind": "subnet",
            "scope_id": "sn_test",
            "idempotency_key": "grant-typed-member-1",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )

    assert result["grant"]["subject"] == {"kind": "user", "id": "member"}


def test_grant_role_rejects_non_user_typed_reference(service: _Service) -> None:
    with pytest.raises(ValueError, match="identify a user"):
        plane.handlers()["users_access.grant_role"](
            {
                "subject_id": "device:phone",
                "role": "member",
                "scope_kind": "subnet",
                "scope_id": "sn_test",
                "idempotency_key": "grant-device-1",
                "_mcp_context": _context(),
            },
            dry_run=False,
        )


def test_invite_creation_and_revocation_are_replay_safe(service: _Service) -> None:
    arguments = {
        "kind": "targeted",
        "role": "member",
        "profile_hint": "Alex",
        "scope_kind": "subnet",
        "scope_id": "sn_test",
        "expires_in_minutes": 30,
        "idempotency_key": "invite-alex-1",
        "_mcp_context": _context(),
    }
    first = plane.handlers()["users_access.create_invite"](arguments, dry_run=False)
    second = plane.handlers()["users_access.create_invite"](arguments, dry_run=False)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["invite"]["claim_url"].startswith("https://app.test/")

    revoke = {
        "invite_id": first["invite"]["invite_id"],
        "idempotency_key": "revoke-alex-1",
        "_mcp_context": _context(),
    }
    assert (
        plane.handlers()["users_access.revoke_invite"](revoke, dry_run=False)[
            "duplicate"
        ]
        is False
    )
    assert (
        plane.handlers()["users_access.revoke_invite"](revoke, dry_run=False)[
            "duplicate"
        ]
        is True
    )


def test_device_and_session_revocation_are_replay_safe(service: _Service) -> None:
    device = {
        "device_id": "phone",
        "idempotency_key": "device-1",
        "_mcp_context": _context(),
    }
    session = {
        "session_id": "browser",
        "idempotency_key": "session-1",
        "_mcp_context": _context(),
    }
    assert (
        plane.handlers()["users_access.revoke_device"](device, dry_run=False)[
            "duplicate"
        ]
        is False
    )
    assert (
        plane.handlers()["users_access.revoke_device"](device, dry_run=False)[
            "duplicate"
        ]
        is True
    )
    assert (
        plane.handlers()["users_access.revoke_session"](session, dry_run=False)[
            "duplicate"
        ]
        is False
    )
    assert (
        plane.handlers()["users_access.revoke_session"](session, dry_run=False)[
            "duplicate"
        ]
        is True
    )


def test_actor_context_is_required(service: _Service) -> None:
    with pytest.raises(ValueError, match="actor context"):
        plane.handlers()["users_access.summary"]({}, dry_run=False)
