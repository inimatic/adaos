"""Restart-reconcilable local process execution provider."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import psutil

from adaos.domain.execution import (
    ExecutionAttempt,
    ExecutionContractError,
    ExecutionSpec,
    ExecutorProviderCapabilities,
)
from adaos.domain.ownership import OwnershipIsolationError, validate_owner_ref
from adaos.domain.runtime_bindings import ContentRef


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _attempt_id(owner_ref: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{owner_ref}\0{idempotency_key}".encode("utf-8")).hexdigest()
    return f"attempt.{digest}"


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ExecutionContractError(f"{path.name} must contain an object")
    return payload


def _content_ref(path: Path, *, attempt: ExecutionAttempt, stream: str) -> ContentRef:
    data = path.read_bytes() if path.exists() else b""
    digest = hashlib.sha256(data).hexdigest()
    return ContentRef(
        uri=f"adaos-execution:{attempt.attempt_id}/{stream}",
        digest=f"sha256:{digest}",
        size_bytes=len(data),
        media_type="text/plain; charset=utf-8",
        owner_ref=attempt.owner_ref,
        kind="execution-log",
        metadata={"attempt_id": attempt.attempt_id, "stream": stream},
    )


class LocalProcessExecutor:
    """Local reference adapter with durable attempt identity and receipts.

    It is an operational process boundary, not a hostile-code sandbox. CPU,
    memory, GPU, secret injection, and container isolation are rejected until
    dedicated adapters can enforce them.
    """

    provider_id = "local-process"

    @property
    def capabilities(self) -> ExecutorProviderCapabilities:
        return ExecutorProviderCapabilities(
            provider_id=self.provider_id,
            features=(
                "idempotency",
                "cancellation",
                "restart_reconciliation",
                "stdout",
                "stderr",
                "wall_time",
            ),
            hostile_isolation=False,
        )

    def __init__(self, *, state_root: Path, allowed_roots: tuple[Path, ...]) -> None:
        self._root = (Path(state_root).expanduser().resolve() / "executions" / "local").resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        roots = tuple(Path(item).expanduser().resolve() for item in allowed_roots)
        if not roots:
            raise ValueError("at least one allowed working-directory root is required")
        self._allowed_roots = roots
        self._lock = threading.RLock()

    def _attempt_dir(self, attempt_id: str) -> Path:
        token = str(attempt_id or "").strip()
        if not token.startswith("attempt.") or len(token) != 72:
            raise ExecutionContractError("invalid local attempt id")
        target = (self._root / token).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as exc:  # pragma: no cover - token guard
            raise ExecutionContractError("attempt path escaped execution state root") from exc
        return target

    def _validate_spec(self, spec: ExecutionSpec) -> Path:
        cwd = Path(spec.working_directory).expanduser().resolve()
        if not any(self._is_under(cwd, root) for root in self._allowed_roots):
            raise PermissionError(f"execution working directory is outside allowed roots: {cwd}")
        if spec.secret_refs:
            raise ExecutionContractError("local-process provider does not resolve secret_refs")
        if spec.resources.cpu_cores is not None:
            raise ExecutionContractError("local-process provider does not enforce cpu_cores")
        if spec.resources.memory_mb is not None:
            raise ExecutionContractError("local-process provider does not enforce memory_mb")
        if spec.resources.gpu_count:
            raise ExecutionContractError("local-process provider does not allocate GPUs")
        return cwd

    @staticmethod
    def _is_under(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def submit(self, spec: ExecutionSpec, *, idempotency_key: str) -> ExecutionAttempt:
        key = str(idempotency_key or "").strip()
        if not key:
            raise ExecutionContractError("idempotency_key must be non-empty")
        cwd = self._validate_spec(spec)
        owner_ref = validate_owner_ref(spec.owner_ref)
        attempt_id = _attempt_id(owner_ref, key)
        attempt_dir = self._attempt_dir(attempt_id)
        attempt_path = attempt_dir / "attempt.json"
        spec_path = attempt_dir / "spec.json"

        with self._lock:
            if attempt_path.exists():
                existing = ExecutionAttempt.from_dict(_load_json(attempt_path))
                if existing.owner_ref != owner_ref:
                    self._raise_owner(existing, owner_ref)
                persisted_spec = _load_json(spec_path)
                if str(persisted_spec.get("digest") or "") != spec.digest:
                    raise ExecutionContractError(
                        "idempotency key is already bound to a different execution spec"
                    )
                return self.reconcile(attempt_id, owner_ref=owner_ref)

            attempt_dir.mkdir(parents=True, exist_ok=False)
            _atomic_json(spec_path, {"digest": spec.digest, "spec": spec.to_dict()})
            attempt = ExecutionAttempt(
                attempt_id=attempt_id,
                owner_ref=owner_ref,
                spec_id=spec.spec_id,
                spec_digest=spec.digest,
                provider_id=self.provider_id,
                provider_attempt_id=attempt_id,
                idempotency_key=key,
                status="submitting",
            )
            self._write_attempt(attempt)

            worker = Path(__file__).with_name("local_worker.py").resolve()
            try:
                process = subprocess.Popen(
                    [sys.executable, str(worker), "--attempt-dir", str(attempt_dir)],
                    cwd=str(cwd),
                    env=dict(os.environ),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
                    start_new_session=(os.name != "nt"),
                )
                process_create_time = psutil.Process(process.pid).create_time()
            except Exception as exc:
                failed = replace(
                    attempt,
                    status="failed",
                    updated_at=_now(),
                    finished_at=_now(),
                    failure={"reason": "provider_submit_failed", "type": type(exc).__name__, "message": str(exc)},
                )
                self._write_attempt(failed)
                return failed

            running = replace(
                attempt,
                status="running",
                updated_at=_now(),
                started_at=_now(),
                pid=process.pid,
                process_create_time=process_create_time,
            )
            self._write_attempt(running)
            return running

    @staticmethod
    def _raise_owner(attempt: ExecutionAttempt, owner_ref: str) -> None:
        raise OwnershipIsolationError(
            f"attempt {attempt.attempt_id!r} belongs to {attempt.owner_ref!r}, not {owner_ref!r}"
        )

    def _write_attempt(self, attempt: ExecutionAttempt) -> None:
        _atomic_json(self._attempt_dir(attempt.attempt_id) / "attempt.json", attempt.to_dict())

    def _load_attempt(self, attempt_id: str, owner_ref: str) -> ExecutionAttempt:
        owner = validate_owner_ref(owner_ref)
        path = self._attempt_dir(attempt_id) / "attempt.json"
        if not path.exists():
            raise FileNotFoundError(f"execution attempt not found: {attempt_id}")
        attempt = ExecutionAttempt.from_dict(_load_json(path))
        if attempt.owner_ref != owner:
            self._raise_owner(attempt, owner)
        return attempt

    @staticmethod
    def _process_alive(attempt: ExecutionAttempt) -> bool:
        if attempt.pid is None or attempt.process_create_time is None:
            return False
        try:
            process = psutil.Process(attempt.pid)
            if abs(process.create_time() - attempt.process_create_time) > 0.01:
                return False
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except (psutil.Error, OSError):
            return False

    def reconcile(self, attempt_id: str, *, owner_ref: str) -> ExecutionAttempt:
        with self._lock:
            attempt = self._load_attempt(attempt_id, owner_ref)
            if attempt.terminal:
                return attempt
            attempt_dir = self._attempt_dir(attempt_id)
            receipt_path = attempt_dir / "receipt.json"
            if not receipt_path.exists() and not self._process_alive(attempt):
                for _ in range(10):
                    time.sleep(0.02)
                    if receipt_path.exists():
                        break
            if receipt_path.exists():
                receipt = _load_json(receipt_path)
                status = str(receipt.get("status") or "unknown").strip().lower()
                if status not in {"succeeded", "failed", "cancelled"}:
                    status = "unknown"
                terminal = replace(
                    attempt,
                    status=status,
                    updated_at=_now(),
                    started_at=str(receipt.get("started_at") or attempt.started_at or _now()),
                    finished_at=str(receipt.get("finished_at") or _now()) if status != "unknown" else None,
                    exit_code=receipt.get("exit_code"),
                    failure=dict(receipt.get("failure") or {}) if receipt.get("failure") is not None else None,
                    stdout=_content_ref(attempt_dir / "stdout.log", attempt=attempt, stream="stdout"),
                    stderr=_content_ref(attempt_dir / "stderr.log", attempt=attempt, stream="stderr"),
                )
                self._write_attempt(terminal)
                return terminal
            if self._process_alive(attempt):
                if attempt.status != "running":
                    attempt = replace(attempt, status="running", updated_at=_now())
                    self._write_attempt(attempt)
                return attempt
            lost = replace(
                attempt,
                status="lost",
                updated_at=_now(),
                finished_at=_now(),
                failure={"reason": "provider_process_missing_without_receipt"},
                stdout=_content_ref(attempt_dir / "stdout.log", attempt=attempt, stream="stdout"),
                stderr=_content_ref(attempt_dir / "stderr.log", attempt=attempt, stream="stderr"),
            )
            self._write_attempt(lost)
            return lost

    def cancel(self, attempt_id: str, *, owner_ref: str) -> ExecutionAttempt:
        with self._lock:
            attempt = self.reconcile(attempt_id, owner_ref=owner_ref)
            if attempt.terminal:
                return attempt
            cancelling = replace(attempt, status="cancelling", updated_at=_now())
            self._write_attempt(cancelling)
            if attempt.pid is not None and self._process_alive(attempt):
                try:
                    process = psutil.Process(attempt.pid)
                    children = process.children(recursive=True)
                except psutil.Error:
                    children = []
                    process = None
                for child in reversed(children):
                    try:
                        child.kill()
                    except psutil.Error:
                        pass
                if process is not None:
                    try:
                        process.kill()
                        process.wait(timeout=5.0)
                    except psutil.Error:
                        pass
            attempt_dir = self._attempt_dir(attempt_id)
            receipt = {
                "status": "cancelled",
                "started_at": attempt.started_at,
                "finished_at": _now(),
                "exit_code": None,
                "failure": {"reason": "cancelled_by_owner"},
            }
            _atomic_json(attempt_dir / "receipt.json", receipt)
            return self.reconcile(attempt_id, owner_ref=owner_ref)


__all__ = ["LocalProcessExecutor"]
