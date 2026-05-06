from __future__ import annotations

from typing import Any

from adaos.services import device_inventory as _service


def list_devices(kind: str | None = None) -> list[dict[str, Any]]:
    normalized = str(kind or "").strip().lower() or None
    return _service.list_devices(kind=normalized if normalized in {"browser", "member"} else None)


def get_device(device_ref: str) -> dict[str, Any] | None:
    return _service.get_device(str(device_ref or ""))


def inspect_device(device_ref: str) -> dict[str, Any] | None:
    return _service.inspect_device(str(device_ref or ""))


def make_device_ref(kind: str, link_id: str) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized not in {"browser", "member"}:
        raise ValueError("kind must be 'browser' or 'member'")
    return _service.make_device_ref(normalized, str(link_id or ""))

