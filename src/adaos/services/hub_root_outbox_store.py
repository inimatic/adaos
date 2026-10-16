from __future__ import annotations

import base64
import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from adaos.services.artifact_pipeline.storage import atomic_write_json, read_atomic_json
from adaos.services.runtime_paths import current_state_dir

_LOCK = threading.RLock()


def _base_state_dir() -> Path:
    return current_state_dir()


def _outbox_root() -> Path:
    root = _base_state_dir() / "hub_root_outboxes"
    root.mkdir(parents=True, exist_ok=True)
    return root


def outbox_store_path(name: str) -> Path:
    key = str(name or "").strip().lower() or "default"
    return _outbox_root() / f"{key}.json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload)


def load_outbox_items(name: str) -> deque[tuple[str, bytes, dict[str, Any] | None]]:
    path = outbox_store_path(name)
    with _LOCK:
        try:
            payload = read_atomic_json(path)
        except FileNotFoundError:
            return deque()
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError("outbox_store_corrupt: explicit recovery required") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("outbox_store_corrupt: items must be a list")
    result: deque[tuple[str, bytes, dict[str, Any] | None]] = deque()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("outbox_store_corrupt: invalid item")
        subject = str(raw.get("subject") or "").strip()
        data_b64 = raw.get("data_b64")
        if not subject or not isinstance(data_b64, str):
            raise ValueError("outbox_store_corrupt: missing subject or payload")
        try:
            data = base64.b64decode(data_b64.encode("ascii"), validate=True)
        except Exception:
            raise ValueError("outbox_store_corrupt: invalid payload encoding") from None
        meta = raw.get("meta")
        if meta is not None and not isinstance(meta, dict):
            raise ValueError("outbox_store_corrupt: invalid metadata")
        result.append((subject, data, dict(meta) if isinstance(meta, dict) else None))
    return result


def save_outbox_items(name: str, items: Any) -> int:
    path = outbox_store_path(name)
    serialized: list[dict[str, Any]] = []
    for item in list(items or []):
        if not isinstance(item, tuple):
            raise ValueError("outbox item must be a tuple")
        if len(item) < 2:
            raise ValueError("outbox item must contain subject and payload")
        subject = str(item[0] or "").strip()
        data = bytes(item[1] or b"")
        meta = item[2] if len(item) >= 3 and isinstance(item[2], dict) else None
        if not subject:
            raise ValueError("outbox subject is required")
        serialized.append(
            {
                "subject": subject,
                "data_b64": base64.b64encode(data).decode("ascii"),
                "meta": dict(meta) if isinstance(meta, dict) else None,
            }
        )
    payload = {"items": serialized, "updated_at": time.time()}
    with _LOCK:
        # A failed startup read must never turn retained messages into an empty
        # queue on the next save. Preserve corrupt bytes for explicit recovery.
        load_outbox_items(name)
        _write_json(path, payload)
    return len(serialized)


__all__ = [
    "load_outbox_items",
    "outbox_store_path",
    "save_outbox_items",
]
