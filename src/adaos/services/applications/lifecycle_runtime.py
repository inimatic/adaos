"""Core lease observation and derived-UI recovery, independent of all apps."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.domain.application import RuntimeSelection
from .lifecycle import observe_runtime_admission
from .lifecycle_events import LifecycleEvents
from .materialization import reconcile_materializations, request_path
from .runtime_channel import ApplicationRuntimeChannel
from .store import ApplicationStore


_LOG = logging.getLogger(__name__)
_TASKS: dict[str, asyncio.Task] = {}


def observe_lifecycle_leases(owner, *, now: datetime | None = None) -> dict:
    """Bounded metadata observation; no SDK jobs, hooks, repair or deep hashing."""
    owner = getattr(owner, "authority_context", None) or owner
    state = Path(owner.paths.state_dir()).resolve()
    observed = now or datetime.now(timezone.utc)
    store = ApplicationStore(state)
    grouped = {}
    for application in store.list_applications():
        grouped[application.application_id] = []
    for selection in store.list_runtime_selections():
        grouped.setdefault(selection.application_id, []).append(selection)
    changed = 0
    for application_id, selections in grouped.items():
        selection = next(iter(selections), None)
        if selection is None:
            try:
                baseline = store.get_installation(application_id)
                if baseline.status == "active":
                    selection = RuntimeSelection(webspace_id="desktop", application_id=application_id,
                        source="stable_installation", release_digest=baseline.installed_release_digest,
                        runtime_root_ref="workspace", revision=1)
            except FileNotFoundError:
                pass
        fact = {"source": selection.source if selection else None,
                "release_digest": selection.release_digest if selection else None,
                "runtime_root_ref": selection.runtime_root_ref if selection else None,
                "webspace_ids": sorted(s.webspace_id for s in selections),
                "admission": "admitted", "reason": "StableInstallation", "expires_at": None}
        try:
            admission = observe_runtime_admission(state, selection, now=observed, store=store)
            # Remaining seconds is display-only, not a new event per tick.
            admission.pop("remaining_seconds", None)
            fact.update(admission)
        except (OSError, ValueError, KeyError):
            fact.update(admission="unavailable", reason="InvalidAdmissionMetadata")
        fact["transition_pending"] = bool(ApplicationRuntimeChannel(state, application_id).pending_transitions())
        path = request_path(state, application_id)
        if path.is_file():
            materialization = json.loads(path.read_text(encoding="utf-8"))
            fact["materialization"] = materialization.get("status", "unknown")
        changed += LifecycleEvents(state).record(application_id, fact, observed_at=observed.isoformat())
    return {"applications": len(grouped), "changed": changed}


async def _run(owner, key: str):
    try:
        while True:
            try:
                await asyncio.to_thread(observe_lifecycle_leases, owner)
                await reconcile_materializations(owner)
            except Exception:
                _LOG.warning("Core lifecycle observation/reconciliation failed", exc_info=True)
            await asyncio.sleep(15)
    finally:
        _TASKS.pop(key, None)


@subscribe("sys.ready")
async def on_lifecycle_runtime_ready(_event):
    owner = get_ctx()
    owner = getattr(owner, "authority_context", None) or owner
    key = str(Path(owner.paths.state_dir()).resolve())
    if key not in _TASKS or _TASKS[key].done():
        _TASKS[key] = asyncio.create_task(_run(owner, key), name="application-core-lifecycle")
