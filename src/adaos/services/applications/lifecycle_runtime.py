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
from adaos.domain.artifact_release import canonical_payload_digest
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
    applications = {}
    latest_operations = {item['application_id']: item for item in store.latest_operation_summaries()}
    for application in store.list_applications():
        grouped[application.application_id] = []
        applications[application.application_id] = application
    for selection in store.list_runtime_selections():
        grouped.setdefault(selection.application_id, []).append(selection)
    changed = 0
    slot_observations = {}
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
        application = applications.get(application_id)
        fact["application_revision"] = application.revision if application else None
        fact["channel_revision"] = store.get_channels(application_id).get("revision") if application else None
        operation = latest_operations.get(application_id)
        if operation:
            fact['operation'] = {key: operation[key] for key in ('operation_id', 'status', 'revision')}
        fact["selections"] = [{key: getattr(item, key) for key in (
            "webspace_id", "source", "release_digest", "runtime_root_ref", "revision")}
            for item in sorted(selections, key=lambda item: item.webspace_id)]
        if selection and selection.runtime_root_ref == "workspace":
            # The existing invalidation stream must also observe provenance
            # adoption and same-version slot changes. Only bounded slot facts
            # are read here; no package hashing, activation or repair.
            from .runtime_integrity import runtime_slot_observation
            try:
                release = store.get_release(application_id, selection.release_digest).project_release
                skills = {item.artifact_id for item in (*release.components, *release.resolved_dependencies)
                          if item.kind == "skill"}
                for skill in sorted(skills - slot_observations.keys()):
                    slot_observations[skill] = runtime_slot_observation(Path(owner.paths.workspace_dir()) / "skills", skill)
                fact["runtime_generation"] = canonical_payload_digest({skill: slot_observations[skill] for skill in sorted(skills)})
            except (OSError, ValueError, KeyError):
                fact["runtime_generation"] = "unavailable"
        path = request_path(state, application_id)
        if path.is_file():
            materialization = json.loads(path.read_text(encoding="utf-8"))
            fact["materialization"] = materialization.get("status", "unknown")
            fact["materialization_request_digest"] = materialization.get("request_digest")
        changed += LifecycleEvents(state).record(application_id, fact, observed_at=observed.isoformat())
    return {"applications": len(grouped), "changed": changed}


async def _run(owner, key: str):
    try:
        while True:
            try:
                from .finalization_runtime import reconcile_finalizations
                await asyncio.to_thread(reconcile_finalizations, owner)
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
