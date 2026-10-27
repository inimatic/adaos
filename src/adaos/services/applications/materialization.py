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


def _materialization_identity(application_id: str, selections, rooms, priority) -> str:
    payload = {"application_id": application_id, "selections": [s.to_dict() for s in selections], "webspace_ids": rooms}
    # Preserve pre-priority request identities: deploying a scheduling hint
    # must not invalidate and rebuild every completed Application projection.
    if priority:
        payload["priority_webspace_ids"] = priority
    return canonical_payload_digest(payload)


def _record_materialization_request(state: Path, application_id: str, selections, webspace_ids, *, retained_webspace_ids=()) -> dict:
    priority = sorted(set(webspace_ids))
    rooms = sorted(set(priority) | set(retained_webspace_ids) | {s.webspace_id for s in selections})
    identity = _materialization_identity(application_id, selections, rooms, priority)
    path = request_path(state, application_id)
    with mutation_lock(path.with_suffix(".lock")):
        prior = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if prior.get("request_digest") == identity and prior.get("status") in {"pending", "completed"}:
            return {"ok": prior["status"] == "completed", "pending": prior["status"] != "completed",
                    "status": "refreshed" if prior["status"] == "completed" else "refresh_pending",
                    "request_digest": identity, "webspace_ids": rooms}
        atomic_write_json(path, {"schema": "adaos.application.materialization_request.v1",
            "application_id": application_id, "request_digest": identity, "webspace_ids": rooms,
            "priority_webspace_ids": priority,
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
        expected = _materialization_identity(request["application_id"], selections, rooms, request.get("priority_webspace_ids", []))
        if request.get("request_digest") != expected:
            _record_materialization_request(state, request["application_id"], selections,
                request.get("priority_webspace_ids", []), retained_webspace_ids=rooms)
            request = json.loads(path.read_text(encoding="utf-8"))
        if request.get("status") != "completed":
            work.append((path, request))
    return work


def _record_materialization_observations(path: Path, request_digest: str, observations: list[dict], *, advance_attempt: bool = True) -> str | None:
    with mutation_lock(path.with_suffix(".lock")):
        current = json.loads(path.read_text(encoding="utf-8"))
        if current.get("request_digest") != request_digest:
            return None
        done = all(item["ok"] for item in observations)
        current.update(status="completed" if done else "pending", observed_at=utc_now(),
                       attempts=int(current.get("attempts") or 0) + int(advance_attempt), observations=observations)
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
        priority = set(request.get("priority_webspace_ids", []))
        rooms = sorted(request["webspace_ids"], key=lambda room: (room not in priority, room))
        previous = {item["webspace_id"]: item for item in request.get("observations", [])}
        observations = [previous.get(room, {"webspace_id": room, "ok": False, "reason": "queued"}) for room in rooms]
        status = request.get("status")
        attempted = False
        for index, room in enumerate(rooms):
            if observations[index].get("ok"):
                continue
            try:
                await asyncio.to_thread(lambda: ComponentUpdateService(state_dir=state).reconcile_local_trials(
                    room, application_id=application_id))
                result = await rebuild_webspace_from_sources(room, action="runtime_selection_refresh",
                    source_of_truth="application_core_lifecycle", reseed_from_scenario=False)
                observations[index] = {"webspace_id": room, "ok": bool(result.get("ok"))}
            except Exception as exc:
                observations[index] = {"webspace_id": room, "ok": False, "reason": type(exc).__name__}
            status = await asyncio.to_thread(_record_materialization_observations, path, request["request_digest"],
                observations, advance_attempt=not attempted)
            attempted = True
            if status is None:
                break
        if not attempted:
            status = await asyncio.to_thread(_record_materialization_observations, path, request["request_digest"],
                observations, advance_attempt=False)
        if status is not None:
            results.append({"application_id": application_id, "status": status})
    return results
