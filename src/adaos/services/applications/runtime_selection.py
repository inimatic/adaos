"""Resolve product execution and launcher identity from persisted selections."""

from pathlib import Path

from adaos.services.agent_context import AgentContext
from adaos.services.applications.store import ApplicationStore
from adaos.services.applications.trial_runtime import NativeTrialRuntime, TrialRuntimeUnavailable


def selection_snapshot(ctx: AgentContext, webspace_id: str) -> list[dict]:
    store = ApplicationStore(Path(ctx.paths.state_dir()))
    return [item.to_dict() for item in store.list_runtime_selections() if item.webspace_id == webspace_id]


def selected_trial(ctx: AgentContext, webspace_id: str, kind: str, component_id: str):
    store = ApplicationStore(Path(ctx.paths.state_dir()))
    matches = []
    for selection in store.list_runtime_selections():
        if selection.webspace_id != webspace_id or selection.runtime_root_ref == "workspace":
            continue
        release = store.get_release(selection.application_id, selection.release_digest)
        if any(item.kind == kind and item.artifact_id == component_id for item in release.project_release.components):
            candidate = selection.runtime_root_ref.removeprefix("trial:")
            if release.accepted_candidate_id != candidate:
                raise TrialRuntimeUnavailable("RuntimeSelection does not match its release Candidate")
            matches.append(NativeTrialRuntime.resolve(ctx, candidate, selection.release_digest))
    if len(matches) > 1:
        raise TrialRuntimeUnavailable("Ambiguous Application runtime selection for component")
    return matches[0] if matches else None


def trial_launcher_entries(ctx: AgentContext, webspace_id: str) -> list[dict]:
    store = ApplicationStore(Path(ctx.paths.state_dir()))
    entries = []
    for selection in store.list_runtime_selections():
        if selection.webspace_id != webspace_id or selection.runtime_root_ref == "workspace":
            continue
        application = store.get_application(selection.application_id)
        release = store.get_release(selection.application_id, selection.release_digest)
        for entrypoint in application.entrypoints:
            kind, component = entrypoint["presentation_ref"].split(":", 1)
            if kind != "scenario":
                continue
            availability = {"status": "ready"}
            try:
                runtime = selected_trial(ctx, webspace_id, kind, component)
                if runtime is None:
                    raise TrialRuntimeUnavailable("Application entrypoint is missing from selected Trial")
                runtime.verified_source(runtime.component(kind, component))
                for package in runtime.packages:
                    if package.kind == "skill":
                        runtime.ready_manager(package.artifact_id)
            except (ValueError, FileNotFoundError, RuntimeError) as exc:
                # Keep other applications usable; opening this selection still fails closed.
                availability = {"status": "unavailable", "reason": str(exc)}
            entries.append({"id": f"scenario:{component}", "scenario_id": component,
                            "title": application.display["title"], "icon": "apps-outline",
                            "release_stage": "beta", "version": release.project_release.version,
                            "application_id": application.application_id,
                            "runtime_selection": selection.to_dict(),
                            "availability": availability,
                            "component_update": {"stage": "beta", "version": release.project_release.version,
                                                 "component": {"type": "scenario", "id": component},
                                                 "candidate": {"id": release.accepted_candidate_id,
                                                               "release_digest": selection.release_digest}}})
    return entries
