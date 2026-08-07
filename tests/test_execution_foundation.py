from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from adaos.domain.execution import (
    ExecutionContractError,
    ExecutionResourceRequest,
    ExecutionSpec,
)
from adaos.domain.ownership import OwnershipIsolationError
from adaos.services.execution.local import LocalProcessExecutor


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
