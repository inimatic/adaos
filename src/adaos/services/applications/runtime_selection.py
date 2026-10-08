"""Resolve product execution and launcher identity from persisted selections."""

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from adaos.services.agent_context import AgentContext
from adaos.services.applications.store import ApplicationStore
from adaos.services.applications.trial_runtime import NativeTrialRuntime, TrialRuntimeUnavailable


_SELECTED_TRIAL_CACHE_LOCK = threading.Lock()
_SELECTED_TRIAL_CACHE: dict[
    tuple[object, ...],
    tuple[tuple[tuple[str, int, int], ...], NativeTrialRuntime | None],
] = {}
_SELECTED_TRIAL_CACHE_MAX = 128
_TRIAL_SELECTION_FINGERPRINT_TTL_SECONDS = 1.0
_TRIAL_SELECTION_FINGERPRINT_CACHE: dict[
    str, tuple[float, tuple[tuple[str, int, int], ...]]
] = {}
_EXACT_TRIAL_SELECTION_EXPIRES_AT: dict[tuple[object, ...], float | None] = {}
_EXACT_TRIAL_SELECTION_FINGERPRINT_CACHE: dict[
    tuple[object, ...], tuple[float, tuple[tuple[str, int, int], ...]]
] = {}
_SELECTED_APPLICATION_CACHE: dict[
    tuple[object, ...],
    tuple[tuple[tuple[str, int, int], ...], object | None],
] = {}
_APPLICATION_AUTHORITY_INDEX_CACHE: dict[
    tuple[object, ...],
    tuple[
        tuple[tuple[str, int, int], ...],
        dict[tuple[str, str, str], tuple[object, ...]],
        dict[tuple[str, str], tuple[tuple[str, str], ...]],
    ],
] = {}
_APPLICATION_AUTHORITY_INDEX_CACHE_MAX = 16


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
        *root.glob("installations/*/current.json"),
    ]
    rows: list[tuple[str, int, int]] = []
    for path in sorted(paths, key=lambda item: str(item)):
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append((str(path), int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(rows)


def runtime_authority_fingerprint(state_dir: Path) -> str:
    """Fingerprint mutable Application selections used by UI materialization."""

    rows = _trial_selection_fingerprint(Path(state_dir))
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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

        try:
            with use_ctx(ctx):
                workspace = get_workspace(target_webspace)
        except (AttributeError, RuntimeError):
            # Runtime component resolution is also used by bounded recovery and
            # materialization tests whose context deliberately has no SQL
            # catalog. Missing workspace classification must not make stable
            # Application authority unreadable; an explicitly classified DEV
            # workspace still fails closed below.
            workspace = None
        if workspace is not None and workspace.is_dev:
            return None
    state_dir = Path(os.path.abspath(os.fspath(Path(ctx.paths.state_dir()).expanduser())))
    cache_key = (
        id(ctx),
        id(ApplicationStore),
        str(state_dir),
        target_webspace,
        str(kind or "").strip(),
        str(component_id or "").strip(),
    )
    with _SELECTED_TRIAL_CACHE_LOCK:
        state_key = str(state_dir)
        now = time.monotonic()
        fingerprint_entry = _TRIAL_SELECTION_FINGERPRINT_CACHE.get(state_key)
        if (
            fingerprint_entry is not None
            and now - fingerprint_entry[0] <= _TRIAL_SELECTION_FINGERPRINT_TTL_SECONDS
        ):
            fingerprint = fingerprint_entry[1]
        else:
            fingerprint = _trial_selection_fingerprint(state_dir)
            _TRIAL_SELECTION_FINGERPRINT_CACHE[state_key] = (now, fingerprint)
        cached = _SELECTED_APPLICATION_CACHE.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

        index_key = (id(ctx), id(ApplicationStore), str(state_dir))
        authority_index = _APPLICATION_AUTHORITY_INDEX_CACHE.get(index_key)
        if authority_index is None or authority_index[0] != fingerprint:
            store = ApplicationStore(state_dir)
            mutable: dict[tuple[str, str, str], list[object]] = {}
            for selection in store.list_runtime_selections():
                release = store.get_release(selection.application_id, selection.release_digest)
                selection_webspace = str(selection.webspace_id or "").strip()
                for component in release.project_release.components:
                    component_kind = str(component.kind or "").strip()
                    artifact_id = str(component.artifact_id or "").strip()
                    if not component_kind or not artifact_id:
                        continue
                    mutable.setdefault(
                        (selection_webspace, component_kind, artifact_id), []
                    ).append(selection)

            installed_mutable: dict[tuple[str, str], list[tuple[str, str]]] = {}
            list_installations = getattr(store, "list_installations", None)
            installations = list_installations() if callable(list_installations) else ()
            for installation in installations:
                if installation.status != "active":
                    continue
                for item in installation.component_refs:
                    component_ref = str(item.get("component_ref") or "").strip()
                    component_kind, separator, artifact_id = component_ref.partition(":")
                    if not separator or not component_kind or not artifact_id:
                        continue
                    installed_mutable.setdefault(
                        (component_kind, artifact_id), []
                    ).append(
                        (
                            str(installation.application_id or "").strip(),
                            str(installation.installed_release_digest or "").strip(),
                        )
                    )
            selection_index = {
                key: tuple(value) for key, value in mutable.items()
            }
            installation_index = {
                key: tuple(value) for key, value in installed_mutable.items()
            }
            if len(_APPLICATION_AUTHORITY_INDEX_CACHE) >= _APPLICATION_AUTHORITY_INDEX_CACHE_MAX:
                _APPLICATION_AUTHORITY_INDEX_CACHE.pop(
                    next(iter(_APPLICATION_AUTHORITY_INDEX_CACHE))
                )
            authority_index = (fingerprint, selection_index, installation_index)
            _APPLICATION_AUTHORITY_INDEX_CACHE[index_key] = authority_index
        else:
            selection_index = authority_index[1]
            installation_index = authority_index[2]

        if target_webspace:
            matches = list(
                selection_index.get(
                    (target_webspace, str(kind or "").strip(), str(component_id or "").strip()),
                    (),
                )
            )
        else:
            matches = [
                selection
                for (selection_webspace, component_kind, artifact_id), selections in selection_index.items()
                if component_kind == str(kind or "").strip()
                and artifact_id == str(component_id or "").strip()
                for selection in selections
            ]
        if len(matches) > 1:
            raise TrialRuntimeUnavailable(
                "Ambiguous Application runtime selection for component"
            )
        if matches:
            resolved: object | None = matches[0]
        else:
            # Stable compatibility installs intentionally do not require a
            # per-Webspace RuntimeSelection. They still have exact Application
            # and release authority in ApplicationInstallation.
            installed = [
                    InstalledApplicationAuthority(
                        application_id=application_id,
                        release_digest=release_digest,
                        webspace_id=target_webspace,
                    )
                for application_id, release_digest in installation_index.get(
                    (str(kind or "").strip(), str(component_id or "").strip()),
                    (),
                )
            ]
            if len(installed) > 1:
                raise TrialRuntimeUnavailable(
                    "Ambiguous installed Application authority for component"
                )
            resolved = installed[0] if installed else None
        if len(_SELECTED_APPLICATION_CACHE) >= _SELECTED_TRIAL_CACHE_MAX:
            _SELECTED_APPLICATION_CACHE.pop(next(iter(_SELECTED_APPLICATION_CACHE)))
        _SELECTED_APPLICATION_CACHE[cache_key] = (fingerprint, resolved)
        _TRIAL_SELECTION_FINGERPRINT_CACHE[state_key] = (
            time.monotonic(),
            fingerprint,
        )
        return resolved


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
    state_dir = Path(os.path.abspath(os.fspath(Path(ctx.paths.state_dir()).expanduser())))
    cache_key = (
        id(ctx),
        id(ApplicationStore),
        str(state_dir),
        str(webspace_id or "").strip(),
        str(kind or "").strip(),
        str(component_id or "").strip(),
    )
    # Keep the lock through the cold resolution. Page startup asks for several
    # projections concurrently; single-flight here turns N identical catalog
    # scans into one without blocking the asyncio owner thread (callers invoke
    # this function through asyncio.to_thread).
    with _SELECTED_TRIAL_CACHE_LOCK:
        state_key = str(state_dir)
        now = time.monotonic()
        fingerprint_entry = _TRIAL_SELECTION_FINGERPRINT_CACHE.get(state_key)
        if (
            fingerprint_entry is not None
            and now - fingerprint_entry[0] <= _TRIAL_SELECTION_FINGERPRINT_TTL_SECONDS
        ):
            fingerprint = fingerprint_entry[1]
        else:
            fingerprint = _trial_selection_fingerprint(state_dir)
            _TRIAL_SELECTION_FINGERPRINT_CACHE[state_key] = (now, fingerprint)
        cached = _SELECTED_TRIAL_CACHE.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        resolved = _selected_trial_uncached(ctx, webspace_id, kind, component_id)
        if len(_SELECTED_TRIAL_CACHE) >= _SELECTED_TRIAL_CACHE_MAX:
            _SELECTED_TRIAL_CACHE.pop(next(iter(_SELECTED_TRIAL_CACHE)))
        _SELECTED_TRIAL_CACHE[cache_key] = (fingerprint, resolved)
        # Start the short collapse window after the expensive cold resolution,
        # so callers waiting on the single-flight lock reuse the same result.
        _TRIAL_SELECTION_FINGERPRINT_CACHE[state_key] = (time.monotonic(), fingerprint)
        return resolved


def selected_trial_exact(
    ctx: AgentContext,
    webspace_id: str,
    application_id: str,
    release_digest: str,
    kind: str,
    component_id: str,
    workspace_classified: bool = False,
):
    """Resolve an exact Trial authority without scanning every Application.

    Browser materialization already carries the immutable Application release
    identity.  Reading its one runtime channel is both stricter and much
    cheaper than rediscovering component ownership from the full catalog.
    """

    target_webspace = str(webspace_id or "").strip()
    target_application = str(application_id or "").strip()
    target_release = str(release_digest or "").strip()
    if not target_webspace or not target_application or not target_release:
        raise TrialRuntimeUnavailable("Exact Application runtime identity is incomplete")
    state_dir = Path(os.path.abspath(os.fspath(Path(ctx.paths.state_dir()).expanduser())))
    cache_key = (
        "exact",
        id(ctx),
        id(ApplicationStore),
        str(state_dir),
        target_webspace,
        target_application,
        target_release,
        str(kind or "").strip(),
        str(component_id or "").strip(),
        bool(workspace_classified),
    )
    with _SELECTED_TRIAL_CACHE_LOCK:
        # Exact Application identity is shared by every data source on the
        # rendered page.  Keep fingerprinting inside the same single-flight
        # lock as cold resolution and start the collapse window only after the
        # result is ready.  Previously a first-paint burst made every caller
        # walk the same runtime-channel/release files concurrently before any
        # of them could populate the result cache.
        cached = _SELECTED_TRIAL_CACHE.get(cache_key)
        cached_runtime = cached[1] if cached is not None else None
        # A page opens several declarative reads for the same immutable
        # Application authority at once. On Windows each stat may itself wait
        # behind antivirus/SQLite activity, so making every waiter repeat the
        # same path walk turned a cheap cache lookup into seconds of serialized
        # pre-tool latency. Collapse only that first-paint burst; the short
        # window keeps runtime-channel/activation changes promptly visible.
        now = time.monotonic()
        fingerprint_entry = _EXACT_TRIAL_SELECTION_FINGERPRINT_CACHE.get(cache_key)
        if (
            fingerprint_entry is not None
            and now - fingerprint_entry[0] <= _TRIAL_SELECTION_FINGERPRINT_TTL_SECONDS
        ):
            fingerprint = fingerprint_entry[1]
        else:
            fingerprint = _exact_trial_selection_stat_fingerprint(
                state_dir,
                target_webspace,
                target_application,
                target_release,
                cached_runtime,
            )
            _EXACT_TRIAL_SELECTION_FINGERPRINT_CACHE[cache_key] = (now, fingerprint)
        if (
            cached is not None
            and cached[0] == fingerprint
            and (
                _EXACT_TRIAL_SELECTION_EXPIRES_AT.get(cache_key) is None
                or time.time() < float(_EXACT_TRIAL_SELECTION_EXPIRES_AT[cache_key] or 0.0)
            )
        ):
            return cached[1]
        # Revalidate semantic selection and Trial expiry after a channel,
        # release, or activation mutation. The exact stat token makes periodic
        # catalog/SQLite re-reads unnecessary during first paint.
        _exact_trial_selection_fingerprint(
            ctx,
            state_dir,
            target_webspace,
            target_application,
            target_release,
        )
        resolved = _selected_trial_exact_uncached(
            ctx,
            state_dir,
            target_webspace,
            target_application,
            target_release,
            kind,
            component_id,
            workspace_classified,
        )
        if len(_SELECTED_TRIAL_CACHE) >= _SELECTED_TRIAL_CACHE_MAX:
            _SELECTED_TRIAL_CACHE.pop(next(iter(_SELECTED_TRIAL_CACHE)))
        stable_fingerprint = _exact_trial_selection_stat_fingerprint(
            state_dir,
            target_webspace,
            target_application,
            target_release,
            resolved,
        )
        _SELECTED_TRIAL_CACHE[cache_key] = (stable_fingerprint, resolved)
        _EXACT_TRIAL_SELECTION_FINGERPRINT_CACHE[cache_key] = (
            time.monotonic(),
            stable_fingerprint,
        )
        _EXACT_TRIAL_SELECTION_EXPIRES_AT[cache_key] = _exact_trial_expiry_timestamp(
            state_dir,
            resolved,
        )
        return resolved


def _exact_trial_selection_stat_fingerprint(
    state_dir: Path,
    webspace_id: str,
    application_id: str,
    release_digest: str,
    cached_runtime: NativeTrialRuntime | None,
) -> tuple[tuple[str, int, int], ...]:
    """Cheap immediate invalidation token for an exact Application channel."""

    from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel
    from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore

    store = ApplicationStore(state_dir)
    paths: list[tuple[str, Path]] = [
        (
            "channel",
            ApplicationRuntimeChannel(state_dir, application_id).path,
        ),
    ]
    current_path = getattr(store, "_current_path", None)
    if callable(current_path):
        paths.append(
            (
                "legacy_selection",
                current_path("runtime_selections", f"{webspace_id}:{application_id}"),
            )
        )
    release_path = getattr(store, "_release_path", None)
    if callable(release_path):
        paths.append(("release", release_path(application_id, release_digest)))
    if cached_runtime is not None and hasattr(cached_runtime, "candidate_id"):
        paths.append(
            (
                "activation",
                TrialActivationStore(
                    state_dir / "artifact_pipeline" / "trial-activations"
                ).path(cached_runtime.candidate_id),
            )
        )
    rows: list[tuple[str, int, int]] = []
    for label, path in paths:
        try:
            stat = path.stat()
        except OSError:
            rows.append((label, 0, 0))
        else:
            rows.append((label, int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(rows)


def _exact_trial_selection_fingerprint(
    ctx: AgentContext,
    state_dir: Path,
    webspace_id: str,
    application_id: str,
    release_digest: str,
) -> tuple[tuple[str, int, int], ...]:
    """Read only the mutable authority records for one exact Trial.

    The broad fingerprint walks every Application channel. Worse, a channel's
    SQLite mtime changes for ordinary execution leases, so an unrelated tool
    call invalidated every exact Trial cache. The browser already supplied one
    immutable Application identity; a semantic read of that one selection plus
    its release and activation is sufficient and remains cross-process safe.
    """

    from adaos.services.artifact_pipeline.trial_activation import (
        TrialActivationStore,
    )

    store = ApplicationStore(state_dir)
    try:
        selection = store.get_runtime_selection(webspace_id, application_id)
    except FileNotFoundError:
        return (("selection:missing", 0, 0),)
    selection_payload = selection.to_dict()
    selection_digest = hashlib.sha256(
        json.dumps(
            selection_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    runtime_root_ref = str(selection.runtime_root_ref or "").strip()
    # ``workspace`` is the stable runtime root, not a Trial candidate named
    # "workspace".  Treating every non-empty root as a candidate made exact
    # stable Application identities look up a non-existent activation and
    # rejected Builder data sources with ``Trial is missing or inactive``.
    candidate = (
        runtime_root_ref.removeprefix("trial:")
        if runtime_root_ref.startswith("trial:")
        else ""
    )
    rows: list[tuple[str, int, int]] = [
        (f"selection:{selection_digest}", int(selection.revision), 0),
    ]
    if not callable(getattr(store, "_release_path", None)):
        return tuple(rows)

    def append_stat(label: str, path: Path) -> None:
        try:
            stat = path.stat()
        except OSError:
            rows.append((label, 0, 0))
        else:
            rows.append((label, int(stat.st_mtime_ns), int(stat.st_size)))

    append_stat("release", store._release_path(application_id, release_digest))
    if candidate:
        activation_store = TrialActivationStore(
            state_dir / "artifact_pipeline" / "trial-activations"
        )
        activation_path = activation_store.path(candidate)
        append_stat("activation", activation_path)
        activation = activation_store.load(candidate)
        if activation is None or activation.get("status") not in {"active", "completed"}:
            raise TrialRuntimeUnavailable("Trial is missing or inactive")
        expiry = str(activation.get("expires_at") or "").strip()
        if expiry and datetime.fromisoformat(expiry) <= datetime.now(timezone.utc):
            raise TrialRuntimeUnavailable("Trial has expired")
    return tuple(rows)


def _exact_trial_expiry_timestamp(
    state_dir: Path,
    runtime: NativeTrialRuntime | None,
) -> float | None:
    if runtime is None or not hasattr(runtime, "candidate_id"):
        return None
    from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore

    activation = TrialActivationStore(
        state_dir / "artifact_pipeline" / "trial-activations"
    ).load(runtime.candidate_id)
    expiry = str((activation or {}).get("expires_at") or "").strip()
    if not expiry:
        return None
    try:
        return datetime.fromisoformat(expiry).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _selected_trial_exact_uncached(
    ctx: AgentContext,
    state_dir: Path,
    target_webspace: str,
    target_application: str,
    target_release: str,
    kind: str,
    component_id: str,
    workspace_classified: bool,
):
    from adaos.services.agent_context import use_ctx
    from adaos.services.workspaces.index import get_workspace

    if not workspace_classified:
        with use_ctx(ctx):
            workspace = get_workspace(target_webspace)
        if workspace is not None and workspace.is_dev:
            return None
    store = ApplicationStore(state_dir)
    try:
        selection = store.get_runtime_selection(target_webspace, target_application)
    except FileNotFoundError:
        return None
    if selection.release_digest != target_release:
        raise TrialRuntimeUnavailable("Application release is not active in this Webspace")
    if selection.runtime_root_ref == "workspace":
        return None
    release = store.get_release(target_application, target_release)
    if not any(
        item.kind == kind and item.artifact_id == component_id
        for item in release.project_release.components
    ):
        return None
    candidate = selection.runtime_root_ref.removeprefix("trial:")
    if not candidate or release.accepted_candidate_id != candidate:
        raise TrialRuntimeUnavailable("RuntimeSelection does not match its release Candidate")
    return NativeTrialRuntime.resolve(ctx, candidate, target_release)


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
                # The Application identity is also the caller identity for
                # dependency tools. It must not make a shared/stable skill
                # execute inside the caller's Trial channel. Only fence this
                # mismatch when the exact release actually owns the component.
                store = ApplicationStore(state_dir)
                try:
                    verified_release = store.get_release(
                        verified_application_id,
                        verified_release_digest,
                    )
                    release_owns_component = any(
                        item.kind == "skill" and item.artifact_id == skill_name
                        for item in verified_release.project_release.components
                    )
                except Exception:
                    # Fail closed when claimed package authority cannot be
                    # verified; only the explicit non-owner case may fall
                    # through to normal component discovery.
                    release_owns_component = True
                if release_owns_component:
                    raise RuntimeChannelConflict(
                        "This Application runtime is inactive; reopen the selected channel"
                    )
                channel_values = ()
        if channel_values:
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
