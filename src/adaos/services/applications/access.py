from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from adaos.domain.application import TrialAccessGrant, utc_now
from adaos.domain.application_access import (
    ApplicationAccessDecision,
    ApplicationAccessGrant,
    evaluate_application_access,
    is_high_risk_permission,
)
from adaos.services.artifact_pipeline.storage import atomic_write_bytes, atomic_write_json, mutation_lock

from .service import ApplicationService, ApplicationServiceError
from .store import _read


class TrialAccessError(ApplicationServiceError):
    pass


class ApplicationAccessError(ApplicationServiceError):
    pass


class TrialAccessService:
    """Issues and redeems bounded capability links without persisting bearer secrets."""

    def __init__(self, applications: ApplicationService) -> None:
        self.applications = applications
        self.store = applications.store

    @property
    def _issuer_key_path(self) -> Path:
        return self.store.root / "keys" / "trial-link-issuer.key"

    def _issuer_key(self) -> bytes:
        path = self._issuer_key_path
        with mutation_lock(self.store.lock_path, timeout_s=30.0):
            if path.is_file():
                key = path.read_bytes()
                if len(key) != 32:
                    raise TrialAccessError("Trial link issuer key is corrupt")
                return key
            key = secrets.token_bytes(32)
            atomic_write_bytes(path, key)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            return key

    @staticmethod
    def _timestamp(value: str, *, field: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise TrialAccessError(f"{field} must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise TrialAccessError(f"{field} must include a timezone")
        return parsed.astimezone(timezone.utc)

    def _token(self, grant_id: str) -> str:
        digest = hmac.new(self._issuer_key(), grant_id.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"tal_{digest}"

    def _credential_path(self, grant_id: str) -> Path:
        return self.store.root / "trial_access_credentials" / f"{hashlib.sha256(grant_id.encode()).hexdigest()}.json"

    def _redemption_path(self, redemption_id: str) -> Path:
        token = hashlib.sha256(redemption_id.encode("utf-8")).hexdigest()
        return self.store.root / "trial_access_redemptions" / f"{token}.json"

    def issue(
        self,
        application_id: str,
        *,
        publisher_ref: str,
        recipient_subnet_ref: str,
        recipient_key_ref: str,
        scope: str,
        expires_at: str,
        allowed_zones: tuple[str, ...],
        idempotency_key: str,
        release_digest: str | None = None,
        max_uses: int = 1,
    ) -> dict[str, Any]:
        application = self.store.get_application(application_id)
        if application.publisher_ref != publisher_ref:
            raise TrialAccessError("only the Application publisher may issue Trial access")
        if self._timestamp(expires_at, field="expires_at") <= datetime.now(timezone.utc):
            raise TrialAccessError("Trial access expiry must be in the future")
        key = str(idempotency_key or "").strip()
        if not key:
            raise TrialAccessError("idempotency_key is required")
        identity = ":".join((application_id, publisher_ref, key))
        grant_id = "trialgrant." + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        nonce = hashlib.sha256(f"{grant_id}:nonce".encode("utf-8")).hexdigest()
        value = TrialAccessGrant(
            grant_id=grant_id,
            application_id=application_id,
            publisher_ref=publisher_ref,
            scope=scope,
            release_digest=release_digest,
            recipient_subnet_ref=recipient_subnet_ref,
            recipient_key_ref=recipient_key_ref,
            expires_at=expires_at,
            max_uses=max_uses,
            uses=0,
            nonce=nonce,
            allowed_zones=allowed_zones,
            status="active",
            revision=1,
        )
        if release_digest is not None:
            self.store.get_release(application_id, release_digest)
        token = self._token(grant_id)
        with mutation_lock(self.store.lock_path, timeout_s=30.0):
            try:
                existing = self.store.get_grant(grant_id)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                expected = replace(value, issued_at=existing.issued_at)
                if existing != expected:
                    raise TrialAccessError("idempotency key already names different Trial access")
                value = existing
            credential = {
                "schema": "adaos.application.trial_access_credential.v1",
                "grant_id": grant_id,
                "token_hash": "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest(),
                "idempotency_key_hash": "sha256:"
                + hashlib.sha256(key.encode("utf-8")).hexdigest(),
                "issued_at": value.issued_at,
            }
            if existing is None:
                self.store.save_grant(value, expected_revision=0)
                atomic_write_json(self._credential_path(grant_id), credential)
            else:
                stored_credential = _read(self._credential_path(grant_id))
                if stored_credential != credential:
                    raise TrialAccessError("Trial access credential record is inconsistent")
        return {
            "grant": value.to_dict(),
            "link": f"adaos://applications/trial/{grant_id}?token={token}",
        }

    @staticmethod
    def _parse_link(link: str) -> tuple[str, str]:
        parsed = urlparse(str(link or ""))
        parts = [part for part in parsed.path.split("/") if part]
        token = (parse_qs(parsed.query).get("token") or [""])[0]
        if parsed.scheme != "adaos" or parsed.netloc != "applications" or len(parts) != 2 or parts[0] != "trial":
            raise TrialAccessError("unsupported Trial capability link")
        if not token:
            raise TrialAccessError("Trial capability token is required")
        return parts[1], token

    def resolve(
        self,
        link: str,
        *,
        recipient_subnet_ref: str,
        recipient_key_ref: str,
        zone: str,
        redemption_id: str,
        now: str | None = None,
    ) -> dict[str, Any]:
        grant_id, token = self._parse_link(link)
        redemption_token = str(redemption_id or "").strip()
        if not redemption_token:
            raise TrialAccessError("redemption_id is required")
        current_time = self._timestamp(now or utc_now(), field="now")
        with mutation_lock(self.store.lock_path, timeout_s=30.0):
            grant = self.store.get_grant(grant_id)
            credential = _read(self._credential_path(grant_id))
            if credential.get("grant_id") != grant_id:
                raise TrialAccessError("Trial access credential record is inconsistent")
            actual_hash = "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(str(credential.get("token_hash") or ""), actual_hash):
                raise TrialAccessError("Trial capability token is invalid")
            if grant.recipient_subnet_ref != recipient_subnet_ref:
                raise TrialAccessError("Trial access belongs to another subnet")
            if grant.recipient_key_ref != recipient_key_ref:
                raise TrialAccessError("Trial access belongs to another recipient key")
            if zone not in grant.allowed_zones:
                raise TrialAccessError("Trial access is not valid in this zone")
            receipt_path = self._redemption_path(redemption_token)
            if receipt_path.is_file():
                receipt = _read(receipt_path)
                expected_receipt_identity = {
                    "schema": "adaos.application.trial_access_redemption.v1",
                    "redemption_id": redemption_token,
                    "grant_id": grant_id,
                    "application_id": grant.application_id,
                    "recipient_subnet_ref": recipient_subnet_ref,
                    "recipient_key_ref": recipient_key_ref,
                    "zone": zone,
                }
                if any(
                    receipt.get(field) != value
                    for field, value in expected_receipt_identity.items()
                ):
                    raise TrialAccessError("redemption identity already names another capability use")
                return {**receipt, "idempotent_replay": True}
            if grant.status != "active":
                raise TrialAccessError(f"Trial access is {grant.status}")
            if current_time >= self._timestamp(grant.expires_at, field="expires_at"):
                expired = replace(grant, status="expired", revision=grant.revision + 1)
                self.store.save_grant(expired, expected_revision=grant.revision)
                raise TrialAccessError("Trial access has expired")
            if grant.scope == "exact_release":
                release_digest = str(grant.release_digest)
            else:
                release_digest = str(
                    (self.store.get_channels(grant.application_id).get("channels") or {}).get("prerelease") or ""
                )
                if not release_digest:
                    raise TrialAccessError("Application has no current prerelease")
            self.store.get_release(grant.application_id, release_digest)
            uses = grant.uses + 1
            updated = replace(
                grant,
                uses=uses,
                status="consumed" if uses >= grant.max_uses else "active",
                revision=grant.revision + 1,
            )
            self.store.save_grant(updated, expected_revision=grant.revision)
            receipt = {
                "schema": "adaos.application.trial_access_redemption.v1",
                "redemption_id": redemption_token,
                "grant_id": grant_id,
                "application_id": grant.application_id,
                "release_digest": release_digest,
                "recipient_subnet_ref": recipient_subnet_ref,
                "recipient_key_ref": recipient_key_ref,
                "zone": zone,
                "redeemed_at": current_time.isoformat(),
                "grant_revision": updated.revision,
                "idempotent_replay": False,
            }
            atomic_write_json(receipt_path, receipt)
            return receipt

    def revoke(self, grant_id: str, *, publisher_ref: str, expected_revision: int) -> TrialAccessGrant:
        grant = self.store.get_grant(grant_id)
        if grant.publisher_ref != publisher_ref:
            raise TrialAccessError("only the Application publisher may revoke Trial access")
        if grant.revision != expected_revision:
            from .store import ApplicationRevisionConflict

            raise ApplicationRevisionConflict(expected=expected_revision, observed=grant.revision)
        if grant.status == "revoked":
            return grant
        revoked = replace(grant, status="revoked", revision=grant.revision + 1)
        return self.store.save_grant(revoked, expected_revision=grant.revision)


class ApplicationAccessService:
    """Application-scoped role and permission grants over release declarations."""

    def __init__(self, applications: ApplicationService) -> None:
        self.applications = applications
        self.store = applications.store

    @staticmethod
    def _grant_id(application_id: str, subject_ref: str, idempotency_key: str) -> str:
        identity = "\0".join(
            (
                str(application_id or "").strip(),
                str(subject_ref or "").strip(),
                str(idempotency_key or "").strip(),
            )
        )
        return "appgrant." + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _subject_kind(subject_ref: str, constraints: dict[str, Any]) -> str:
        explicit = str(constraints.get("subject_kind") or "").strip().lower()
        if explicit:
            return explicit
        token = str(subject_ref or "")
        if token.startswith("session:guest") or ":guest" in token:
            return "guest"
        if token.startswith("child:") or ":child" in token:
            return "child"
        return "user"

    def _audit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.store.append_application_access_audit(
            {
                "occurred_at": utc_now(),
                **dict(payload),
            }
        )

    def grant_access(
        self,
        application_id: str,
        *,
        release_digest: str,
        subject_ref: str,
        application_roles: tuple[str, ...],
        issuer_ref: str,
        idempotency_key: str,
        permission_ceiling: tuple[str, ...] | None = None,
        explicit_denies: tuple[str, ...] = (),
        constraints: Mapping[str, Any] | None = None,
        expires_at: str | None = None,
    ) -> ApplicationAccessGrant:
        release = self.store.get_release(application_id, release_digest)
        profile = release.permission_profile
        role_map = {item.role_id: item for item in release.application_roles}
        role_ids = set(role_map)
        unknown_roles = sorted(set(application_roles) - role_ids)
        if unknown_roles:
            raise ApplicationAccessError("unknown Application roles: " + ", ".join(unknown_roles))
        ceiling = tuple(permission_ceiling or profile.flat_permissions)
        unknown_permissions = sorted(set(ceiling) - set(profile.flat_permissions))
        if unknown_permissions:
            raise ApplicationAccessError("unknown Application permissions: " + ", ".join(unknown_permissions))
        deny_unknown = sorted(set(explicit_denies) - set(profile.flat_permissions))
        if deny_unknown:
            raise ApplicationAccessError("unknown explicit deny permissions: " + ", ".join(deny_unknown))
        constraint_payload = dict(constraints or {})
        subject_kind = self._subject_kind(subject_ref, constraint_payload)
        constraint_payload.setdefault("subject_kind", subject_kind)
        platform_role = str(
            constraint_payload.get("platform_role")
            or ("guest" if subject_kind == "guest" else "child" if subject_kind == "child" else "member")
        ).strip().lower()
        unassignable = sorted(
            role_id
            for role_id in application_roles
            if platform_role not in role_map[role_id].assignable_to
        )
        if unassignable:
            raise ApplicationAccessError(
                f"Application roles are not assignable to {platform_role}: " + ", ".join(unassignable)
            )
        high_risk = sorted(item for item in ceiling if is_high_risk_permission(item))
        if subject_kind == "guest":
            if expires_at is None:
                raise ApplicationAccessError("guest Application access requires expires_at")
            if constraint_payload.get("profile_binding") is True:
                raise ApplicationAccessError("guest Application access cannot bind a user profile")
            if constraint_payload.get("session_bound") is False:
                raise ApplicationAccessError("guest Application access must be session-bound")
            if constraint_payload.get("durable_approvals") is True:
                raise ApplicationAccessError("guest Application access cannot create durable approvals")
            constraint_payload["profile_binding"] = False
            constraint_payload["session_bound"] = True
            constraint_payload["durable_approvals"] = False
            if subject_ref.startswith("session:"):
                constraint_payload.setdefault("session_ref", subject_ref)
            if high_risk and not constraint_payload.get("guest_sensitive_override"):
                raise ApplicationAccessError("guest Application access cannot include sensitive permissions by default")
        child_external_data = bool(
            profile.privacy_labels.get("sent_off_device")
            or profile.privacy_labels.get("tracking")
        )
        if (
            subject_kind == "child"
            and (high_risk or child_external_data)
            and not constraint_payload.get("guardian_approval_id")
        ):
            raise ApplicationAccessError("child sensitive Application access requires guardian approval")
        grant = ApplicationAccessGrant(
            grant_id=self._grant_id(application_id, subject_ref, idempotency_key),
            subject_ref=subject_ref,
            application_id=application_id,
            application_roles=application_roles,
            permission_ceiling=ceiling,
            explicit_denies=explicit_denies,
            constraints=constraint_payload,
            issuer_ref=issuer_ref,
            reviewed_permission_profile_digest=profile.digest,
            expires_at=expires_at,
            revision=1,
        )
        try:
            existing = self.store.get_application_access_grant(grant.grant_id)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            expected = replace(grant, created_at=existing.created_at, updated_at=existing.updated_at, revision=existing.revision)
            if existing != expected:
                raise ApplicationAccessError("idempotency key already names different Application access")
            return existing
        saved = self.store.save_application_access_grant(grant, expected_revision=0)
        self._audit(
            {
                "action": "grant_create",
                "application_id": saved.application_id,
                "subject_ref": saved.subject_ref,
                "grant_id": saved.grant_id,
                "issuer_ref": issuer_ref,
                "application_roles": list(saved.application_roles),
                "permission_ceiling": list(saved.permission_ceiling),
                "explicit_denies": list(saved.explicit_denies),
                "constraints": dict(saved.constraints),
                "expires_at": saved.expires_at,
                "status": saved.status,
                "reviewed_permission_profile_digest": saved.reviewed_permission_profile_digest,
                "subject_kind": subject_kind,
            }
        )
        if subject_kind == "guest":
            self._audit(
                {
                    "action": "guest_access",
                    "application_id": saved.application_id,
                    "subject_ref": saved.subject_ref,
                    "grant_id": saved.grant_id,
                    "expires_at": saved.expires_at,
                    "session_bound": True,
                    "profile_binding": False,
                    "reviewed_permission_profile_digest": saved.reviewed_permission_profile_digest,
                }
            )
        if subject_kind == "child" and constraint_payload.get("guardian_approval_id"):
            self._audit(
                {
                    "action": "guardian_approval",
                    "application_id": saved.application_id,
                    "subject_ref": saved.subject_ref,
                    "grant_id": saved.grant_id,
                    "approval_id": constraint_payload["guardian_approval_id"],
                    "reviewed_permission_profile_digest": saved.reviewed_permission_profile_digest,
                }
            )
        return saved

    def change_access(
        self,
        grant_id: str,
        *,
        release_digest: str,
        application_roles: tuple[str, ...],
        issuer_ref: str,
        expected_revision: int,
        permission_ceiling: tuple[str, ...] | None = None,
        explicit_denies: tuple[str, ...] | None = None,
        constraints: Mapping[str, Any] | None = None,
        expires_at: str | None = None,
    ) -> ApplicationAccessGrant:
        current = self.store.get_application_access_grant(grant_id)
        if current.revision != expected_revision:
            from .store import ApplicationRevisionConflict

            raise ApplicationRevisionConflict(expected=expected_revision, observed=current.revision)
        release = self.store.get_release(current.application_id, release_digest)
        profile = release.permission_profile
        role_map = {item.role_id: item for item in release.application_roles}
        unknown_roles = sorted(set(application_roles) - set(role_map))
        if unknown_roles:
            raise ApplicationAccessError("unknown Application roles: " + ", ".join(unknown_roles))
        ceiling = tuple(permission_ceiling if permission_ceiling is not None else current.permission_ceiling)
        denies = tuple(explicit_denies if explicit_denies is not None else current.explicit_denies)
        unknown_permissions = sorted((set(ceiling) | set(denies)) - set(profile.flat_permissions))
        if unknown_permissions:
            raise ApplicationAccessError("unknown Application permissions: " + ", ".join(unknown_permissions))
        next_constraints = dict(current.constraints if constraints is None else constraints)
        subject_kind = self._subject_kind(current.subject_ref, next_constraints)
        next_constraints.setdefault("subject_kind", subject_kind)
        platform_role = str(
            next_constraints.get("platform_role")
            or ("guest" if subject_kind == "guest" else "child" if subject_kind == "child" else "member")
        ).strip().lower()
        unassignable = sorted(
            role_id for role_id in application_roles if platform_role not in role_map[role_id].assignable_to
        )
        if unassignable:
            raise ApplicationAccessError(
                f"Application roles are not assignable to {platform_role}: " + ", ".join(unassignable)
            )
        high_risk = sorted(item for item in ceiling if is_high_risk_permission(item))
        next_expiry = expires_at if expires_at is not None else current.expires_at
        if subject_kind == "guest":
            if next_expiry is None:
                raise ApplicationAccessError("guest Application access requires expires_at")
            if next_constraints.get("profile_binding") is True:
                raise ApplicationAccessError("guest Application access cannot bind a user profile")
            if next_constraints.get("session_bound") is False:
                raise ApplicationAccessError("guest Application access must be session-bound")
            if next_constraints.get("durable_approvals") is True:
                raise ApplicationAccessError("guest Application access cannot create durable approvals")
            next_constraints["profile_binding"] = False
            next_constraints["session_bound"] = True
            next_constraints["durable_approvals"] = False
            if current.subject_ref.startswith("session:"):
                next_constraints.setdefault("session_ref", current.subject_ref)
            if high_risk and not next_constraints.get("guest_sensitive_override"):
                raise ApplicationAccessError("guest Application access cannot include sensitive permissions by default")
        if subject_kind == "child" and (
            high_risk
            or profile.privacy_labels.get("sent_off_device")
            or profile.privacy_labels.get("tracking")
            or any(role_map[role_id].sensitive for role_id in application_roles)
        ) and not next_constraints.get("guardian_approval_id"):
            raise ApplicationAccessError("child sensitive Application access requires guardian approval")
        changed = replace(
            current,
            application_roles=application_roles,
            permission_ceiling=ceiling,
            explicit_denies=denies,
            constraints=next_constraints,
            issuer_ref=issuer_ref,
            reviewed_permission_profile_digest=profile.digest,
            expires_at=next_expiry,
            status="active",
            revision=current.revision + 1,
            updated_at=utc_now(),
        )
        saved = self.store.save_application_access_grant(changed, expected_revision=current.revision)
        self._audit(
            {
                "action": "role_change",
                "application_id": saved.application_id,
                "subject_ref": saved.subject_ref,
                "grant_id": saved.grant_id,
                "issuer_ref": issuer_ref,
                "previous_application_roles": list(current.application_roles),
                "application_roles": list(saved.application_roles),
                "permission_ceiling": list(saved.permission_ceiling),
                "explicit_denies": list(saved.explicit_denies),
                "constraints": dict(saved.constraints),
                "expires_at": saved.expires_at,
                "reviewed_permission_profile_digest": saved.reviewed_permission_profile_digest,
            }
        )
        return saved

    def revoke_access(
        self,
        grant_id: str,
        *,
        issuer_ref: str,
        expected_revision: int,
    ) -> ApplicationAccessGrant:
        grant = self.store.get_application_access_grant(grant_id)
        if grant.status == "revoked":
            return grant
        if grant.revision != expected_revision:
            from .store import ApplicationRevisionConflict

            raise ApplicationRevisionConflict(expected=expected_revision, observed=grant.revision)
        revoked = replace(grant, status="revoked", revision=grant.revision + 1, updated_at=utc_now())
        saved = self.store.save_application_access_grant(revoked, expected_revision=grant.revision)
        self._audit(
            {
                "action": "grant_revoke",
                "application_id": saved.application_id,
                "subject_ref": saved.subject_ref,
                "grant_id": saved.grant_id,
                "issuer_ref": issuer_ref,
                "application_roles": list(saved.application_roles),
                "permission_ceiling": list(saved.permission_ceiling),
                "explicit_denies": list(saved.explicit_denies),
                "constraints": dict(saved.constraints),
                "expires_at": saved.expires_at,
                "status": saved.status,
                "reviewed_permission_profile_digest": saved.reviewed_permission_profile_digest,
            }
        )
        return saved

    def decide(
        self,
        application_id: str,
        *,
        release_digest: str,
        subject_ref: str,
        permission_id: str,
        app_capability: str,
        actor_chain: Mapping[str, Any],
        component_capabilities: tuple[str, ...] = (),
        approval_id: str | None = None,
    ) -> ApplicationAccessDecision:
        release = self.store.get_release(application_id, release_digest)
        subject_grants = tuple(
            self.store.list_application_access_grants(
                application_id,
                subject_ref=subject_ref,
            )
        )
        grant = next(
            (
                item
                for item in subject_grants
                if item.reviewed_permission_profile_digest == release.permission_profile.digest
            ),
            subject_grants[0] if subject_grants else None,
        )
        chain = {
            "application_id": application_id,
            "subject_ref": subject_ref,
            **dict(actor_chain or {}),
        }
        decision = evaluate_application_access(
            profile=release.permission_profile,
            roles=release.application_roles,
            grant=grant,
            permission_id=permission_id,
            app_capability=app_capability,
            actor_chain=chain,
            component_capabilities=component_capabilities,
            approval_id=approval_id,
        )
        self._audit(
            {
                "action": "permission_decision",
                "application_id": application_id,
                "subject_ref": subject_ref,
                "grant_id": decision.grant_id,
                "decision": decision.decision,
                "reason_code": decision.reason_code,
                "permission_id": decision.permission_id,
                "app_capability": decision.app_capability,
                "approval_id": decision.approval_id,
                "reviewed_permission_profile_digest": release.permission_profile.digest,
                "actor_chain": dict(decision.actor_chain),
            }
        )
        return decision


__all__ = [
    "ApplicationAccessError",
    "ApplicationAccessService",
    "TrialAccessError",
    "TrialAccessService",
]
