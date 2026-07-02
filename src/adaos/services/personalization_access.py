from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

from adaos.domain.personalization_access import (
    AuditRecord,
    DeviceKey,
    Grant,
    GrantConstraint,
    Invite,
    Membership,
    PolicyDecision,
    Preference,
    RecoveryAction,
    ROLE_PRESET_CAPABILITIES,
    ScopeRef,
    SessionKey,
    SubjectRef,
    UserKey,
    UserProfile,
    validate_capability,
)


class PersonalizationAccessError(RuntimeError):
    """Raised when the Phase 1 access kernel rejects a state transition."""


def _now_ts() -> float:
    return float(time.time())


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _ref_key(ref: SubjectRef | ScopeRef | Mapping[str, Any] | None) -> str:
    if ref is None:
        return ""
    if isinstance(ref, (SubjectRef, ScopeRef)):
        return ref.ref()
    data = _dict(ref)
    kind = str(data.get("kind") or "").strip()
    ref_id = str(data.get("id") or "").strip()
    return f"{kind}:{ref_id}" if kind and ref_id else ""


def _subject_from_dict(value: Mapping[str, Any]) -> SubjectRef:
    return SubjectRef(str(value.get("kind") or ""), str(value.get("id") or ""))


def _scope_from_dict(value: Mapping[str, Any]) -> ScopeRef:
    return ScopeRef(str(value.get("kind") or ""), str(value.get("id") or ""))


def _scopes_from_list(value: Any) -> tuple[ScopeRef, ...]:
    scopes: list[ScopeRef] = []
    for item in _list(value):
        data = _dict(item)
        if not data:
            continue
        scopes.append(_scope_from_dict(data))
    return tuple(scopes)


def _grant_constraint_from_dict(value: Mapping[str, Any] | None, *, fallback_expires_at: Any = None) -> GrantConstraint:
    data = _dict(value)
    expires_at = data.get("expires_at", fallback_expires_at)
    return GrantConstraint(
        expires_at=float(expires_at) if expires_at is not None else None,
        requires_approval_for=tuple(str(item) for item in _list(data.get("requires_approval_for"))),
        child_mode=bool(data.get("child_mode", False)),
        allowed_scopes=_scopes_from_list(data.get("allowed_scopes")),
        allowed_skill_classes=tuple(str(item) for item in _list(data.get("allowed_skill_classes"))),
        allowed_tool_classes=tuple(str(item) for item in _list(data.get("allowed_tool_classes"))),
        delegation=tuple(str(item) for item in _list(data.get("delegation"))),
    )


def _scope_matches(grant_scope: Mapping[str, Any] | None, requested_scope: ScopeRef | None) -> bool:
    if requested_scope is None:
        return True
    if not isinstance(grant_scope, Mapping):
        return False
    if _ref_key(grant_scope) == requested_scope.ref():
        return True
    # A subnet-level grant is intentionally broad for Phase 1.
    return str(grant_scope.get("kind") or "") == "subnet"


def _not_expired(record: Mapping[str, Any], *, now: float) -> bool:
    expires_at = record.get("expires_at")
    if expires_at is None:
        constraints = _dict(record.get("constraints"))
        expires_at = constraints.get("expires_at")
    if expires_at is None:
        return True
    try:
        return float(expires_at) > now
    except Exception:
        return False


def _record_status(record: Mapping[str, Any]) -> str:
    return str(record.get("status") or "active").strip() or "active"


class PersonalizationAccessStore:
    """Small JSON-backed Phase 1 store for identity/access facts.

    The store persists JSON-able contract dictionaries. Runtime services can
    later replace this backend without changing the policy kernel contract.
    """

    _BUCKETS = (
        "users",
        "profiles",
        "preferences",
        "user_keys",
        "device_keys",
        "sessions",
        "memberships",
        "grants",
        "invites",
        "recovery_actions",
        "revocations",
        "audit",
    )

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._data: dict[str, Any] = {key: ({} if key != "audit" else []) for key in self._BUCKETS}
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        for key in self._BUCKETS:
            if key == "audit":
                self._data[key] = _list(payload.get(key))
            else:
                self._data[key] = _dict(payload.get(key))

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._data, ensure_ascii=False))

    def put_user(self, subject: SubjectRef, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if subject.kind != "user":
            raise PersonalizationAccessError(f"user subject expected: {subject.ref()}")
        data = {
            "user_id": subject.id,
            "subject": subject.to_dict(),
            "metadata": dict(metadata or {}),
        }
        self._data["users"][subject.id] = data
        self.save()
        return data

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        data = self._data["users"].get(str(user_id or "").strip())
        return dict(data) if isinstance(data, Mapping) else None

    def put_profile(self, profile: UserProfile) -> dict[str, Any]:
        data = profile.to_dict()
        self._data["profiles"][profile.user_id] = data
        self.save()
        return data

    def get_profile(self, user_id: str) -> dict[str, Any] | None:
        data = self._data["profiles"].get(str(user_id or "").strip())
        return dict(data) if isinstance(data, Mapping) else None

    def put_preference(self, preference: Preference) -> dict[str, Any]:
        data = preference.to_dict()
        key = self._preference_key(preference.subject, preference.key, preference.scope)
        self._data["preferences"][key] = data
        self.save()
        return data

    def get_preference(
        self,
        subject: SubjectRef,
        key: str,
        scope: ScopeRef | None = None,
    ) -> dict[str, Any] | None:
        data = self._data["preferences"].get(self._preference_key(subject, key, scope))
        return dict(data) if isinstance(data, Mapping) else None

    def list_preferences(self, subject: SubjectRef, scope: ScopeRef | None = None) -> list[dict[str, Any]]:
        scope_key = scope.ref() if scope else ""
        result: list[dict[str, Any]] = []
        for raw in self._data["preferences"].values():
            if not isinstance(raw, Mapping):
                continue
            item = dict(raw)
            if _ref_key(item.get("subject")) != subject.ref():
                continue
            if scope is not None and _ref_key(item.get("scope")) != scope_key:
                continue
            result.append(item)
        result.sort(key=lambda item: str(item.get("key") or ""))
        return result

    def _preference_key(self, subject: SubjectRef, key: str, scope: ScopeRef | None = None) -> str:
        return "\0".join([subject.ref(), scope.ref() if scope else "", str(key or "").strip()])

    def put_user_key(self, key: UserKey) -> dict[str, Any]:
        data = key.to_dict()
        self._data["user_keys"][key.key_id] = data
        self.save()
        return data

    def get_user_key(self, key_id: str) -> dict[str, Any] | None:
        data = self._data["user_keys"].get(str(key_id or "").strip())
        return dict(data) if isinstance(data, Mapping) else None

    def update_user_key(self, key_id: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        data = self.get_user_key(key_id)
        if data is None:
            raise PersonalizationAccessError(f"user key not found: {key_id}")
        data.update(dict(patch))
        self._data["user_keys"][key_id] = data
        self.save()
        return data

    def put_device_key(self, key: DeviceKey) -> dict[str, Any]:
        data = key.to_dict()
        self._data["device_keys"][key.device_id] = data
        self.save()
        return data

    def get_device_key(self, device_id: str) -> dict[str, Any] | None:
        data = self._data["device_keys"].get(str(device_id or "").strip())
        return dict(data) if isinstance(data, Mapping) else None

    def update_device_key(self, device_id: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        data = self.get_device_key(device_id)
        if data is None:
            raise PersonalizationAccessError(f"device key not found: {device_id}")
        data.update(dict(patch))
        self._data["device_keys"][device_id] = data
        self.save()
        return data

    def put_session(self, session: SessionKey) -> dict[str, Any]:
        data = session.to_dict()
        self._data["sessions"][session.session_id] = data
        self.save()
        return data

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        data = self._data["sessions"].get(str(session_id or "").strip())
        return dict(data) if isinstance(data, Mapping) else None

    def update_session(self, session_id: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        data = self.get_session(session_id)
        if data is None:
            raise PersonalizationAccessError(f"session not found: {session_id}")
        data.update(dict(patch))
        self._data["sessions"][session_id] = data
        self.save()
        return data

    def put_membership(self, membership: Membership) -> dict[str, Any]:
        data = membership.to_dict()
        key = membership.grant_id or f"{membership.subject.ref()}@{membership.scope.ref()}"
        self._data["memberships"][key] = data
        self.save()
        return data

    def put_grant(self, grant: Grant) -> dict[str, Any]:
        data = grant.to_dict()
        self._data["grants"][grant.grant_id] = data
        self.save()
        return data

    def get_grant(self, grant_id: str) -> dict[str, Any] | None:
        data = self._data["grants"].get(str(grant_id or "").strip())
        return dict(data) if isinstance(data, Mapping) else None

    def update_grant(self, grant_id: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        data = self.get_grant(grant_id)
        if data is None:
            raise PersonalizationAccessError(f"grant not found: {grant_id}")
        data.update(dict(patch))
        self._data["grants"][grant_id] = data
        self.save()
        return data

    def put_invite(self, invite: Invite) -> dict[str, Any]:
        existing = _dict(self._data["invites"].get(invite.invite_id))
        if existing and _record_status(existing) in {"accepted", "expired", "revoked"}:
            raise PersonalizationAccessError(f"invite is not mutable: {invite.invite_id}")
        data = invite.to_dict()
        self._data["invites"][invite.invite_id] = data
        self.save()
        return data

    def get_invite(self, invite_id: str) -> dict[str, Any] | None:
        data = self._data["invites"].get(str(invite_id or "").strip())
        return dict(data) if isinstance(data, Mapping) else None

    def update_invite(self, invite_id: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        data = self.get_invite(invite_id)
        if data is None:
            raise PersonalizationAccessError(f"invite not found: {invite_id}")
        data.update(dict(patch))
        self._data["invites"][invite_id] = data
        self.save()
        return data

    def put_recovery_action(self, action: RecoveryAction) -> dict[str, Any]:
        existing = _dict(self._data["recovery_actions"].get(action.recovery_id))
        if existing and _record_status(existing) in {"accepted", "expired", "revoked"}:
            raise PersonalizationAccessError(f"recovery action is not mutable: {action.recovery_id}")
        data = action.to_dict()
        self._data["recovery_actions"][action.recovery_id] = data
        self.save()
        return data

    def get_recovery_action(self, recovery_id: str) -> dict[str, Any] | None:
        data = self._data["recovery_actions"].get(str(recovery_id or "").strip())
        return dict(data) if isinstance(data, Mapping) else None

    def update_recovery_action(self, recovery_id: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        data = self.get_recovery_action(recovery_id)
        if data is None:
            raise PersonalizationAccessError(f"recovery action not found: {recovery_id}")
        data.update(dict(patch))
        self._data["recovery_actions"][recovery_id] = data
        self.save()
        return data

    def append_audit(self, record: AuditRecord) -> dict[str, Any]:
        data = record.to_dict()
        self._data["audit"].append(data)
        self.save()
        return data

    def append_revocation(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record)
        data.setdefault("ts", _now_ts())
        self._data["revocations"][str(data.get("revocation_id") or f"revocation-{uuid4().hex}")] = data
        self.save()
        return data

    def iter_sessions(self, *, status: str = "active") -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self._data["sessions"].values()
            if isinstance(item, Mapping) and _record_status(item) == status
        ]

    def iter_invites(self, *, status: str = "pending") -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self._data["invites"].values()
            if isinstance(item, Mapping) and _record_status(item) == status
        ]

    def iter_grants(self, *, status: str = "active") -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self._data["grants"].values()
            if isinstance(item, Mapping) and _record_status(item) == status
        ]

    def iter_memberships(self, *, status: str = "active") -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self._data["memberships"].values()
            if isinstance(item, Mapping) and _record_status(item) == status
        ]

    def list_audit(
        self,
        *,
        actor: SubjectRef | None = None,
        subject: SubjectRef | None = None,
        scope: ScopeRef | None = None,
        device: SubjectRef | None = None,
        session: SubjectRef | None = None,
        source: str | None = None,
        decision: str | None = None,
        event_type: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        max_items = max(1, int(limit))
        matches: list[dict[str, Any]] = []
        for raw in reversed(self._data["audit"]):
            if not isinstance(raw, Mapping):
                continue
            item = dict(raw)
            if actor is not None and _ref_key(item.get("actor")) != actor.ref():
                continue
            if subject is not None and _ref_key(item.get("subject")) != subject.ref():
                continue
            if scope is not None and _ref_key(item.get("scope")) != scope.ref():
                continue
            if device is not None and _ref_key(item.get("device")) != device.ref():
                continue
            if session is not None and _ref_key(item.get("session")) != session.ref():
                continue
            if source and str(item.get("source") or "") != source:
                continue
            if event_type and str(item.get("event_type") or "") != event_type:
                continue
            ts = float(item.get("ts") or 0)
            if since is not None and ts < float(since):
                continue
            if until is not None and ts > float(until):
                continue
            if decision:
                item_decision = _dict(item.get("decision"))
                if str(item_decision.get("decision") or "") != decision:
                    continue
            matches.append(item)
            if len(matches) >= max_items:
                break
        return matches


class PersonalizationAccessService:
    """Phase 1 policy/audit kernel over the Phase 0 contract records."""

    def __init__(
        self,
        store: PersonalizationAccessStore | None = None,
        *,
        owner: SubjectRef | None = None,
        access_link_denier: Callable[[str], Any] | None = None,
    ) -> None:
        self.store = store or PersonalizationAccessStore()
        self.owner = owner or SubjectRef("user", "owner")
        self.access_link_denier = access_link_denier

    def put_user(
        self,
        subject: SubjectRef,
        *,
        actor: SubjectRef | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = self.store.put_user(subject, metadata=metadata)
        self._audit(
            "user.created",
            actor=actor or SubjectRef("service", "personalization_access"),
            subject=subject,
            metadata={"user_id": subject.id},
        )
        return data

    def put_profile(self, profile: UserProfile, *, actor: SubjectRef | None = None) -> dict[str, Any]:
        data = self.store.put_profile(profile)
        self._audit(
            "profile.updated",
            actor=actor or SubjectRef("service", "personalization_access"),
            subject=SubjectRef("user", profile.user_id),
            redacted_diff={"profile": "<redacted>"},
        )
        return data

    def put_preference(self, preference: Preference, *, actor: SubjectRef | None = None) -> dict[str, Any]:
        data = self.store.put_preference(preference)
        self._audit(
            "preference.updated",
            actor=actor or preference.subject,
            subject=preference.subject,
            scope=preference.scope,
            redacted_diff={"preference": "<redacted>", "key": preference.key},
            metadata={"key": preference.key, "device_override": preference.device_override},
        )
        return data

    def list_preferences(self, subject: SubjectRef, scope: ScopeRef | None = None) -> list[dict[str, Any]]:
        return self.store.list_preferences(subject, scope=scope)

    def put_user_key(self, key: UserKey, *, actor: SubjectRef | None = None) -> dict[str, Any]:
        data = self.store.put_user_key(key)
        self._audit(
            "key.user_created",
            actor=actor or SubjectRef("service", "personalization_access"),
            subject=SubjectRef("user", key.user_id),
            metadata={"key_id": key.key_id, "status": key.status},
        )
        return data

    def revoke_user_key(self, key_id: str, *, actor: SubjectRef, reason: str | None = None) -> dict[str, Any]:
        data = self.store.update_user_key(key_id, {"status": "revoked", "revoked_at": _now_ts()})
        self.store.append_revocation(
            {
                "revocation_id": f"user_key:{key_id}",
                "kind": "user_key",
                "key_id": key_id,
                "actor": actor.to_dict(),
                "reason": str(reason or "").strip() or None,
            }
        )
        self._audit(
            "key.user_revoked",
            actor=actor,
            subject=SubjectRef("user", str(data.get("user_id") or "")),
            metadata={"key_id": key_id, "reason": str(reason or "").strip() or None},
        )
        return data

    def put_device_key(self, key: DeviceKey, *, actor: SubjectRef | None = None) -> dict[str, Any]:
        data = self.store.put_device_key(key)
        self._audit(
            "device.paired",
            actor=actor or SubjectRef("service", "personalization_access"),
            subject=SubjectRef("user", key.user_id),
            device=SubjectRef("device", key.device_id),
            metadata={"device_id": key.device_id, "key_id": key.key_id, "trust_level": key.trust_level},
        )
        return data

    def revoke_device(self, device_id: str, *, actor: SubjectRef, reason: str | None = None) -> dict[str, Any]:
        decision = self.evaluate(actor=actor, action="devices.revoke.any", subject=SubjectRef("device", device_id))
        if decision.decision != "allow":
            raise PermissionError(f"policy denied: {decision.reason_code or 'devices.revoke.any'}")
        data = self.store.update_device_key(device_id, {"status": "revoked", "revoked_at": _now_ts()})
        revoked_sessions: list[str] = []
        denier = self.access_link_denier
        if callable(denier):
            denier(device_id)
        for session in self.store.iter_sessions():
            if str(session.get("device_id") or "") != device_id:
                continue
            session_id = str(session.get("session_id") or "")
            if not session_id:
                continue
            self.store.update_session(session_id, {"status": "revoked", "revoked_at": _now_ts()})
            if callable(denier):
                denier(session_id)
            revoked_sessions.append(session_id)
        self.store.append_revocation(
            {
                "revocation_id": f"device:{device_id}",
                "kind": "device",
                "device_id": device_id,
                "actor": actor.to_dict(),
                "revoked_sessions": revoked_sessions,
                "reason": str(reason or "").strip() or None,
            }
        )
        self._audit(
            "device.revoked",
            actor=actor,
            subject=SubjectRef("user", str(data.get("user_id") or "")),
            device=SubjectRef("device", device_id),
            metadata={
                "device_id": device_id,
                "revoked_sessions": revoked_sessions,
                "reason": str(reason or "").strip() or None,
            },
        )
        return data

    def put_session(self, session: SessionKey) -> dict[str, Any]:
        data = self.store.put_session(session)
        self._audit(
            "session.created",
            actor=session.subject or SubjectRef("service", "personalization_access"),
            subject=session.subject,
            session=SubjectRef("session", session.session_id),
            metadata={"session_id": session.session_id, "device_id": session.device_id, "status": session.status},
        )
        return data

    def revoke_session(self, session_id: str, *, actor: SubjectRef, reason: str | None = None) -> dict[str, Any]:
        data = self.store.update_session(session_id, {"status": "revoked", "revoked_at": _now_ts()})
        self.store.append_revocation(
            {
                "revocation_id": f"session:{session_id}",
                "kind": "session",
                "session_id": session_id,
                "actor": actor.to_dict(),
                "reason": str(reason or "").strip() or None,
            }
        )
        self._audit(
            "session.revoked",
            actor=actor,
            subject=_subject_from_dict(_dict(data.get("subject"))) if data.get("subject") else None,
            session=SubjectRef("session", session_id),
            metadata={"session_id": session_id, "reason": str(reason or "").strip() or None},
        )
        return data

    def put_membership(self, membership: Membership, *, actor: SubjectRef | None = None) -> dict[str, Any]:
        data = self.store.put_membership(membership)
        self._audit(
            "membership.granted",
            actor=actor or membership.issued_by or SubjectRef("service", "personalization_access"),
            subject=membership.subject,
            scope=membership.scope,
            metadata={"role": membership.role, "status": membership.status},
        )
        return data

    def put_grant(self, grant: Grant, *, actor: SubjectRef | None = None) -> dict[str, Any]:
        data = self.store.put_grant(grant)
        self._audit(
            "grant.created",
            actor=actor or grant.issued_by or SubjectRef("service", "personalization_access"),
            subject=grant.subject,
            scope=grant.scope,
            metadata={"grant_id": grant.grant_id, "role": grant.role, "capabilities": list(grant.capabilities)},
        )
        return data

    def revoke_grant(self, grant_id: str, *, actor: SubjectRef, reason: str | None = None) -> dict[str, Any]:
        data = self.store.update_grant(grant_id, {"status": "revoked", "revoked_at": _now_ts()})
        self._audit(
            "grant.revoked",
            actor=actor,
            subject=_subject_from_dict(_dict(data.get("subject"))),
            scope=_scope_from_dict(_dict(data.get("scope"))),
            metadata={"grant_id": grant_id, "reason": str(reason or "").strip() or None},
        )
        return data

    def put_invite(self, invite: Invite) -> dict[str, Any]:
        data = self.store.put_invite(invite)
        self._audit(
            "invite.created",
            actor=invite.issued_by,
            scope=invite.scope,
            metadata={"invite_id": invite.invite_id, "kind": invite.kind, "role": invite.role},
        )
        return data

    def create_guest_join_link(
        self,
        *,
        invite_id: str,
        scope: ScopeRef,
        issued_by: SubjectRef,
        expires_at: float | None,
        max_sessions: int = 50,
        max_pending_per_issuer: int = 5,
    ) -> dict[str, Any]:
        if expires_at is None:
            raise PersonalizationAccessError("guest_join_link requires expires_at")
        self._check_invite_rate(issued_by, "guest_join_link", max_pending_per_issuer)
        return self.put_invite(
            Invite(
                invite_id=invite_id,
                kind="guest_join_link",
                scope=scope,
                role="guest",
                issued_by=issued_by,
                expires_at=expires_at,
                single_use=False,
                max_sessions=max_sessions,
            )
        )

    def create_targeted_invite_link(
        self,
        *,
        invite_id: str,
        scope: ScopeRef,
        role: str,
        issued_by: SubjectRef,
        profile_hint: str,
        expires_at: float | None,
        constraints: GrantConstraint | None = None,
        max_pending_per_issuer: int = 20,
    ) -> dict[str, Any]:
        if expires_at is None:
            raise PersonalizationAccessError("targeted_invite_link requires expires_at")
        self._check_invite_rate(issued_by, "targeted_invite_link", max_pending_per_issuer)
        return self.put_invite(
            Invite(
                invite_id=invite_id,
                kind="targeted_invite_link",
                scope=scope,
                role=role,  # type: ignore[arg-type]
                issued_by=issued_by,
                profile_hint=profile_hint,
                expires_at=expires_at,
                single_use=True,
                max_sessions=1,
                constraints=constraints or GrantConstraint(),
            )
        )

    def create_device_pairing_link(
        self,
        *,
        invite_id: str,
        subject: SubjectRef,
        scope: ScopeRef,
        role: str,
        issued_by: SubjectRef,
        expires_at: float | None,
        device_id: str | None = None,
        device_name: str | None = None,
        max_pending_per_issuer: int = 20,
    ) -> dict[str, Any]:
        if subject.kind != "user":
            raise PersonalizationAccessError(f"user subject expected: {subject.ref()}")
        if expires_at is None:
            raise PersonalizationAccessError("device_pairing_link requires expires_at")
        action = "devices.add.self" if issued_by.ref() == subject.ref() else "devices.add.any"
        decision = self.evaluate(actor=issued_by, action=action, subject=subject)
        if decision.decision != "allow":
            raise PermissionError(f"policy denied: {decision.reason_code or action}")
        self._check_invite_rate(issued_by, "device_pairing_link", max_pending_per_issuer)
        invite = self.put_invite(
            Invite(
                invite_id=invite_id,
                kind="device_pairing_link",
                scope=scope,
                role=role,  # type: ignore[arg-type]
                issued_by=issued_by,
                profile_hint=subject.id,
                expires_at=expires_at,
                single_use=True,
                max_sessions=1,
            )
        )
        return self.store.update_invite(
            invite_id,
            {
                **invite,
                "subject_id": subject.id,
                "device_id": str(device_id or "").strip() or None,
                "device_name": str(device_name or "").strip() or None,
            },
        )

    def claim_device_pairing_link(
        self,
        invite_id: str,
        *,
        subject: SubjectRef,
        actor: SubjectRef | None = None,
        device_id: str,
        key_id: str | None = None,
        public_key_ref: str | None = None,
        session_id: str | None = None,
        device_name: str | None = None,
    ) -> dict[str, Any]:
        invite = self.store.get_invite(invite_id)
        if invite is None:
            raise PersonalizationAccessError(f"invite not found: {invite_id}")
        if str(invite.get("kind") or "") != "device_pairing_link":
            raise PersonalizationAccessError(f"invite is not a device pairing link: {invite_id}")
        expected_subject_id = str(invite.get("subject_id") or invite.get("profile_hint") or "").strip()
        if expected_subject_id and subject.id != expected_subject_id:
            raise PersonalizationAccessError("device pairing subject mismatch")
        clean_device_id = str(device_id or invite.get("device_id") or "").strip()
        if not clean_device_id:
            raise PersonalizationAccessError("device_id is required for device pairing")
        clean_session_id = str(session_id or clean_device_id).strip()
        accepted = self.claim_invite(
            invite_id,
            accepted_by=subject,
            actor=actor or subject,
            session_id=clean_session_id,
            create_grant=False,
        )
        self.put_user(subject, actor=actor or subject)
        key = DeviceKey(
            user_id=subject.id,
            device_id=clean_device_id,
            key_id=str(key_id or f"device:{clean_device_id}").strip(),
            public_key_ref=str(public_key_ref or f"local-device:{clean_device_id}").strip(),
            trust_level="trusted",
            created_at=_now_ts(),
            last_used_at=_now_ts(),
        )
        device = self.put_device_key(key, actor=actor or subject)
        session = self.put_session(
            SessionKey(
                session_id=clean_session_id,
                key_id=key.key_id,
                subject=subject,
                device_id=clean_device_id,
                expires_at=accepted.get("expires_at"),
            )
        )
        data = self.store.update_invite(
            invite_id,
            {
                "paired_device_id": clean_device_id,
                "device_name": str(device_name or invite.get("device_name") or "").strip() or None,
                "session_id": clean_session_id,
            },
        )
        self._audit(
            "device.pairing_completed",
            actor=actor or subject,
            subject=subject,
            scope=_scope_from_dict(_dict(data.get("scope"))),
            device=SubjectRef("device", clean_device_id),
            session=SubjectRef("session", clean_session_id),
            metadata={"invite_id": invite_id, "device_id": clean_device_id, "session_id": clean_session_id},
        )
        return {"invite": data, "device": device, "session": session}

    def create_admin_recovery_link(
        self,
        *,
        invite_id: str,
        recovery_id: str,
        subject: SubjectRef,
        scope: ScopeRef,
        issued_by: SubjectRef,
        expires_at: float | None,
        replacement_device_id: str | None = None,
        revoked_device_ids: tuple[str, ...] = (),
        reason: str | None = None,
        max_pending_per_issuer: int = 20,
    ) -> dict[str, Any]:
        if subject.kind != "user":
            raise PersonalizationAccessError(f"user subject expected: {subject.ref()}")
        if expires_at is None:
            raise PersonalizationAccessError("admin_recovery_link requires expires_at")
        decision = self.evaluate(actor=issued_by, action="devices.add.any", subject=subject)
        if decision.decision != "allow":
            raise PermissionError(f"policy denied: {decision.reason_code or 'devices.add.any'}")
        self._check_invite_rate(issued_by, "admin_recovery_link", max_pending_per_issuer)
        recovery = self.put_recovery_action(
            RecoveryAction(
                recovery_id=recovery_id,
                subject=subject,
                issued_by=issued_by,
                replacement_device_id=replacement_device_id,
                revoked_device_ids=revoked_device_ids,
                reason=reason,
                created_at=_now_ts(),
            )
        )
        invite = self.put_invite(
            Invite(
                invite_id=invite_id,
                kind="admin_recovery_link",
                scope=scope,
                role="member",
                issued_by=issued_by,
                profile_hint=subject.id,
                expires_at=expires_at,
                single_use=True,
                max_sessions=1,
            )
        )
        data = self.store.update_invite(
            invite_id,
            {
                **invite,
                "subject_id": subject.id,
                "recovery_id": recovery_id,
                "replacement_device_id": str(replacement_device_id or "").strip() or None,
                "revoked_device_ids": list(revoked_device_ids),
                "reason": str(reason or "").strip() or None,
            },
        )
        return {"invite": data, "recovery": recovery}

    def complete_admin_recovery_link(
        self,
        invite_id: str,
        *,
        subject: SubjectRef,
        actor: SubjectRef | None = None,
        replacement_device_id: str,
        key_id: str | None = None,
        public_key_ref: str | None = None,
        session_id: str | None = None,
        revoke_device_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        invite = self.store.get_invite(invite_id)
        if invite is None:
            raise PersonalizationAccessError(f"invite not found: {invite_id}")
        if str(invite.get("kind") or "") != "admin_recovery_link":
            raise PersonalizationAccessError(f"invite is not an admin recovery link: {invite_id}")
        expected_subject_id = str(invite.get("subject_id") or invite.get("profile_hint") or "").strip()
        if expected_subject_id and subject.id != expected_subject_id:
            raise PersonalizationAccessError("recovery subject mismatch")
        clean_device_id = str(replacement_device_id or invite.get("replacement_device_id") or "").strip()
        if not clean_device_id:
            raise PersonalizationAccessError("replacement_device_id is required for admin recovery")
        clean_session_id = str(session_id or clean_device_id).strip()
        issued_by = _subject_from_dict(_dict(invite.get("issued_by")))
        admin_actor = actor or issued_by
        accepted = self.claim_invite(
            invite_id,
            accepted_by=subject,
            actor=subject,
            session_id=clean_session_id,
            create_grant=False,
        )
        self.put_user(subject, actor=admin_actor)
        key = DeviceKey(
            user_id=subject.id,
            device_id=clean_device_id,
            key_id=str(key_id or f"device:{clean_device_id}").strip(),
            public_key_ref=str(public_key_ref or f"local-device:{clean_device_id}").strip(),
            trust_level="trusted",
            created_at=_now_ts(),
            last_used_at=_now_ts(),
        )
        device = self.put_device_key(key, actor=admin_actor)
        session = self.put_session(
            SessionKey(
                session_id=clean_session_id,
                key_id=key.key_id,
                subject=subject,
                device_id=clean_device_id,
                expires_at=accepted.get("expires_at"),
            )
        )
        revoked_devices: list[str] = []
        for item in [*list(revoke_device_ids), *list(_list(invite.get("revoked_device_ids")))]:
            old_device_id = str(item or "").strip()
            if not old_device_id or old_device_id in revoked_devices:
                continue
            if self.store.get_device_key(old_device_id):
                self.revoke_device(old_device_id, actor=admin_actor, reason="admin_recovery")
                revoked_devices.append(old_device_id)
        recovery_id = str(invite.get("recovery_id") or "").strip()
        recovery = self.complete_recovery_action(recovery_id, actor=admin_actor) if recovery_id else None
        data = self.store.update_invite(
            invite_id,
            {
                "replacement_device_id": clean_device_id,
                "session_id": clean_session_id,
                "revoked_device_ids": revoked_devices,
            },
        )
        self._audit(
            "admin_recovery.completed",
            actor=admin_actor,
            subject=subject,
            scope=_scope_from_dict(_dict(data.get("scope"))),
            device=SubjectRef("device", clean_device_id),
            session=SubjectRef("session", clean_session_id),
            metadata={
                "invite_id": invite_id,
                "recovery_id": recovery_id or None,
                "replacement_device_id": clean_device_id,
                "revoked_device_ids": revoked_devices,
            },
        )
        return {"invite": data, "recovery": recovery, "device": device, "session": session}

    def preview_invite(self, invite_id: str, *, expected_scope: ScopeRef | None = None) -> dict[str, Any]:
        invite = self.store.get_invite(invite_id)
        if invite is None:
            raise PersonalizationAccessError(f"invite not found: {invite_id}")
        scope = _scope_from_dict(_dict(invite.get("scope")))
        if expected_scope is not None and scope.ref() != expected_scope.ref():
            raise PersonalizationAccessError(f"invite scope mismatch: {invite_id}")
        status = _record_status(invite)
        can_accept = status == "pending" and _not_expired(invite, now=_now_ts())
        return {
            "invite_id": invite_id,
            "kind": invite.get("kind"),
            "scope": scope.to_dict(),
            "role": invite.get("role"),
            "expires_at": invite.get("expires_at"),
            "profile_hint": invite.get("profile_hint"),
            "subject_id": invite.get("subject_id"),
            "device_id": invite.get("device_id") or invite.get("replacement_device_id"),
            "device_name": invite.get("device_name"),
            "recovery_id": invite.get("recovery_id"),
            "revoked_device_ids": list(_list(invite.get("revoked_device_ids"))),
            "requires_acceptance": True,
            "status": status,
            "can_accept": can_accept,
            "claim_count": int(invite.get("claim_count") or 0),
            "max_sessions": int(invite.get("max_sessions") or 1),
        }

    def claim_invite(
        self,
        invite_id: str,
        *,
        accepted_by: SubjectRef,
        actor: SubjectRef | None = None,
        expected_scope: ScopeRef | None = None,
        session_id: str | None = None,
        create_grant: bool = True,
    ) -> dict[str, Any]:
        invite = self.store.get_invite(invite_id)
        if invite is None:
            raise PersonalizationAccessError(f"invite not found: {invite_id}")
        now = _now_ts()
        scope = _scope_from_dict(_dict(invite.get("scope")))
        if expected_scope is not None and scope.ref() != expected_scope.ref():
            raise PersonalizationAccessError(f"invite scope mismatch: {invite_id}")
        if _record_status(invite) != "pending":
            raise PersonalizationAccessError(f"invite is not pending: {invite_id}")
        if not _not_expired(invite, now=now):
            self.store.update_invite(invite_id, {"status": "expired"})
            raise PersonalizationAccessError(f"invite expired: {invite_id}")
        claims = [dict(item) for item in _list(invite.get("claims")) if isinstance(item, Mapping)]
        max_sessions = max(1, int(invite.get("max_sessions") or 1))
        if len(claims) >= max_sessions:
            self.store.update_invite(invite_id, {"status": "accepted"})
            raise PersonalizationAccessError(f"invite session limit reached: {invite_id}")
        kind = str(invite.get("kind") or "")
        if kind == "guest_join_link" and accepted_by.kind == "user":
            raise PersonalizationAccessError("guest_join_link cannot bind a personal profile")
        claim = {
            "subject": accepted_by.to_dict(),
            "accepted_at": now,
        }
        clean_session_id = str(session_id or "").strip() or None
        if clean_session_id:
            claim["session_id"] = clean_session_id
        claims.append(claim)
        single_use = bool(invite.get("single_use", True))
        new_status = "accepted" if single_use or len(claims) >= max_sessions else "pending"
        data = self.store.update_invite(
            invite_id,
            {
                "status": new_status,
                "accepted_by": accepted_by.to_dict(),
                "accepted_at": now,
                "claim_count": len(claims),
                "claims": claims,
            },
        )
        grant_id: str | None = None
        if create_grant:
            grant_id = self._issue_invite_grant(data, accepted_by=accepted_by, actor=actor or accepted_by, session_id=clean_session_id)
        self._audit(
            "invite.accepted",
            actor=actor or accepted_by,
            subject=accepted_by,
            scope=scope,
            session=SubjectRef("session", clean_session_id) if clean_session_id else None,
            metadata={"invite_id": invite_id, "kind": data.get("kind"), "grant_id": grant_id},
        )
        return data

    def bind_session_to_profile(
        self,
        *,
        session_id: str,
        subject: SubjectRef,
        actor: SubjectRef,
        profile: UserProfile | None = None,
    ) -> dict[str, Any]:
        if subject.kind != "user":
            raise PersonalizationAccessError(f"user subject expected: {subject.ref()}")
        self.put_user(subject, actor=actor)
        if profile is not None:
            self.put_profile(profile, actor=actor)
        existing = self.store.get_session(session_id)
        patch = {"subject": subject.to_dict(), "status": "active"}
        if existing is None:
            data = self.put_session(SessionKey(session_id=session_id, key_id=f"profile-bind:{session_id}", subject=subject))
        else:
            data = self.store.update_session(session_id, patch)
        self._audit(
            "session.bound",
            actor=actor,
            subject=subject,
            session=SubjectRef("session", session_id),
            metadata={"session_id": session_id},
        )
        return data

    def revoke_invite(
        self,
        invite_id: str,
        *,
        actor: SubjectRef,
        reason: str | None = None,
        deny_access_link: Callable[[str], Any] | None = None,
    ) -> dict[str, Any]:
        invite = self.store.get_invite(invite_id)
        if invite is None:
            raise PersonalizationAccessError(f"invite not found: {invite_id}")
        data = self.store.update_invite(invite_id, {"status": "revoked", "revoked_at": _now_ts()})
        revoked_grants: list[str] = []
        for grant in self.store.iter_grants():
            metadata = _dict(grant.get("metadata"))
            grant_id = str(grant.get("grant_id") or "")
            if metadata.get("invite_id") != invite_id and not grant_id.startswith(f"invite:{invite_id}:"):
                continue
            self.revoke_grant(grant_id, actor=actor, reason=reason or "invite_revoked")
            revoked_grants.append(grant_id)
        revoked_sessions: list[str] = []
        denier = deny_access_link or self.access_link_denier
        for claim in _list(data.get("claims")):
            claim_data = _dict(claim)
            session_id = str(claim_data.get("session_id") or "").strip()
            if not session_id:
                subject = _dict(claim_data.get("subject"))
                if str(subject.get("kind") or "") == "session":
                    session_id = str(subject.get("id") or "").strip()
            if not session_id:
                continue
            if self.store.get_session(session_id):
                self.revoke_session(session_id, actor=actor, reason=reason or "invite_revoked")
            if callable(denier):
                denier(session_id)
            revoked_sessions.append(session_id)
        self._audit(
            "invite.revoked",
            actor=actor,
            scope=_scope_from_dict(_dict(data.get("scope"))),
            metadata={
                "invite_id": invite_id,
                "reason": str(reason or "").strip() or None,
                "revoked_grants": revoked_grants,
                "revoked_sessions": revoked_sessions,
            },
        )
        return data

    def revoke_guest_join_sessions(
        self,
        invite_id: str,
        *,
        actor: SubjectRef,
        reason: str | None = None,
        deny_access_link: Callable[[str], Any] | None = None,
    ) -> dict[str, Any]:
        invite = self.store.get_invite(invite_id)
        if invite is None:
            raise PersonalizationAccessError(f"invite not found: {invite_id}")
        if str(invite.get("kind") or "") != "guest_join_link":
            raise PersonalizationAccessError(f"invite is not a guest join link: {invite_id}")
        return self.revoke_invite(invite_id, actor=actor, reason=reason, deny_access_link=deny_access_link)

    def _check_invite_rate(self, issued_by: SubjectRef, kind: str, max_pending: int) -> None:
        if max_pending <= 0:
            return
        now = _now_ts()
        count = 0
        for invite in self.store.iter_invites():
            if str(invite.get("kind") or "") != kind:
                continue
            if _ref_key(invite.get("issued_by")) != issued_by.ref():
                continue
            if not _not_expired(invite, now=now):
                continue
            count += 1
        if count >= max_pending:
            raise PersonalizationAccessError(f"pending invite rate limit reached for {issued_by.ref()}")

    def _issue_invite_grant(
        self,
        invite: Mapping[str, Any],
        *,
        accepted_by: SubjectRef,
        actor: SubjectRef,
        session_id: str | None,
    ) -> str:
        invite_id = str(invite.get("invite_id") or "")
        kind = str(invite.get("kind") or "")
        role = str(invite.get("role") or "guest")
        scope = _scope_from_dict(_dict(invite.get("scope")))
        grant_id = f"invite:{invite_id}:{accepted_by.ref()}"
        constraints = _grant_constraint_from_dict(_dict(invite.get("constraints")), fallback_expires_at=invite.get("expires_at"))
        grant = Grant(
            grant_id=grant_id,
            subject=accepted_by,
            scope=scope,
            role=role,  # type: ignore[arg-type]
            constraints=constraints,
            issued_by=actor,
        )
        self.put_grant(grant, actor=actor)
        self.store.update_grant(
            grant_id,
            {
                "metadata": {
                    "invite_id": invite_id,
                    "invite_kind": kind,
                    "session_id": session_id,
                }
            },
        )
        if accepted_by.kind == "user":
            self.put_user(accepted_by, actor=actor)
            self.put_membership(
                Membership(
                    subject=accepted_by,
                    scope=scope,
                    role=role,  # type: ignore[arg-type]
                    grant_id=f"membership:{grant_id}",
                    issued_by=actor,
                    expires_at=invite.get("expires_at"),
                ),
                actor=actor,
            )
        if session_id:
            existing = self.store.get_session(session_id)
            patch = {"subject": accepted_by.to_dict(), "status": "active"}
            if existing is None:
                self.put_session(
                    SessionKey(
                        session_id=session_id,
                        key_id=f"invite:{invite_id}",
                        subject=accepted_by,
                        device_id=session_id,
                        expires_at=invite.get("expires_at"),
                    )
                )
            else:
                self.store.update_session(session_id, patch)
        return grant_id

    def grant_role_preset(
        self,
        *,
        subject: SubjectRef,
        scope: ScopeRef,
        role: str,
        actor: SubjectRef,
        expires_at: float | None = None,
    ) -> dict[str, Any]:
        decision = self.evaluate(actor=actor, action="memberships.grant", subject=subject, scope=scope)
        if decision.decision != "allow":
            raise PermissionError(f"policy denied: {decision.reason_code or 'memberships.grant'}")
        grant_id = f"grant:{scope.ref()}:{subject.ref()}:{role}:{uuid4().hex}"
        grant = Grant(
            grant_id=grant_id,
            subject=subject,
            scope=scope,
            role=role,  # type: ignore[arg-type]
            constraints=GrantConstraint(expires_at=expires_at),
            issued_by=actor,
        )
        grant_data = self.put_grant(grant, actor=actor)
        membership = Membership(
            subject=subject,
            scope=scope,
            role=role,  # type: ignore[arg-type]
            grant_id=f"membership:{grant_id}",
            issued_by=actor,
            expires_at=expires_at,
        )
        membership_data = self.put_membership(membership, actor=actor)
        self.put_user(subject, actor=actor, metadata={"source": "admin_grant"})
        return {"grant": grant_data, "membership": membership_data}

    def admin_summary(self, *, actor: SubjectRef, audit_limit: int = 50) -> dict[str, Any]:
        decision = self.evaluate(actor=actor, action="users.manage")
        if decision.decision != "allow":
            raise PermissionError(f"policy denied: {decision.reason_code or 'users.manage'}")
        snapshot = self.store.snapshot()

        def values(bucket: str) -> list[dict[str, Any]]:
            raw = snapshot.get(bucket)
            if not isinstance(raw, Mapping):
                return []
            return [dict(item) for item in raw.values() if isinstance(item, Mapping)]

        profiles: list[dict[str, Any]] = []
        for item in values("profiles"):
            profiles.append(
                {
                    "user_id": item.get("user_id"),
                    "display_name": item.get("display_name"),
                    "preferred_name": item.get("preferred_name"),
                    "locale": item.get("locale"),
                    "language": item.get("language"),
                    "timezone": item.get("timezone"),
                    "metadata_only": True,
                }
            )
        return {
            "users": values("users"),
            "profiles": profiles,
            "devices": values("device_keys"),
            "sessions": values("sessions"),
            "memberships": values("memberships"),
            "grants": values("grants"),
            "invites": values("invites"),
            "recovery_actions": values("recovery_actions"),
            "audit": self.store.list_audit(limit=audit_limit),
        }

    def put_recovery_action(self, action: RecoveryAction) -> dict[str, Any]:
        data = self.store.put_recovery_action(action)
        self._audit(
            "recovery.started",
            actor=action.issued_by,
            subject=action.subject,
            metadata={"recovery_id": action.recovery_id, "replacement_device_id": action.replacement_device_id},
        )
        return data

    def complete_recovery_action(self, recovery_id: str, *, actor: SubjectRef) -> dict[str, Any]:
        action = self.store.get_recovery_action(recovery_id)
        if action is None:
            raise PersonalizationAccessError(f"recovery action not found: {recovery_id}")
        if _record_status(action) != "pending":
            raise PersonalizationAccessError(f"recovery action is not pending: {recovery_id}")
        data = self.store.update_recovery_action(recovery_id, {"status": "accepted", "completed_at": _now_ts()})
        self._audit(
            "recovery.completed",
            actor=actor,
            subject=_subject_from_dict(_dict(data.get("subject"))),
            metadata={"recovery_id": recovery_id, "replacement_device_id": data.get("replacement_device_id")},
        )
        return data

    def evaluate(
        self,
        *,
        actor: SubjectRef,
        action: str,
        subject: SubjectRef | None = None,
        scope: ScopeRef | None = None,
        resource: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> PolicyDecision:
        capability = validate_capability(action)
        context_data = _dict(context)
        now = _now_ts()
        actor_for_policy = actor
        session_ref: SubjectRef | None = None
        device_ref: SubjectRef | None = None
        if actor.kind == "session":
            session_ref = actor
            session_record = self.store.get_session(actor.id)
            if session_record is None or _record_status(session_record) != "active" or not _not_expired(
                session_record, now=now
            ):
                decision = PolicyDecision(
                    decision="deny",
                    actor=actor,
                    action=capability,
                    subject=subject,
                    scope=scope,
                    resource=resource,
                    reason_code="inactive_session",
                )
                self._audit(
                    "policy.deny",
                    actor=actor,
                    subject=subject,
                    scope=scope,
                    session=session_ref,
                    decision=decision,
                    metadata={"resource": resource, "reason_code": decision.reason_code},
                )
                return decision
            session_subject = _dict(session_record.get("subject"))
            if session_subject:
                actor_for_policy = _subject_from_dict(session_subject)
            device_id = str(session_record.get("device_id") or "").strip()
            if device_id:
                device_ref = SubjectRef("device", device_id)
                device_record = self.store.get_device_key(device_id)
                if device_record and _record_status(device_record) != "active":
                    decision = PolicyDecision(
                        decision="deny",
                        actor=actor,
                        action=capability,
                        subject=subject,
                        scope=scope,
                        resource=resource,
                        reason_code="inactive_device",
                    )
                    self._audit(
                        "policy.deny",
                        actor=actor,
                        subject=subject,
                        scope=scope,
                        device=device_ref,
                        session=session_ref,
                        decision=decision,
                        metadata={"resource": resource, "reason_code": decision.reason_code},
                    )
                    return decision
        decision = self._evaluate_without_audit(
            actor=actor_for_policy,
            action=capability,
            subject=subject,
            scope=scope,
            resource=resource,
            context=context_data,
            now=now,
        )
        if actor_for_policy.ref() != actor.ref():
            decision = PolicyDecision(
                decision=decision.decision,
                actor=actor,
                action=decision.action,
                subject=decision.subject,
                scope=decision.scope,
                resource=decision.resource,
                reason_code=decision.reason_code,
                grant_ids=decision.grant_ids,
                trace_id=decision.trace_id,
            )
        self._audit(
            f"policy.{decision.decision}",
            actor=actor,
            subject=subject,
            scope=scope,
            device=device_ref,
            session=session_ref,
            decision=decision,
            metadata={"resource": resource, "reason_code": decision.reason_code},
        )
        return decision

    def list_audit(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.store.list_audit(**kwargs)

    def _evaluate_without_audit(
        self,
        *,
        actor: SubjectRef,
        action: str,
        subject: SubjectRef | None,
        scope: ScopeRef | None,
        resource: str | None,
        context: Mapping[str, Any],
        now: float,
    ) -> PolicyDecision:
        if actor.ref() == self.owner.ref():
            return PolicyDecision(
                decision="allow",
                actor=actor,
                action=action,
                subject=subject,
                scope=scope,
                resource=resource,
                reason_code="owner_implicit_subnet_admin",
            )
        for record in [*self.store.iter_grants(), *self.store.iter_memberships()]:
            if not _not_expired(record, now=now):
                continue
            if _ref_key(record.get("subject")) != actor.ref():
                continue
            if not _scope_matches(_dict(record.get("scope")), scope):
                continue
            if not self._action_allowed_by_record(record, action):
                continue
            constraints = _dict(record.get("constraints"))
            requires_approval_for = set(str(item) for item in _list(constraints.get("requires_approval_for")))
            if action in requires_approval_for and not str(context.get("approval_id") or "").strip():
                return PolicyDecision(
                    decision="deny",
                    actor=actor,
                    action=action,
                    subject=subject,
                    scope=scope,
                    resource=resource,
                    reason_code="approval_required",
                    grant_ids=tuple(str(record.get("grant_id") or "") for _ in [0] if record.get("grant_id")),
                )
            return PolicyDecision(
                decision="allow",
                actor=actor,
                action=action,
                subject=subject,
                scope=scope,
                resource=resource,
                reason_code="grant_capability",
                grant_ids=tuple(str(record.get("grant_id") or "") for _ in [0] if record.get("grant_id")),
            )
        if (
            subject is not None
            and actor.ref() == subject.ref()
            and (scope is None or scope.kind == "user_private")
            and action in {
            "profile.read.self",
            "profile.write.self",
            "preferences.read.self",
            "preferences.write.self",
            }
        ):
            return PolicyDecision(
                decision="allow",
                actor=actor,
                action=action,
                subject=subject,
                scope=scope,
                resource=resource,
                reason_code="self_profile_preference",
            )
        return PolicyDecision(
            decision="deny",
            actor=actor,
            action=action,
            subject=subject,
            scope=scope,
            resource=resource,
            reason_code="missing_capability",
        )

    def _action_allowed_by_record(self, record: Mapping[str, Any], action: str) -> bool:
        capabilities = set(str(item) for item in _list(record.get("capabilities")))
        role = str(record.get("role") or "").strip()
        if role:
            capabilities.update(ROLE_PRESET_CAPABILITIES.get(role, ()))
        if action in capabilities:
            return True
        prefix = action.split(".", 1)[0] + ".*"
        return prefix in capabilities

    def _audit(
        self,
        event_type: str,
        *,
        actor: SubjectRef,
        subject: SubjectRef | None = None,
        scope: ScopeRef | None = None,
        device: SubjectRef | None = None,
        session: SubjectRef | None = None,
        decision: PolicyDecision | None = None,
        redacted_diff: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_metadata = {key: value for key, value in dict(metadata or {}).items() if value is not None}
        record = AuditRecord(
            audit_id=f"audit-{uuid4().hex}",
            event_type=event_type,
            actor=actor,
            subject=subject,
            scope=scope,
            device=device,
            session=session,
            source="personalization_access",
            decision=decision,
            redacted_diff=redacted_diff or {},
            metadata=clean_metadata,
            ts=_now_ts(),
        )
        return self.store.append_audit(record)


__all__ = [
    "PersonalizationAccessError",
    "PersonalizationAccessService",
    "PersonalizationAccessStore",
]
