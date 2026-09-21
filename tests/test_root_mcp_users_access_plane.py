from __future__ import annotations

from types import SimpleNamespace

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

    def create_device_pairing_link(
        self,
        *,
        invite_id,
        subject,
        scope,
        role,
        issued_by,
        expires_at,
        device_id,
        device_name,
    ):
        invite = {
            "invite_id": invite_id,
            "kind": "device_pairing_link",
            "subject_id": subject.id,
            "role": role,
            "scope": scope.to_dict(),
            "status": "pending",
            "expires_at": expires_at,
            "device_id": device_id,
            "device_name": device_name,
        }
        self.store.invites[invite_id] = invite
        return dict(invite)

    def create_admin_recovery_link(
        self,
        *,
        invite_id,
        recovery_id,
        subject,
        scope,
        issued_by,
        expires_at,
        replacement_device_id,
        revoked_device_ids,
        reason,
    ):
        invite = {
            "invite_id": invite_id,
            "kind": "admin_recovery_link",
            "subject_id": subject.id,
            "role": "member",
            "scope": scope.to_dict(),
            "status": "pending",
            "expires_at": expires_at,
            "replacement_device_id": replacement_device_id,
            "revoked_device_ids": list(revoked_device_ids),
            "reason": reason,
        }
        self.store.invites[invite_id] = invite
        return {"invite": dict(invite), "recovery": {"recovery_id": recovery_id}}

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
    profile_values = {
        "display_name": "Owner",
        "preferred_name": "",
        "language": "en",
        "locale": "en-US",
        "timezone": "UTC",
    }
    preferences = {"start_destination": "home", "show_presence": True}

    class _ProfileService:
        def get_profile(self):
            return SimpleNamespace(
                user_id="owner",
                avatar_ref=None,
                preferences=dict(preferences),
                **profile_values,
            )

        def update_profile(self, patch, *, actor):
            profile_values.update(patch)

        def update_preferences(self, patch, *, actor):
            preferences.update(patch)

    monkeypatch.setattr(plane, "_service", lambda: value)
    monkeypatch.setattr(
        plane.personalization_runtime,
        "current_user_profile_service",
        lambda _ctx: _ProfileService(),
    )
    monkeypatch.setattr(
        plane.personalization_runtime,
        "invalidate_current_user_header_settings",
        lambda _ctx: None,
    )
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
        items["users_access.scope_options"].required_capability
        == "users_access.read"
    )
    assert items["users_access.summary"].metadata["webui_data_binding"][
        "result_paths"
    ] == {
        "people": "response.result.users_access.people",
        "guests": "response.result.users_access.guests",
        "children": "response.result.users_access.children",
        "subjects": "response.result.users_access.subjects",
        "devices": "response.result.users_access.devices",
        "sessions": "response.result.users_access.sessions",
        "application_access": "response.result.users_access.application_access",
        "permissions": "response.result.users_access.permissions",
        "memberships": "response.result.administration.memberships",
        "grants": "response.result.administration.grants",
        "invites": "response.result.administration.invites",
        "recovery_actions": "response.result.administration.recovery_actions",
        "audit": "response.result.administration.audit",
    }
    binding = items["users_access.summary"].metadata["webui_data_binding"]
    assert binding["section_item_types"]["people"] == "person"
    assert binding["section_item_types"]["sessions"] == "session"
    assert binding["section_item_types"]["application_access"] == (
        "application_access"
    )
    assert binding["item_fields"]["person"]["memberships"] == "array<object>"
    assert binding["item_fields"]["person"]["display_label"] == "string"
    assert binding["item_fields"]["application_access"]["application_roles"] == (
        "array<string>"
    )
    assert (
        items["users_access.create_invite"].required_capability == "users_access.invite"
    )
    assert (
        items["users_access.revoke_device"].required_capability == "users_access.manage"
    )
    assert items["users_access.current_profile"].required_capability == "profile.read.self"
    assert items["users_access.update_current_profile"].required_capability == "profile.write.self"
    assert items["users_access.update_current_profile"].side_effects == "write"
    assert items["users_access.create_device_pairing"].side_effects == "write"
    assert items["users_access.create_admin_recovery"].side_effects == "write"
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


def test_scope_options_use_authoritative_context_and_workspace_index(
    service: _Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Workspace:
        def __init__(self, workspace_id: str, title: str, is_dev: bool) -> None:
            self.workspace_id = workspace_id
            self.title = title
            self.is_dev = is_dev

    monkeypatch.setattr(
        plane.workspace_index,
        "list_workspaces",
        lambda: [
            Workspace("$ctx.webspace_id", "$ctx.webspace_id", False),
            Workspace("desktop", "Desktop", False),
            Workspace("desktop-dev", "DEV: Desktop", True),
        ],
    )

    handler = plane.handlers()["users_access.scope_options"]
    assert handler(
        {"scope_kind": "subnet", "_mcp_context": _context()}, dry_run=False
    )["items"] == [
        {
            "id": "sn_test",
            "label": "Subnet sn_test",
            "kind": "subnet",
            "current": True,
        }
    ]
    assert [
        item["id"]
        for item in handler(
            {"scope_kind": "workspace", "_mcp_context": _context()},
            dry_run=False,
        )["items"]
    ] == ["desktop"]
    assert [
        item["id"]
        for item in handler(
            {"scope_kind": "webspace", "_mcp_context": _context()},
            dry_run=False,
        )["items"]
    ] == ["desktop", "desktop-dev"]


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
                    "display_label": "Owner",
                    "display_label_source": "profile",
                    "initials": "O",
                    "profile": {"display_name": "Owner"},
                    "memberships": [
                        {"role": "owner", "scope": {"kind": "subnet", "id": "home"}},
                        {
                            "role": "owner",
                            "scope": {"kind": "workspace", "id": "desktop"},
                        },
                    ],
                    "membership_summary": "owner",
                    "membership_count": 2,
                    "primary_role": "owner",
                    "application_access": [{"grant_id": "grant-1"}],
                    "application_access_count": 1,
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
            "display_label": "Owner",
            "display_label_source": "profile",
            "initials": "O",
            "profile": {"display_name": "Owner"},
            "memberships": [
                {"role": "owner", "scope": {"kind": "subnet", "id": "home"}},
                {"role": "owner", "scope": {"kind": "workspace", "id": "desktop"}},
            ],
            "membership_summary": "owner",
            "membership_count": 2,
            "primary_role": "owner",
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
    assert first["invite"]["subject_id"].startswith("user-")
    assert second["invite"]["subject_id"] == first["invite"]["subject_id"]
    assert second["invite"]["claim_url"].startswith("https://app.test/")
    assert second["invite"]["qr_text"] == second["invite"]["claim_url"]
    assert second["invite"]["telegram_share_url"].startswith("https://t.me/share/url?")

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


@pytest.mark.parametrize(
    ("handler_id", "arguments", "kind"),
    [
        (
            "users_access.create_device_pairing",
            {
                "subject_id": "owner",
                "role": "owner",
                "scope_kind": "subnet",
                "scope_id": "sn_test",
                "device_name": "Tablet",
                "expires_in_minutes": 30,
                "idempotency_key": "pair-owner-tablet",
            },
            "device_pairing_link",
        ),
        (
            "users_access.create_admin_recovery",
            {
                "subject_id": "owner",
                "scope_kind": "subnet",
                "scope_id": "sn_test",
                "replacement_device_id": "tablet",
                "expires_in_minutes": 30,
                "idempotency_key": "recover-owner-tablet",
            },
            "admin_recovery_link",
        ),
    ],
)
def test_specialized_invites_are_replay_safe(
    service: _Service, handler_id: str, arguments: dict, kind: str
) -> None:
    arguments["_mcp_context"] = _context()
    first = plane.handlers()[handler_id](arguments, dry_run=False)
    second = plane.handlers()[handler_id](arguments, dry_run=False)

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["invite"]["kind"] == kind
    assert first["invite"]["claim_url"].startswith("https://app.test/")


def test_summary_enriches_invites_with_shareable_links(service: _Service) -> None:
    service.store.invites["targeted-1"] = {
        "invite_id": "targeted-1",
        "kind": "targeted_invite_link",
        "status": "pending",
        "role": "member",
    }

    result = plane.handlers()["users_access.summary"](
        {"sections": ["invites"], "_mcp_context": _context()}, dry_run=False
    )
    invite = result["administration"]["invites"][0]
    assert invite["claim_url"] == "https://app.test/?adaos_invite=targeted-1"
    assert invite["qr_text"] == invite["claim_url"]


def test_current_profile_read_and_update_share_one_authority(service: _Service) -> None:
    read = plane.handlers()["users_access.current_profile"]({}, dry_run=False)
    assert read["profile"]["display_name"] == "Owner"
    assert read["profile"]["timezone"] == "UTC"

    updated = plane.handlers()["users_access.update_current_profile"](
        {
            "display_name": "Dmitry",
            "timezone": "Europe/Moscow",
            "show_presence": False,
            "idempotency_key": "profile-dmitry-1",
            "_mcp_context": _context(),
        },
        dry_run=False,
    )
    assert updated["profile"]["display_name"] == "Dmitry"
    assert updated["profile"]["timezone"] == "Europe/Moscow"
    assert updated["profile"]["show_presence"] is False


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
