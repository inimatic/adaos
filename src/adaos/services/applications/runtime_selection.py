"""Resolve product execution and launcher identity from persisted selections."""

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
from pathlib import Path
import threading

from adaos.services.agent_context import AgentContext
from adaos.services.applications.store import ApplicationStore
from adaos.services.applications.trial_runtime import NativeTrialRuntime, TrialRuntimeUnavailable


_SELECTED_TRIAL_CACHE_LOCK = threading.Lock()
_SELECTED_TRIAL_CACHE: dict[
    tuple[int, int, str, str, str, str],
    tuple[tuple[tuple[str, int, int], ...], NativeTrialRuntime | None],
] = {}
_SELECTED_TRIAL_CACHE_MAX = 128


def _trial_selection_fingerprint(state_dir: Path) -> tuple[tuple[str, int, int], ...]:
    """Return a cheap invalidation token for the mutable runtime authority.

    Releases and Trial packages are immutable. Runtime-channel SQLite files and
    legacy RuntimeSelection JSON records are the only mutable inputs needed to
    resolve which Trial owns a component. Statting them avoids repeatedly
    deserializing the complete Application catalog on every WebUI read.
    """

    root = Path(state_dir) / "applications"
    paths = [
        *root.glob("runtime_channels/*.sqlite3"),
        *root.glob("runtime_selections/*/current.json"),
    ]
    rows: list[tuple[str, int, int]] = []
    for path in sorted(paths, key=lambda item: str(item)):
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append((str(path), int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class InstalledApplicationAuthority:
    """Exact stable installation authority for one materialized component."""

    application_id: str
    release_digest: str
    webspace_id: str
    runtime_root_ref: str = "workspace"


def selection_snapshot(ctx: AgentContext, webspace_id: str) -> list[dict]:
    store = ApplicationStore(Path(ctx.paths.state_dir()))
    return [item.to_dict() for item in store.list_runtime_selections() if item.webspace_id == webspace_id]


def selected_application(
    ctx: AgentContext,
    webspace_id: str,
    kind: str,
    component_id: str,
):
    """Resolve one selected Application which delivers a materialized component."""

    target_webspace = str(webspace_id or "").strip()
    if target_webspace:
        from adaos.services.agent_context import use_ctx
        from adaos.services.workspaces.index import get_workspace

        with use_ctx(ctx):
            workspace = get_workspace(target_webspace)
        if workspace is not None and workspace.is_dev:
            return None
    store = ApplicationStore(Path(ctx.paths.state_dir()))
    matches = []
    for selection in store.list_runtime_selections():
        if target_webspace and selection.webspace_id != target_webspace:
            continue
        release = store.get_release(selection.application_id, selection.release_digest)
        if any(
            item.kind == kind and item.artifact_id == component_id
            for item in release.project_release.components
        ):
            matches.append(selection)
    if len(matches) > 1:
        raise TrialRuntimeUnavailable(
            "Ambiguous Application runtime selection for component"
        )
    if matches:
        return matches[0]

    # Stable compatibility installs intentionally do not require a per-Webspace
    # RuntimeSelection.  They still have exact Application and release authority
    # in ApplicationInstallation.  Omitting that authority from the
    # materialization identity made the browser fall back to scenario names and
    # broke Applications whose semantic identity differs from their presentation
    # (for example research_platform -> scenario:research_workbench).  Resolve
    # only one active exact installation and fail closed on conflicting owners.
    installed = []
    for installation in store.list_installations():
        if installation.status != "active":
            continue
        if not any(
            str(item.get("component_ref") or "").strip()
            == f"{kind}:{component_id}"
            for item in installation.component_refs
        ):
            continue
        installed.append(
            InstalledApplicationAuthority(
                application_id=installation.application_id,
                release_digest=installation.installed_release_digest,
                webspace_id=target_webspace,
            )
        )
    if len(installed) > 1:
        raise TrialRuntimeUnavailable(
            "Ambiguous installed Application authority for component"
        )
    return installed[0] if installed else None


def _selected_trial_uncached(ctx: AgentContext, webspace_id: str, kind: str, component_id: str):
    store = ApplicationStore(Path(ctx.paths.state_dir()))
    matches = []
    seen = set()
    for selection in store.list_runtime_selections():
        if selection.runtime_root_ref == "workspace" or selection.application_id in seen:
            continue
        release = store.get_release(selection.application_id, selection.release_digest)
        if any(item.kind == kind and item.artifact_id == component_id for item in release.project_release.components):
            seen.add(selection.application_id)
            candidate = selection.runtime_root_ref.removeprefix("trial:")
            if release.accepted_candidate_id != candidate:
                raise TrialRuntimeUnavailable("RuntimeSelection does not match its release Candidate")
            matches.append((candidate, selection.release_digest))
    if len(matches) > 1:
        raise TrialRuntimeUnavailable("Ambiguous Application runtime selection for component")
    if matches and webspace_id:
        from adaos.services.workspaces.index import get_workspace
        from adaos.services.agent_context import use_ctx

        with use_ctx(ctx):
            workspace = get_workspace(webspace_id)
        if workspace is not None and workspace.is_dev:
            return None
    return NativeTrialRuntime.resolve(ctx, *matches[0]) if matches else None


def selected_trial(ctx: AgentContext, webspace_id: str, kind: str, component_id: str):
    state_dir = Path(ctx.paths.state_dir()).resolve()
    cache_key = (
        id(ctx),
        id(ApplicationStore),
        str(state_dir),
        str(webspace_id or "").strip(),
        str(kind or "").strip(),
        str(component_id or "").strip(),
    )
    fingerprint = _trial_selection_fingerprint(state_dir)
    # Keep the lock through the cold resolution. Page startup asks for several
    # projections concurrently; single-flight here turns N identical catalog
    # scans into one without blocking the asyncio owner thread (callers invoke
    # this function through asyncio.to_thread).
    with _SELECTED_TRIAL_CACHE_LOCK:
        cached = _SELECTED_TRIAL_CACHE.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        resolved = _selected_trial_uncached(ctx, webspace_id, kind, component_id)
        if len(_SELECTED_TRIAL_CACHE) >= _SELECTED_TRIAL_CACHE_MAX:
            _SELECTED_TRIAL_CACHE.pop(next(iter(_SELECTED_TRIAL_CACHE)))
        _SELECTED_TRIAL_CACHE[cache_key] = (fingerprint, resolved)
        return resolved


@contextmanager
def application_execution(ctx: AgentContext, skill_name: str):
    """Fence selected production runtimes for the entire synchronous/awaited call."""
    from .runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict

    state_dir = Path(getattr(ctx, "authority_state_dir", None) or ctx.paths.state_dir())
    # Tool ingress has already resolved and authorized one exact Application
    # release before SkillManager enters this fence.  Reuse that trusted
    # identity instead of scanning every RuntimeSelection and deserializing
    # every ApplicationRelease for every tool invocation.  The exact channel
    # transaction remains authoritative and still fails closed during a
    # pending cutover.  Calls without a verified ingress context (lifecycle
    # hooks, transitions, tests and internal execution) keep the exhaustive
    # discovery path below so newly introduced components are fenced before
    # selection commits.
    try:
        from adaos.services.policy.application import current_application

        verified = current_application() or {}
    except Exception:
        verified = {}
    verified_component = str(verified.get("component_ref") or "").strip()
    verified_application_id = str(verified.get("application_id") or "").strip()
    verified_release_digest = str(verified.get("release_digest") or "").strip()
    verified_runtime_root = str(verified.get("runtime_root_ref") or "").strip()
    if (
        verified_component == f"skill:{skill_name}"
        and verified_application_id
        and verified_release_digest
        and verified_runtime_root
    ):
        channel = ApplicationRuntimeChannel(state_dir, verified_application_id)
        # Do not create a new empty channel from request context.  Nodes still
        # carrying a legacy JSON-only RuntimeSelection must take the migration
        # compatible discovery path once.
        # An empty channel is an authoritative tombstone for obsolete
        # RuntimeSelection projections, but it is not an active execution
        # selection.  Stable Applications can still be installed through the
        # compatibility lifecycle without a per-Webspace selection.  Treating
        # the empty tombstone as an exact channel fenced those installations
        # forever after a rejected first Beta, even after a later Stable
        # release was installed.
        channel_values = channel.read()
        if channel_values:
            actual_root = str(getattr(ctx.paths, "runtime_channel_ref", "workspace"))
            if actual_root != verified_runtime_root:
                raise RuntimeChannelConflict(
                    "This Application runtime is inactive; reopen the selected channel"
                )
            with channel.execution(
                verified_runtime_root,
                verified_release_digest,
            ):
                yield
            return

    store = ApplicationStore(state_dir)
    selected = {}
    selections = store.list_runtime_selections()
    # Newly introduced skills are fenced too, before the new release is selected.
    guarded_selections = (*selections, *ApplicationRuntimeChannel.list_selections(state_dir, include_pending=True))
    for selection in guarded_selections:
        if selection.application_id in selected:
            continue
        release = store.get_release(selection.application_id, selection.release_digest)
        if any(item.kind == "skill" and item.artifact_id == skill_name for item in release.project_release.components):
            selected[selection.application_id] = selection
    with ExitStack() as stack:
        for application_id, selection in sorted(selected.items()):
            actual_root = str(getattr(ctx.paths, "runtime_channel_ref", "workspace"))
            if actual_root != selection.runtime_root_ref:
                raise RuntimeChannelConflict("This Application runtime is inactive; reopen the selected channel")
            stack.enter_context(ApplicationRuntimeChannel(state_dir, application_id).execution(
                selection.runtime_root_ref, selection.release_digest,
                legacy=tuple(item for item in selections if item.application_id == application_id)))
        yield


def trial_launcher_entries(ctx: AgentContext, webspace_id: str) -> list[dict]:
    store = ApplicationStore(Path(ctx.paths.state_dir()))
    entries = []
    for selection in store.list_runtime_selections():
        if selection.webspace_id != webspace_id or selection.runtime_root_ref == "workspace":
            continue
        application = store.get_application(selection.application_id)
        release = store.get_release(selection.application_id, selection.release_digest)
        release_catalog = release.project_release.catalog or {}
        icon = str(release_catalog.get("icon") or "apps-outline").strip() or "apps-outline"
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
                            "title": application.display["title"], "icon": icon,
                            "release_stage": "beta", "version": release.project_release.version,
                            "application_id": application.application_id,
                            "runtime_selection": selection.to_dict(),
                            "availability": availability,
                            "component_update": {"stage": "beta", "version": release.project_release.version,
                                                 "component": {"type": "scenario", "id": component},
                                                 "candidate": {"id": release.accepted_candidate_id,
                                                               "release_digest": selection.release_digest}}})
    return entries
