"""Optional OCI-backed execution provider for hostile or generated workloads."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from adaos.domain.execution import (
    ExecutionContractError,
    ExecutionNetworkPolicy,
    ExecutionResourceRequest,
    ExecutionSpec,
    ExecutorProviderCapabilities,
)
from adaos.services.execution.local import LocalProcessExecutor


class OCIExecutor(LocalProcessExecutor):
    """Run a digest-pinned OCI image through an installed Docker-compatible CLI.

    The host process is still tracked by the durable local receipt machinery,
    while the workload receives the stronger container boundary. Allowlisted
    egress and secret injection remain fail-closed until an operator network and
    secret driver is configured.
    """

    provider_id = "oci"

    def __init__(
        self,
        *,
        state_root: Path,
        allowed_roots: tuple[Path, ...],
        runtime: str = "docker",
    ) -> None:
        executable = shutil.which(runtime)
        if executable is None:
            raise RuntimeError(f"OCI runtime is unavailable: {runtime}")
        self._oci_runtime = executable
        super().__init__(state_root=state_root, allowed_roots=allowed_roots)

    @property
    def capabilities(self) -> ExecutorProviderCapabilities:
        return ExecutorProviderCapabilities(
            provider_id=self.provider_id,
            features=(
                "idempotency",
                "cancellation",
                "restart_reconciliation",
                "checkpoint_inputs",
                "cpu_limit",
                "memory_limit",
                "gpu_allocation",
                "network_offline",
                "bounded_logs",
                "declared_outputs",
            ),
            hostile_isolation=True,
        )

    def _container_spec(self, spec: ExecutionSpec, *, idempotency_key: str) -> ExecutionSpec:
        image = str(spec.metadata.get("container_image") or "").strip()
        if "@sha256:" not in image:
            raise ExecutionContractError("OCI execution requires a digest-pinned container_image")
        if spec.network.mode == "allowlist":
            raise ExecutionContractError("OCI allowlist network requires an operator network driver")
        if spec.secret_refs:
            raise ExecutionContractError("OCI secret_refs require an operator secret driver")
        cwd = Path(spec.working_directory).expanduser().resolve()
        command = [
            self._oci_runtime,
            "run",
            "--rm",
            "--name",
            f"adaos-{idempotency_key.encode('utf-8').hex()[:32]}",
            "--network",
            "none" if spec.network.mode == "offline" else "bridge",
            "-v",
            f"{cwd}:/work:rw",
            "-w",
            "/work",
        ]
        if spec.resources.cpu_cores is not None:
            command.extend(("--cpus", str(spec.resources.cpu_cores)))
        if spec.resources.memory_mb is not None:
            command.extend(("--memory", f"{spec.resources.memory_mb}m"))
        if spec.resources.gpu_count:
            command.extend(("--gpus", str(spec.resources.gpu_count)))
        command.append(image)
        command.extend(spec.command)
        return replace(
            spec,
            command=tuple(command),
            network=ExecutionNetworkPolicy(mode="unrestricted"),
            resources=ExecutionResourceRequest(
                wall_time_s=spec.resources.wall_time_s,
                max_log_bytes=spec.resources.max_log_bytes,
            ),
            metadata={**dict(spec.metadata), "oci_original_spec_digest": spec.digest},
        )

    def submit(self, spec: ExecutionSpec, *, idempotency_key: str):
        container_spec = self._container_spec(spec, idempotency_key=idempotency_key)
        attempt = super().submit(container_spec, idempotency_key=idempotency_key)
        rebound = replace(
            attempt,
            spec_id=spec.spec_id,
            spec_digest=spec.digest,
            provider_id=self.provider_id,
            provider_binding={
                "provider_id": self.provider_id,
                "protocol_version": self.capabilities.protocol_version,
                "hostile_isolation": True,
                "image": str(spec.metadata["container_image"]),
            },
        )
        self._write_attempt(rebound)
        return rebound


__all__ = ["OCIExecutor"]
