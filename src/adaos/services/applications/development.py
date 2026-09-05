from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from adaos.domain.application import utc_now
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


class ApplicationDevelopmentError(RuntimeError):
    pass


class ApplicationDevelopmentOutcomeUnknown(ApplicationDevelopmentError):
    pass


class ApplicationDevelopmentCoordinator:
    """Durable idempotency boundary around bounded Builder lifecycle adapters."""

    _CAPABILITIES = {
        "create": "applications.develop",
        "materialize": "applications.develop",
        "preview": "applications.develop",
        "create_trial": "applications.develop",
        "decide_trial": "applications.develop",
        "publish_trial": "applications.publish",
        "publish_prerelease": "applications.publish",
        "promote_stable": "applications.publish",
        "publish_stable_source": "applications.publish",
    }

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()

    @property
    def root(self) -> Path:
        path = self.state_dir / "applications" / "development_operations"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def lock_path(self) -> Path:
        return self.root / ".mutation.lock"

    @staticmethod
    def _identity(value: str, field: str) -> str:
        token = str(value or "").strip()
        if not token:
            raise ApplicationDevelopmentError(f"{field} is required")
        return token

    def _path(self, operation_id: str) -> Path:
        digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        return self.root / "operations" / f"{digest}.json"

    def _idempotency_path(self, idempotency_key: str) -> Path:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return self.root / "idempotency" / f"{digest}.json"

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApplicationDevelopmentError("Application development operation is unreadable") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "adaos.application.development_operation.v1":
            raise ApplicationDevelopmentError("Application development operation is invalid")
        return payload

    def get(self, operation_id: str) -> dict[str, Any]:
        path = self._path(self._identity(operation_id, "operation_id"))
        if not path.is_file():
            raise FileNotFoundError(f"Application development operation not found: {operation_id}")
        operation = self._read(path)
        if operation.get("operation_id") != operation_id:
            raise ApplicationDevelopmentError("Application development operation identity mismatch")
        return operation

    def list(self, application_id: str | None = None) -> list[dict[str, Any]]:
        parent = self.root / "operations"
        values = [self._read(path) for path in parent.glob("*.json")] if parent.is_dir() else []
        if application_id is not None:
            values = [item for item in values if item.get("application_id") == application_id]
        return sorted(values, key=lambda item: (item["created_at"], item["operation_id"]), reverse=True)

    def execute(
        self,
        action: str,
        application_id: str,
        *,
        actor_ref: str,
        subnet_ref: str,
        capability: str,
        expected_revision: int,
        idempotency_key: str,
        intent: Mapping[str, Any],
        callback: Callable[[], Mapping[str, Any]],
    ) -> dict[str, Any]:
        action_id = self._identity(action, "action").lower()
        required = self._CAPABILITIES.get(action_id)
        if required is None:
            raise ApplicationDevelopmentError("unsupported Application development action")
        actor = self._identity(actor_ref, "actor_ref")
        subnet = self._identity(subnet_ref, "subnet_ref").lower()
        application = self._identity(application_id, "application_id")
        if not subnet.startswith("subnet:"):
            raise ApplicationDevelopmentError("subnet_ref must use subnet:<id>")
        if capability != required:
            raise ApplicationDevelopmentError(f"{required} capability is required")
        if isinstance(expected_revision, bool) or int(expected_revision) < 0:
            raise ApplicationDevelopmentError("expected_revision must be a non-negative integer")
        key = self._identity(idempotency_key, "idempotency_key")
        bounded_intent = dict(intent)
        intent_digest = canonical_payload_digest(bounded_intent)
        identity = hashlib.sha256(
            f"{action_id}\x1f{application}\x1f{key}\x1f{intent_digest}".encode("utf-8")
        ).hexdigest()[:32]
        operation_id = f"appdevop.{identity}"
        path = self._path(operation_id)
        index_path = self._idempotency_path(key)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if index_path.is_file():
                index = json.loads(index_path.read_text(encoding="utf-8"))
                existing = self.get(str(index.get("operation_id") or ""))
                expected_identity = {
                    "application_id": application,
                    "action": action_id,
                    "actor_ref": actor,
                    "subnet_ref": subnet,
                    "capability": capability,
                    "expected_revision": int(expected_revision),
                    "idempotency_key": key,
                    "intent_digest": intent_digest,
                }
                if any(existing.get(field) != value for field, value in expected_identity.items()):
                    raise ApplicationDevelopmentError(
                        "idempotency key names another development authority or intent"
                    )
                if existing["status"] == "succeeded":
                    return existing
                raise ApplicationDevelopmentOutcomeUnknown(
                    "development operation requires reconciliation before retry"
                )
            now = utc_now()
            operation = {
                "schema": "adaos.application.development_operation.v1",
                "operation_id": operation_id,
                "application_id": application,
                "action": action_id,
                "status": "planned",
                "actor_ref": actor,
                "subnet_ref": subnet,
                "capability": capability,
                "expected_revision": int(expected_revision),
                "idempotency_key": key,
                "intent_digest": intent_digest,
                "intent": bounded_intent,
                "result": {},
                "recovery_reason": None,
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
            atomic_write_json(path, operation)
            atomic_write_json(
                index_path,
                {
                    "schema": "adaos.application.development_idempotency.v1",
                    "idempotency_key": key,
                    "operation_id": operation_id,
                    "intent_digest": intent_digest,
                },
            )
            operation.update(status="applying", revision=2, updated_at=utc_now())
            atomic_write_json(path, operation)
        try:
            result = dict(callback())
        except Exception as exc:
            with mutation_lock(self.lock_path, timeout_s=30.0):
                operation.update(
                    status="unknown",
                    recovery_reason=f"outcome_unknown:{type(exc).__name__}:{exc}"[:1000],
                    revision=3,
                    updated_at=utc_now(),
                )
                atomic_write_json(path, operation)
            raise
        terminal = "succeeded" if bool(result.get("ok", True)) and not result.get("error") else "failed"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            operation.update(
                status=terminal,
                result=result,
                recovery_reason=None if terminal == "succeeded" else str(result.get("error") or result.get("status") or "adapter_rejected"),
                revision=3,
                updated_at=utc_now(),
            )
            atomic_write_json(path, operation)
        return operation

    def reconcile(
        self,
        operation_id: str,
        *,
        observer: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> dict[str, Any]:
        operation = self.get(operation_id)
        if operation["status"] not in {"applying", "unknown"}:
            return operation
        observed = dict(observer(operation))
        status = str(observed.get("status") or "unknown")
        if status not in {"succeeded", "failed", "unknown"}:
            status = "unknown"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            current = self.get(operation_id)
            current.update(
                status=status,
                result=observed,
                recovery_reason=None if status != "unknown" else str(observed.get("reason") or "observer_inconclusive"),
                revision=int(current["revision"]) + 1,
                updated_at=utc_now(),
            )
            atomic_write_json(self._path(operation_id), current)
        return current

    def recover(
        self,
        operation_id: str,
        *,
        actor_ref: str,
        subnet_ref: str,
        capability: str,
        callback: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Retry one stored intent after its bounded adapter outcome became unknown."""

        actor = self._identity(actor_ref, "actor_ref")
        subnet = self._identity(subnet_ref, "subnet_ref").lower()
        if capability != "applications.recover":
            raise ApplicationDevelopmentError("applications.recover capability is required")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            operation = self.get(self._identity(operation_id, "operation_id"))
            if operation["actor_ref"] != actor or operation["subnet_ref"] != subnet:
                raise ApplicationDevelopmentError(
                    "only the original actor and publisher subnet may recover this operation"
                )
            if operation["status"] not in {"applying", "unknown"}:
                return operation
            if operation["status"] == "applying":
                try:
                    updated_at = datetime.fromisoformat(
                        str(operation["updated_at"]).replace("Z", "+00:00")
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ApplicationDevelopmentError(
                        "applying operation has an invalid recovery timestamp"
                    ) from exc
                age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
                if age_seconds < 300:
                    raise ApplicationDevelopmentOutcomeUnknown(
                        "development operation is still within its active recovery lease"
                    )
            operation.update(
                status="applying",
                recovery_reason="recovery_in_progress",
                recovery_attempt=int(operation.get("recovery_attempt") or 0) + 1,
                revision=int(operation["revision"]) + 1,
                updated_at=utc_now(),
            )
            atomic_write_json(self._path(operation_id), operation)
        try:
            result = dict(callback(operation))
        except Exception as exc:
            with mutation_lock(self.lock_path, timeout_s=30.0):
                current = self.get(operation_id)
                current.update(
                    status="unknown",
                    recovery_reason=f"recovery_outcome_unknown:{type(exc).__name__}:{exc}"[:1000],
                    revision=int(current["revision"]) + 1,
                    updated_at=utc_now(),
                )
                atomic_write_json(self._path(operation_id), current)
            raise
        terminal = "succeeded" if bool(result.get("ok", True)) and not result.get("error") else "failed"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            current = self.get(operation_id)
            current.update(
                status=terminal,
                result=result,
                recovery_reason=(
                    None
                    if terminal == "succeeded"
                    else str(result.get("error") or result.get("status") or "adapter_rejected")
                ),
                revision=int(current["revision"]) + 1,
                updated_at=utc_now(),
            )
            atomic_write_json(self._path(operation_id), current)
        return current


__all__ = [
    "ApplicationDevelopmentCoordinator",
    "ApplicationDevelopmentError",
    "ApplicationDevelopmentOutcomeUnknown",
]
