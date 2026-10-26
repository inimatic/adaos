"""Core lease observation and derived-UI recovery, independent of all apps."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore
from .lifecycle import observe_runtime_admission
from .lifecycle_events import LifecycleEvents
from .local_beta_admission import read_local_beta_admission
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
    for selection in store.list_runtime_selections():
        grouped.setdefault(selection.application_id, []).append(selection)
    changed = 0
    for application_id, selections in grouped.items():
        selection = selections[0]
        fact = {"source": selection.source, "release_digest": selection.release_digest,
                "runtime_root_ref": selection.runtime_root_ref,
                "webspace_ids": sorted(s.webspace_id for s in selections),
                "admission": "admitted", "reason": "StableInstallation", "expires_at": None}
        if selection.source == "local_trial":
            try:
                admission = observe_runtime_admission(state, selection, now=observed)
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
