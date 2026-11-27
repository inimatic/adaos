"""Reconcile historical restart requests against the selected Application."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping
from zipfile import ZipFile

from adaos.services.applications.runtime_channel import (
    ApplicationRuntimeChannel,
    RUNTIME_TRANSITION_PENDING_MESSAGE,
)


SERVICE_RECOVERY_KIND = "runtime.recovery.service_supervisor_failure"
NO_SERVICE_REASON = "selected_component_has_no_service"


def fenced_restart_skill(action: Mapping[str, Any]) -> str:
    """Identify the historical publisher and the exact fence failure."""
    if action.get("kind") != SERVICE_RECOVERY_KIND:
        return ""
    producer = action.get("producer") or {}
    domain = action.get("domain_ref") or {}
    metadata = action.get("metadata") or {}
    if not all(isinstance(item, Mapping) for item in (producer, domain, metadata)):
        return ""
    if (producer.get("type") == "system" and producer.get("system_id") == "runtime_recovery"
            and domain.get("operation") == "restart_service"
            and metadata.get("source") == "service_supervisor_runtime"
            and metadata.get("error_type") == "RuntimeChannelConflict"
            and metadata.get("error") == RUNTIME_TRANSITION_PENDING_MESSAGE):
        return str(domain.get("skill_name") or "").strip()
    return ""


def _nonservice_evidence(ctx, skill_names: set[str]) -> dict[str, dict[str, str]]:
    from adaos.services.applications.runtime_integrity import runtime_slot_observation
    from adaos.services.applications.store import ApplicationStore
    from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
    from adaos.services.yaml_documents import load_safe_yaml

    state = Path(ctx.paths.state_dir())
    store = ApplicationStore(state)
    packages = ContentAddressedPackageStore(state / "artifact_pipeline/packages")
    owners: dict[str, list[tuple[Any, Any, Any]]] = {name: [] for name in skill_names}
    seen = set()
    for selection in store.list_runtime_selections():
        key = (selection.application_id, selection.release_digest, selection.runtime_root_ref)
        if key in seen:
            continue
        seen.add(key)
        release = store.get_release(selection.application_id, selection.release_digest)
        for package in release.project_release.components:
            if package.kind == "skill" and package.artifact_id in owners:
                owners[package.artifact_id].append((selection, release, package))

    evidence = {}
    for name, matches in owners.items():
        if len(matches) != 1:
            continue
        selection, release, package = matches[0]
        if (selection.source != "stable_installation" or selection.runtime_root_ref != "workspace"
                or ApplicationRuntimeChannel(state, selection.application_id).pending_transitions()):
            continue
        try:
            installation = store.get_installation(selection.application_id)
            if installation.status != "active" or installation.installed_release_digest != selection.release_digest:
                continue
            observation = runtime_slot_observation(Path(ctx.paths.skills_dir()), name)
            if (observation.get("package_digest") != package.digest
                    or observation.get("ready") is not True or observation.get("active") is not True):
                continue
            archive, verified = packages.read_verified(package.digest)
            if verified.ref != package:
                continue
            with ZipFile(BytesIO(archive)) as bundle:
                manifest = load_safe_yaml(bundle.read("skill.yaml").decode("utf-8-sig"))
        except (OSError, ValueError, RuntimeError, KeyError):
            continue
        if not isinstance(manifest, Mapping):
            continue
        runtime = manifest.get("runtime") or {}
        if not isinstance(runtime, Mapping) or runtime.get("kind") == "service":
            continue
        if selection not in store.list_application_runtime_selections(selection.application_id):
            continue
        evidence[name] = {
            "application_id": selection.application_id,
            "release_digest": selection.release_digest,
            "version": release.project_release.version,
            "component_ref": f"skill:{name}",
            "package_digest": package.digest,
        }
    return evidence


async def reconcile_fenced_service_recovery_actions(ctx, *, webspace_id: str, limit: int = 40) -> dict[str, int]:
    """Archive requests that are obsolete for a verified current runtime."""
    from adaos.services.pending_actions import _ctx_node_id, cancel_pending_action_async, list_pending_actions_async

    node_id = _ctx_node_id(ctx)
    result = {"candidates": 0, "archived": 0}
    if not node_id:
        return result
    snapshot = await list_pending_actions_async(webspace_id=webspace_id, include_terminal=False)
    candidates = {}
    for action in snapshot.get("active_items") or []:
        if (action.get("webspace_id") != webspace_id
                or (action.get("owner_scope") or {}).get("node_id") != node_id
                or (action.get("producer") or {}).get("node_id") != node_id):
            continue
        skill_name = fenced_restart_skill(action)
        if skill_name and action.get("id"):
            candidates[action["id"]] = skill_name
        if len(candidates) >= limit:
            break
    result["candidates"] = len(candidates)
    if not candidates:
        return result
    evidence = await asyncio.to_thread(_nonservice_evidence, ctx, set(candidates.values()))
    for action_id, skill_name in candidates.items():
        if skill_name not in evidence:
            continue
        outcome = await cancel_pending_action_async(
            action_id, ctx=ctx, webspace_id=webspace_id, reason=NO_SERVICE_REASON,
            actor={"type": "system", "system_id": "runtime_recovery"}, evidence=evidence[skill_name],
        )
        if not outcome["duplicate"]:
            result["archived"] += 1
    return result
