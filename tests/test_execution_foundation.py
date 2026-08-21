from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.domain.execution import (
    AcceleratorAllocation,
    AcceleratorInventory,
    CheckpointManifest,
    ExecutionBudget,
    ExecutionDeterminism,
    ExecutionNetworkPolicy,
    ExecutionContractError,
    ExecutionResourceRequest,
    ExecutionSpec,
    PreemptionPolicy,
)
from adaos.domain.ownership import OwnershipIsolationError
from adaos.domain.runtime_bindings import ContentRef
from adaos.services.execution import local as local_execution
from adaos.services.execution import local_worker
from adaos.services.execution.local import LocalProcessExecutor
from adaos.services.execution.oci import OCIExecutor
from adaos.services.execution.service import ExecutionService


def _wait_terminal(
    executor: LocalProcessExecutor,
    attempt_id: str,
    *,
    owner_ref: str,
    timeout_s: float = 10.0,
):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        attempt = executor.reconcile(attempt_id, owner_ref=owner_ref)
        if attempt.terminal:
            return attempt
        time.sleep(0.03)
    raise AssertionError(f"attempt did not become terminal: {attempt_id}")


def _spec(tmp_path: Path, *command: str, wall_time_s: float | None = None) -> ExecutionSpec:
    return ExecutionSpec(
        spec_id="fixture.echo.v1",
        owner_ref="skill:research_manager",
        command=tuple(command),
        working_directory=str(tmp_path),
        resources=ExecutionResourceRequest(wall_time_s=wall_time_s),
    )


def test_execution_service_exposes_owner_scoped_provider_capabilities(tmp_path) -> None:
    provider = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    current = SimpleNamespace(name="research_manager_skill", path=tmp_path)
    ctx = SimpleNamespace(
        skill_ctx=SimpleNamespace(get=lambda: current),
        execution_provider=provider,
    )

    snapshot = ExecutionService(ctx).capabilities()

    assert snapshot["provider_id"] == "local-process"
    assert snapshot["protocol_version"] == "1.0"
    assert "network_offline" not in snapshot["features"]


@pytest.mark.parametrize("module", (local_execution, local_worker))
def test_execution_atomic_state_write_retries_transient_replace_lock(
    module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / f"{module.__name__.rsplit('.', 1)[-1]}.json"
    original = module.os.replace
    calls = 0

    def transient_once(source, target):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = PermissionError("sharing violation")
            error.winerror = 32
            raise error
        return original(source, target)

    monkeypatch.setattr(module.os, "replace", transient_once)
    module._atomic_json(destination, {"status": "running"})

    assert calls == 2
    assert json.loads(destination.read_text(encoding="utf-8")) == {"status": "running"}


def test_local_execution_is_idempotent_and_restart_reconcilable(tmp_path) -> None:
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    spec = _spec(
        tmp_path,
        sys.executable,
        "-c",
        "import sys; print('hello'); print('diagnostic', file=sys.stderr)",
    )
    submitted = executor.submit(spec, idempotency_key="trial-1-attempt-1")
    duplicate = executor.submit(spec, idempotency_key="trial-1-attempt-1")
    assert duplicate.attempt_id == submitted.attempt_id

    restarted = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    terminal = _wait_terminal(
        restarted,
        submitted.attempt_id,
        owner_ref="skill:research_manager",
    )
    assert terminal.status == "succeeded"
    assert terminal.exit_code == 0
    assert terminal.stdout is not None
    assert terminal.stderr is not None
    assert terminal.stdout.digest.startswith("sha256:")
    assert terminal.stdout.owner_ref == "skill:research_manager"
    assert terminal.stdout.size_bytes > 0


def test_idempotency_key_cannot_be_reused_for_another_spec(tmp_path) -> None:
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    first = _spec(tmp_path, sys.executable, "-c", "print('one')")
    second = _spec(tmp_path, sys.executable, "-c", "print('two')")
    submitted = executor.submit(first, idempotency_key="same-key")
    try:
        with pytest.raises(ExecutionContractError, match="different execution spec"):
            executor.submit(second, idempotency_key="same-key")
    finally:
        _wait_terminal(executor, submitted.attempt_id, owner_ref=first.owner_ref)


def test_attempt_access_is_owner_scoped(tmp_path) -> None:
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    spec = _spec(tmp_path, sys.executable, "-c", "print('owned')")
    attempt = executor.submit(spec, idempotency_key="owner-key")
    try:
        with pytest.raises(OwnershipIsolationError):
            executor.reconcile(attempt.attempt_id, owner_ref="skill:other_skill")
    finally:
        _wait_terminal(executor, attempt.attempt_id, owner_ref=spec.owner_ref)


def test_local_execution_timeout_is_a_typed_failure(tmp_path) -> None:
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    spec = _spec(
        tmp_path,
        sys.executable,
        "-c",
        "import time; time.sleep(5)",
        wall_time_s=0.2,
    )
    attempt = executor.submit(spec, idempotency_key="timeout-key")
    terminal = _wait_terminal(executor, attempt.attempt_id, owner_ref=spec.owner_ref)
    assert terminal.status == "failed"
    assert terminal.failure is not None
    assert terminal.failure["reason"] == "wall_time_exceeded"


def test_local_execution_can_be_cancelled_without_creating_a_new_attempt(tmp_path) -> None:
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    spec = _spec(tmp_path, sys.executable, "-c", "import time; time.sleep(30)")
    attempt = executor.submit(spec, idempotency_key="cancel-key")
    cancelled = executor.cancel(attempt.attempt_id, owner_ref=spec.owner_ref)
    assert cancelled.attempt_id == attempt.attempt_id
    assert cancelled.status == "cancelled"
    assert cancelled.failure == {"reason": "cancelled_by_owner"}


def test_local_provider_rejects_unenforced_resources_and_unsafe_cwd(tmp_path) -> None:
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path / "allowed",))
    (tmp_path / "allowed").mkdir()
    outside = _spec(tmp_path, sys.executable, "-c", "print('outside')")
    with pytest.raises(PermissionError, match="outside allowed roots"):
        executor.submit(outside, idempotency_key="outside")

    gpu = ExecutionSpec(
        spec_id="fixture.gpu.v1",
        owner_ref="skill:research_manager",
        command=(sys.executable, "-c", "print('gpu')"),
        working_directory=str(tmp_path / "allowed"),
        resources=ExecutionResourceRequest(gpu_count=1),
    )
    with pytest.raises(ExecutionContractError, match="does not allocate GPUs"):
        executor.submit(gpu, idempotency_key="gpu")

    offline = ExecutionSpec(
        spec_id="fixture.offline.v1",
        owner_ref="skill:research_manager",
        command=(sys.executable, "-c", "print('offline')"),
        working_directory=str(tmp_path / "allowed"),
        network=ExecutionNetworkPolicy(mode="offline"),
    )
    with pytest.raises(ExecutionContractError, match="cannot enforce"):
        executor.submit(offline, idempotency_key="offline")


def test_confirmatory_execution_requires_named_rng_and_immutable_digests(tmp_path) -> None:
    with pytest.raises(ExecutionContractError, match="missing RNG streams"):
        ExecutionDeterminism(mode="confirmatory", rng_streams={"analysis": 1})

    streams = {name: index for index, name in enumerate(ExecutionDeterminism.REQUIRED_STREAMS)}
    with pytest.raises(ExecutionContractError, match="immutable code"):
        ExecutionSpec(
            spec_id="confirmatory.v1",
            owner_ref="skill:research_manager",
            command=(sys.executable, "-c", "print('x')"),
            working_directory=str(tmp_path),
            determinism=ExecutionDeterminism(mode="confirmatory", rng_streams=streams),
        )


def test_declared_outputs_logs_heartbeats_and_resources_are_persisted(tmp_path) -> None:
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    script = "from pathlib import Path; print('x' * 5000); Path('result.bin').write_bytes(b'ok')"
    spec = ExecutionSpec(
        spec_id="fixture.output.v1",
        owner_ref="skill:research_manager",
        command=(sys.executable, "-c", script),
        working_directory=str(tmp_path),
        run_id="run-1",
        trial_id="trial-1",
        resources=ExecutionResourceRequest(max_log_bytes=1024),
        expected_outputs=("result.bin",),
        budget=ExecutionBudget(max_attempts=2, max_storage_bytes=4096),
    )
    attempt = executor.submit(spec, idempotency_key="output-1")
    terminal = _wait_terminal(executor, attempt.attempt_id, owner_ref=spec.owner_ref)
    assert terminal.status == "succeeded"
    assert terminal.run_id == "run-1"
    assert terminal.trial_id == "trial-1"
    assert terminal.last_heartbeat_at
    assert terminal.resource_observations
    assert terminal.outputs[0].metadata["path"] == "result.bin"
    assert terminal.stdout is not None and terminal.stdout.size_bytes == 1024
    assert [item["status"] for item in terminal.status_history][-1] == "succeeded"


def test_missing_declared_output_and_memory_budget_fail_closed(tmp_path) -> None:
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    missing = ExecutionSpec(
        spec_id="fixture.missing-output.v1",
        owner_ref="skill:research_manager",
        command=(sys.executable, "-c", "print('done')"),
        working_directory=str(tmp_path),
        expected_outputs=("absent.bin",),
    )
    result = _wait_terminal(
        executor,
        executor.submit(missing, idempotency_key="missing-output").attempt_id,
        owner_ref=missing.owner_ref,
    )
    assert result.failure and result.failure["reason"] == "declared_output_missing"

    memory = ExecutionSpec(
        spec_id="fixture.memory.v1",
        owner_ref="skill:research_manager",
        command=(sys.executable, "-c", "import time; x=bytearray(80*1024*1024); time.sleep(2)"),
        working_directory=str(tmp_path),
        resources=ExecutionResourceRequest(memory_mb=32),
    )
    limited = _wait_terminal(
        executor,
        executor.submit(memory, idempotency_key="memory-limit").attempt_id,
        owner_ref=memory.owner_ref,
    )
    assert limited.failure and limited.failure["reason"] == "memory_limit_exceeded"


def test_unknown_outcome_must_be_reconciled_before_same_run_retry(tmp_path) -> None:
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    spec = ExecutionSpec(
        spec_id="fixture.unknown.v1",
        owner_ref="skill:research_manager",
        command=(sys.executable, "-c", "import time; time.sleep(30)"),
        working_directory=str(tmp_path),
        run_id="run-unknown",
        budget=ExecutionBudget(max_attempts=2),
    )
    attempt = executor.submit(spec, idempotency_key="unknown-1")
    ps = __import__("psutil").Process(attempt.pid)
    for child in ps.children(recursive=True):
        child.kill()
    ps.kill()
    ps.wait(timeout=5)
    attempt_dir = tmp_path / "state" / "executions" / "local" / attempt.attempt_id
    (attempt_dir / "receipt.json").unlink(missing_ok=True)
    unknown = executor.reconcile(attempt.attempt_id, owner_ref=spec.owner_ref)
    assert unknown.status == "unknown"
    with pytest.raises(ExecutionContractError, match="unknown provider outcome"):
        executor.submit(spec, idempotency_key="unknown-2")
    lost = executor.reconcile(attempt.attempt_id, owner_ref=spec.owner_ref)
    assert lost.status == "lost"
    retry = executor.submit(spec, idempotency_key="unknown-2")
    try:
        assert retry.run_id == attempt.run_id
        assert retry.attempt_number == 2
        assert retry.sample_generation == attempt.sample_generation
    finally:
        executor.cancel(retry.attempt_id, owner_ref=spec.owner_ref)


def test_lost_heartbeat_enters_unknown_until_provider_recovers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_EXECUTION_HEARTBEAT_TIMEOUT_S", "0.2")
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    spec = _spec(tmp_path, sys.executable, "-c", "import time; time.sleep(30)")
    attempt = executor.submit(spec, idempotency_key="stale-heartbeat")
    heartbeat = (
        tmp_path / "state" / "executions" / "local" / attempt.attempt_id / "heartbeat.json"
    )
    deadline = time.monotonic() + 3
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    worker = __import__("psutil").Process(attempt.pid)
    worker.suspend()
    try:
        heartbeat.write_text(
            json.dumps({"at": "2000-01-01T00:00:00+00:00", "resource_observations": []}),
            encoding="utf-8",
        )
        unknown = executor.reconcile(attempt.attempt_id, owner_ref=spec.owner_ref)
        assert unknown.status == "unknown"
        assert unknown.failure == {"reason": "heartbeat_lease_expired"}
    finally:
        worker.resume()
        executor.cancel(attempt.attempt_id, owner_ref=spec.owner_ref)


def test_duplicate_terminal_callback_is_idempotent_and_cancellation_race_is_a_noop(tmp_path) -> None:
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    spec = _spec(tmp_path, sys.executable, "-c", "print('fast')")
    terminal = _wait_terminal(
        executor,
        executor.submit(spec, idempotency_key="terminal-once").attempt_id,
        owner_ref=spec.owner_ref,
    )
    duplicate = executor.reconcile(terminal.attempt_id, owner_ref=spec.owner_ref)
    raced = executor.cancel(terminal.attempt_id, owner_ref=spec.owner_ref)
    assert duplicate.to_dict() == terminal.to_dict()
    assert raced.status == "succeeded"
    assert len(raced.status_history) == len(terminal.status_history)


def test_accelerator_inventory_and_allocation_contracts_are_provider_neutral() -> None:
    inventory = AcceleratorInventory(
        accelerator_id="gpu-0",
        kind="cuda",
        model="A100",
        memory_mb=40_960,
        exclusive=True,
        ready=True,
        provider_id="oci",
    )
    assert inventory.can_satisfy(
        ExecutionResourceRequest(
            gpu_count=1,
            gpu_type="A100",
            gpu_memory_mb=20_000,
            gpu_exclusive=True,
        )
    )
    allocation = AcceleratorAllocation(
        allocation_id="allocation-1",
        provider_id="oci",
        attempt_id="attempt-1",
        accelerator_ids=("gpu-0",),
        exclusive=True,
    )
    assert allocation.to_dict()["accelerator_ids"] == ["gpu-0"]


def test_preempted_run_requires_compatible_checkpoint_and_bounded_policy(tmp_path) -> None:
    executor = LocalProcessExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    digest = "sha256:" + "a" * 64
    base = ExecutionSpec(
        spec_id="fixture.preemption.v1",
        owner_ref="skill:research_manager",
        command=(sys.executable, "-c", "print('checkpointed')"),
        working_directory=str(tmp_path),
        run_id="run-preempted",
        code_digest=digest,
        environment_digest=digest,
        budget=ExecutionBudget(max_attempts=3),
    )
    first = _wait_terminal(
        executor,
        executor.submit(base, idempotency_key="preemption-1").attempt_id,
        owner_ref=base.owner_ref,
    )
    preempted = replace(first, status="failed", failure={"reason": "provider_preempted"})
    executor._write_attempt(preempted)

    no_checkpoint = replace(
        base,
        preemption=PreemptionPolicy(enabled=True, max_preemptions=1),
    )
    with pytest.raises(ExecutionContractError, match="requires a proven checkpoint"):
        executor.submit(no_checkpoint, idempotency_key="preemption-2")

    content = ContentRef(
        uri="adaos-checkpoint:run-preempted/checkpoint-1",
        digest="sha256:" + "b" * 64,
        size_bytes=16,
        media_type="application/octet-stream",
        owner_ref=base.owner_ref,
        kind="execution-checkpoint",
    )
    checkpoint = CheckpointManifest(
        checkpoint_id="checkpoint-1",
        content=content,
        producer_attempt_id=first.attempt_id,
        code_digest=digest,
        environment_digest=digest,
        rng_state_digest="sha256:" + "c" * 64,
    )
    resumed_spec = replace(no_checkpoint, checkpoint=checkpoint)
    resumed = _wait_terminal(
        executor,
        executor.submit(resumed_spec, idempotency_key="preemption-2").attempt_id,
        owner_ref=base.owner_ref,
    )
    assert resumed.status == "succeeded"
    assert resumed.attempt_number == 2
    assert resumed.run_id == first.run_id


def test_oci_provider_requires_digest_pin_and_builds_hostile_isolation_boundary(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("adaos.services.execution.oci.shutil.which", lambda _: "docker")
    executor = OCIExecutor(state_root=tmp_path / "state", allowed_roots=(tmp_path,))
    unpinned = ExecutionSpec(
        spec_id="oci.unpinned.v1",
        owner_ref="skill:research_manager",
        command=("python", "fixture.py"),
        working_directory=str(tmp_path),
        metadata={"container_image": "example/research:latest"},
    )
    with pytest.raises(ExecutionContractError, match="digest-pinned"):
        executor._container_spec(unpinned, idempotency_key="oci-unpinned")

    isolated = replace(
        unpinned,
        resources=ExecutionResourceRequest(cpu_cores=2, memory_mb=512, gpu_count=1),
        network=ExecutionNetworkPolicy(mode="offline"),
        metadata={"container_image": "example/research@sha256:" + "d" * 64},
    )
    container_spec = executor._container_spec(isolated, idempotency_key="oci-pinned")
    assert executor.capabilities.hostile_isolation is True
    assert "--network" in container_spec.command
    assert "none" in container_spec.command
    assert "--cpus" in container_spec.command
    assert "--memory" in container_spec.command
    assert "--gpus" in container_spec.command
    assert container_spec.metadata["oci_original_spec_digest"] == isolated.digest
