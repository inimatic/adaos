"""Attach a desktop to an already installed Application channel on explicit open."""

from dataclasses import replace
from pathlib import Path

from adaos.domain.application import utc_now
from adaos.services.agent_context import use_ctx
from adaos.services.workspaces.index import get_workspace

from .lifecycle import observe_runtime_admission
from .runtime_channel import RuntimeChannelConflict
from .store import ApplicationRevisionConflict, ApplicationStore
from .trial_runtime import TrialRuntimeUnavailable


def attach_scenario_runtime(ctx, webspace_id: str, scenario_id: str) -> bool:
    """Join the same installed Beta; never select another edition or copy data.

    Home pinning is a presentation operation. Opening its launcher is the point
    at which a previously unused desktop needs an exact runtime placement.
    Temporary Trials remain confined to their explicitly admitted placements.
    """
    with use_ctx(ctx):
        workspace = get_workspace(webspace_id)
    if workspace is None or workspace.is_dev:
        return False
    state = Path(ctx.paths.state_dir())
    store = ApplicationStore(state)
    selections = store.list_runtime_selections()
    owners = {}
    seen = set()
    for selected in selections:
        if selected.application_id in seen:
            continue
        seen.add(selected.application_id)
        if not store.release_has_component(selected.application_id, selected.release_digest,
                                           kind="scenario", artifact_id=scenario_id):
            continue
        application = store.get_application(selected.application_id)
        if any(entry.get("presentation_ref") == f"scenario:{scenario_id}"
               for entry in application.entrypoints):
            owners[selected.application_id] = selected
    if len(owners) > 1:
        raise RuntimeChannelConflict("Ambiguous Application owner for scenario")
    selected = next(iter(owners.values()), None)
    if selected is None or selected.runtime_root_ref == "workspace":
        return False
    if any(item.application_id == selected.application_id and item.webspace_id == webspace_id
           for item in selections):
        return False
    admission = observe_runtime_admission(state, selected, store=store)
    if admission.get("admission") not in {"admitted", "expiring"}:
        raise TrialRuntimeUnavailable(str(admission.get("reason") or "TrialAdmissionUnavailable"))
    if admission.get("admission_kind") != "installed_local_beta":
        raise TrialRuntimeUnavailable("Open the temporary Trial in its admitted desktop")
    target = replace(selected, webspace_id=webspace_id, revision=1, updated_at=utc_now())
    try:
        # The channel's transaction rejects a concurrent cutover: joining a
        # desktop must never switch the edition selected by another desktop.
        store.save_runtime_selection(target, expected_revision=0, join_existing=True)
    except ApplicationRevisionConflict:
        current = store.get_runtime_selection(webspace_id, selected.application_id)
        if ((current.source, current.release_digest, current.runtime_root_ref)
                != (target.source, target.release_digest, target.runtime_root_ref)):
            raise RuntimeChannelConflict("Application channel changed while opening the desktop")
        # Both callers observed a previously unbound desktop. Its old page
        # still needs rebuilding even when the other opener won the CAS.
        return True
    return True
