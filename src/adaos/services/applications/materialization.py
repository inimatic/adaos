"""Rebuildable Core work queue for derived Application UI materialization."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from adaos.domain.application import utc_now
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from .store import ApplicationStore


def request_path(state: Path, application_id: str) -> Path:
    return Path(state) / "applications/materialization_requests" / (
        hashlib.sha256(application_id.encode()).hexdigest() + ".json")


def request_materialization(owner: Any, application_id: str, *, webspace_ids=()) -> dict:
    authority = getattr(owner, "authority_context", None) or owner
    state = Path(authority.paths.state_dir())
    selections = tuple(s for s in ApplicationStore(state).list_runtime_selections()
                       if s.application_id == application_id)
    return _record_materialization_request(state, application_id, selections, webspace_ids)


def _record_materialization_request(state: Path, application_id: str, selections, webspace_ids) -> dict:
    rooms = sorted(set(webspace_ids) | {s.webspace_id for s in selections})
    identity = canonical_payload_digest({"application_id": application_id,
        "selections": [s.to_dict() for s in selections], "webspace_ids": rooms})
    path = request_path(state, application_id)
    with mutation_lock(path.with_suffix(".lock")):
        prior = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if prior.get("request_digest") == identity and prior.get("status") in {"pending", "completed"}:
            return {"ok": prior["status"] == "completed", "pending": prior["status"] != "completed",
                    "status": "refreshed" if prior["status"] == "completed" else "refresh_pending",
                    "request_digest": identity, "webspace_ids": rooms}
        atomic_write_json(path, {"schema": "adaos.application.materialization_request.v1",
            "application_id": application_id, "request_digest": identity, "webspace_ids": rooms,
            "status": "pending", "requested_at": utc_now(), "attempts": 0})
    return {"ok": False, "pending": True, "status": "refresh_pending",
            "request_digest": identity, "webspace_ids": rooms}


def _prepare_materialization_work(state: Path, limit: int) -> list[tuple[Path, dict]]:
    """One metadata snapshot per tick; no filesystem work on the Yjs owner."""
    paths = sorted((state / "applications/materialization_requests").glob("*.json"))
    if not paths:
        return []
    grouped = {}
    for selection in ApplicationStore(state).list_runtime_selections():
        grouped.setdefault(selection.application_id, []).append(selection)
    work = []
    for path in paths:
        if len(work) >= max(1, min(limit, 32)):
            break
        request = json.loads(path.read_text(encoding="utf-8"))
        selections = grouped.get(request["application_id"], [])
        rooms = sorted(set(request["webspace_ids"]) | {s.webspace_id for s in selections})
        expected = canonical_payload_digest({"application_id": request["application_id"],
            "selections": [s.to_dict() for s in selections], "webspace_ids": rooms})
        if request.get("request_digest") != expected:
            _record_materialization_request(state, request["application_id"], selections, rooms)
            request = json.loads(path.read_text(encoding="utf-8"))
        if request.get("status") != "completed":
            work.append((path, request))
    return work


def _record_materialization_observations(path: Path, request_digest: str, observations: list[dict]) -> str | None:
    with mutation_lock(path.with_suffix(".lock")):
        current = json.loads(path.read_text(encoding="utf-8"))
        if current.get("request_digest") != request_digest:
            return None
        done = all(item["ok"] for item in observations)
        current.update(status="completed" if done else "pending", observed_at=utc_now(),
                       attempts=int(current.get("attempts") or 0) + 1, observations=observations)
        atomic_write_json(path, current)
    return current["status"]


async def reconcile_materializations(owner: Any, *, limit: int = 8) -> list[dict]:
    """Refresh current selected authority; never replay migrations or publications."""
    from adaos.services.component_updates import ComponentUpdateService
    from adaos.services.scenario.webspace_runtime import rebuild_webspace_from_sources

    owner = getattr(owner, "authority_context", None) or owner
    state = Path(owner.paths.state_dir())
    results = []
    work = await asyncio.to_thread(_prepare_materialization_work, state, limit)
    for path, request in work:
        application_id = request["application_id"]
        rooms = request["webspace_ids"]
        observations = []
        for room in rooms:
            try:
                await asyncio.to_thread(lambda: ComponentUpdateService(state_dir=state).reconcile_local_trials(
                    room, application_id=application_id))
                result = await rebuild_webspace_from_sources(room, action="runtime_selection_refresh",
                    source_of_truth="application_core_lifecycle", reseed_from_scenario=False)
                observations.append({"webspace_id": room, "ok": bool(result.get("ok"))})
            except Exception as exc:
                observations.append({"webspace_id": room, "ok": False, "reason": type(exc).__name__})
        status = await asyncio.to_thread(_record_materialization_observations, path, request["request_digest"], observations)
        if status is not None:
            results.append({"application_id": application_id, "status": status})
    return results
