from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from adaos.domain.application import utc_now
from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.domain.application_access import (
    ApplicationAccessGrant,
    ApplicationVerificationReport,
    VerificationCheck,
    build_application_verification_report,
    canonical_payload_digest,
    classify_access_profile_diff,
    evaluate_application_access,
    is_high_risk_permission,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock

from .access import ApplicationAccessError, ApplicationAccessService
from .service import ApplicationService
from .store import _read


_log = logging.getLogger("adaos.applications.access")


ROLE_TEMPLATES: dict[str, tuple[dict[str, Any], ...]] = {
    "classroom": (
        {"id": "teacher", "title": "Teacher", "grants": ["application.manage", "work.assign", "work.review"]},
        {"id": "student", "title": "Student", "grants": ["application.use", "work.read", "work.complete"], "assignable_to": ["member", "child"]},
        {"id": "observer", "title": "Observer", "grants": ["application.use", "work.read"], "assignable_to": ["member", "guest"]},
    ),
    "household_tasks": (
        {"id": "coordinator", "title": "Coordinator", "grants": ["application.manage", "task.assign", "task.complete"]},
        {"id": "participant", "title": "Participant", "grants": ["application.use", "task.read", "task.complete"], "assignable_to": ["member", "child"]},
        {"id": "guest_reader", "title": "Guest reader", "grants": ["application.use", "task.read"], "assignable_to": ["guest"]},
    ),
    "dashboard": (
        {"id": "operator", "title": "Operator", "grants": ["application.manage", "dashboard.read", "dashboard.configure"]},
        {"id": "viewer", "title": "Viewer", "grants": ["application.use", "dashboard.read"], "assignable_to": ["member", "child", "guest"]},
    ),
    "moderation": (
        {"id": "moderator", "title": "Moderator", "grants": ["application.manage", "queue.read", "queue.decide"], "sensitive": True},
        {"id": "reviewer", "title": "Reviewer", "grants": ["application.use", "queue.read"]},
    ),
    "research_review": (
        {"id": "lead", "title": "Research lead", "grants": ["application.manage", "research.read", "research.annotate", "research.publish"], "sensitive": True},
        {"id": "reviewer", "title": "Reviewer", "grants": ["application.use", "research.read", "research.annotate"]},
        {"id": "reader", "title": "Reader", "grants": ["application.use", "research.read"], "assignable_to": ["member", "guest"]},
    ),
    "media_queue": (
        {"id": "curator", "title": "Curator", "grants": ["application.manage", "media.read", "media.queue", "media.remove"]},
        {"id": "listener", "title": "Listener", "grants": ["application.use", "media.read", "media.queue"], "assignable_to": ["member", "child", "guest"]},
    ),
}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _subject_kind(grant: ApplicationAccessGrant) -> str:
    explicit = str(grant.constraints.get("subject_kind") or "").strip().lower()
    if explicit:
        return explicit
    if grant.subject_ref.startswith("child:") or ":child" in grant.subject_ref:
        return "child"
    if grant.subject_ref.startswith("guest:") or ":guest" in grant.subject_ref:
        return "guest"
    return "user"


def _redacted_account(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "account_id",
        "provider_id",
        "subject_ref",
        "mode",
        "scopes",
        "status",
        "token_expires_at",
        "scope_changed_at",
        "created_at",
        "updated_at",
    }
    return {key: value[key] for key in sorted(allowed) if key in value}


class ApplicationAccessManagementService:
    """Shared read/write model for Applications, Users & Access, Builder and chat."""

    def __init__(self, applications: ApplicationService) -> None:
        self.applications = applications
        self.store = applications.store
        self.access = ApplicationAccessService(applications)

    def _document_path(self, collection: str, key: str) -> Path:
        digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
        return self.store.root / collection / f"{digest}.json"

    def resolve_runtime_context(
        self,
        *,
        skill_name: str,
        requested_application_id: str = "",
        requested_release_digest: str = "",
        webspace_id: str = "",
    ) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        selected = {
            (item.application_id, item.release_digest): item
            for item in self.store.list_runtime_selections()
            if not webspace_id or item.webspace_id == webspace_id
        }
        for installation in self.store.list_installations():
            if installation.status != "active":
                continue
            release = self.store.get_release(
                installation.application_id,
                installation.installed_release_digest,
            )
            if not any(
                item.kind == "skill" and item.artifact_id == skill_name
                for item in release.project_release.components
            ):
                continue
            selection = selected.get((installation.application_id, release.release_digest))
            candidates.append(
                {
                    "application_id": installation.application_id,
                    "release_digest": release.release_digest,
                    "permission_profile_digest": release.permission_profile.digest,
                    "installation_revision": installation.revision,
                    "runtime_selection": selection.to_dict() if selection else None,
                    "release": release,
                }
            )
        installed_identities = {
            (item["application_id"], item["release_digest"]) for item in candidates
        }
        for selection in selected.values():
            identity = (selection.application_id, selection.release_digest)
            if identity in installed_identities:
                continue
            try:
                release = self.store.get_release(*identity)
            except FileNotFoundError:
                continue
            if not any(
                item.kind == "skill" and item.artifact_id == skill_name
                for item in release.project_release.components
            ):
                continue
            candidates.append(
                {
                    "application_id": selection.application_id,
                    "release_digest": release.release_digest,
                    "permission_profile_digest": release.permission_profile.digest,
                    "installation_revision": None,
                    "runtime_selection": selection.to_dict(),
                    "release": release,
                }
            )
        if requested_application_id:
            candidates = [item for item in candidates if item["application_id"] == requested_application_id]
        if requested_release_digest:
            candidates = [item for item in candidates if item["release_digest"] == requested_release_digest]
        if not candidates:
            return None
        if len(candidates) != 1:
            raise ApplicationAccessError("Application runtime context is ambiguous")
        return candidates[0]

    @staticmethod
    def runtime_permission(
        *,
        side_effects: str,
        application_access: Mapping[str, Any],
        component_capabilities: Iterable[str],
    ) -> tuple[str, str]:
        explicit_permission = str(application_access.get("permission") or "").strip().lower()
        explicit_capability = str(application_access.get("capability") or "").strip().lower()
        if explicit_permission and explicit_capability:
            return explicit_permission, explicit_capability
        effects = str(side_effects or "").strip().lower().replace("-", "_")
        permission_by_effect = {
            "safe": "workspace.read",
            "none": "workspace.read",
            "read": "workspace.read",
            "read_only": "workspace.read",
            "readonly": "workspace.read",
            "ui_navigation": "workspace.read",
            "local_write": "workspace.write",
            "runtime_write": "workspace.write",
            "external_write": "network.egress",
            "network": "network.egress",
            "cross_node": "network.egress",
            "device_control": "devices.control",
        }
        admitted = tuple(sorted({str(item).strip().lower() for item in component_capabilities if str(item).strip()}))
        preferred = (
            "secrets.use",
            "secrets.read",
            "secrets.write",
            "notifications.send",
            "background.run",
            "external_provider.use",
            "llm.generate",
            "model.use",
        )
        permission = explicit_permission or next((item for item in preferred if item in admitted), "")
        permission = permission or permission_by_effect.get(effects, "")
        capability = explicit_capability or "application.use"
        return permission, capability

    def record_runtime_observation(
        self,
        *,
        application_id: str,
        subject_ref: str,
        permission_id: str,
        actor_chain: Mapping[str, Any],
        outcome: str,
        grant_id: str = "",
        network_destination: str = "",
        data_categories: Iterable[str] = (),
    ) -> dict[str, Any]:
        return self.store.append_application_access_audit(
            {
                "occurred_at": utc_now(),
                "action": "runtime_observation",
                "application_id": application_id,
                "subject_ref": subject_ref,
                "permission_id": permission_id,
                "outcome": outcome,
                "grant_id": str(grant_id or "").strip(),
                "network_destination": str(network_destination or "").strip(),
                "data_categories": sorted({str(item).strip() for item in data_categories if str(item).strip()}),
                "actor_chain": dict(actor_chain),
            }
        )

    def simulate(
        self,
        application_id: str,
        *,
        release_digest: str,
        subject_ref: str,
        permission_id: str,
        app_capability: str,
        application_roles: Iterable[str],
        permission_ceiling: Iterable[str],
        explicit_denies: Iterable[str] = (),
        constraints: Mapping[str, Any] | None = None,
        actor_chain: Mapping[str, Any] | None = None,
        component_capabilities: Iterable[str] = (),
    ) -> dict[str, Any]:
        release = self.store.get_release(application_id, release_digest)
        grant = ApplicationAccessGrant(
            grant_id="simulation:application-access",
            subject_ref=subject_ref,
            application_id=application_id,
            application_roles=tuple(application_roles),
            permission_ceiling=tuple(permission_ceiling),
            explicit_denies=tuple(explicit_denies),
            constraints=dict(constraints or {}),
            issuer_ref="system:policy-simulator",
            reviewed_permission_profile_digest=release.permission_profile.digest,
        )
        decision = evaluate_application_access(
            profile=release.permission_profile,
            roles=release.application_roles,
            grant=grant,
            permission_id=permission_id,
            app_capability=app_capability,
            actor_chain={"application_id": application_id, "subject_ref": subject_ref, **dict(actor_chain or {})},
            component_capabilities=component_capabilities,
        )
        return {"simulation": True, "persisted": False, "decision": decision.to_dict()}

    def put_connected_account(
        self,
        application_id: str,
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        release_digest = str(value.get("release_digest") or "").strip()
        release = self.store.get_release(application_id, release_digest)
        provider_id = str(value.get("provider_id") or "").strip().lower()
        declared = {
            str(item.get("id") or "").strip().lower(): item
            for item in release.permission_profile.external_providers
        }
        if provider_id not in declared:
            raise ApplicationAccessError("connected account provider is not declared")
        provider = declared[provider_id]
        account_id = str(value.get("account_id") or "").strip()
        subject_ref = str(value.get("subject_ref") or "").strip()
        mode = str(value.get("mode") or "delegated_user").strip().lower()
        status = str(value.get("status") or "missing").strip().lower()
        scopes = sorted({str(item).strip() for item in value.get("scopes") or () if str(item).strip()})
        if not account_id or not subject_ref:
            raise ApplicationAccessError("connected account identity is required")
        if mode not in {"delegated_user", "app_service"}:
            raise ApplicationAccessError("connected account mode is invalid")
        allowed_modes = {
            str(item).strip().lower()
            for item in provider.get("account_modes") or ("delegated_user", "app_service")
            if str(item).strip()
        }
        if mode not in allowed_modes:
            raise ApplicationAccessError("connected account mode is not declared for provider")
        if status not in {"missing", "connected", "expired", "revoked", "denied"}:
            raise ApplicationAccessError("connected account status is invalid")
        declared_scopes = {
            str(item).strip() for item in provider.get("scopes") or () if str(item).strip()
        }
        unknown_scopes = sorted(set(scopes) - declared_scopes)
        if unknown_scopes:
            raise ApplicationAccessError(
                "connected account scopes are not declared: " + ", ".join(unknown_scopes)
            )
        now = utc_now()
        path = self._document_path("connected_accounts", f"{application_id}\0{account_id}")
        previous = _read(path) if path.is_file() else {}
        token_expires_at = value.get("token_expires_at")
        expiry = _parse_time(str(token_expires_at or ""))
        if status == "connected" and expiry and expiry <= datetime.now(timezone.utc):
            status = "expired"
        previous_scopes = sorted(
            {str(item).strip() for item in previous.get("scopes") or () if str(item).strip()}
        )
        scope_changed_at = value.get("scope_changed_at")
        if previous and previous_scopes != scopes and not scope_changed_at:
            scope_changed_at = now
        record = {
            "schema": "adaos.application.connected_account.v1",
            "application_id": application_id,
            "release_digest": release_digest,
            "permission_profile_digest": release.permission_profile.digest,
            "account_id": account_id,
            "provider_id": provider_id,
            "subject_ref": subject_ref,
            "mode": mode,
            "scopes": scopes,
            "status": status,
            "token_expires_at": token_expires_at,
            "scope_changed_at": scope_changed_at,
            "created_at": str(previous.get("created_at") or value.get("created_at") or now),
            "updated_at": now,
        }
        with mutation_lock(self.store.lock_path, timeout_s=30.0):
            atomic_write_json(path, record)
        self.store.append_application_access_audit(
            {
                "occurred_at": now,
                "action": "connected_account_state",
                "application_id": application_id,
                "subject_ref": subject_ref,
                "provider_id": provider_id,
                "account_id": account_id,
                "status": status,
                "scopes": scopes,
                "previous_scopes": previous_scopes,
                "scope_changed": bool(previous and previous_scopes != scopes),
                "reviewed_permission_profile_digest": release.permission_profile.digest,
            }
        )
        return _redacted_account(record)

    def connected_accounts(
        self,
        application_id: str | None = None,
        *,
        subject_ref: str | None = None,
    ) -> list[dict[str, Any]]:
        parent = self.store.root / "connected_accounts"
        values = [_read(path) for path in parent.glob("*.json")] if parent.is_dir() else []
        now = datetime.now(timezone.utc)
        redacted = []
        for item in sorted(values, key=lambda value: (str(value.get("application_id")), str(value.get("account_id")))):
            if (application_id and item.get("application_id") != application_id) or (
                subject_ref and item.get("subject_ref") != subject_ref
            ):
                continue
            account = _redacted_account(item)
            expiry = _parse_time(str(account.get("token_expires_at") or ""))
            if account.get("status") == "connected" and expiry and expiry <= now:
                account["status"] = "expired"
            redacted.append(account)
        return redacted

    def privacy_report(self, application_id: str, *, release_digest: str) -> dict[str, Any]:
        release = self.store.get_release(application_id, release_digest)
        observations = [
            item
            for item in self.store.list_application_access_audit(application_id=application_id)
            if item.get("action") == "runtime_observation"
        ]
        observed_permissions = sorted({str(item.get("permission_id")) for item in observations if item.get("permission_id")})
        destinations = sorted({str(item.get("network_destination")) for item in observations if item.get("network_destination")})
        data_categories = sorted(
            {
                str(category)
                for item in observations
                for category in item.get("data_categories") or ()
                if str(category).strip()
            }
        )
        counts = {
            prefix: sum(1 for item in observations if str(item.get("permission_id") or "").startswith(prefix))
            for prefix in ("llm.", "model.", "secrets.", "notifications.", "background.")
        }
        return {
            "schema": "adaos.application.privacy_report.v1",
            "application_id": application_id,
            "release_digest": release_digest,
            "permission_profile_digest": release.permission_profile.digest,
            "declared": {
                "permissions": list(release.permission_profile.flat_permissions),
                "data_categories": list(release.permission_profile.data_practices.get("collected") or ()),
                "external_providers": [dict(item) for item in release.permission_profile.external_providers],
                "privacy_labels": dict(release.permission_profile.privacy_labels),
            },
            "observed": {
                "permissions": observed_permissions,
                "data_categories": data_categories,
                "network_destinations": destinations,
                "model_calls": counts["llm."] + counts["model."],
                "secret_uses": counts["secrets."],
                "notifications": counts["notifications."],
                "background_runs": counts["background."],
            },
        }

    def access_reviews(
        self,
        *,
        application_id: str | None = None,
        now: datetime | None = None,
        stale_days: int = 90,
    ) -> list[dict[str, Any]]:
        current = now or datetime.now(timezone.utc)
        cutoff = current - timedelta(days=max(1, stale_days))
        audit = self.store.list_application_access_audit(application_id=application_id)
        last_use: dict[str, datetime] = {}
        for item in audit:
            grant_id = str(item.get("grant_id") or "")
            observed = _parse_time(str(item.get("occurred_at") or ""))
            if grant_id and observed and (grant_id not in last_use or observed > last_use[grant_id]):
                last_use[grant_id] = observed
        findings: list[dict[str, Any]] = []
        for grant in self.store.list_application_access_grants(application_id):
            if grant.status != "active":
                continue
            kind = _subject_kind(grant)
            expiry = _parse_time(grant.expires_at)
            updated = _parse_time(grant.updated_at)
            reasons = []
            if kind == "guest" and (expiry is None or expiry > current + timedelta(days=30)):
                reasons.append("long_lived_guest")
            if last_use.get(grant.grant_id, updated or current) < cutoff:
                reasons.append("unused_grant")
            if grant.constraints.get("device_status") in {"stale", "revoked"}:
                reasons.append("stale_device")
            if any(is_high_risk_permission(item) for item in grant.permission_ceiling):
                reasons.append("sensitive_access")
            if reasons:
                findings.append(
                    {
                        "finding_id": "review." + hashlib.sha256((grant.grant_id + "|" + "|".join(reasons)).encode()).hexdigest()[:20],
                        "application_id": grant.application_id,
                        "subject_ref": grant.subject_ref,
                        "grant_id": grant.grant_id,
                        "reasons": reasons,
                        "recommended_action": "review_or_revoke",
                    }
                )
        for account in self.connected_accounts(application_id):
            if account.get("status") in {"expired", "revoked"}:
                findings.append(
                    {
                        "finding_id": f"review.account.{account['account_id']}",
                        "application_id": application_id,
                        "subject_ref": account.get("subject_ref"),
                        "account_id": account["account_id"],
                        "reasons": ["unused_or_inactive_connected_account"],
                        "recommended_action": "reconnect_or_remove",
                    }
                )
        seen_updates: set[tuple[str, str, str]] = set()
        for item in audit:
            if item.get("action") != "update_review" or not item.get("review_required"):
                continue
            identity = (
                str(item.get("application_id") or ""),
                str(item.get("old_release_digest") or ""),
                str(item.get("new_release_digest") or ""),
            )
            if identity in seen_updates:
                continue
            seen_updates.add(identity)
            findings.append(
                {
                    "finding_id": "review.update."
                    + hashlib.sha256("|".join(identity).encode()).hexdigest()[:20],
                    "application_id": identity[0],
                    "reasons": ["newly_elevated_permissions_or_roles"],
                    "old_release_digest": identity[1],
                    "new_release_digest": identity[2],
                    "affected_users": item.get("affected_users") or {},
                    "recommended_action": "review_update_before_resume",
                }
            )
        return findings

    def anomalies(self, application_id: str, *, release_digest: str) -> list[dict[str, Any]]:
        report = self.privacy_report(application_id, release_digest=release_digest)
        release = self.store.get_release(application_id, release_digest)
        declared = set(report["declared"]["permissions"])
        findings = [
            {"kind": "unexpected_permission", "permission_id": permission, "severity": "high" if is_high_risk_permission(permission) else "medium"}
            for permission in report["observed"]["permissions"]
            if permission not in declared
        ]
        provider_hosts = {
            str(item.get("destination") or item.get("host") or "").strip().lower()
            for item in report["declared"]["external_providers"]
        }
        findings.extend(
            {"kind": "unexpected_network_destination", "destination": destination, "severity": "high"}
            for destination in report["observed"]["network_destinations"]
            if destination.lower() not in provider_hosts
        )
        role_counts: dict[str, int] = {}
        for grant in self.store.list_application_access_grants(application_id):
            if grant.status != "active":
                continue
            for role_id in grant.application_roles:
                role_counts[role_id] = role_counts.get(role_id, 0) + 1
        sensitive_roles = {
            role.role_id for role in release.application_roles if role.sensitive
        }
        findings.extend(
            {
                "kind": "broad_sensitive_role_assignment",
                "role_id": role_id,
                "assignment_count": count,
                "severity": "high",
            }
            for role_id, count in sorted(role_counts.items())
            if role_id in sensitive_roles and count >= 3
        )
        return findings

    def application_detail(self, application_id: str, *, release_digest: str | None = None) -> dict[str, Any]:
        application = self.store.get_application(application_id)
        installation = None
        try:
            installation = self.store.get_installation(application_id)
        except FileNotFoundError:
            pass
        digest = release_digest or (installation.installed_release_digest if installation else "")
        if not digest:
            channels = self.store.get_channels(application_id).get("channels") or {}
            digest = str(channels.get("stable") or channels.get("prerelease") or "")
        release = self.store.get_release(application_id, digest)
        grants = self.store.list_application_access_grants(application_id)
        reports = self.list_verification_reports(application_id)
        latest_report = next((item for item in reports if item.get("release_digest") == digest), None)
        return {
            "schema": "adaos.application.access_surface.v1",
            "application": application.to_dict(),
            "release": release.to_dict(),
            "installation": installation.to_dict() if installation else None,
            "sections": {
                "permissions": {
                    "profile": release.permission_profile.to_dict(),
                    "digest": release.permission_profile.digest,
                    "privacy_report": self.privacy_report(application_id, release_digest=digest),
                    "badges": self.privacy_badges(application_id, release_digest=digest),
                },
                "access": [item.to_dict() for item in grants],
                "roles": [item.to_dict() for item in release.application_roles],
                "connected_accounts": self.connected_accounts(application_id),
                "release_readiness": latest_report,
                "activity": self.store.list_application_access_audit(application_id=application_id),
            },
        }

    def users_access(self, personalization: Mapping[str, Any] | None = None) -> dict[str, Any]:
        grants = self.store.list_application_access_grants()
        people: dict[str, dict[str, Any]] = {}
        for grant in grants:
            item = people.setdefault(
                grant.subject_ref,
                {
                    "subject_ref": grant.subject_ref,
                    "kind": _subject_kind(grant),
                    "application_access": [],
                },
            )
            item["application_access"].append(grant.to_dict())
        directory = dict(personalization or {})
        profiles = {
            str(item.get("user_id") or ""): {
                key: item.get(key)
                for key in (
                    "display_name",
                    "preferred_name",
                    "locale",
                    "language",
                    "timezone",
                    "metadata_only",
                )
            }
            for item in directory.get("profiles") or ()
            if isinstance(item, Mapping) and str(item.get("user_id") or "")
        }
        membership_by_subject: dict[str, list[dict[str, Any]]] = {}
        for membership in directory.get("memberships") or ():
            if not isinstance(membership, Mapping):
                continue
            subject = membership.get("subject") or {}
            subject_ref = (
                f"{subject.get('kind')}:{subject.get('id')}"
                if isinstance(subject, Mapping)
                and subject.get("kind")
                and subject.get("id")
                else ""
            )
            if subject_ref:
                membership_by_subject.setdefault(subject_ref, []).append(
                    {
                        key: membership.get(key)
                        for key in ("scope", "role", "status", "expires_at")
                    }
                )
        for user in directory.get("users") or ():
            if not isinstance(user, Mapping):
                continue
            subject = user.get("subject") or {}
            user_id = str(user.get("user_id") or "")
            subject_ref = (
                f"{subject.get('kind')}:{subject.get('id')}"
                if isinstance(subject, Mapping)
                and subject.get("kind")
                and subject.get("id")
                else f"user:{user_id}"
                if user_id
                else ""
            )
            if not subject_ref:
                continue
            memberships = membership_by_subject.get(subject_ref, [])
            platform_roles = {
                str(item.get("role") or "") for item in memberships
            }
            kind = (
                "child"
                if "child" in platform_roles
                else "guest"
                if "guest" in platform_roles
                else "user"
            )
            person = people.setdefault(
                subject_ref,
                {"subject_ref": subject_ref, "kind": kind, "application_access": []},
            )
            person["kind"] = kind
            person["profile"] = profiles.get(user_id, {"metadata_only": True})
            person["memberships"] = memberships
        pending_guests = []
        for invite in directory.get("invites") or ():
            if not isinstance(invite, Mapping) or invite.get("kind") != "guest_join_link":
                continue
            pending_guests.append(
                {
                    "subject_ref": f"invite:{invite.get('invite_id')}",
                    "kind": "guest",
                    "application_access": [],
                    "invite": {
                        key: invite.get(key)
                        for key in (
                            "invite_id",
                            "status",
                            "expires_at",
                            "single_use",
                            "max_sessions",
                        )
                    },
                }
            )
        devices = list(directory.get("devices") or ())
        sessions = list(directory.get("sessions") or ())
        activity = self.store.list_application_access_audit()
        person_values = sorted(people.values(), key=lambda item: item["subject_ref"])
        return {
            "schema": "adaos.users_access.surface.v1",
            "people": [item for item in person_values if item["kind"] == "user"],
            "guests": [
                *[item for item in person_values if item["kind"] == "guest"],
                *pending_guests,
            ],
            "children": [item for item in person_values if item["kind"] == "child"],
            "devices": devices,
            "sessions": sessions,
            "application_access": [item.to_dict() for item in grants],
            "activity": activity,
            "diagnostics": {
                "content_redacted": True,
                "source": "personalization_metadata_and_application_access",
                "fields": ["subject_ref", "grant_id", "roles", "permissions", "decision", "reason_code", "device/session status"],
            },
        }

    def update_review(
        self,
        application_id: str,
        *,
        old_release_digest: str,
        new_release_digest: str,
    ) -> dict[str, Any]:
        old = self.store.get_release(application_id, old_release_digest)
        new = self.store.get_release(application_id, new_release_digest)
        assignments: dict[str, list[str]] = {}
        for grant in self.store.list_application_access_grants(application_id):
            for role in grant.application_roles:
                assignments.setdefault(role, []).append(grant.subject_ref)
        diff = classify_access_profile_diff(
            old.permission_profile,
            new.permission_profile,
            old_roles=old.application_roles,
            new_roles=new.application_roles,
            existing_role_assignments=assignments,
        )
        permission_changes = diff["permission_changes"]
        role_changes = diff["role_changes"]
        review_required = bool(
            permission_changes["added"]
            or permission_changes["elevated"]
            or permission_changes["sensitive_added"]
            or role_changes["elevated"]
            or role_changes["remap_required"]
        )
        result = {
            **diff,
            "review_required": review_required,
            "auto_update_allowed": not review_required,
        }
        self.store.append_application_access_audit(
            {
                "occurred_at": utc_now(),
                "action": "update_review",
                "application_id": application_id,
                "subject_ref": f"application:{application_id}",
                "old_release_digest": old_release_digest,
                "new_release_digest": new_release_digest,
                "review_required": review_required,
                "decision": "update_blocked" if review_required else "update_allowed",
                "permission_changes": permission_changes,
                "role_changes": role_changes,
                "affected_users": role_changes.get("affected_users") or {},
                "reviewed_permission_profile_digest": new.permission_profile.digest,
            }
        )
        return result

    def permission_profiler(
        self,
        application_id: str,
        *,
        release_digest: str,
        observed_capabilities: Iterable[str] = (),
        inferred_capabilities: Iterable[str] = (),
        previous_release_digest: str | None = None,
    ) -> dict[str, Any]:
        release = self.store.get_release(application_id, release_digest)
        declared = set(release.permission_profile.flat_permissions)
        observed = {str(item).strip().lower() for item in observed_capabilities if str(item).strip()}
        inferred = {str(item).strip().lower() for item in inferred_capabilities if str(item).strip()}
        used = observed | inferred
        role_matrix = []
        for role in release.application_roles:
            compatible = {
                kind: kind in role.assignable_to
                and not (
                    kind == "guest"
                    and (
                        role.sensitive
                        or any(is_high_risk_permission(item) for item in role.requires_permissions)
                    )
                )
                for kind in ("owner", "member", "child", "guest")
            }
            role_matrix.append({"role": role.to_dict(), "compatible": compatible})
        preview_modes = {
            kind: [
                item["role"]["id"]
                for item in role_matrix
                if item["compatible"].get(kind)
            ]
            for kind in ("owner", "member", "child", "guest")
        }
        preview_modes["custom"] = [item.role_id for item in release.application_roles]
        role_diff = None
        if previous_release_digest:
            role_diff = self.update_review(
                application_id,
                old_release_digest=previous_release_digest,
                new_release_digest=release_digest,
            )
        return {
            "schema": "adaos.builder.application_permission_profile.v1",
            "application_id": application_id,
            "release_digest": release_digest,
            "permission_profile_digest": release.permission_profile.digest,
            "declared": sorted(declared),
            "statically_inferred": sorted(inferred),
            "observed": sorted(observed),
            "undeclared_observed": sorted(used - declared),
            "undeclared_high_risk": sorted(item for item in used - declared if is_high_risk_permission(item)),
            "unused": sorted(declared - used),
            "role_matrix": role_matrix,
            "preview_modes": preview_modes,
            "role_diff": role_diff,
            "verification_inputs": {
                "observed_capabilities": sorted(observed),
                "inferred_capabilities": sorted(inferred),
                "access_matrix_required": bool(release.application_roles),
            },
        }

    def export_snapshot(self, application_id: str) -> dict[str, Any]:
        grants = [item.to_dict() for item in self.store.list_application_access_grants(application_id)]
        accounts = self.connected_accounts(application_id)
        payload = {
            "schema": "adaos.application.access_snapshot.v1",
            "application_id": application_id,
            "grants": grants,
            "connected_accounts": accounts,
            "exported_at": utc_now(),
        }
        return {**payload, "snapshot_digest": canonical_payload_digest(payload)}

    def import_snapshot(
        self,
        value: Mapping[str, Any],
        *,
        issuer_ref: str,
        apply: bool = False,
    ) -> dict[str, Any]:
        payload = dict(value)
        supplied_digest = str(payload.pop("snapshot_digest", ""))
        if payload.get("schema") != "adaos.application.access_snapshot.v1":
            raise ApplicationAccessError("unsupported Application access snapshot")
        if supplied_digest != canonical_payload_digest(payload):
            raise ApplicationAccessError("Application access snapshot digest mismatch")
        application_id = str(payload.get("application_id") or "")
        planned: list[dict[str, Any]] = []
        for raw in payload.get("grants") or ():
            grant = ApplicationAccessGrant.from_mapping(raw)
            if grant.application_id != application_id:
                raise ApplicationAccessError("snapshot grant belongs to another Application")
            if grant.status != "active":
                continue
            planned.append(grant.to_dict())
            if apply:
                release = next(
                    item
                    for item in self.store.list_releases(application_id)
                    if item.permission_profile.digest == grant.reviewed_permission_profile_digest
                )
                self.access.grant_access(
                    application_id,
                    release_digest=release.release_digest,
                    subject_ref=grant.subject_ref,
                    application_roles=grant.application_roles,
                    issuer_ref=issuer_ref,
                    idempotency_key=f"snapshot:{supplied_digest}:{grant.subject_ref}",
                    permission_ceiling=grant.permission_ceiling,
                    explicit_denies=grant.explicit_denies,
                    constraints=grant.constraints,
                    expires_at=grant.expires_at,
                )
        return {"valid": True, "applied": apply, "application_id": application_id, "planned_grants": planned}

    def privacy_badges(self, application_id: str, *, release_digest: str) -> list[dict[str, Any]]:
        report = self.privacy_report(application_id, release_digest=release_digest)
        labels = report["declared"]["privacy_labels"]
        badges = [
            {"id": "local_data", "status": "warning" if labels.get("sent_off_device") else "passed"},
            {"id": "tracking", "status": "warning" if labels.get("tracking") else "passed"},
            {"id": "user_linked", "status": "disclosed" if labels.get("linked_to_user") else "not_collected"},
            {"id": "observed_matches_declared", "status": "failed" if self.anomalies(application_id, release_digest=release_digest) else "passed"},
        ]
        return badges

    def save_verification_report(self, report: ApplicationVerificationReport) -> dict[str, Any]:
        path = self._document_path(
            "verification_reports",
            "\0".join(
                (
                    report.application_id,
                    report.release_digest,
                    report.release_scope,
                    str(report.report_digest or report.computed_digest()),
                )
            ),
        )
        with mutation_lock(self.store.lock_path, timeout_s=30.0):
            if path.is_file():
                existing = ApplicationVerificationReport.from_mapping(_read(path))
                if existing != report:
                    raise ApplicationAccessError(
                        "immutable Application verification report differs"
                    )
            else:
                atomic_write_json(path, report.to_dict())
        return report.to_dict()

    def list_verification_reports(self, application_id: str | None = None) -> list[dict[str, Any]]:
        parent = self.store.root / "verification_reports"
        values = [_read(path) for path in parent.glob("*.json")] if parent.is_dir() else []
        reports = [ApplicationVerificationReport.from_mapping(value).to_dict() for value in values]
        return [
            item
            for item in sorted(reports, key=lambda value: str(value.get("created_at") or ""), reverse=True)
            if not application_id or item.get("application_id") == application_id
        ]

    def admit_release_stage(
        self,
        application_id: str,
        *,
        release_digest: str,
        stage: str,
    ) -> dict[str, Any]:
        """Require a matching passed report for access-aware release movement."""

        required_scope = str(stage or "").strip().lower()
        allowed_scopes = {
            "trial": {"trial", "publication"},
            "prerelease": {"publication"},
            "publication": {"publication"},
            "stable": {"publication"},
        }
        if required_scope not in allowed_scopes:
            raise ApplicationAccessError("unsupported Application release stage")
        release = self.store.get_release(application_id, release_digest)
        composition = release.project_release.composition_lock
        if composition is None or composition.permission_profile is None:
            return {
                "required": False,
                "admitted": True,
                "reason": "legacy_application_contract",
            }
        candidates = []
        for payload in self.list_verification_reports(application_id):
            if payload.get("release_digest") != release_digest:
                continue
            report = ApplicationVerificationReport.from_mapping(payload)
            if report.release_scope not in allowed_scopes[required_scope]:
                continue
            if report.permission_profile_digest != release.permission_profile.digest:
                continue
            candidates.append(report)
        passed = [item for item in candidates if item.overall == "passed"]
        if not passed:
            raise ApplicationAccessError(
                f"passed Application Final Verification for {required_scope} is required"
            )
        selected = sorted(passed, key=lambda item: item.created_at, reverse=True)[0]
        return {
            "required": True,
            "admitted": True,
            "stage": required_scope,
            "report_digest": selected.report_digest,
            "release_scope": selected.release_scope,
            "permission_profile_digest": selected.permission_profile_digest,
        }

    def final_verification(
        self,
        application_id: str,
        *,
        release_digest: str,
        source_commit: str,
        observed_capabilities: Iterable[str],
        inferred_capabilities: Iterable[str],
        regression_evidence: Iterable[str],
        access_matrix_evidence: Iterable[str],
        pending_action_evidence: Iterable[str],
        audit_evidence: Iterable[str],
        disclosure_evidence: Iterable[str],
        redaction_evidence: Iterable[str],
        release_scope: str = "candidate",
        actor_ref: str = "system:builder",
    ) -> dict[str, Any]:
        release = self.store.get_release(application_id, release_digest)
        disclosure_refs = tuple(str(item).strip() for item in disclosure_evidence if str(item).strip())
        redaction_refs = tuple(str(item).strip() for item in redaction_evidence if str(item).strip())
        regression_refs = tuple(
            str(item).strip() for item in regression_evidence if str(item).strip()
        )
        publication_regression = any(
            item.startswith(("release:", "suite:release", "skip:bounded:"))
            for item in regression_refs
        )
        base = build_application_verification_report(
            application_id=application_id,
            release_digest=release_digest,
            source_commit=source_commit,
            profile=release.permission_profile,
            roles=release.application_roles,
            observed_capabilities=observed_capabilities,
            inferred_capabilities=inferred_capabilities,
            regression_evidence=regression_evidence,
            access_matrix_evidence=access_matrix_evidence,
            pending_action_fallbacks=pending_action_evidence,
            audit_evidence=audit_evidence,
        )
        extra = (
            VerificationCheck(
                "disclosures.external_effects",
                "hard_gate",
                "passed" if disclosure_refs else "inconclusive",
                evidence=disclosure_refs[0] if disclosure_refs else None,
                message="External effects disclosure evidence recorded." if disclosure_refs else "Disclosure evidence is required.",
            ),
            VerificationCheck(
                "secrets.redaction",
                "hard_gate",
                "passed" if redaction_refs else "inconclusive",
                evidence=redaction_refs[0] if redaction_refs else None,
                message="Secret redaction evidence recorded." if redaction_refs else "Secret redaction evidence is required.",
            ),
            VerificationCheck(
                "release.scope",
                "attestation",
                "passed",
                message=f"Verification scope: {release_scope}.",
                actor_ref=actor_ref,
            ),
            VerificationCheck(
                "permissions.flat_compatibility",
                "hard_gate",
                "passed"
                if tuple(release.project_release.permissions)
                == tuple(release.permission_profile.flat_permissions)
                else "failed",
                message="Flat permission compatibility projection matches the structured profile.",
            ),
            VerificationCheck(
                "regression.publication_profile",
                "hard_gate",
                "passed"
                if release_scope != "publication" or publication_regression
                else "inconclusive",
                evidence=(regression_refs[0] if publication_regression else None),
                message=(
                    "Publication release test profile or bounded skip is recorded."
                    if release_scope != "publication" or publication_regression
                    else "Publication requires release: or suite:release evidence, or skip:bounded:<reason>."
                ),
            ),
        )
        report = replace(
            base,
            checks=base.checks + extra,
            release_scope=release_scope,
            report_digest=None,
        ).seal()
        statement = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": application_id, "digest": {"sha256": release_digest.removeprefix("sha256:")}}],
            "predicateType": "https://adaos.dev/attestations/application-verification/v1",
            "predicate": {
                "report_digest": report.report_digest,
                "permission_profile_digest": report.permission_profile_digest,
                "observed_capabilities_digest": report.observed_capabilities_digest,
                "overall": report.overall,
                "source_commit": source_commit,
                "release_scope": release_scope,
            },
        }
        saved = self.save_verification_report(report)
        bundle = {
            "schema": "adaos.application.release_evidence_bundle.v1",
            "application_id": application_id,
            "release_digest": release_digest,
            "permission_profile_digest": report.permission_profile_digest,
            "observed_capabilities_digest": report.observed_capabilities_digest,
            "release_scope": release_scope,
            "source_commit": source_commit,
            "report_digest": report.report_digest,
            "overall": report.overall,
            "evidence_refs": sorted(
                {
                    *regression_refs,
                    *(str(item).strip() for item in access_matrix_evidence if str(item).strip()),
                    *(str(item).strip() for item in pending_action_evidence if str(item).strip()),
                    *(str(item).strip() for item in audit_evidence if str(item).strip()),
                    *disclosure_refs,
                    *redaction_refs,
                }
            ),
            "attestation_statement_digest": canonical_payload_digest(statement),
        }
        return {
            "report": saved,
            "checklist": sorted(saved["checks"], key=lambda item: (item["result"] not in {"failed", "inconclusive"}, item["id"])),
            "publication_allowed": report.overall == "passed",
            "attestation": {**statement, "statement_digest": canonical_payload_digest(statement)},
            "evidence_bundle": {
                **bundle,
                "bundle_digest": canonical_payload_digest(bundle),
            },
            "ci_status": "passed" if report.overall == "passed" else "failed",
        }

    def apply_pending_action_response(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply one authoritative Pending Action response to its access grant."""

        action = payload.get("pending_action")
        action = dict(action) if isinstance(action, Mapping) else {}
        response = payload.get("response")
        response = dict(response) if isinstance(response, Mapping) else {}
        domain = payload.get("domain_ref")
        domain = dict(domain) if isinstance(domain, Mapping) else dict(action.get("domain_ref") or {})
        pending_action_id = str(
            payload.get("pending_action_id") or action.get("id") or ""
        ).strip()
        if action.get("kind") != "application.access.revoke":
            raise ApplicationAccessError("unsupported Application access Pending Action")
        if pending_action_id != str(action.get("id") or "").strip():
            raise ApplicationAccessError("Application access Pending Action identity mismatch")
        response_action_id = str(
            payload.get("response_action_id")
            or response.get("response_action_id")
            or ""
        ).strip()
        if response_action_id not in {"approve", "refuse", "postpone"}:
            raise ApplicationAccessError("unsupported Application access response")
        grant_id = str(domain.get("grant_id") or "").strip()
        expected_revision = int(domain.get("expected_revision") or 0)
        if not grant_id or expected_revision < 1:
            raise ApplicationAccessError("Application access response is missing grant revision")
        grant = self.store.get_application_access_grant(grant_id)
        if grant.application_id != str(domain.get("application_id") or "").strip():
            raise ApplicationAccessError("Application access response grant mismatch")
        if grant.subject_ref != str(domain.get("subject_ref") or "").strip():
            raise ApplicationAccessError("Application access response subject mismatch")
        if grant.reviewed_permission_profile_digest != str(
            domain.get("reviewed_permission_profile_digest") or ""
        ).strip():
            raise ApplicationAccessError(
                "Application access response permission profile mismatch"
            )
        responder = response.get("responder")
        responder = dict(responder) if isinstance(responder, Mapping) else {}
        responder_type = str(responder.get("type") or "user").strip()
        responder_id = next(
            (
                str(responder.get(key) or "").strip()
                for key in ("user_id", "session_id", "system_id", "instance_id", "id")
                if str(responder.get(key) or "").strip()
            ),
            "unknown",
        )
        issuer_ref = str(responder.get("ref") or "").strip() or (
            f"{responder_type}:{responder_id}"
        )
        audit = self.store.append_application_access_audit(
            {
                "action": "pending_action_response",
                "application_id": grant.application_id,
                "subject_ref": grant.subject_ref,
                "grant_id": grant.grant_id,
                "pending_action_id": pending_action_id,
                "response_action_id": response_action_id,
                "issuer_ref": issuer_ref,
                "expected_revision": expected_revision,
                "reviewed_permission_profile_digest": grant.reviewed_permission_profile_digest,
            }
        )
        if response_action_id != "approve":
            return {"applied": False, "grant": grant.to_dict(), "audit": audit}
        revoked = self.access.revoke_access(
            grant_id,
            issuer_ref=issuer_ref,
            expected_revision=expected_revision,
        )
        return {"applied": True, "grant": revoked.to_dict(), "audit": audit}

    @staticmethod
    def surface_contract() -> dict[str, Any]:
        return {
            "schema": "adaos.application.access_ui_contract.v1",
            "applications_tabs": ["permissions", "access", "roles", "connected_accounts", "release_readiness", "activity"],
            "users_access_tabs": [
                "people",
                "guests",
                "children",
                "devices",
                "sessions",
                "application_access",
                "activity",
            ],
            "commands": ["assign_role", "change_role", "revoke_access", "simulate_policy", "export_snapshot", "import_snapshot"],
            "responsive": {"compact": "single_column", "wide": "master_detail"},
            "keyboard": {"tabs": ["ArrowLeft", "ArrowRight"], "activate": ["Enter", "Space"], "close": ["Escape"]},
            "locales": ["en", "ru"],
            "embedded_role_management": {"writes": "platform_api_only"},
        }


@subscribe("application.access.conversation")
async def _on_application_access_conversation(evt: Any) -> None:
    payload = getattr(evt, "payload", None)
    if not isinstance(payload, Mapping):
        return
    try:
        ctx: AgentContext = get_ctx()
        state = Path(getattr(ctx, "authority_state_dir", None) or ctx.paths.state_dir())
        from .runtime import get_application_service

        management = ApplicationAccessManagementService(get_application_service(state))
        await asyncio.to_thread(management.apply_pending_action_response, dict(payload))
    except Exception:
        _log.warning("failed to apply Application access Pending Action response", exc_info=True)


__all__ = ["ApplicationAccessManagementService", "ROLE_TEMPLATES"]
