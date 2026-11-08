"""Desktop admission and system rights, independent of Application UI roles."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import hashlib
import hmac
import math
import secrets
import time
from typing import Any, Iterable, Mapping

from adaos.domain.personalization_access import Grant, GrantConstraint, ScopeRef, SubjectRef
from adaos.services.personalization_access import PersonalizationAccessService
from adaos.services.policy.caller import CallerAccessDenied


ADMIN_ROLES = frozenset({"owner", "co_owner", "admin"})
ADMIN_COMMANDS = frozenset({
    "desktop.webspace.create", "desktop.webspace.rename", "desktop.webspace.update",
    "desktop.webspace.delete", "desktop.webspace.set_home", "desktop.webspace.set_home_current",
    "desktop.webspace.reload", "desktop.webspace.reset", "desktop.webspace.restore",
    "desktop.webspace.backup", "desktop.webspace.ensure_dev", "desktop.webspace.refresh",
    "desktop.webspace.share", "desktop.webspace.revoke", "desktop.webspace.access.set",
})
_CREDENTIAL_PREFIX = "adaos-desktop-v1."


def _active(record: Mapping[str, Any], now: float) -> bool:
    if record.get("status", "active") != "active" or record.get("revoked_at") is not None:
        return False
    constraints = record.get("constraints") or {}
    expiry = record.get("expires_at")
    if expiry is None:
        expiry = constraints.get("expires_at")
    if expiry is not None:
        try:
            return math.isfinite(float(expiry)) and float(expiry) > now
        except (ValueError, TypeError):
            return False
    return True


@dataclass(frozen=True)
class DesktopPrincipal:
    actor: SubjectRef
    session_id: str | None = None
    expires_at: float | None = None


class DesktopAccessService:
    def __init__(self, access: PersonalizationAccessService | None = None, *, subnet_id: str | None = None):
        if access is None:
            from adaos.services.personalization_runtime import personalization_access_service
            access = personalization_access_service()
        self.access = access
        self.subnet_id = subnet_id

    def _subjects(self, principal: DesktopPrincipal) -> tuple[SubjectRef, ...]:
        if principal.expires_at is not None and principal.expires_at <= time.time():
            raise CallerAccessDenied("desktop_credential_expired")
        actor = principal.actor
        session_id = principal.session_id or (actor.id if actor.kind == "session" else None)
        if not session_id:
            return (actor,)
        record = self.access.store.get_session(session_id)
        if not record or not _active(record, time.time()):
            raise CallerAccessDenied("desktop_session_inactive")
        subjects = [SubjectRef("session", session_id)]
        bound = record.get("subject") or {}
        if bound.get("kind") and bound.get("id"):
            subject = SubjectRef(bound["kind"], bound["id"])
            if actor.kind != "session" and actor != subject:
                raise CallerAccessDenied("desktop_session_subject_mismatch")
            if subject not in subjects:
                subjects.append(subject)
        device_id = record.get("device_id")
        if device_id:
            device = self.access.store.get_device_key(device_id)
            if not device or not _active(device, time.time()):
                raise CallerAccessDenied("desktop_session_device_inactive")
        return tuple(subjects)

    def _records(self, subjects: tuple[SubjectRef, ...], desktop_id: str | None):
        keys = {subject.ref() for subject in subjects}
        for original in [*self.access.store.iter_grants(), *self.access.store.iter_memberships()]:
            record = dict(original)
            linked_id = str(record.get("grant_id") or "")
            if linked_id.startswith("membership:"):
                linked = self.access.store.get_grant(linked_id[len("membership:"):])
                if not linked or not _active(linked, time.time()):
                    continue
                record["metadata"] = linked.get("metadata") or {}
            subject = record.get("subject") or {}
            if f"{subject.get('kind')}:{subject.get('id')}" not in keys or not _active(record, time.time()):
                continue
            scope = record.get("scope") or {}
            if scope.get("kind") == "subnet":
                from adaos.services.personalization_runtime import current_subnet_id
                # A grant for another subnet must never become a wildcard.
                if scope.get("id") != (self.subnet_id or current_subnet_id()):
                    continue
            elif not (desktop_id and scope.get("kind") == "webspace" and scope.get("id") == desktop_id):
                continue
            allowed_scopes = (record.get("constraints") or {}).get("allowed_scopes") or []
            if allowed_scopes and {"kind": "webspace", "id": desktop_id} not in allowed_scopes:
                continue
            metadata = record.get("metadata") or {}
            invite_id = metadata.get("invite_id")
            if invite_id:
                invite = self.access.store.get_invite(invite_id)
                if not invite or invite.get("status") in {"revoked", "expired"}:
                    continue
                if (invite.get("scope") or {}).get("kind") == "webspace":
                    record["metadata"] = {**metadata, "desktop_participation_only": True}
            yield record

    def allowed(self, principal: DesktopPrincipal, desktop_id: str | None, *, administer: bool = False) -> bool:
        with self.access.batch():
            subjects = self._subjects(principal)
            if self.access.owner in subjects:
                return True
            for subject in subjects:
                limit = self.access.store.get_desktop_policy(subject)
                if limit and limit.get("allowed_desktops") is not None:
                    if not desktop_id or desktop_id not in limit["allowed_desktops"]:
                        return False
            for record in self._records(subjects, desktop_id):
                role = record.get("role")
                # A sharing invite never confers system administration, even
                # if its historical preset has a privileged display name.
                shared = (record.get("metadata") or {}).get("desktop_participation_only")
                if administer:
                    if role in ADMIN_ROLES and not shared:
                        return True
                elif role or "workspace.read" in (record.get("capabilities") or ()):
                    return True
            return False

    def require(self, principal: DesktopPrincipal | None, desktop_id: str | None, *, administer: bool = False) -> None:
        if principal is None or not self.allowed(principal, desktop_id, administer=administer):
            raise CallerAccessDenied("desktop_system_access_denied" if administer else "desktop_access_denied")

    def require_attachment(self, principal: DesktopPrincipal | None, desktop_id: str) -> None:
        self.require(principal, desktop_id)
        from adaos.services.workspaces.index import get_workspace
        if get_workspace(desktop_id) is None:
            raise CallerAccessDenied("desktop_access_denied")

    def filter_catalog(self, principal: DesktopPrincipal, items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for item in items:
            target = str(item.get("id") or item.get("webspace_id") or "")
            if self.allowed(principal, target):
                result.append({**item, "can_administer": self.allowed(principal, target, administer=True)})
        return result

    def set_allowed_desktops(self, actor: DesktopPrincipal, subject: SubjectRef,
                             desktops: list[str] | None) -> dict[str, Any]:
        self.require(actor, None, administer=True)
        values = sorted({str(value).strip() for value in desktops if str(value).strip()}) if desktops is not None else None
        return self.access.store.put_desktop_policy(subject, values)

    def share(self, actor: DesktopPrincipal, desktop_id: str, subject: SubjectRef,
              *, expires_at: float | None = None) -> dict[str, Any]:
        self.require(actor, desktop_id, administer=True)
        grant = Grant(grant_id=f"desktop:{desktop_id}:{secrets.token_hex(16)}", subject=subject,
                      scope=ScopeRef("webspace", desktop_id), capabilities=("workspace.read", "workspace.write"),
                      constraints=GrantConstraint(expires_at=expires_at), issued_by=actor.actor)
        with self.access.batch():
            self.access.put_grant(grant, actor=actor.actor)
            return self.access.store.update_grant(grant.grant_id, {"metadata": {"desktop_participation_only": True}})

    def revoke(self, actor: DesktopPrincipal, desktop_id: str, grant_id: str) -> dict[str, Any]:
        self.require(actor, desktop_id, administer=True)
        record = self.access.store.get_grant(grant_id)
        if not record or record.get("scope") != ScopeRef("webspace", desktop_id).to_dict():
            raise CallerAccessDenied("desktop_grant_scope_mismatch")
        return self.access.revoke_grant(grant_id, actor=actor.actor)

    def invite(self, actor: DesktopPrincipal, desktop_id: str, *, ttl_seconds: int = 3600,
               max_sessions: int = 1) -> dict[str, Any]:
        self.require(actor, desktop_id, administer=True)
        return self.access.create_guest_join_link(
            invite_id=f"desktop-{secrets.token_hex(24)}", scope=ScopeRef("webspace", desktop_id),
            issued_by=actor.actor, expires_at=time.time() + max(1, min(ttl_seconds, 86400 * 30)),
            max_sessions=max(1, min(max_sessions, 100)))

    def issue_credential(self, principal: DesktopPrincipal, *, ttl_seconds: int = 3600) -> dict[str, Any]:
        """Provision only after an ingress has verified an invite claim/session."""
        if not principal.session_id:
            raise CallerAccessDenied("desktop_session_required")
        with self.access.batch():
            self._subjects(principal)
            now = time.time()
            record = self.access.store.get_session(principal.session_id) or {}
            expires_at = min(now + max(1, min(ttl_seconds, 86400)), float(record.get("expires_at") or now + ttl_seconds))
            token = f"{_CREDENTIAL_PREFIX}{principal.session_id}.{secrets.token_urlsafe(32)}"
            self.access.store.update_session(principal.session_id, {"desktop_credential": {
                "token_hash": hashlib.sha256(token.encode()).hexdigest(), "expires_at": expires_at,
            }})
        return {"access_token": token, "token_type": "Bearer", "expires_at": expires_at}

    def authenticate(self, token: str) -> DesktopPrincipal:
        if not token.startswith(_CREDENTIAL_PREFIX) or len(token) > 1024:
            raise CallerAccessDenied("desktop_credential_invalid")
        session_id, separator, secret = token[len(_CREDENTIAL_PREFIX):].rpartition(".")
        if not separator or not session_id or len(secret) != 43:
            raise CallerAccessDenied("desktop_credential_invalid")
        principal = DesktopPrincipal(SubjectRef("session", session_id), session_id)
        with self.access.batch():
            self._subjects(principal)
            credential = (self.access.store.get_session(session_id) or {}).get("desktop_credential") or {}
            try:
                expiry = float(credential.get("expires_at") or 0)
            except (ValueError, TypeError) as exc:
                raise CallerAccessDenied("desktop_credential_invalid") from exc
            if not math.isfinite(expiry) or expiry <= time.time() or not hmac.compare_digest(
                    str(credential.get("token_hash") or ""), hashlib.sha256(token.encode()).hexdigest()):
                raise CallerAccessDenied("desktop_credential_invalid")
        return DesktopPrincipal(principal.actor, principal.session_id, expiry)


async def watch_desktop_access(principal: DesktopPrincipal, desktop_id: str | None, close,
                               *, administer: bool = False) -> None:
    """Expire idle attachments as well as checking access on every data frame."""
    while True:
        await asyncio.sleep(1)
        try:
            if desktop_id is None:
                await asyncio.to_thread(DesktopAccessService().require, principal, None, administer=administer)
            else:
                await asyncio.to_thread(DesktopAccessService().require_attachment, principal, desktop_id)
        except (PermissionError, ValueError, RuntimeError):
            await close()
            return


def require_sdk_desktop(desktop_id: str | None, *, administer: bool = False) -> DesktopPrincipal:
    """SDK/MCP invocations must carry Core-bound caller context."""
    from adaos.services.policy.caller import current_caller
    actor = current_caller()
    if actor is None:
        raise CallerAccessDenied("desktop_verified_caller_required")
    principal = DesktopPrincipal(actor, actor.id if actor.kind == "session" else None)
    DesktopAccessService().require(principal, desktop_id, administer=administer)
    return principal


def resolve_sdk_desktop(desktop_id: str | None = None) -> str:
    """Resolve omitted SDK addresses from the invocation, never device focus."""
    from adaos.services.policy.caller import current_caller, current_caller_scope
    from adaos.services.policy.invocation import current_invocation
    from adaos.services.yjs.webspace import coerce_webspace_id
    invocation = current_invocation() or {}
    scope = current_caller_scope()
    target = coerce_webspace_id(desktop_id or invocation.get("webspace_id") or
        (scope.id if scope is not None and scope.kind == "webspace" else None))
    if current_caller() is not None:
        require_sdk_desktop(target)
    return target


def desktop_event_allowed(principal: DesktopPrincipal | None, message: Mapping[str, Any]) -> bool:
    """Global administrative events cannot leak through wildcard subscriptions."""
    if principal is None:
        return False
    payload = message.get("payload") or {}
    meta = payload.get("_meta") if isinstance(payload, Mapping) else {}
    meta = meta if isinstance(meta, Mapping) else {}
    target = str(payload.get("webspace_id") or meta.get("webspace_id") or "") if isinstance(payload, Mapping) else ""
    kind = str(message.get("kind") or message.get("type") or "")
    topic_target = None
    if kind.startswith(("webio.stream.", "webio.yjs.")):
        parts = kind.split(".")
        if len(parts) > 3:
            topic_target = parts[2]
            target = target or topic_target
    try:
        policy = DesktopAccessService()
        return policy.allowed(principal, target or None, administer=not bool(target)) and (
            not topic_target or policy.allowed(principal, topic_target))
    except (PermissionError, ValueError, RuntimeError):
        return False


def filter_desktop_topics(principal: DesktopPrincipal | None, raw_topics: Any) -> list[str]:
    if not isinstance(raw_topics, list):
        return []
    return [str(topic) for topic in raw_topics[:128] if (
        not str(topic).startswith(("webio.stream.", "webio.yjs."))
        or desktop_event_allowed(principal, {"kind": str(topic), "payload": {}}))]


def require_desktop_command(kind: str, payload: Mapping[str, Any], desktop_id: str,
                            principal: DesktopPrincipal | None) -> None:
    """Called by every transport before dispatch; payload roles/actors are ignored."""
    if not isinstance(kind, str) or not isinstance(payload, Mapping):
        raise CallerAccessDenied("invalid_desktop_command")
    if kind == "skill.event.publish":
        event = str(payload.get("event_type") or payload.get("type") or "")
        nested = payload.get("payload") or {}
        if not isinstance(nested, Mapping) or event == "skill.event.publish":
            raise CallerAccessDenied("invalid_desktop_command")
        require_desktop_command(event, {**nested, "webspace_id": nested.get("webspace_id") or payload.get("webspace_id")}, desktop_id, principal)
    if kind.startswith(("skills.", "nlp.teacher.", "sys.", "core.", "node.", "personalization.", "access.")):
        DesktopAccessService().require(principal, None, administer=True)
    target = str(payload.get("webspace_id") or payload.get("workspace_id") or
                 (payload.get("id") if kind.startswith("desktop.webspace.") else None) or desktop_id).strip()
    administrative = kind in ADMIN_COMMANDS or (
        kind in {"desktop.scenario.set", "desktop.webspace.use", "desktop.webspace.go_home"}
        and bool(payload.get("set_home") or payload.get("persist_home") or payload.get("recreate_room")))
    if kind.startswith("desktop.webspace.") and kind not in {
            "desktop.webspace.use", "desktop.webspace.go_home", "desktop.webspace.list", "desktop.webspace.describe"}:
        administrative = True
    DesktopAccessService().require(principal, None if kind == "desktop.webspace.create" else target,
                                   administer=administrative)
    if kind == "device.register":
        # Registration is presence, never permission to resurrect a deleted
        # desktop while an old invitation/grant still exists.
        DesktopAccessService().require_attachment(principal, target)
    if kind != "desktop.webspace.create":
        for alternate in [payload.get("workspace_id"),
                          payload.get("id") if kind.startswith("desktop.webspace.") else None]:
            if alternate:
                DesktopAccessService().require(principal, str(alternate), administer=administrative)
    meta = payload.get("_meta") or {}
    if isinstance(meta, Mapping) and meta.get("webspace_id"):
        DesktopAccessService().require(principal, str(meta["webspace_id"]), administer=administrative)
