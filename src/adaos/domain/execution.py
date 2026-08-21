"""Provider-neutral long-running execution contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Mapping, Sequence

from .ownership import validate_owner_ref
from .runtime_bindings import ContentRef


EXECUTION_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "lost"})
EXECUTION_STATUSES = frozenset(
    {"accepted", "submitting", "running", "cancelling", "unknown", *EXECUTION_TERMINAL_STATUSES}
)
EXECUTION_PROTECTED_ENVIRONMENT_KEYS = frozenset(
    {
        "ADAOS_SKILL_ENV_PATH",
        "ADAOS_SKILL_MEMORY_PATH",
        "ADAOS_SKILL_INTERNAL_DATA_ROOT",
        "ADAOS_SKILL_INTERNAL_ACTIVE_PATH",
        "ADAOS_SKILL_INTERNAL_TARGET_PATH",
        "ADAOS_SKILL_NAME",
        "ADAOS_SKILL_PACKAGE",
        "ADAOS_SKILL_ROOT",
        "ADAOS_SKILL_MODE",
    }
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
    gpu_type: str | None = None
    gpu_memory_mb: int | None = None
    gpu_exclusive: bool = False
    wall_time_s: float | None = None
    max_log_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.cpu_cores is not None and float(self.cpu_cores) <= 0:
            raise ExecutionContractError("cpu_cores must be > 0")
        if self.memory_mb is not None and int(self.memory_mb) < 1:
            raise ExecutionContractError("memory_mb must be >= 1")
        if int(self.gpu_count) < 0:
            raise ExecutionContractError("gpu_count must be >= 0")
        gpu_type = str(self.gpu_type or "").strip() or None
        gpu_memory_mb = None if self.gpu_memory_mb is None else int(self.gpu_memory_mb)
        if gpu_memory_mb is not None and gpu_memory_mb < 1:
            raise ExecutionContractError("gpu_memory_mb must be >= 1")
        if int(self.gpu_count) == 0 and (gpu_type or gpu_memory_mb or self.gpu_exclusive):
            raise ExecutionContractError("GPU constraints require gpu_count > 0")
        if self.wall_time_s is not None and float(self.wall_time_s) <= 0:
            raise ExecutionContractError("wall_time_s must be > 0")
        object.__setattr__(self, "cpu_cores", None if self.cpu_cores is None else float(self.cpu_cores))
        object.__setattr__(self, "memory_mb", None if self.memory_mb is None else int(self.memory_mb))
        object.__setattr__(self, "gpu_count", int(self.gpu_count))
        object.__setattr__(self, "gpu_type", gpu_type)
        object.__setattr__(self, "gpu_memory_mb", gpu_memory_mb)
        object.__setattr__(self, "wall_time_s", None if self.wall_time_s is None else float(self.wall_time_s))
        if int(self.max_log_bytes) < 1024:
            raise ExecutionContractError("max_log_bytes must be >= 1024")
        object.__setattr__(self, "max_log_bytes", int(self.max_log_bytes))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExecutionNetworkPolicy:
    mode: str = "unrestricted"
    allow_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        mode = str(self.mode or "").strip().lower()
        if mode not in {"offline", "allowlist", "unrestricted"}:
            raise ExecutionContractError("unsupported network policy")
        hosts = tuple(dict.fromkeys(str(item).strip().lower() for item in self.allow_hosts if str(item).strip()))
        if mode != "allowlist" and hosts:
            raise ExecutionContractError("allow_hosts requires allowlist network mode")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "allow_hosts", hosts)

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "allow_hosts": list(self.allow_hosts)}


@dataclass(frozen=True, slots=True)
class ExecutionDeterminism:
    mode: str = "exploratory"
    rng_streams: Mapping[str, int] = field(default_factory=dict)
    deterministic_algorithms: bool = False

    REQUIRED_STREAMS: ClassVar[tuple[str, ...]] = (
        "initialization",
        "data_ordering",
        "augmentation",
        "operator_initialization",
        "analysis",
    )

    def __post_init__(self) -> None:
        mode = str(self.mode or "").strip().lower()
        if mode not in {"exploratory", "confirmatory"}:
            raise ExecutionContractError("determinism mode must be exploratory or confirmatory")
        streams = {str(key).strip(): int(value) for key, value in dict(self.rng_streams).items()}
        if any(not key for key in streams):
            raise ExecutionContractError("RNG stream names must be non-empty")
        missing = tuple(item for item in self.REQUIRED_STREAMS if item not in streams)
        if mode == "confirmatory" and missing:
            raise ExecutionContractError(
                f"confirmatory execution is missing RNG streams: {', '.join(missing)}"
            )
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "rng_streams", streams)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "rng_streams": dict(self.rng_streams),
            "deterministic_algorithms": self.deterministic_algorithms,
        }


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    max_attempts: int = 1
    max_compute_seconds: float | None = None
    max_storage_bytes: int | None = None
    max_cost: float | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        if int(self.max_attempts) < 1:
            raise ExecutionContractError("max_attempts must be >= 1")
        for name in ("max_compute_seconds", "max_storage_bytes", "max_cost"):
            value = getattr(self, name)
            if value is not None and float(value) <= 0:
                raise ExecutionContractError(f"{name} must be > 0")
        if self.max_cost is not None and not str(self.currency or "").strip():
            raise ExecutionContractError("currency is required with max_cost")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AcceleratorInventory:
    accelerator_id: str
    kind: str
    model: str
    memory_mb: int
    exclusive: bool
    ready: bool
    provider_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "accelerator_id", _token(self.accelerator_id, "accelerator_id"))
        object.__setattr__(self, "kind", _token(self.kind, "accelerator kind").lower())
        object.__setattr__(self, "model", _token(self.model, "accelerator model"))
        if int(self.memory_mb) < 1:
            raise ExecutionContractError("accelerator memory_mb must be >= 1")
        object.__setattr__(self, "memory_mb", int(self.memory_mb))
        object.__setattr__(self, "provider_id", _token(self.provider_id, "provider_id").lower())

    def can_satisfy(self, request: ExecutionResourceRequest) -> bool:
        if not self.ready or request.gpu_count < 1:
            return False
        if request.gpu_type and request.gpu_type.lower() not in {
            self.kind.lower(),
            self.model.lower(),
        }:
            return False
        if request.gpu_memory_mb is not None and self.memory_mb < request.gpu_memory_mb:
            return False
        if request.gpu_exclusive and not self.exclusive:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AcceleratorAllocation:
    allocation_id: str
    provider_id: str
    attempt_id: str
    accelerator_ids: tuple[str, ...]
    exclusive: bool
    status: str = "allocated"
    allocated_at: str = field(default_factory=_now)
    released_at: str | None = None

    def __post_init__(self) -> None:
        identifiers = tuple(
            dict.fromkeys(_token(item, "accelerator_ids item") for item in self.accelerator_ids)
        )
        if not identifiers:
            raise ExecutionContractError("accelerator allocation must contain at least one device")
        status = str(self.status or "").strip().lower()
        if status not in {"allocated", "released"}:
            raise ExecutionContractError("unsupported accelerator allocation status")
        if status == "released" and not self.released_at:
            raise ExecutionContractError("released accelerator allocation requires released_at")
        object.__setattr__(self, "allocation_id", _token(self.allocation_id, "allocation_id"))
        object.__setattr__(self, "provider_id", _token(self.provider_id, "provider_id").lower())
        object.__setattr__(self, "attempt_id", _token(self.attempt_id, "attempt_id"))
        object.__setattr__(self, "accelerator_ids", identifiers)
        object.__setattr__(self, "status", status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocation_id": self.allocation_id,
            "provider_id": self.provider_id,
            "attempt_id": self.attempt_id,
            "accelerator_ids": list(self.accelerator_ids),
            "exclusive": self.exclusive,
            "status": self.status,
            "allocated_at": self.allocated_at,
            "released_at": self.released_at,
        }


@dataclass(frozen=True, slots=True)
class CheckpointManifest:
    SCHEMA: ClassVar[str] = "adaos.execution.checkpoint.v1"

    checkpoint_id: str
    content: ContentRef
    producer_attempt_id: str
    code_digest: str
    environment_digest: str
    rng_state_digest: str
    parent_digest: str | None = None
    resume_policy: str = "compatible_only"

    def __post_init__(self) -> None:
        for name in ("code_digest", "environment_digest", "rng_state_digest"):
            value = str(getattr(self, name) or "").strip().lower()
            if not value.startswith("sha256:") or len(value) != 71:
                raise ExecutionContractError(f"{name} must be a sha256 digest")
            object.__setattr__(self, name, value)
        if self.parent_digest is not None:
            parent = str(self.parent_digest).strip().lower()
            if not parent.startswith("sha256:") or len(parent) != 71:
                raise ExecutionContractError("parent_digest must be sha256")
            object.__setattr__(self, "parent_digest", parent)
        if self.resume_policy not in {"compatible_only", "operator_override", "never"}:
            raise ExecutionContractError("unsupported checkpoint resume policy")
        object.__setattr__(self, "checkpoint_id", _token(self.checkpoint_id, "checkpoint_id"))
        object.__setattr__(
            self,
            "producer_attempt_id",
            _token(self.producer_attempt_id, "producer_attempt_id"),
        )

    @property
    def digest(self) -> str:
        raw = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{hashlib.sha256(raw).hexdigest()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "checkpoint_id": self.checkpoint_id,
            "content": self.content.to_dict(),
            "producer_attempt_id": self.producer_attempt_id,
            "code_digest": self.code_digest,
            "environment_digest": self.environment_digest,
            "rng_state_digest": self.rng_state_digest,
            "parent_digest": self.parent_digest,
            "resume_policy": self.resume_policy,
        }

    def assert_compatible(self, *, code_digest: str, environment_digest: str) -> None:
        if self.resume_policy == "never":
            raise ExecutionContractError("checkpoint resume policy forbids resume")
        if self.resume_policy == "compatible_only" and (
            self.code_digest != code_digest or self.environment_digest != environment_digest
        ):
            raise ExecutionContractError("checkpoint is incompatible with code or environment")


@dataclass(frozen=True, slots=True)
class PreemptionPolicy:
    enabled: bool = False
    require_checkpoint: bool = True
    max_preemptions: int = 0

    def __post_init__(self) -> None:
        maximum = int(self.max_preemptions)
        if maximum < 0:
            raise ExecutionContractError("max_preemptions must be >= 0")
        if self.enabled and maximum < 1:
            raise ExecutionContractError("enabled preemption requires max_preemptions >= 1")
        object.__setattr__(self, "max_preemptions", maximum)

    def admit(self, *, checkpoint: CheckpointManifest | None, prior_preemptions: int) -> None:
        if not self.enabled:
            raise ExecutionContractError("preemption is not enabled")
        if self.require_checkpoint and checkpoint is None:
            raise ExecutionContractError("preemption requires a proven checkpoint")
        if int(prior_preemptions) > int(self.max_preemptions):
            raise ExecutionContractError("preemption budget exhausted")

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
    data_owner_ref: str | None = None
    trial_id: str | None = None
    run_id: str | None = None
    sample_generation: int = 0
    package_ref: ContentRef | None = None
    code_digest: str | None = None
    environment_digest: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    secret_refs: tuple[str, ...] = ()
    resources: ExecutionResourceRequest = field(default_factory=ExecutionResourceRequest)
    network: ExecutionNetworkPolicy = field(default_factory=ExecutionNetworkPolicy)
    determinism: ExecutionDeterminism = field(default_factory=ExecutionDeterminism)
    budget: ExecutionBudget = field(default_factory=ExecutionBudget)
    inputs: tuple[ContentRef, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    checkpoint: CheckpointManifest | None = None
    preemption: PreemptionPolicy = field(default_factory=PreemptionPolicy)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        spec_id = _token(self.spec_id, "spec_id")
        owner_ref = validate_owner_ref(self.owner_ref)
        data_owner_ref = validate_owner_ref(self.data_owner_ref or owner_ref)
        command = tuple(_token(item, "command item") for item in self.command)
        if not command:
            raise ExecutionContractError("command must contain at least one item")
        working_directory = _token(self.working_directory, "working_directory")
        trial_id = str(self.trial_id or "").strip() or None
        run_id = str(self.run_id or "").strip() or None
        sample_generation = int(self.sample_generation)
        if sample_generation < 0:
            raise ExecutionContractError("sample_generation must be >= 0")
        code_digest = str(self.code_digest or "").strip().lower() or None
        environment_digest = str(self.environment_digest or "").strip().lower() or None
        for field_name, digest in (("code_digest", code_digest), ("environment_digest", environment_digest)):
            if digest is not None and (not digest.startswith("sha256:") or len(digest) != 71):
                raise ExecutionContractError(f"{field_name} must be a sha256 digest")
        if self.determinism.mode == "confirmatory" and (code_digest is None or environment_digest is None):
            raise ExecutionContractError(
                "confirmatory execution requires immutable code and environment digests"
            )
        if self.checkpoint is not None:
            if code_digest is None or environment_digest is None:
                raise ExecutionContractError("checkpoint resume requires code and environment digests")
            self.checkpoint.assert_compatible(
                code_digest=code_digest,
                environment_digest=environment_digest,
            )
        environment = {
            _token(key, "environment key"): str(value)
            for key, value in dict(self.environment).items()
        }
        protected = sorted(EXECUTION_PROTECTED_ENVIRONMENT_KEYS.intersection(environment))
        if protected:
            raise ExecutionContractError(
                "execution environment contains core-owned skill bindings: "
                + ", ".join(protected)
            )
        secret_refs = tuple(dict.fromkeys(_token(item, "secret_refs item") for item in self.secret_refs))
        expected_outputs = tuple(
            dict.fromkeys(_token(item, "expected_outputs item") for item in self.expected_outputs)
        )
        for output in expected_outputs:
            normalized = PurePosixPath(output.replace("\\", "/"))
            if normalized.is_absolute() or ".." in normalized.parts:
                raise ExecutionContractError("expected_outputs must be relative paths without '..'")
        object.__setattr__(self, "spec_id", spec_id)
        object.__setattr__(self, "owner_ref", owner_ref)
        object.__setattr__(self, "data_owner_ref", data_owner_ref)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "working_directory", working_directory)
        object.__setattr__(self, "trial_id", trial_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "sample_generation", sample_generation)
        object.__setattr__(self, "code_digest", code_digest)
        object.__setattr__(self, "environment_digest", environment_digest)
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "secret_refs", secret_refs)
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "expected_outputs", expected_outputs)
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema": self.SCHEMA,
            "spec_id": self.spec_id,
            "owner_ref": self.owner_ref,
            "command": list(self.command),
            "working_directory": self.working_directory,
            "trial_id": self.trial_id,
            "run_id": self.run_id,
            "sample_generation": self.sample_generation,
            "package_ref": self.package_ref.to_dict() if self.package_ref else None,
            "code_digest": self.code_digest,
            "environment_digest": self.environment_digest,
            "environment": dict(self.environment),
            "secret_refs": list(self.secret_refs),
            "resources": self.resources.to_dict(),
            "network": self.network.to_dict(),
            "determinism": self.determinism.to_dict(),
            "budget": self.budget.to_dict(),
            "inputs": [item.to_dict() for item in self.inputs],
            "expected_outputs": list(self.expected_outputs),
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint else None,
            "preemption": self.preemption.to_dict(),
            "metadata": dict(self.metadata),
        }
        # Preserve the digest of existing v1 same-owner specifications.  The
        # optional field is serialized only when the workload data owner is
        # intentionally delegated by the control-plane owner.
        if self.data_owner_ref != self.owner_ref:
            value["data_owner_ref"] = self.data_owner_ref
        return value

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

        def _checkpoint(raw: Any) -> CheckpointManifest | None:
            if not isinstance(raw, Mapping):
                return None
            content = _content(raw.get("content"))
            if content is None:
                raise ExecutionContractError("checkpoint content ref is required")
            return CheckpointManifest(
                checkpoint_id=str(raw.get("checkpoint_id") or ""),
                content=content,
                producer_attempt_id=str(raw.get("producer_attempt_id") or ""),
                code_digest=str(raw.get("code_digest") or ""),
                environment_digest=str(raw.get("environment_digest") or ""),
                rng_state_digest=str(raw.get("rng_state_digest") or ""),
                parent_digest=str(raw.get("parent_digest")) if raw.get("parent_digest") else None,
                resume_policy=str(raw.get("resume_policy") or "compatible_only"),
            )

        resources = dict(value.get("resources") or {})
        network = dict(value.get("network") or {})
        determinism = dict(value.get("determinism") or {})
        budget = dict(value.get("budget") or {})
        preemption = dict(value.get("preemption") or {})
        inputs = tuple(
            item for item in (_content(raw) for raw in value.get("inputs") or []) if item is not None
        )
        return cls(
            spec_id=str(value.get("spec_id") or ""),
            owner_ref=str(value.get("owner_ref") or ""),
            data_owner_ref=(
                str(value.get("data_owner_ref")) if value.get("data_owner_ref") else None
            ),
            command=tuple(str(item) for item in value.get("command") or ()),
            working_directory=str(value.get("working_directory") or ""),
            trial_id=str(value.get("trial_id")) if value.get("trial_id") else None,
            run_id=str(value.get("run_id")) if value.get("run_id") else None,
            sample_generation=int(value.get("sample_generation") or 0),
            package_ref=_content(value.get("package_ref")),
            code_digest=str(value.get("code_digest")) if value.get("code_digest") else None,
            environment_digest=(
                str(value.get("environment_digest")) if value.get("environment_digest") else None
            ),
            environment=dict(value.get("environment") or {}),
            secret_refs=tuple(str(item) for item in value.get("secret_refs") or ()),
            resources=ExecutionResourceRequest(**resources),
            network=ExecutionNetworkPolicy(**network),
            determinism=ExecutionDeterminism(**determinism),
            budget=ExecutionBudget(**budget),
            inputs=inputs,
            expected_outputs=tuple(str(item) for item in value.get("expected_outputs") or ()),
            checkpoint=_checkpoint(value.get("checkpoint")),
            preemption=PreemptionPolicy(**preemption),
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
    trial_id: str | None = None
    run_id: str | None = None
    sample_generation: int = 0
    attempt_number: int = 1
    provider_binding: Mapping[str, Any] = field(default_factory=dict)
    status_history: tuple[Mapping[str, Any], ...] = ()
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    pid: int | None = None
    process_create_time: float | None = None
    exit_code: int | None = None
    last_heartbeat_at: str | None = None
    lease_expires_at: str | None = None
    cancellation: Mapping[str, Any] | None = None
    resource_observations: tuple[Mapping[str, Any], ...] = ()
    failure: Mapping[str, Any] | None = None
    stdout: ContentRef | None = None
    stderr: ContentRef | None = None
    outputs: tuple[ContentRef, ...] = ()
    checkpoint: CheckpointManifest | None = None
    accelerator_allocation: AcceleratorAllocation | None = None
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
        object.__setattr__(self, "trial_id", str(self.trial_id or "").strip() or None)
        object.__setattr__(self, "run_id", str(self.run_id or "").strip() or None)
        if int(self.sample_generation) < 0:
            raise ExecutionContractError("sample_generation must be >= 0")
        if int(self.attempt_number) < 1:
            raise ExecutionContractError("attempt_number must be >= 1")
        object.__setattr__(self, "sample_generation", int(self.sample_generation))
        object.__setattr__(self, "attempt_number", int(self.attempt_number))
        object.__setattr__(self, "provider_binding", dict(self.provider_binding))
        object.__setattr__(self, "status_history", tuple(dict(item) for item in self.status_history))
        object.__setattr__(self, "pid", None if self.pid is None else int(self.pid))
        object.__setattr__(
            self,
            "process_create_time",
            None if self.process_create_time is None else float(self.process_create_time),
        )
        object.__setattr__(self, "exit_code", None if self.exit_code is None else int(self.exit_code))
        object.__setattr__(self, "failure", None if self.failure is None else dict(self.failure))
        object.__setattr__(
            self,
            "cancellation",
            None if self.cancellation is None else dict(self.cancellation),
        )
        object.__setattr__(
            self,
            "resource_observations",
            tuple(dict(item) for item in self.resource_observations),
        )
        object.__setattr__(self, "outputs", tuple(self.outputs))
        if self.accelerator_allocation is not None and (
            self.accelerator_allocation.attempt_id != self.attempt_id
            or self.accelerator_allocation.provider_id != self.provider_id
        ):
            raise ExecutionContractError(
                "accelerator allocation must belong to the attempt and provider"
            )
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
            "trial_id": self.trial_id,
            "run_id": self.run_id,
            "sample_generation": self.sample_generation,
            "attempt_number": self.attempt_number,
            "provider_binding": dict(self.provider_binding),
            "status_history": [dict(item) for item in self.status_history],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pid": self.pid,
            "process_create_time": self.process_create_time,
            "exit_code": self.exit_code,
            "last_heartbeat_at": self.last_heartbeat_at,
            "lease_expires_at": self.lease_expires_at,
            "cancellation": dict(self.cancellation) if self.cancellation is not None else None,
            "resource_observations": [dict(item) for item in self.resource_observations],
            "failure": dict(self.failure) if self.failure is not None else None,
            "stdout": self.stdout.to_dict() if self.stdout else None,
            "stderr": self.stderr.to_dict() if self.stderr else None,
            "outputs": [item.to_dict() for item in self.outputs],
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint else None,
            "accelerator_allocation": (
                self.accelerator_allocation.to_dict() if self.accelerator_allocation else None
            ),
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

        def _checkpoint(raw: Any) -> CheckpointManifest | None:
            if not isinstance(raw, Mapping):
                return None
            content = _content(raw.get("content"))
            if content is None:
                raise ExecutionContractError("checkpoint content ref is required")
            return CheckpointManifest(
                checkpoint_id=str(raw.get("checkpoint_id") or ""),
                content=content,
                producer_attempt_id=str(raw.get("producer_attempt_id") or ""),
                code_digest=str(raw.get("code_digest") or ""),
                environment_digest=str(raw.get("environment_digest") or ""),
                rng_state_digest=str(raw.get("rng_state_digest") or ""),
                parent_digest=str(raw.get("parent_digest")) if raw.get("parent_digest") else None,
                resume_policy=str(raw.get("resume_policy") or "compatible_only"),
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
            trial_id=str(value.get("trial_id")) if value.get("trial_id") else None,
            run_id=str(value.get("run_id")) if value.get("run_id") else None,
            sample_generation=int(value.get("sample_generation") or 0),
            attempt_number=int(value.get("attempt_number") or 1),
            provider_binding=dict(value.get("provider_binding") or {}),
            status_history=tuple(dict(item) for item in value.get("status_history") or ()),
            created_at=str(value.get("created_at") or _now()),
            updated_at=str(value.get("updated_at") or _now()),
            started_at=str(value.get("started_at")) if value.get("started_at") else None,
            finished_at=str(value.get("finished_at")) if value.get("finished_at") else None,
            pid=value.get("pid"),
            process_create_time=value.get("process_create_time"),
            exit_code=value.get("exit_code"),
            last_heartbeat_at=(
                str(value.get("last_heartbeat_at")) if value.get("last_heartbeat_at") else None
            ),
            lease_expires_at=(
                str(value.get("lease_expires_at")) if value.get("lease_expires_at") else None
            ),
            cancellation=(
                dict(value.get("cancellation") or {}) if value.get("cancellation") is not None else None
            ),
            resource_observations=tuple(
                dict(item) for item in value.get("resource_observations") or ()
            ),
            failure=dict(value.get("failure") or {}) if value.get("failure") is not None else None,
            stdout=_content(value.get("stdout")),
            stderr=_content(value.get("stderr")),
            outputs=tuple(
                item
                for item in (_content(raw) for raw in value.get("outputs") or ())
                if item is not None
            ),
            checkpoint=_checkpoint(value.get("checkpoint")),
            accelerator_allocation=(
                AcceleratorAllocation(
                    allocation_id=str(value["accelerator_allocation"].get("allocation_id") or ""),
                    provider_id=str(value["accelerator_allocation"].get("provider_id") or ""),
                    attempt_id=str(value["accelerator_allocation"].get("attempt_id") or ""),
                    accelerator_ids=tuple(
                        str(item)
                        for item in value["accelerator_allocation"].get("accelerator_ids") or ()
                    ),
                    exclusive=bool(value["accelerator_allocation"].get("exclusive")),
                    status=str(value["accelerator_allocation"].get("status") or "allocated"),
                    allocated_at=str(value["accelerator_allocation"].get("allocated_at") or _now()),
                    released_at=(
                        str(value["accelerator_allocation"].get("released_at"))
                        if value["accelerator_allocation"].get("released_at")
                        else None
                    ),
                )
                if isinstance(value.get("accelerator_allocation"), Mapping)
                else None
            ),
            metadata=dict(value.get("metadata") or {}),
        )


__all__ = [
    "AcceleratorAllocation",
    "AcceleratorInventory",
    "CheckpointManifest",
    "EXECUTION_STATUSES",
    "EXECUTION_TERMINAL_STATUSES",
    "ExecutionAttempt",
    "ExecutionBudget",
    "ExecutionContractError",
    "ExecutorProviderCapabilities",
    "ExecutionResourceRequest",
    "ExecutionDeterminism",
    "ExecutionNetworkPolicy",
    "ExecutionSpec",
    "PreemptionPolicy",
]
