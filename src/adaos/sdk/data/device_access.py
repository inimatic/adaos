from __future__ import annotations

from adaos.services import device_access as _service


def get_command_profile(device_ref: str) -> dict | None:
    return _service.get_command_profile(str(device_ref or ""))


def rename_device(device_ref: str, display_name: str) -> dict:
    return _service.rename_device(str(device_ref or ""), str(display_name or ""))


def set_device_lifetime(device_ref: str, preset: str) -> dict:
    return _service.set_device_lifetime(str(device_ref or ""), str(preset or ""))


def detach_device(device_ref: str) -> dict:
    return _service.detach_device(str(device_ref or ""))

