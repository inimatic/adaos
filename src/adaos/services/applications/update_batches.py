"""Durable reviewed batches for subnet-scoped Application updates."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


class ApplicationUpdateBatchError(RuntimeError):
    pass


def update_batch_id(subnet_ref: str, idempotency_key: str) -> str:
    identity = f"{str(subnet_ref).strip()}\0{str(idempotency_key).strip()}"
    return f"appbatch.{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


class ApplicationUpdateBatchStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()

    @property
    def root(self) -> Path:
        path = self.state_dir / "applications" / "update_batches"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def lock_path(self) -> Path:
        return self.root / ".mutation.lock"

    def path(self, batch_id: str) -> Path:
        token = str(batch_id or "").strip()
        if not token.startswith("appbatch.") or len(token) > 80:
            raise ApplicationUpdateBatchError(
                "invalid Application update batch identity"
            )
        return self.root / token / "current.json"

    def get(self, batch_id: str) -> dict[str, Any]:
        path = self.path(batch_id)
        if not path.is_file():
            raise FileNotFoundError(f"Application update batch not found: {batch_id}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApplicationUpdateBatchError(
                "cannot read Application update batch"
            ) from exc
        if not isinstance(value, Mapping):
            raise ApplicationUpdateBatchError(
                "Application update batch is not an object"
            )
        payload = dict(value)
        if payload.get("schema") != "adaos.application.update_batch.v1":
            raise ApplicationUpdateBatchError(
                "unsupported Application update batch schema"
            )
        if payload.get("batch_id") != batch_id:
            raise ApplicationUpdateBatchError(
                "Application update batch identity mismatch"
            )
        return payload

    def save(self, value: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(value)
        batch_id = str(payload.get("batch_id") or "").strip()
        if payload.get("schema") != "adaos.application.update_batch.v1":
            raise ApplicationUpdateBatchError(
                "unsupported Application update batch schema"
            )
        path = self.path(batch_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            atomic_write_json(path, payload)
        return payload


__all__ = [
    "ApplicationUpdateBatchError",
    "ApplicationUpdateBatchStore",
    "update_batch_id",
]
