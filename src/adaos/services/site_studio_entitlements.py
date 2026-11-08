from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from uuid import uuid4

from adaos.services.agent_context import get_ctx
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.root_mcp_errors import RootMcpToolError


ENTITLEMENT_SCHEMA = "adaos.site_studio.service_entitlement.v1"
REGISTRY_SCHEMA = "adaos.site_studio.service_entitlement_registry.v1"
STATUS_SCHEMA = "adaos.site_studio.service_entitlement_status.v1"
RECEIPT_SCHEMA = "adaos.site_studio.service_entitlement_receipt.v1"
POLICY_REVISION = "site-studio-beta-1"

FEATURES = ("authoring", "publication", "custom_domains")
ENTITLEMENT_STATUSES = {"active", "suspended", "revoked"}
AVAILABILITY_STATUSES = {"available", "limited", "suspended", "unavailable"}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,239}$")

DEFAULT_LIMITS: dict[str, int] = {
    "projects": 10,
    "retained_revisions": 500,
    "uploaded_bytes": 1024 * 1024 * 1024,
    "compile_operations_1h": 120,
    "prepare_operations_1h": 20,
    "publish_operations_24h": 20,
    "custom_domains": 5,
    "llm_requests_24h": 200,
}

_OPERATION_LIMITS: dict[str, tuple[str, timedelta | None]] = {
    "project.create": ("projects", None),
    "revision.retain": ("retained_revisions", None),
    "asset.upload_bytes": ("uploaded_bytes", None),
    "compile": ("compile_operations_1h", timedelta(hours=1)),
    "prepare": ("prepare_operations_1h", timedelta(hours=1)),
    "publish": ("publish_operations_24h", timedelta(hours=24)),
    "domain.request": ("custom_domains", None),
    "llm.request": ("llm_requests_24h", timedelta(hours=24)),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).replace(microsecond=0).isoformat()


def _datetime(value: Any) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("effective dates must be ISO-8601 timestamps") from exc
    if parsed.tzinfo is None:
        raise ValueError("effective dates must include a timezone")
    return parsed.astimezone(timezone.utc)


def _identifier(value: Any, label: str) -> str:
    token = str(value or "").strip()
    if not _ID.fullmatch(token):
        raise ValueError(f"{label} is invalid")
    return token


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _empty_registry() -> dict[str, Any]:
    return {
        "schema": REGISTRY_SCHEMA,
        "revision": 0,
        "policy_revision": POLICY_REVISION,
        "updated_at": _iso(),
        "entitlements": {},
        "switches": {
            feature: {
                "status": "available",
                "reason_code": "available",
                "updated_at": _iso(),
            }
            for feature in FEATURES
        },
        "usage_events": [],
        "receipts": [],
    }


class SiteStudioEntitlementService:
    """Root-owned admission, quota, and operational availability authority."""

    def __init__(self, *, state_dir: Path | None = None) -> None:
        if state_dir is None:
            raw = get_ctx().paths.root_mcp_state_dir()
            state_dir = Path(raw() if callable(raw) else raw)
        self.root = Path(state_dir).resolve() / "site-studio-entitlements"
        self.path = self.root / "registry.json"
        self.lock_path = self.root / ".mutation.lock"

    def grant(
        self,
        *,
        scope_kind: str,
        scope_id: str,
        environment: str,
        scenario_id: str,
        skill_id: str,
        actor: str,
        reason: str,
        features: Sequence[str] = FEATURES,
        application_id: str | None = None,
        effective_from: str | None = None,
        effective_until: str | None = None,
        limits: Mapping[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        scope_kind = str(scope_kind or "").strip().lower()
        if scope_kind not in {"subject", "organization"}:
            raise ValueError("scope_kind must be subject or organization")
        scope_id = _identifier(scope_id, "scope_id")
        environment = _identifier(environment, "environment").lower()
        scenario_id = _identifier(scenario_id, "scenario_id")
        skill_id = _identifier(skill_id, "skill_id")
        application_id = (
            _identifier(application_id, "application_id") if application_id else None
        )
        actor = _identifier(actor, "actor")
        reason = str(reason or "").strip()
        if not reason or len(reason) > 500:
            raise ValueError("a bounded grant reason is required")
        normalized_features = tuple(dict.fromkeys(str(item).strip() for item in features))
        if not normalized_features or any(item not in FEATURES for item in normalized_features):
            raise ValueError("features contain an unsupported Site Studio feature")
        starts = _datetime(effective_from) or _now()
        ends = _datetime(effective_until)
        if ends is not None and ends <= starts:
            raise ValueError("effective_until must be later than effective_from")
        normalized_limits = self._limits(limits)
        now = _iso()
        entitlement_id = "siteent_" + uuid4().hex
        entitlement = {
            "schema": ENTITLEMENT_SCHEMA,
            "entitlement_id": entitlement_id,
            "scope": {"kind": scope_kind, "id": scope_id},
            "environment": environment,
            "application_id": application_id,
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "policy_revision": POLICY_REVISION,
            "status": "active",
            "features": {
                feature: {
                    "status": "active" if feature in normalized_features else "revoked"
                }
                for feature in FEATURES
            },
            "limits": normalized_limits,
            "effective_from": _iso(starts),
            "effective_until": _iso(ends) if ends else None,
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        }
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            existing = self._matching_binding(
                registry,
                scope_kind=scope_kind,
                scope_id=scope_id,
                environment=environment,
                scenario_id=scenario_id,
                skill_id=skill_id,
                application_id=application_id,
                include_revoked=False,
            )
            if existing is not None:
                raise ValueError("an active entitlement already exists for this binding")
            entitlements = dict(registry["entitlements"])
            entitlements[entitlement_id] = entitlement
            receipt = self._receipt(
                registry,
                action="grant",
                entitlement_id=entitlement_id,
                actor=actor,
                reason=reason,
                request_id=request_id,
                change={
                    "status": "active",
                    "features": list(normalized_features),
                    "policy_revision": POLICY_REVISION,
                },
            )
            registry["entitlements"] = entitlements
            self._commit(registry, receipt)
        return self._project(entitlement, registry, receipt=receipt)

    def set_status(
        self,
        entitlement_id: str,
        *,
        status: str,
        actor: str,
        reason: str,
        expected_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        entitlement_id = _identifier(entitlement_id, "entitlement_id")
        status = str(status or "").strip().lower()
        if status not in ENTITLEMENT_STATUSES:
            raise ValueError("entitlement status is invalid")
        actor = _identifier(actor, "actor")
        reason = str(reason or "").strip()
        if not reason or len(reason) > 500:
            raise ValueError("a bounded status reason is required")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            current = dict(registry["entitlements"].get(entitlement_id) or {})
            if not current:
                raise KeyError(entitlement_id)
            if int(current.get("revision") or 0) != int(expected_revision):
                raise RootMcpToolError(
                    "site_studio_entitlement_revision_conflict",
                    "Site Studio entitlement changed; reload before retrying.",
                )
            if current.get("status") == "revoked" and status != "revoked":
                raise ValueError("a revoked entitlement cannot be reactivated")
            current.update(
                {
                    "status": status,
                    "revision": int(current["revision"]) + 1,
                    "updated_at": _iso(),
                }
            )
            receipt = self._receipt(
                registry,
                action="revoke" if status == "revoked" else "set_status",
                entitlement_id=entitlement_id,
                actor=actor,
                reason=reason,
                request_id=request_id,
                change={"status": status, "revision": current["revision"]},
            )
            registry["entitlements"][entitlement_id] = current
            self._commit(registry, receipt)
        return self._project(current, registry, receipt=receipt)

    def set_switch(
        self,
        feature: str,
        *,
        status: str,
        reason_code: str,
        actor: str,
        reason: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        feature = str(feature or "").strip()
        status = str(status or "").strip().lower()
        if feature not in FEATURES or status not in AVAILABILITY_STATUSES:
            raise ValueError("Site Studio feature switch is invalid")
        reason_code = _identifier(reason_code, "reason_code")
        actor = _identifier(actor, "actor")
        reason = str(reason or "").strip()
        if not reason or len(reason) > 500:
            raise ValueError("a bounded switch reason is required")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            switch = {
                "status": status,
                "reason_code": reason_code,
                "updated_at": _iso(),
            }
            registry["switches"][feature] = switch
            receipt = self._receipt(
                registry,
                action="set_switch",
                entitlement_id=None,
                actor=actor,
                reason=reason,
                request_id=request_id,
                change={"feature": feature, **switch},
            )
            self._commit(registry, receipt)
        return {"feature": feature, **switch, "receipt": self._receipt_view(receipt)}

    def status(
        self,
        *,
        scope_refs: Sequence[str],
        environment: str,
        scenario_id: str,
        skill_id: str,
        application_id: str | None = None,
    ) -> dict[str, Any]:
        registry = self._read()
        entitlement = self._resolve(
            registry,
            scope_refs=scope_refs,
            environment=environment,
            scenario_id=scenario_id,
            skill_id=skill_id,
            application_id=application_id,
        )
        return self._status_projection(registry, entitlement)

    def authorize(
        self,
        *,
        scope_refs: Sequence[str],
        environment: str,
        scenario_id: str,
        skill_id: str,
        feature: str,
        operation: str,
        application_id: str | None = None,
        amount: int = 1,
        resource_id: str | None = None,
        idempotency_key: str | None = None,
        consume: bool = False,
        actor: str | None = None,
    ) -> dict[str, Any]:
        if feature not in FEATURES:
            raise ValueError("unsupported Site Studio feature")
        operation = str(operation or "").strip()
        if operation not in _OPERATION_LIMITS and operation != "status":
            raise ValueError("unsupported Site Studio operation")
        if isinstance(amount, bool) or int(amount) < 1:
            raise ValueError("quota amount must be a positive integer")
        amount = int(amount)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            entitlement = self._resolve(
                registry,
                scope_refs=scope_refs,
                environment=environment,
                scenario_id=scenario_id,
                skill_id=skill_id,
                application_id=application_id,
            )
            status = self._status_projection(registry, entitlement)
            feature_view = dict(status["features"].get(feature) or {})
            if feature_view.get("status") not in {"available", "limited"}:
                code = str(feature_view.get("reason_code") or "site_studio_entitlement_required")
                raise RootMcpToolError(
                    code,
                    str(feature_view.get("message") or "Site Studio feature is unavailable."),
                    details={
                        "feature": feature,
                        "availability": feature_view.get("status"),
                        "administrative_action": feature_view.get("administrative_action"),
                    },
                )
            if operation == "status":
                return status
            limit_key, window = _OPERATION_LIMITS[operation]
            key = str(idempotency_key or "").strip()
            duplicate = None
            if consume:
                if not key:
                    raise ValueError("consuming quota requires an idempotency key")
                duplicate = next(
                    (
                        item
                        for item in registry["usage_events"]
                        if item.get("entitlement_id") == entitlement["entitlement_id"]
                        and item.get("idempotency_key") == key
                    ),
                    None,
                )
                if duplicate is not None:
                    return status
            usage = self._usage(
                registry,
                entitlement_id=str(entitlement["entitlement_id"]),
                limits=dict(entitlement["limits"]),
            )
            row = dict(usage[limit_key])
            if int(row["used"]) + amount > int(row["limit"]):
                raise RootMcpToolError(
                    "site_studio_quota_exhausted",
                    "The Site Studio service quota is exhausted.",
                    details={
                        "feature": feature,
                        "operation": operation,
                        "quota": limit_key,
                        "used": row["used"],
                        "limit": row["limit"],
                        "resets_at": row.get("resets_at"),
                    },
                )
            if consume:
                registry["usage_events"].append(
                    {
                        "event_id": "siteusage_" + uuid4().hex,
                        "entitlement_id": entitlement["entitlement_id"],
                        "feature": feature,
                        "operation": operation,
                        "quota": limit_key,
                        "amount": amount,
                        "resource_id": str(resource_id or "")[:240] or None,
                        "idempotency_key": key[:240],
                        "actor": str(actor or "")[:240] or None,
                        "created_at": _iso(),
                    }
                )
                self._commit(registry)
            return self._status_projection(registry, entitlement)

    def list_entitlements(self) -> dict[str, Any]:
        registry = self._read()
        items = [
            self._project(item, registry)
            for item in registry["entitlements"].values()
        ]
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return {
            "schema": "adaos.site_studio.service_entitlement_collection.v1",
            "policy_revision": registry["policy_revision"],
            "items": items,
        }

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return _empty_registry()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Site Studio entitlement registry is unreadable") from exc
        if payload.get("schema") != REGISTRY_SCHEMA:
            raise RuntimeError("Site Studio entitlement registry schema is invalid")
        payload.setdefault("entitlements", {})
        payload.setdefault("usage_events", [])
        payload.setdefault("receipts", [])
        payload.setdefault("switches", _empty_registry()["switches"])
        return payload

    def _commit(
        self, registry: dict[str, Any], receipt: Mapping[str, Any] | None = None
    ) -> None:
        if receipt is not None:
            registry["receipts"] = [*registry.get("receipts", []), dict(receipt)]
        cutoff = _now() - timedelta(days=31)
        registry["usage_events"] = [
            item
            for item in registry.get("usage_events", [])
            if (_datetime(item.get("created_at")) or _now()) >= cutoff
        ]
        registry["revision"] = int(registry.get("revision") or 0) + 1
        registry["updated_at"] = _iso()
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, registry)

    @staticmethod
    def _limits(value: Mapping[str, Any] | None) -> dict[str, int]:
        result = dict(DEFAULT_LIMITS)
        for key, raw in dict(value or {}).items():
            if key not in result or isinstance(raw, bool):
                raise ValueError("limits contain an unsupported quota")
            try:
                parsed = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("quota limits must be positive integers") from exc
            if parsed < 1:
                raise ValueError("quota limits must be positive integers")
            result[key] = parsed
        return result

    @staticmethod
    def _matching_binding(
        registry: Mapping[str, Any],
        *,
        scope_kind: str,
        scope_id: str,
        environment: str,
        scenario_id: str,
        skill_id: str,
        application_id: str | None,
        include_revoked: bool,
    ) -> dict[str, Any] | None:
        for item in (registry.get("entitlements") or {}).values():
            if (
                item.get("scope") == {"kind": scope_kind, "id": scope_id}
                and item.get("environment") == environment
                and item.get("scenario_id") == scenario_id
                and item.get("skill_id") == skill_id
                and item.get("application_id") == application_id
                and (include_revoked or item.get("status") != "revoked")
            ):
                return dict(item)
        return None

    def _resolve(
        self,
        registry: Mapping[str, Any],
        *,
        scope_refs: Sequence[str],
        environment: str,
        scenario_id: str,
        skill_id: str,
        application_id: str | None,
    ) -> dict[str, Any] | None:
        refs = {str(item or "").strip() for item in scope_refs if str(item or "").strip()}
        candidates: list[dict[str, Any]] = []
        for item in (registry.get("entitlements") or {}).values():
            scope = dict(item.get("scope") or {})
            scope_ref = f"{scope.get('kind')}:{scope.get('id')}"
            if (
                scope_ref in refs
                and item.get("environment") == environment
                and item.get("scenario_id") == scenario_id
                and item.get("skill_id") == skill_id
                and (
                    item.get("application_id") is None
                    or item.get("application_id") == application_id
                )
            ):
                candidates.append(dict(item))
        candidates.sort(
            key=lambda item: (
                item.get("application_id") == application_id,
                str(item.get("updated_at") or ""),
            ),
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _status_projection(
        self, registry: Mapping[str, Any], entitlement: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        now = _now()
        base_reason = "available"
        base_status = "available"
        administrative_action: str | None = None
        if entitlement is None:
            base_status = "unavailable"
            base_reason = "site_studio_entitlement_required"
            administrative_action = "request_entitlement"
        else:
            starts = _datetime(entitlement.get("effective_from"))
            ends = _datetime(entitlement.get("effective_until"))
            if entitlement.get("status") == "revoked":
                base_status, base_reason = "unavailable", "site_studio_entitlement_revoked"
                administrative_action = "contact_administrator"
            elif entitlement.get("status") == "suspended":
                base_status, base_reason = "suspended", "site_studio_entitlement_suspended"
                administrative_action = "contact_administrator"
            elif starts and starts > now:
                base_status, base_reason = "unavailable", "site_studio_entitlement_not_effective"
                administrative_action = "wait_for_effective_date"
            elif ends and ends <= now:
                base_status, base_reason = "unavailable", "site_studio_entitlement_expired"
                administrative_action = "renew_entitlement"
        features: dict[str, Any] = {}
        for feature in FEATURES:
            status, reason = base_status, base_reason
            action = administrative_action
            if entitlement is not None and status == "available":
                feature_state = dict(entitlement.get("features") or {}).get(feature, {})
                if feature_state.get("status") != "active":
                    status, reason = "unavailable", "site_studio_feature_not_entitled"
                    action = "request_feature"
            switch = dict((registry.get("switches") or {}).get(feature) or {})
            switch_status = str(switch.get("status") or "available")
            if switch_status != "available" and status == "available":
                status = switch_status
                reason = str(switch.get("reason_code") or "site_studio_feature_unavailable")
                action = "retry_later" if status in {"limited", "unavailable"} else "contact_administrator"
            features[feature] = {
                "status": status,
                "reason_code": reason,
                "message": self._message(reason),
                "administrative_action": action,
            }
        overall = "available"
        values = {item["status"] for item in features.values()}
        if values == {"unavailable"}:
            overall = "unavailable"
        elif "suspended" in values:
            overall = "suspended"
        elif values != {"available"}:
            overall = "limited"
        usage = (
            self._usage(
                registry,
                entitlement_id=str(entitlement["entitlement_id"]),
                limits=dict(entitlement["limits"]),
            )
            if entitlement is not None
            else {key: {"used": 0, "limit": value, "remaining": 0} for key, value in DEFAULT_LIMITS.items()}
        )
        return {
            "schema": STATUS_SCHEMA,
            "status": overall,
            "policy_revision": str(registry.get("policy_revision") or POLICY_REVISION),
            "entitlement_id": entitlement.get("entitlement_id") if entitlement else None,
            "entitlement_revision": entitlement.get("revision") if entitlement else None,
            "features": features,
            "usage": usage,
            "effective_from": entitlement.get("effective_from") if entitlement else None,
            "effective_until": entitlement.get("effective_until") if entitlement else None,
            "observed_at": _iso(),
        }

    def _usage(
        self,
        registry: Mapping[str, Any],
        *,
        entitlement_id: str,
        limits: Mapping[str, int],
    ) -> dict[str, Any]:
        now = _now()
        out: dict[str, Any] = {}
        events = [
            item
            for item in registry.get("usage_events", [])
            if item.get("entitlement_id") == entitlement_id
        ]
        for limit_key, limit in limits.items():
            windows = [window for key, window in _OPERATION_LIMITS.values() if key == limit_key]
            window = windows[0] if windows else None
            relevant = [item for item in events if item.get("quota") == limit_key]
            if window is not None:
                relevant = [
                    item
                    for item in relevant
                    if (_datetime(item.get("created_at")) or now) > now - window
                ]
            if limit_key in {"projects", "custom_domains"}:
                used = len(
                    {
                        str(item.get("resource_id") or item.get("event_id"))
                        for item in relevant
                    }
                )
            else:
                used = sum(int(item.get("amount") or 0) for item in relevant)
            row: dict[str, Any] = {
                "used": used,
                "limit": int(limit),
                "remaining": max(0, int(limit) - used),
            }
            if window is not None and relevant:
                oldest = min(_datetime(item.get("created_at")) or now for item in relevant)
                row["resets_at"] = _iso(oldest + window)
            out[limit_key] = row
        return out

    def _receipt(
        self,
        registry: Mapping[str, Any],
        *,
        action: str,
        entitlement_id: str | None,
        actor: str,
        reason: str,
        request_id: str | None,
        change: Mapping[str, Any],
    ) -> dict[str, Any]:
        previous = list(registry.get("receipts") or [])
        payload = {
            "schema": RECEIPT_SCHEMA,
            "receipt_id": "siteentrcpt_" + uuid4().hex,
            "action": action,
            "entitlement_id": entitlement_id,
            "actor": actor,
            "reason": reason,
            "request_id": str(request_id or "")[:240] or None,
            "change": deepcopy(dict(change)),
            "created_at": _iso(),
            "previous_receipt_digest": previous[-1].get("receipt_digest") if previous else None,
        }
        payload["receipt_digest"] = _canonical_digest(payload)
        return payload

    def _project(
        self,
        entitlement: Mapping[str, Any],
        registry: Mapping[str, Any],
        *,
        receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        projected = deepcopy(dict(entitlement))
        projected["availability"] = self._status_projection(registry, entitlement)
        if receipt is not None:
            projected["receipt"] = self._receipt_view(receipt)
        return projected

    @staticmethod
    def _receipt_view(receipt: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: deepcopy(receipt.get(key))
            for key in (
                "schema",
                "receipt_id",
                "receipt_digest",
                "action",
                "entitlement_id",
                "actor",
                "reason",
                "change",
                "created_at",
            )
            if receipt.get(key) is not None
        }

    @staticmethod
    def _message(reason: str) -> str:
        return {
            "available": "Site Studio feature is available.",
            "site_studio_entitlement_required": "Site Studio is not enabled for this account.",
            "site_studio_entitlement_revoked": "The Site Studio entitlement was revoked.",
            "site_studio_entitlement_suspended": "The Site Studio entitlement is suspended.",
            "site_studio_entitlement_not_effective": "The Site Studio entitlement is not active yet.",
            "site_studio_entitlement_expired": "The Site Studio entitlement expired.",
            "site_studio_feature_not_entitled": "This Site Studio feature is not included.",
        }.get(reason, "Site Studio feature is temporarily unavailable.")


def entitlement_scope_refs(context: Mapping[str, Any]) -> tuple[str, ...]:
    scope = context.get("scope") if isinstance(context.get("scope"), Mapping) else {}
    auth = (
        context.get("auth_context")
        if isinstance(context.get("auth_context"), Mapping)
        else {}
    )
    subnet_id = str(scope.get("subnet_id") or auth.get("subnet_id") or "").strip()
    organization_id = str(auth.get("organization_id") or "").strip()
    refs = []
    if subnet_id:
        refs.append("subject:subnet:" + subnet_id)
    if organization_id:
        refs.append("organization:" + organization_id)
    return tuple(refs)


def site_studio_entitlement_service() -> SiteStudioEntitlementService:
    return SiteStudioEntitlementService()


__all__ = [
    "DEFAULT_LIMITS",
    "ENTITLEMENT_SCHEMA",
    "FEATURES",
    "POLICY_REVISION",
    "SiteStudioEntitlementService",
    "entitlement_scope_refs",
    "site_studio_entitlement_service",
]
