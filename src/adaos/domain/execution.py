"""Provider-neutral long-running execution contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Mapping, Sequence

from .ownership import validate_owner_ref
from .runtime_bindings import ContentRef


EXECUTION_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "lost"})
EXECUTION_STATUSES = frozenset(
    {"accepted", "submitting", "running", "cancelling", "unknown", *EXECUTION_TERMINAL_STATUSES}
)


class ExecutionContractError(ValueError):
    """Raised when an execution specification or attempt is invalid."""


@dataclass(frozen=True, slots=True)
class ExecutorProviderCapabilities:
    provider_id: str
    protocol_version: str = "1.0"
    features: tuple[str, ...] = ()
    hostile_isolation: bool = False

    def __post_init__(self) -> None:
        provider_id = _token(self.provider_id, "provider_id").lower()
        protocol = str(self.protocol_version or "").strip()
        if protocol != "1.0":
            raise ExecutionContractError("unsupported executor provider protocol version")
        features = tuple(
            dict.fromkeys(str(item).strip().lower() for item in self.features if str(item).strip())
        )
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "protocol_version", protocol)
        object.__setattr__(self, "features", features)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "protocol_version": self.protocol_version,
            "features": list(self.features),
            "hostile_isolation": self.hostile_isolation,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token(value: Any, field_name: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ExecutionContractError(f"{field_name} must be non-empty")
    return token


@dataclass(frozen=True, slots=True)
class ExecutionResourceRequest:
    cpu_cores: float | None = None
    memory_mb: int | None = None
    gpu_count: int = 0
    wall_time_s: float | None = None

    def __post_init__(self) -> None:
        if self.cpu_cores is not None and float(self.cpu_cores) <= 0:
            raise ExecutionContractError("cpu_cores must be > 0")
        if self.memory_mb is not None and int(self.memory_mb) < 1:
            raise ExecutionContractError("memory_mb must be >= 1")
        if int(self.gpu_count) < 0:
            raise ExecutionContractError("gpu_count must be >= 0")
        if self.wall_time_s is not None and float(self.wall_time_s) <= 0:
            raise ExecutionContractError("wall_time_s must be > 0")
        object.__setattr__(self, "cpu_cores", None if self.cpu_cores is None else float(self.cpu_cores))
        object.__setattr__(self, "memory_mb", None if self.memory_mb is None else int(self.memory_mb))
        object.__setattr__(self, "gpu_count", int(self.gpu_count))
        object.__setattr__(self, "wall_time_s", None if self.wall_time_s is None else float(self.wall_time_s))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExecutionSpec:
    """Immutable workload input. Scientific run identity remains domain-owned."""

    SCHEMA: ClassVar[str] = "adaos.execution.spec.v1"

    spec_id: str
    owner_ref: str
    command: tuple[str, ...]
    working_directory: str
    environment: Mapping[str, str] = field(default_factory=dict)
    secret_refs: tuple[str, ...] = ()
    resources: ExecutionResourceRequest = field(default_factory=ExecutionResourceRequest)
    inputs: tuple[ContentRef, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    checkpoint: ContentRef | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        spec_id = _token(self.spec_id, "spec_id")
        owner_ref = validate_owner_ref(self.owner_ref)
        command = tuple(_token(item, "command item") for item in self.command)
        if not command:
            raise ExecutionContractError("command must contain at least one item")
        working_directory = _token(self.working_directory, "working_directory")
        environment = {
            _token(key, "environment key"): str(value)
            for key, value in dict(self.environment).items()
        }
        secret_refs = tuple(dict.fromkeys(_token(item, "secret_refs item") for item in self.secret_refs))
        expected_outputs = tuple(
            dict.fromkeys(_token(item, "expected_outputs item") for item in self.expected_outputs)
        )
        object.__setattr__(self, "spec_id", spec_id)
        object.__setattr__(self, "owner_ref", owner_ref)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "working_directory", working_directory)
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "secret_refs", secret_refs)
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "expected_outputs", expected_outputs)
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "spec_id": self.spec_id,
            "owner_ref": self.owner_ref,
            "command": list(self.command),
            "working_directory": self.working_directory,
            "environment": dict(self.environment),
            "secret_refs": list(self.secret_refs),
            "resources": self.resources.to_dict(),
            "inputs": [item.to_dict() for item in self.inputs],
            "expected_outputs": list(self.expected_outputs),
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExecutionSpec":
        def _content(raw: Any) -> ContentRef | None:
            if not isinstance(raw, Mapping):
                return None
            return ContentRef(
                uri=str(raw.get("uri") or ""),
                digest=str(raw.get("digest") or ""),
                size_bytes=int(raw.get("size_bytes") or 0),
                media_type=str(raw.get("media_type") or "application/octet-stream"),
                owner_ref=str(raw.get("owner_ref") or ""),
                kind=str(raw.get("kind") or "artifact"),
                metadata=dict(raw.get("metadata") or {}),
            )

        resources = dict(value.get("resources") or {})
        inputs = tuple(
            item for item in (_content(raw) for raw in value.get("inputs") or []) if item is not None
        )
        return cls(
            spec_id=str(value.get("spec_id") or ""),
            owner_ref=str(value.get("owner_ref") or ""),
            command=tuple(str(item) for item in value.get("command") or ()),
            working_directory=str(value.get("working_directory") or ""),
            environment=dict(value.get("environment") or {}),
            secret_refs=tuple(str(item) for item in value.get("secret_refs") or ()),
            resources=ExecutionResourceRequest(**resources),
            inputs=inputs,
            expected_outputs=tuple(str(item) for item in value.get("expected_outputs") or ()),
            checkpoint=_content(value.get("checkpoint")),
            metadata=dict(value.get("metadata") or {}),
        )

    @property
    def digest(self) -> str:
        raw = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(raw).hexdigest()}"


@dataclass(frozen=True, slots=True)
class ExecutionAttempt:
    """One physical provider submission for an immutable execution spec."""

    SCHEMA: ClassVar[str] = "adaos.execution.attempt.v1"

    attempt_id: str
    owner_ref: str
    spec_id: str
    spec_digest: str
    provider_id: str
    provider_attempt_id: str
    idempotency_key: str
    status: str
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    pid: int | None = None
    process_create_time: float | None = None
    exit_code: int | None = None
    failure: Mapping[str, Any] | None = None
    stdout: ContentRef | None = None
    stderr: ContentRef | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        status = str(self.status or "").strip().lower()
        if status not in EXECUTION_STATUSES:
            raise ExecutionContractError(f"unsupported execution status: {status}")
        spec_digest = str(self.spec_digest or "").strip().lower()
        if not spec_digest.startswith("sha256:") or len(spec_digest) != 71:
            raise ExecutionContractError("spec_digest must be a sha256 digest")
        object.__setattr__(self, "attempt_id", _token(self.attempt_id, "attempt_id"))
        object.__setattr__(self, "owner_ref", validate_owner_ref(self.owner_ref))
        object.__setattr__(self, "spec_id", _token(self.spec_id, "spec_id"))
        object.__setattr__(self, "spec_digest", spec_digest)
        object.__setattr__(self, "provider_id", _token(self.provider_id, "provider_id").lower())
        object.__setattr__(self, "provider_attempt_id", _token(self.provider_attempt_id, "provider_attempt_id"))
        object.__setattr__(self, "idempotency_key", _token(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "pid", None if self.pid is None else int(self.pid))
        object.__setattr__(
            self,
            "process_create_time",
            None if self.process_create_time is None else float(self.process_create_time),
        )
        object.__setattr__(self, "exit_code", None if self.exit_code is None else int(self.exit_code))
        object.__setattr__(self, "failure", None if self.failure is None else dict(self.failure))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def terminal(self) -> bool:
        return self.status in EXECUTION_TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "attempt_id": self.attempt_id,
            "owner_ref": self.owner_ref,
            "spec_id": self.spec_id,
            "spec_digest": self.spec_digest,
            "provider_id": self.provider_id,
            "provider_attempt_id": self.provider_attempt_id,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pid": self.pid,
            "process_create_time": self.process_create_time,
            "exit_code": self.exit_code,
            "failure": dict(self.failure) if self.failure is not None else None,
            "stdout": self.stdout.to_dict() if self.stdout else None,
            "stderr": self.stderr.to_dict() if self.stderr else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExecutionAttempt":
        def _content(raw: Any) -> ContentRef | None:
            if not isinstance(raw, Mapping):
                return None
            return ContentRef(
                uri=str(raw.get("uri") or ""),
                digest=str(raw.get("digest") or ""),
                size_bytes=int(raw.get("size_bytes") or 0),
                media_type=str(raw.get("media_type") or "application/octet-stream"),
                owner_ref=str(raw.get("owner_ref") or ""),
                kind=str(raw.get("kind") or "artifact"),
                metadata=dict(raw.get("metadata") or {}),
            )

        return cls(
            attempt_id=str(value.get("attempt_id") or ""),
            owner_ref=str(value.get("owner_ref") or ""),
            spec_id=str(value.get("spec_id") or ""),
            spec_digest=str(value.get("spec_digest") or ""),
            provider_id=str(value.get("provider_id") or ""),
            provider_attempt_id=str(value.get("provider_attempt_id") or ""),
            idempotency_key=str(value.get("idempotency_key") or ""),
            status=str(value.get("status") or ""),
            created_at=str(value.get("created_at") or _now()),
            updated_at=str(value.get("updated_at") or _now()),
            started_at=str(value.get("started_at")) if value.get("started_at") else None,
            finished_at=str(value.get("finished_at")) if value.get("finished_at") else None,
            pid=value.get("pid"),
            process_create_time=value.get("process_create_time"),
            exit_code=value.get("exit_code"),
            failure=dict(value.get("failure") or {}) if value.get("failure") is not None else None,
            stdout=_content(value.get("stdout")),
            stderr=_content(value.get("stderr")),
            metadata=dict(value.get("metadata") or {}),
        )


__all__ = [
    "EXECUTION_STATUSES",
    "EXECUTION_TERMINAL_STATUSES",
    "ExecutionAttempt",
    "ExecutionContractError",
    "ExecutorProviderCapabilities",
    "ExecutionResourceRequest",
    "ExecutionSpec",
]
