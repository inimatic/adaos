"""Durable, node-local operator defaults exposed by Management:System."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping

from adaos.services.agent_context import get_ctx
from adaos.services.artifact_pipeline.storage import atomic_write_json

DEFAULTS: dict[str, Any] = {
    "core_auto_update": True,
    "application_auto_update_default": True,
    "log_level": "INFO",
    "rasa_enabled": True,
}
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _path() -> Path:
    ctx = get_ctx()
    raw = ctx.paths.state_dir()
    return Path(raw).expanduser().resolve() / "operator" / "runtime_controls.json"


def read_controls() -> dict[str, Any]:
    value = dict(DEFAULTS)
    path = _path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {}
    if isinstance(payload, Mapping):
        for key in DEFAULTS:
            if key in payload:
                value[key] = payload[key]
    value["log_level"] = str(value.get("log_level") or "INFO").upper()
    return value


def update_controls(patch: Mapping[str, Any]) -> dict[str, Any]:
    current = read_controls()
    for key in ("core_auto_update", "application_auto_update_default", "rasa_enabled"):
        if key in patch:
            current[key] = bool(patch[key])
    if "log_level" in patch:
        level = str(patch.get("log_level") or "").strip().upper()
        if level not in _LOG_LEVELS:
            raise ValueError("unsupported log level")
        current["log_level"] = level
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {"schema": "adaos.operator.runtime_controls.v1", **current})
    apply_process_controls(current)
    return current


def apply_process_controls(controls: Mapping[str, Any] | None = None) -> None:
    value = dict(controls or read_controls())
    level = str(value.get("log_level") or "INFO").upper()
    numeric = getattr(logging, level, logging.INFO)
    logging.getLogger("adaos").setLevel(numeric)


def application_update_policy_default() -> str:
    return "auto_compatible" if read_controls()["application_auto_update_default"] else "notify"

