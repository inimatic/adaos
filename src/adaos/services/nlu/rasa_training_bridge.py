from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict
from urllib.request import Request, urlopen

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.interpreter.workspace import InterpreterWorkspace
from adaos.services.nlu.data_registry import sync_from_scenarios_and_skills
from adaos.services.skill.service_supervisor import get_service_supervisor
from .rasa_skill_installer import env_flag, is_rasa_nlu_enabled

_log = logging.getLogger("adaos.nlu.rasa.train")
_START_LOCK = asyncio.Lock()


def _http_post_json(url: str, payload: dict, *, timeout_ms: int = 600_000) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urlopen(req, timeout=timeout_ms / 1000.0) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
        return json.loads(raw)


def _http_get_json(url: str, *, timeout_ms: int = 1_000) -> dict | None:
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout_ms / 1000.0) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _service_health_ok(base_url: str) -> bool:
    payload = _http_get_json(f"{base_url}/health", timeout_ms=1_000)
    return bool(payload and payload.get("ok") is True)


async def _ensure_rasa_service_base_url(supervisor) -> str | None:
    if not is_rasa_nlu_enabled():
        return None

    await supervisor.refresh_discovered(force=True)
    base_url = supervisor.resolve_base_url("rasa_nlu_service_skill")
    if not base_url:
        return None
    if base_url and await asyncio.to_thread(_service_health_ok, base_url):
        return base_url

    async with _START_LOCK:
        base_url = supervisor.resolve_base_url("rasa_nlu_service_skill")
        if not base_url:
            return None
        if base_url and await asyncio.to_thread(_service_health_ok, base_url):
            return base_url
        await supervisor.start("rasa_nlu_service_skill")
        return supervisor.resolve_base_url("rasa_nlu_service_skill")


def _train_sync(ctx) -> dict:
    # 1) Sync NLU data into interpreter workspace files (pure-Python).
    sync_from_scenarios_and_skills(ctx)
    ws = InterpreterWorkspace(ctx)
    project = ws.build_rasa_project()

    models_dir = Path(ctx.paths.models_dir()) / "interpreter"
    models_dir.mkdir(parents=True, exist_ok=True)
    return {"project_dir": str(project), "out_dir": str(models_dir)}


async def train_rasa_nlu_once(*, reason: str = "manual", note: str | None = None) -> dict[str, Any]:
    ctx = get_ctx()
    if not is_rasa_nlu_enabled():
        return {"ok": False, "skipped": True, "reason": "rasa_disabled"}

    supervisor = get_service_supervisor()
    try:
        base_url = await _ensure_rasa_service_base_url(supervisor)
    except Exception:
        _log.warning("failed to start rasa_nlu_service_skill; skip train reason=%s", reason, exc_info=True)
        return {"ok": False, "skipped": True, "reason": "rasa_start_failed"}

    if not base_url:
        _log.warning("rasa service is not configured/installed; skip train reason=%s", reason)
        return {"ok": False, "skipped": True, "reason": "rasa_base_url_unresolved"}

    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(None, _train_sync, ctx)
    try:
        resp = await loop.run_in_executor(None, _http_post_json, f"{base_url}/train", payload)
    except Exception:
        _log.warning("rasa training request failed reason=%s", reason, exc_info=True)
        return {"ok": False, "reason": "rasa_train_request_failed"}
    if not isinstance(resp, dict) or not resp.get("ok"):
        _log.warning("rasa training failed reason=%s resp=%r", reason, resp)
        return {"ok": False, "reason": "rasa_train_failed", "response": resp}
    try:
        InterpreterWorkspace(ctx).record_training(
            note=note or f"rasa-auto:{reason}",
            extra={"engine": "rasa_service", "model_path": resp.get("model_path"), "reason": reason},
        )
    except Exception:
        _log.debug("failed to record rasa training metadata reason=%s", reason, exc_info=True)
    _log.info("rasa trained reason=%s", reason)
    return {"ok": True, "response": resp}


async def _train_if_enabled(reason: str) -> None:
    if not env_flag("ADAOS_NLU_AUTOTRAIN", default=False):
        return
    await train_rasa_nlu_once(reason=reason)


@subscribe("scenarios.synced")
async def _on_scenarios_synced(_: Dict[str, Any]) -> None:
    await _train_if_enabled("scenarios.synced")


@subscribe("skills.activated")
async def _on_skills_activated(_: Dict[str, Any]) -> None:
    await _train_if_enabled("skills.activated")


@subscribe("skills.rolledback")
async def _on_skills_rolledback(_: Dict[str, Any]) -> None:
    await _train_if_enabled("skills.rolledback")


@subscribe("desktop.webspace.reload")
async def _on_webspace_reload(_: Dict[str, Any]) -> None:
    await _train_if_enabled("desktop.webspace.reload")


@subscribe("nlp.rasa.train")
async def _on_manual_train(_: Any) -> None:
    await train_rasa_nlu_once(reason="manual")

