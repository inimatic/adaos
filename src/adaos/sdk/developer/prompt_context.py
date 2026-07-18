"""Bounded Builder/Prompt IDE project context stored in DEV artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from adaos.sdk.developer import projects

_MAX_CONTEXT_BYTES = 512 * 1024
_MAX_TEXT_BYTES = 128 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(kind: str, project_id: str) -> Path:
    return projects._root(kind, project_id) / "prompt_state.json"  # noqa: SLF001


def _default(kind: str, project_id: str) -> dict[str, Any]:
    return {
        "object_type": str(kind),
        "object_id": str(project_id),
        "base_tz": "",
        "tz_addenda": [],
        "prepare": {"general_prompt": "", "iterations": []},
        "generate": {"general_prompt": "", "iterations": []},
        "llm_profile_id": None,
        "builder_llm_model": None,
        "llm_provider": None,
        "llm_profile": None,
        "llm_profile_updated_at": None,
        "target_node_id": None,
        "workflow_state": "tz",
        "archived": False,
    }


def _read(kind: str, project_id: str) -> dict[str, Any]:
    path = _state_path(kind, project_id)
    state = _default(kind, project_id)
    if path.is_file() and path.stat().st_size <= _MAX_CONTEXT_BYTES:
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, Mapping):
            state.update(dict(raw))
    if not str(state.get("base_tz") or ""):
        try:
            state["base_tz"] = projects.read_file(kind, project_id, "tz/base_tz.md")["content"]
        except projects.DeveloperProjectError:
            pass
    return state


def _write(kind: str, project_id: str, state: Mapping[str, Any]) -> None:
    path = _state_path(kind, project_id)
    payload = dict(state)
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(raw) > _MAX_CONTEXT_BYTES:
        raise projects.DeveloperProjectError("prompt context exceeds the bounded state size")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)


def get(kind: str, project_id: str) -> dict[str, Any]:
    """Return render-safe project prompt context and development preferences."""

    state = _read(kind, project_id)
    return {"ok": True, **state}


def save_base(kind: str, project_id: str, text: str) -> dict[str, Any]:
    """Persist the base technical specification in its file and state projection."""

    value = str(text)
    if len(value.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise projects.DeveloperProjectError("base technical specification is too large")
    projects.write_file(kind, project_id, "tz/base_tz.md", value, max_bytes=_MAX_TEXT_BYTES)
    state = _read(kind, project_id)
    state["base_tz"] = value
    state["updated_at"] = _now()
    _write(kind, project_id, state)
    return {"ok": True, **state}


def append_addendum(
    kind: str,
    project_id: str,
    text: str,
    *,
    iteration_ref: str | None = None,
) -> dict[str, Any]:
    """Append one immutable technical-specification addendum."""

    value = str(text).strip()
    if not value:
        raise projects.DeveloperProjectError("addendum text is required")
    if len(value.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise projects.DeveloperProjectError("technical-specification addendum is too large")
    created_at = _now()
    addendum_id = f"addendum-{uuid4().hex}"
    item = {
        "id": addendum_id,
        "text": value,
        "created_at": created_at,
        "iteration_ref": str(iteration_ref or "").strip() or None,
    }
    state = _read(kind, project_id)
    addenda = [dict(entry) for entry in state.get("tz_addenda") or [] if isinstance(entry, Mapping)]
    addenda.append(item)
    state["tz_addenda"] = addenda[-200:]
    state["updated_at"] = created_at
    _write(kind, project_id, state)
    return {"ok": True, "addendum": item, **state}


def set_preferences(
    kind: str,
    project_id: str,
    *,
    llm_model: str | None = None,
    llm_provider: str | None = None,
    llm_profile: Mapping[str, Any] | None = None,
    workflow_state: str | None = None,
    archived: bool | None = None,
) -> dict[str, Any]:
    """Persist the small allowlisted Builder development preference set."""

    state = _read(kind, project_id)
    if llm_model is not None:
        model = str(llm_model).strip()
        if not model:
            raise projects.DeveloperProjectError("llm_model must not be empty")
        state["llm_profile_id"] = model
        state["builder_llm_model"] = model
        state["llm_profile_updated_at"] = _now()
    if llm_provider is not None:
        state["llm_provider"] = str(llm_provider).strip() or None
    if llm_profile is not None:
        state["llm_profile"] = dict(llm_profile)
    if workflow_state is not None:
        value = str(workflow_state).strip()
        if not value:
            raise projects.DeveloperProjectError("workflow_state must not be empty")
        state["workflow_state"] = value
    if archived is not None:
        state["archived"] = bool(archived)
        state["archived_at"] = _now() if archived else None
    state["updated_at"] = _now()
    _write(kind, project_id, state)
    return {"ok": True, **state}


__all__ = ["append_addendum", "get", "save_base", "set_preferences"]
