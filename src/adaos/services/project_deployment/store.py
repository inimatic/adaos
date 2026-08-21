from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar

from adaos.domain.project_deployment import (
    ComponentActivation,
    DeploymentOperation,
    DeploymentPlan,
    DeploymentRevision,
    ProjectDeployment,
    utc_now,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.runtime_paths import current_state_dir


class ProjectDeploymentStoreError(RuntimeError):
    pass


class ProjectDeploymentConflictError(ProjectDeploymentStoreError):
    def __init__(self, *, expected: int, observed: int) -> None:
        super().__init__(
            f"deployment revision conflict: expected {expected}, observed {observed}"
        )
        self.expected = expected
        self.observed = observed


_T = TypeVar("_T")
_OPERATION_TRANSITIONS = {
    "accepted": {"running", "cancelled", "failed"},
    "running": {"running", "succeeded", "partial", "failed", "uncertain", "cancelled"},
    "partial": {"running", "partial", "failed", "uncertain", "rolled_back"},
    "failed": {"rolled_back"},
    "uncertain": set(),
    "succeeded": set(),
    "rolled_back": set(),
    "cancelled": set(),
}


def _token(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _read_mapping(path: Path) -> dict[str, Any]:
    payload: Any = None
    error: OSError | json.JSONDecodeError | None = None
    for attempt in range(8):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            error = None
            break
        except json.JSONDecodeError as exc:
            error = exc
            break
        except OSError as exc:
            error = exc
            retryable = isinstance(exc, PermissionError) or getattr(
                exc, "winerror", None
            ) in {5, 32, 33}
            if not retryable or attempt == 7:
                break
            time.sleep(min(0.01 * (2**attempt), 0.25))
    if error is not None:
        raise ProjectDeploymentStoreError(
            f"cannot read deployment record {path.name}"
        ) from error
    if not isinstance(payload, Mapping):
        raise ProjectDeploymentStoreError(
            f"deployment record {path.name} is not an object"
        )
    return dict(payload)


def _encode_cursor(after: str) -> str:
    payload = json.dumps({"after": after}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> str | None:
    token = str(cursor or "").strip()
    if not token:
        return None
    try:
        padding = "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(token + padding).decode("utf-8"))
    except Exception as exc:
        raise ProjectDeploymentStoreError("invalid inventory cursor") from exc
    if not isinstance(payload, Mapping) or set(payload) != {"after"}:
        raise ProjectDeploymentStoreError("invalid inventory cursor")
    after = str(payload.get("after") or "").strip()
    if not after:
        raise ProjectDeploymentStoreError("invalid inventory cursor")
    return after


def _page(
    values: Sequence[_T],
    *,
    key: Callable[[_T], str],
    cursor: str | None,
    limit: int,
) -> tuple[tuple[_T, ...], str | None]:
    size = max(1, min(int(limit), 200))
    after = _decode_cursor(cursor)
    ordered = sorted(values, key=key)
    if after is not None:
        ordered = [item for item in ordered if key(item) > after]
    selected = ordered[:size]
    next_cursor = None
    if len(ordered) > len(selected) and selected:
        next_cursor = _encode_cursor(key(selected[-1]))
    return tuple(selected), next_cursor


class ProjectDeploymentStore:
    """Durable desired/observed deployment journal with immutable revisions and plans."""

    def __init__(self, *, state_dir: Path | None = None) -> None:
        self.state_dir = Path(state_dir or current_state_dir()).expanduser().resolve()

    @property
    def root(self) -> Path:
        path = self.state_dir / "project_deployments"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def lock_path(self) -> Path:
        return self.root / ".mutation.lock"

    def _deployment_root(self, deployment_id: str) -> Path:
        return self.root / "deployments" / _token(deployment_id)

    def _operation_root(self, operation_id: str) -> Path:
        return self.root / "operations" / _token(operation_id)

    def _audit(self, event: str, **details: Any) -> None:
        payload = {
            "schema": "adaos.project.deployment_audit.v1",
            "event": str(event),
            "at": utc_now(),
            **details,
        }
        path = self.root / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())

    def append_audit(self, event: str, **details: Any) -> None:
        with mutation_lock(self.lock_path, timeout_s=30.0):
            self._audit(event, **details)

    def get_deployment(self, deployment_id: str) -> ProjectDeployment:
        path = self._deployment_root(deployment_id) / "current.json"
        if not path.is_file():
            raise FileNotFoundError(f"deployment not found: {deployment_id}")
        deployment = ProjectDeployment.from_mapping(_read_mapping(path))
        if deployment.deployment_id != deployment_id:
            raise ProjectDeploymentStoreError("deployment path identity mismatch")
        return deployment

    def save_deployment(
        self,
        desired: ProjectDeployment,
        *,
        expected_revision: int,
        actor_ref: str,
        reason: str,
    ) -> DeploymentRevision:
        with mutation_lock(self.lock_path, timeout_s=30.0):
            try:
                previous = self.get_deployment(desired.deployment_id)
            except FileNotFoundError:
                previous = None
            observed = previous.revision if previous is not None else 0
            if int(expected_revision) != observed:
                raise ProjectDeploymentConflictError(
                    expected=int(expected_revision), observed=observed
                )
            if desired.revision != observed + 1:
                raise ProjectDeploymentStoreError(
                    "desired revision must advance the current deployment by exactly one"
                )
            previous_digest = None
            if previous is not None:
                previous_digest = DeploymentRevision(
                    deployment_id=previous.deployment_id,
                    revision=previous.revision,
                    desired=previous,
                    actor_ref="system:recovered",
                    reason="derive previous digest",
                ).desired_digest
            revision = DeploymentRevision(
                deployment_id=desired.deployment_id,
                revision=desired.revision,
                desired=desired,
                previous_desired_digest=previous_digest,
                actor_ref=actor_ref,
                reason=reason,
            )
            root = self._deployment_root(desired.deployment_id)
            revision_path = root / "revisions" / f"{desired.revision:020d}.json"
            if revision_path.exists():
                existing = _read_mapping(revision_path)
                if existing != revision.to_dict():
                    raise ProjectDeploymentStoreError(
                        "immutable deployment revision already exists with different content"
                    )
            else:
                atomic_write_json(revision_path, revision.to_dict())
            atomic_write_json(root / "current.json", desired.to_dict())
            self._audit(
                "deployment.revision.saved",
                deployment_id=desired.deployment_id,
                revision=desired.revision,
                desired_digest=revision.desired_digest,
                actor_ref=actor_ref,
            )
            return revision

    def list_deployments(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> tuple[tuple[ProjectDeployment, ...], str | None]:
        records: list[ProjectDeployment] = []
        root = self.root / "deployments"
        if root.is_dir():
            for path in root.glob("*/current.json"):
                records.append(ProjectDeployment.from_mapping(_read_mapping(path)))
        return _page(
            records,
            key=lambda item: item.deployment_id,
            cursor=cursor,
            limit=limit,
        )

    def put_plan(self, plan: DeploymentPlan) -> DeploymentPlan:
        path = self.root / "plans" / f"{str(plan.plan_digest).split(':', 1)[1]}.json"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if path.exists():
                existing = DeploymentPlan.from_mapping(_read_mapping(path))
                if existing != plan:
                    raise ProjectDeploymentStoreError(
                        "immutable deployment plan already exists with different content"
                    )
                return existing
            atomic_write_json(path, plan.to_dict())
            self._audit(
                "deployment.plan.saved",
                deployment_id=plan.deployment_id,
                plan_digest=plan.plan_digest,
                expected_revision=plan.expected_revision,
                status=plan.status,
            )
        return plan

    def get_plan(self, plan_digest: str) -> DeploymentPlan:
        token = str(plan_digest or "")
        if not token.startswith("sha256:"):
            raise ProjectDeploymentStoreError("plan digest is invalid")
        path = self.root / "plans" / f"{token.split(':', 1)[1]}.json"
        if not path.is_file():
            raise FileNotFoundError(f"deployment plan not found: {plan_digest}")
        plan = DeploymentPlan.from_mapping(_read_mapping(path))
        if plan.plan_digest != plan_digest:
            raise ProjectDeploymentStoreError("deployment plan path identity mismatch")
        return plan

    def put_activation(self, activation: ComponentActivation) -> ComponentActivation:
        path = self.root / "activations" / f"{_token(activation.activation_id)}.json"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if path.is_file():
                previous = ComponentActivation.from_mapping(_read_mapping(path))
                immutable_identity = (
                    "activation_id",
                    "deployment_id",
                    "component_ref",
                    "node_id",
                    "release_digest",
                    "package_digest",
                    "generation",
                    "created_at",
                )
                if any(
                    getattr(previous, field) != getattr(activation, field)
                    for field in immutable_identity
                ):
                    raise ProjectDeploymentStoreError(
                        "component activation immutable identity changed"
                    )
            atomic_write_json(path, activation.to_dict())
            self._audit(
                "deployment.activation.observed",
                deployment_id=activation.deployment_id,
                activation_id=activation.activation_id,
                component_ref=activation.component_ref,
                node_id=activation.node_id,
                generation=activation.generation,
                status=activation.status,
            )
        return activation

    def get_activation(self, activation_id: str) -> ComponentActivation:
        path = self.root / "activations" / f"{_token(activation_id)}.json"
        if not path.is_file():
            raise FileNotFoundError(f"component activation not found: {activation_id}")
        activation = ComponentActivation.from_mapping(_read_mapping(path))
        if activation.activation_id != activation_id:
            raise ProjectDeploymentStoreError(
                "component activation path identity mismatch"
            )
        return activation

    def list_activations(
        self,
        *,
        deployment_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ComponentActivation, ...], str | None]:
        records: list[ComponentActivation] = []
        root = self.root / "activations"
        if root.is_dir():
            for path in root.glob("*.json"):
                record = ComponentActivation.from_mapping(_read_mapping(path))
                if deployment_id is None or record.deployment_id == deployment_id:
                    records.append(record)
        return _page(
            records,
            key=lambda item: item.activation_id,
            cursor=cursor,
            limit=limit,
        )

    def find_operation_by_idempotency(
        self, idempotency_key: str
    ) -> DeploymentOperation | None:
        path = self.root / "operation_idempotency" / f"{_token(idempotency_key)}.json"
        if not path.is_file():
            return None
        pointer = _read_mapping(path)
        if pointer.get("idempotency_key") != idempotency_key:
            raise ProjectDeploymentStoreError("operation idempotency pointer mismatch")
        operation_id = str(pointer.get("operation_id") or "")
        return self.get_operation(operation_id)

    def create_operation(self, operation: DeploymentOperation) -> DeploymentOperation:
        with mutation_lock(self.lock_path, timeout_s=30.0):
            existing = self.find_operation_by_idempotency(operation.idempotency_key)
            if existing is not None:
                if (
                    existing.deployment_id != operation.deployment_id
                    or existing.plan_digest != operation.plan_digest
                    or existing.kind != operation.kind
                ):
                    raise ProjectDeploymentStoreError(
                        "idempotency key is already bound to another deployment operation"
                    )
                return existing
            root = self._operation_root(operation.operation_id)
            if (root / "current.json").exists():
                raise ProjectDeploymentStoreError(
                    "deployment operation id already exists"
                )
            atomic_write_json(root / "history" / "000001.json", operation.to_dict())
            atomic_write_json(root / "current.json", operation.to_dict())
            atomic_write_json(
                self.root
                / "operation_idempotency"
                / f"{_token(operation.idempotency_key)}.json",
                {
                    "schema": "adaos.project.deployment_operation_pointer.v1",
                    "idempotency_key": operation.idempotency_key,
                    "operation_id": operation.operation_id,
                },
            )
            self._audit(
                "deployment.operation.created",
                operation_id=operation.operation_id,
                deployment_id=operation.deployment_id,
                plan_digest=operation.plan_digest,
                kind=operation.kind,
            )
            return operation

    def get_operation(self, operation_id: str) -> DeploymentOperation:
        path = self._operation_root(operation_id) / "current.json"
        if not path.is_file():
            raise FileNotFoundError(f"deployment operation not found: {operation_id}")
        operation = DeploymentOperation.from_mapping(_read_mapping(path))
        if operation.operation_id != operation_id:
            raise ProjectDeploymentStoreError(
                "deployment operation path identity mismatch"
            )
        return operation

    def put_operation_authorization(
        self,
        operation_id: str,
        authorization: Mapping[str, Any],
    ) -> dict[str, Any]:
        root = self._operation_root(operation_id)
        payload = dict(authorization)
        if payload.get("operation_id") != operation_id:
            raise ProjectDeploymentStoreError(
                "deployment operation authorization identity mismatch"
            )
        path = root / "authorization.json"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if path.is_file():
                existing = _read_mapping(path)
                if existing != payload:
                    raise ProjectDeploymentStoreError(
                        "deployment operation authorization is immutable"
                    )
                return existing
            atomic_write_json(path, payload)
        return payload

    def get_operation_authorization(self, operation_id: str) -> dict[str, Any]:
        path = self._operation_root(operation_id) / "authorization.json"
        if not path.is_file():
            raise FileNotFoundError(
                f"deployment operation authorization not found: {operation_id}"
            )
        payload = _read_mapping(path)
        if payload.get("operation_id") != operation_id:
            raise ProjectDeploymentStoreError(
                "deployment operation authorization identity mismatch"
            )
        return payload

    def list_incomplete_operations(
        self, *, limit: int = 100
    ) -> tuple[DeploymentOperation, ...]:
        records: list[DeploymentOperation] = []
        root = self.root / "operations"
        if root.is_dir():
            for path in root.glob("*/current.json"):
                record = DeploymentOperation.from_mapping(_read_mapping(path))
                if record.state in {"accepted", "running"}:
                    records.append(record)
        records.sort(key=lambda item: (item.updated_at, item.operation_id))
        return tuple(records[: max(1, min(int(limit), 1000))])

    def update_operation(
        self,
        operation: DeploymentOperation,
        *,
        expected_state: str,
    ) -> DeploymentOperation:
        with mutation_lock(self.lock_path, timeout_s=30.0):
            current = self.get_operation(operation.operation_id)
            if current.state != expected_state:
                raise ProjectDeploymentStoreError(
                    f"operation state conflict: expected {expected_state}, observed {current.state}"
                )
            immutable_identity = (
                "operation_id",
                "deployment_id",
                "plan_digest",
                "kind",
                "expected_revision",
                "idempotency_key",
                "created_at",
            )
            if any(
                getattr(current, field) != getattr(operation, field)
                for field in immutable_identity
            ):
                raise ProjectDeploymentStoreError(
                    "deployment operation immutable identity changed"
                )
            allowed = _OPERATION_TRANSITIONS.get(current.state, set())
            if operation.state != current.state and operation.state not in allowed:
                raise ProjectDeploymentStoreError(
                    f"invalid deployment operation transition {current.state}->{operation.state}"
                )
            history_root = self._operation_root(operation.operation_id) / "history"
            sequence = len(list(history_root.glob("*.json"))) + 1
            atomic_write_json(
                history_root / f"{sequence:06d}.json", operation.to_dict()
            )
            atomic_write_json(
                self._operation_root(operation.operation_id) / "current.json",
                operation.to_dict(),
            )
            self._audit(
                "deployment.operation.updated",
                operation_id=operation.operation_id,
                deployment_id=operation.deployment_id,
                state=operation.state,
                uncertain=operation.uncertain,
            )
            return operation

    def list_operations(
        self,
        *,
        deployment_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[DeploymentOperation, ...], str | None]:
        records: list[DeploymentOperation] = []
        root = self.root / "operations"
        if root.is_dir():
            for path in root.glob("*/current.json"):
                record = DeploymentOperation.from_mapping(_read_mapping(path))
                if deployment_id is None or record.deployment_id == deployment_id:
                    records.append(record)
        return _page(
            records,
            key=lambda item: item.operation_id,
            cursor=cursor,
            limit=limit,
        )


__all__ = [
    "ProjectDeploymentConflictError",
    "ProjectDeploymentStore",
    "ProjectDeploymentStoreError",
]
