from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from adaos.domain.application import utc_now
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from .publication_attempts import PublicationReviewRequired, reviewed_publication, observed_recovered_adoption


# These bounded adapters reconcile exact remote receipts before resuming their
# retained intent. Other authoring actions keep their own recovery boundaries.
RECOVERABLE_PUBLICATION_ACTIONS = frozenset({
    "promote_stable", "publish_trial", "publish_prerelease", "publish_stable_source", "publish_to_registry",
})


class ApplicationDevelopmentError(RuntimeError):
    pass


class ApplicationDevelopmentOutcomeUnknown(ApplicationDevelopmentError):
    pass


def _publication_preflight_rejection(error):
    from adaos.services.capability_binding_state.default_distribution import DefaultDistributionPreflightError
    cause = error
    for _ in range(8):
        if isinstance(cause, DefaultDistributionPreflightError):
            return {'ok': False, 'status': 'qualification_required', 'message': str(cause),
                    'error': {'code': cause.code, 'message': str(cause),
                    'requirements': list(cause.requirements)[:16]},
                    'recovery': {'action': 'prepare_verified_candidate', 'requires_user_review': True}}
        cause = cause.__cause__
        if cause is None:
            break
    return None


class ApplicationDevelopmentCoordinator:
    """Core idempotency boundary around bounded authoring/lifecycle adapters."""

    _CAPABILITIES = {
        "create": "applications.develop",
        "update_metadata": "applications.develop",
        "delete": "applications.develop",
        "materialize": "applications.develop",
        "preview": "applications.develop",
        "create_trial": "applications.develop",
        "decide_trial": "applications.develop",
        "publish_trial": "applications.publish",
        "publish_prerelease": "applications.publish",
        "promote_stable": "applications.publish",
        "publish_to_registry": "applications.publish",
        "publish_stable_source": "applications.publish",
    }

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()

    @property
    def root_path(self) -> Path:
        return self.state_dir / "applications" / "development_operations"

    @property
    def root(self) -> Path:
        path = self.root_path
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
        return self.root_path / "operations" / f"{digest}.json"

    def _idempotency_path(self, idempotency_key: str) -> Path:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return self.root_path / "idempotency" / f"{digest}.json"

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
        parent = self.root_path / "operations"
        values = [self._read(path) for path in parent.glob("*.json")] if parent.is_dir() else []
        if application_id is not None:
            values = [item for item in values if item.get("application_id") == application_id]
        return sorted(values, key=lambda item: (item["created_at"], item["operation_id"]), reverse=True)

    def summaries(self) -> list[dict[str, Any]]:
        """Read catalog facts without deserializing retained build/Trial results.

        These disposable projections are bound to the authority file's stat.
        Old installations populate each entry once; any operation write makes
        it stale immediately. Recovery always reads the original journal.
        """
        values = []
        parent = self.root_path / "operations"
        from .read_cache import stat_bound_projection
        for path in parent.glob("*.json"):
            cached_path = self.root_path / "summary-cache" / path.name
            def project():
                operation = self._read(path)
                summary = {key: operation.get(key) for key in (
                    "operation_id", "application_id", "action", "status", "subnet_ref", "created_at", "updated_at")}
                summary["intent"] = {"source_webspace_id": (operation.get("intent") or {}).get("source_webspace_id")}
                return summary
            values.append(stat_bound_projection(path, cached_path, project))
        return values

    def publication_summaries(self, application_id: str | None = None) -> list[dict[str, Any]]:
        """Exact release notes without loading unrelated retained build results."""
        from .read_cache import stat_bound_projection
        values = []
        for summary in self.summaries():
            if (summary.get('status') != 'succeeded'
                    or summary.get('action') not in {'publish_to_registry', 'publish_stable_source'}
                    or application_id is not None and summary.get('application_id') != application_id):
                continue
            path = self._path(summary['operation_id'])
            def project():
                operation = self._read(path)
                result = {key: operation.get(key) for key in (
                    'operation_id', 'application_id', 'action', 'status', 'subnet_ref', 'updated_at')}
                intent = operation.get('intent') or {}
                result['intent'] = {key: intent[key][:20_000] for key in ('release_digest', 'release_notes')
                                    if isinstance(intent.get(key), str)}
                return result
            values.append(stat_bound_projection(path, self.root_path / 'publication-summary-cache' / path.name, project))
        return values

    def find_idempotency(self, key: str) -> dict[str, Any] | None:
        path = self._idempotency_path(self._identity(key, "idempotency_key"))
        if not path.is_file():
            return None
        return self.get(str(json.loads(path.read_text(encoding="utf-8"))["operation_id"]))

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
            if action_id == "promote_stable" and bounded_intent.get("candidate_id"):
                operation["recovered_adoption_digest"] = observed_recovered_adoption(
                    self.state_dir, application, str(bounded_intent["candidate_id"]))
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
            with reviewed_publication(operation):
                result = dict(callback())
        except PublicationReviewRequired as exc:
            result = exc.result()
        except Exception as exc:
            rejection = _publication_preflight_rejection(exc)
            with mutation_lock(self.lock_path, timeout_s=30.0):
                operation.update(
                    status="failed" if rejection else "unknown",
                    recovery_reason=rejection['error']['code'] if rejection else f"outcome_unknown:{type(exc).__name__}:{exc}"[:1000],
                    revision=3,
                    updated_at=utc_now(),
                )
                if rejection:
                    operation['result'] = rejection
                atomic_write_json(path, operation)
            if rejection:
                return operation
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
            with reviewed_publication(operation):
                result = dict(callback(operation))
        except PublicationReviewRequired as exc:
            result = exc.result()
        except Exception as exc:
            rejection = _publication_preflight_rejection(exc)
            with mutation_lock(self.lock_path, timeout_s=30.0):
                current = self.get(operation_id)
                current.update(
                    status="failed" if rejection else "unknown",
                    recovery_reason=rejection['error']['code'] if rejection else f"recovery_outcome_unknown:{type(exc).__name__}:{exc}"[:1000],
                    revision=int(current["revision"]) + 1,
                    updated_at=utc_now(),
                )
                if rejection:
                    current['result'] = rejection
                atomic_write_json(self._path(operation_id), current)
            if rejection:
                return current
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
