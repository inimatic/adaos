"""Fail-closed automatic Application updates.

The public registry may advance independently from a node.  This service uses
the ordinary reviewed Application plan/apply protocol and only applies plans
that do not expand authority or require a data migration.  Every decision is
persisted so an update notification is observable even when nothing was
applied.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adaos.domain.application import utc_now
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.applications.service import ApplicationService
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


AUTO_UPDATE_RUN_SCHEMA = "adaos.application.auto_update_run.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def automatic_update_blockers(plan: Mapping[str, Any]) -> list[str]:
    """Return reasons that require a human-reviewed update instead."""

    blockers: list[str] = []
    conflicts = plan.get("conflicts")
    if isinstance(conflicts, list) and conflicts:
        blockers.append("component_conflicts")

    permission_review = plan.get("permission_review")
    if not isinstance(permission_review, Mapping):
        blockers.append("permission_review_unavailable")
    elif bool(permission_review.get("approval_required")):
        blockers.append("permission_approval_required")

    compatibility = plan.get("compatibility")
    if not isinstance(compatibility, Mapping):
        blockers.append("compatibility_unavailable")
    else:
        migration = compatibility.get("migration")
        if not isinstance(migration, Mapping):
            blockers.append("migration_assessment_unavailable")
        elif bool(migration.get("required")):
            blockers.append("migration_required")

    return list(dict.fromkeys(blockers))


def _retryable_terminal_operation(operation: Any) -> bool:
    """Return whether a prior exact update can safely converge in a new operation."""

    status = _text(getattr(operation, "status", "planned"))
    if status == "failed":
        return True
    if status != "unknown":
        return False
    result = getattr(operation, "result", None)
    if not isinstance(result, Mapping):
        return False
    deployment = result.get("deployment_operation")
    if not isinstance(deployment, Mapping):
        return False
    error = deployment.get("error")
    return (
        _text(deployment.get("state")) == "partial"
        and not bool(deployment.get("uncertain"))
        and isinstance(error, Mapping)
        and error.get("manual_reconciliation") is False
    )


class ApplicationAutoUpdateService:
    """Apply exact safe updates for ``auto_compatible`` subscriptions."""

    def __init__(self, state_dir: Path, application_service: ApplicationService) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.application_service = application_service

    @property
    def root(self) -> Path:
        path = self.state_dir / "applications" / "auto_update_runs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def lock_path(self) -> Path:
        return self.root / ".mutation.lock"

    def _persist(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(payload)
        run_id = _text(value.get("run_id"))
        with mutation_lock(self.lock_path, timeout_s=30.0):
            atomic_write_json(self.root / run_id / "run.json", value)
            atomic_write_json(self.root / "current.json", value)
        return value

    def run(
        self,
        *,
        subnet_ref: str,
        trigger: str,
        registry_index_digest: str | None = None,
        application_ids: list[str] | tuple[str, ...] | None = None,
        webspace_id: str = "desktop",
    ) -> dict[str, Any]:
        subnet = _text(subnet_ref)
        if not subnet.startswith("subnet:"):
            raise ValueError("subnet_ref must be a canonical subnet reference")
        trigger_token = _text(trigger) or "registry_sync"
        requested = {
            _text(item) for item in (application_ids or ()) if _text(item)
        }
        models = self.application_service.list_models(
            installed_only=True,
            subscriber_subnet_ref=subnet,
        )
        candidates: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for model in models:
            application = model.get("application")
            installation = model.get("installation")
            effective = model.get("effective_release")
            app_id = _text(
                application.get("application_id")
                if isinstance(application, Mapping)
                else ""
            )
            if requested and app_id not in requested:
                continue
            if not bool(model.get("auto_update_enabled")):
                skipped.append({"application_id": app_id, "reason": "auto_update_disabled"})
                continue
            if not bool(model.get("update_available")):
                skipped.append({"application_id": app_id, "reason": "already_current"})
                continue
            if not isinstance(installation, Mapping):
                skipped.append({"application_id": app_id, "reason": "stable_installation_required"})
                continue
            if list(installation.get("uncertain_operation_refs") or ()):
                skipped.append({"application_id": app_id, "reason": "uncertain_operation_present"})
                continue
            target_digest = _text(
                effective.get("release_digest")
                if isinstance(effective, Mapping)
                else ""
            )
            if not target_digest:
                skipped.append({"application_id": app_id, "reason": "target_release_unavailable"})
                continue
            candidates.append(
                {
                    "application_id": app_id,
                    "target_release_digest": target_digest,
                    "installation_revision": int(installation.get("revision") or 0),
                }
            )

        identity = canonical_payload_digest(
            {
                "subnet_ref": subnet,
                "trigger": trigger_token,
                "registry_index_digest": _text(registry_index_digest) or None,
                "candidates": candidates,
            }
        )
        run_id = f"appautorun.{identity.split(':', 1)[1][:32]}"
        actor_ref = "service:application-auto-update"
        outcomes: list[dict[str, Any]] = []
        for candidate in candidates:
            app_id = candidate["application_id"]
            target_digest = candidate["target_release_digest"]
            child_identity = hashlib.sha256(
                f"{subnet}\0{app_id}\0{target_digest}\0{candidate['installation_revision']}".encode(
                    "utf-8"
                )
            ).hexdigest()
            idempotency_key = f"application-auto-update:{child_identity}"
            item: dict[str, Any] = {
                **candidate,
                "status": "planning",
                "idempotency_key": idempotency_key,
            }
            try:
                retry_chain: list[str] = []
                failed_retry_chain: list[str] = []
                for _attempt in range(16):
                    operation = self.application_service.plan_operation(
                        app_id,
                        "update",
                        release_digest=target_digest,
                        expected_revision=candidate["installation_revision"],
                        actor_ref=actor_ref,
                        subnet_ref=subnet,
                        capability="applications.plan",
                        idempotency_key=idempotency_key,
                    )
                    operation_status = _text(
                        getattr(operation, "status", "planned")
                    )
                    if operation_status == "planned":
                        break
                    if not _retryable_terminal_operation(operation):
                        raise RuntimeError(
                            "automatic update blocked by unresolved operation "
                            f"status: {operation_status or 'unknown'}"
                        )
                    retry_chain.append(_text(operation.operation_id))
                    if operation_status == "failed":
                        failed_retry_chain.append(_text(operation.operation_id))
                    retry_identity = hashlib.sha256(
                        (
                            f"{idempotency_key}\0{operation.operation_id}\0"
                            f"{getattr(operation, 'revision', 0)}"
                        ).encode("utf-8")
                    ).hexdigest()[:20]
                    idempotency_key = (
                        f"application-auto-update:{child_identity}:retry:{retry_identity}"
                    )
                else:
                    raise RuntimeError("automatic update retry chain exhausted")
                item["idempotency_key"] = idempotency_key
                if retry_chain:
                    item["retried_terminal_operation_ids"] = retry_chain
                if failed_retry_chain:
                    item["retried_failed_operation_ids"] = failed_retry_chain
                item["operation_id"] = operation.operation_id
                item["plan_digest"] = operation.plan_digest
                blockers = automatic_update_blockers(operation.plan)
                if blockers:
                    item["status"] = "review_required"
                    item["blockers"] = blockers
                else:
                    receipt = self.application_service.apply_operation(
                        operation.operation_id,
                        plan_digest=operation.plan_digest,
                        idempotency_key=idempotency_key,
                        actor_ref=actor_ref,
                        subnet_ref=subnet,
                        capability="applications.apply",
                    )
                    item["status"] = receipt.status
                    item["operation_revision"] = receipt.revision
                    item["result"] = dict(receipt.result)
                    if receipt.status == "succeeded":
                        try:
                            release = self.application_service.store.get_release(
                                app_id, target_digest
                            )
                            item["addresses_report_ids"] = list(
                                release.addresses_report_ids
                            )
                        except (AttributeError, FileNotFoundError, KeyError):
                            item["addresses_report_ids"] = []
            except Exception as exc:
                item["status"] = "failed"
                item["error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc)[:500],
                }
            outcomes.append(item)

        applied = sum(1 for item in outcomes if item.get("status") == "succeeded")
        review_required = sum(
            1 for item in outcomes if item.get("status") == "review_required"
        )
        failed = sum(1 for item in outcomes if item.get("status") == "failed")
        uncertain = sum(
            1
            for item in outcomes
            if item.get("status") in {"unknown", "applying", "reconciling"}
        )
        payload = {
            "schema": AUTO_UPDATE_RUN_SCHEMA,
            "run_id": run_id,
            "status": "failed" if failed else "uncertain" if uncertain else "completed",
            "trigger": trigger_token,
            "subnet_ref": subnet,
            "webspace_id": _text(webspace_id) or "desktop",
            "registry_index_digest": _text(registry_index_digest) or None,
            "candidate_count": len(candidates),
            "applied_count": applied,
            "review_required_count": review_required,
            "failed_count": failed,
            "uncertain_count": uncertain,
            "skipped_count": len(skipped),
            "outcomes": outcomes,
            "skipped": skipped,
            "updated_at": utc_now(),
        }
        return self._persist(payload)


__all__ = [
    "AUTO_UPDATE_RUN_SCHEMA",
    "ApplicationAutoUpdateService",
    "automatic_update_blockers",
]
