"""Exact Application package authority, independent of friendly release labels."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Mapping
from zipfile import ZipFile

from adaos.services.applications.store import ApplicationStore
from adaos.services.artifact_pipeline.trial_activation import load_workspace_lock


class ManagedRuntimeConflict(RuntimeError):
    """A standalone skill switch would replace an Application-owned package."""

    code = "skill_runtime_application_release_conflict"


def assert_managed_skill_activation(ctx: Any, skill_id: str, package_digest: str | None) -> None:
    """Allow managed switches only to the committed Workspace closure.

    Normal Application activation commits its WorkspaceLock before refreshing
    handlers. An isolated Trial has its own paths and lock. Neither needs a
    bypass flag; standalone skill activation cannot silently update a release.
    Older installations without a lock retain their installation authority.
    """

    state = Path(ctx.paths.state_dir())
    workspace = Path(ctx.paths.workspace_dir())
    lock = load_workspace_lock(workspace / ".adaos/workspace.lock.json")
    key = f"skill:{skill_id}"
    locked = next((item for item in lock.components if item.key == key), None) if lock else None
    expected = {locked.digest} if locked else set()
    owners: list[str] = []
    # Do not bootstrap stores or infer ownership from a directory/version.
    if (state / "applications/installations").is_dir():
        for installation in ApplicationStore(state).list_installations():
            if installation.status != "active":
                continue
            for component in installation.component_refs:
                if component.get("component_ref") == key:
                    owners.append(installation.application_id)
                    if locked is None:
                        expected.add(str(component.get("package_digest") or ""))
    if (locked is not None or owners) and (
        not package_digest or expected != {package_digest}
    ):
        raise ManagedRuntimeConflict(
            f"{ManagedRuntimeConflict.code}: {key}; activate the exact Application "
            "release or an isolated Trial, not an independent skill package"
        )


def compare_runtime_component(expected: Mapping[str, Any], observed: Mapping[str, Any]) -> dict[str, Any]:
    """A matching version is not proof of a matching package or live handler."""

    identity = {k: expected.get(k) for k in ("kind", "artifact_id", "version", "digest")}
    actual = {k: observed.get(k) for k in (
        "version", "package_digest", "package_manifest_digest", "ready", "active_slot",
        "active", "active_selection_valid", "source_verified",
    )}
    reasons = []
    if not observed:
        reasons.append("runtime_unavailable")
    elif not observed.get("package_digest"):
        reasons.append("package_identity_unknown")
    elif observed["package_digest"] != expected.get("digest"):
        reasons.append("package_digest_mismatch")
    if observed.get("version") and observed["version"] != expected.get("version"):
        reasons.append("version_mismatch")
    if observed.get("ready") is False or observed.get("active") is False:
        reasons.append("runtime_not_ready")
    if observed.get("active_selection_valid") is False:
        reasons.append("runtime_selection_invalid")
    if observed.get("source_verified") is False:
        reasons.append("source_drift")
    unknown = not observed or "package_identity_unknown" in reasons
    return {"component_ref": f"{expected['kind']}:{expected['artifact_id']}",
            "expected": identity, "observed": actual, "reasons": reasons,
            "status": "unknown" if unknown else "drift" if reasons else "aligned"}


def runtime_slot_observation(skills_root: Path, skill_id: str) -> dict[str, Any]:
    """Read slot evidence without importing handlers, preparing, or activating."""

    from adaos.services.skill.runtime_env import SkillRuntimeEnvironment

    env = SkillRuntimeEnvironment(skills_root=skills_root, skill_name=skill_id)
    version = env.resolve_active_version()
    if not version:
        return {}
    slot = env.read_active_slot(version)
    metadata = env.read_version_metadata(version)
    info = metadata.get("slots", {}).get(slot, {})
    paths = env.build_slot_paths(version, slot)
    resolved = Path(info.get("resolved_manifest") or paths.resolved_manifest)
    source = paths.src_dir / "skills" / skill_id
    declaration = source / "skill.yaml"
    actual_version = version
    if declaration.is_file():
        import yaml
        actual_version = str((yaml.safe_load(declaration.read_text(encoding="utf-8-sig")) or {}).get("version") or "")
    deactivation = env.read_deactivation() or {}
    return {"version": actual_version, "package_digest": info.get("package_digest"),
            "package_manifest_digest": info.get("package_manifest_digest"),
            "active_slot": slot, "ready": resolved.is_file() and declaration.is_file(),
            "active": not (deactivation.get("deactivated") and
                           str(deactivation.get("version") or version) == str(version) and
                           str(deactivation.get("slot") or slot) == str(slot))}


def _packaged_files_match(ctx, package, source: Path) -> bool:
    from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore, _canonical_package_bytes
    store = ContentAddressedPackageStore(Path(ctx.paths.state_dir()) / "artifact_pipeline/packages")
    archive, verified = store.read_verified(package.digest)
    if verified.ref != package:
        return False
    with ZipFile(BytesIO(archive)) as bundle:
        for name in verified.file_names:
            target = source / name
            if (not target.resolve().is_relative_to(source.resolve()) or target.is_symlink()
                    or not target.is_file() or _canonical_package_bytes(target.read_bytes()) != bundle.read(name)):
                return False
    return True


def _release_observation(ctx, release, *, root: Path, runtime_root_ref: str, deep: bool):
    import yaml
    from adaos.services.skill.runtime_env import SkillRuntimeEnvironment

    lock = load_workspace_lock(root / ".adaos/workspace.lock.json")
    locked = {item.key: item for item in lock.components} if lock else {}
    rows = []
    for package in release.project_release.components:
        source = root / f"{package.kind}s" / package.artifact_id
        if package.kind == "skill":
            actual = runtime_slot_observation(root / "skills", package.artifact_id)
            if actual:
                env = SkillRuntimeEnvironment(skills_root=root / "skills", skill_name=package.artifact_id)
                bucket = env.resolve_active_version()
                source = env.build_slot_paths(bucket, actual["active_slot"]).src_dir / "skills" / package.artifact_id
        else:
            manifest = source / "scenario.yaml"
            identity = locked.get(package.key)
            actual = {"version": (yaml.safe_load(manifest.read_text(encoding="utf-8-sig")) or {}).get("version")
                      if manifest.is_file() else None,
                      "package_digest": identity.digest if identity else None,
                      "ready": manifest.is_file()}
        if deep:
            try:
                actual["source_verified"] = _packaged_files_match(ctx, package, source)
            except (OSError, ValueError, RuntimeError):
                actual["source_verified"] = False
        rows.append(compare_runtime_component(package.to_dict(), actual))
    status = "drift" if any(row["status"] == "drift" for row in rows) else (
        "unknown" if not rows or any(row["status"] == "unknown" for row in rows) else "aligned")
    return {"version": release.project_release.version, "release_digest": release.release_digest,
            "runtime_root_ref": runtime_root_ref, "status": status, "components": rows,
            "evidence_scope": "package_identity_and_packaged_files" if deep else "slot_package_identity"}


def observe_application_revisions(ctx: Any, application_id: str, *, webspace_id: str = "desktop",
                                  deep: bool = False) -> dict[str, Any]:
    """One read model for Builder and Applications; no refresh/activation/repair."""
    import yaml
    from adaos.services.applications.trial_runtime import NativeTrialRuntime

    store = ApplicationStore(Path(ctx.paths.state_dir()))
    app = store.get_application(application_id)
    selections = [s for s in store.list_runtime_selections() if s.application_id == application_id]
    selected = next((s for s in selections if s.webspace_id == webspace_id), None)
    stable = None
    try:
        installation = store.get_installation(application_id)
    except FileNotFoundError:
        installation = None
    if installation is not None and installation.status == "active":
        stable = _release_observation(ctx, store.get_release(application_id, installation.installed_release_digest),
            root=Path(ctx.paths.workspace_dir()), runtime_root_ref="workspace", deep=deep)
    beta = []
    for candidate_id, digest in sorted({(s.runtime_root_ref.removeprefix("trial:"), s.release_digest)
                                       for s in selections if s.runtime_root_ref.startswith("trial:")}):
        release = store.get_release(application_id, digest)
        try:
            runtime = NativeTrialRuntime.resolve(ctx, candidate_id, digest)
            row = _release_observation(ctx, release, root=runtime.root,
                runtime_root_ref=f"trial:{candidate_id}", deep=deep)
        except (OSError, ValueError, RuntimeError) as exc:
            row = {"version": release.project_release.version, "release_digest": digest,
                   "runtime_root_ref": f"trial:{candidate_id}", "status": "unavailable",
                   "reason": type(exc).__name__, "components": []}
        row["webspace_ids"] = sorted(s.webspace_id for s in selections if s.runtime_root_ref == f"trial:{candidate_id}")
        beta.append(row)
    alpha = None
    dev_method = getattr(ctx.paths, "dev_dir", None)
    if callable(dev_method):
        dev = Path(dev_method())
        manifest = dev / "projects" / app.legacy_project_id / "project.yaml"
        if manifest.is_file():
            project = yaml.safe_load(manifest.read_text(encoding="utf-8-sig")) or {}
            components = []
            for member in project.get("components", {}).get("owned", ()):
                kind, _, name = str(member.get("ref") or "").partition(":")
                if kind not in {"skill", "scenario"} or not name:
                    continue
                path = dev / f"{kind}s" / name / f"{kind}.yaml"
                declaration = yaml.safe_load(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
                components.append({"component_ref": f"{kind}:{name}", "version": (declaration or {}).get("version"),
                                   "status": "source_present" if path.is_file() else "source_missing"})
            alpha = {"version": project.get("version"), "status": "mutable_source",
                     "runtime_root_ref": "development", "components": components}
    active = next((row for row in beta if selected and row["runtime_root_ref"] == selected.runtime_root_ref),
                  stable if selected is None or selected.runtime_root_ref == "workspace" else None)
    channels = store.get_channels(application_id).get("channels") or {}
    return {"schema": "adaos.application.runtime_revisions.v1", "application_id": application_id,
            "webspace_id": webspace_id, "selection": selected.to_dict() if selected else None,
            "status": active["status"] if active else "not_selected", "alpha": alpha, "beta": beta,
            "stable": stable, "channels": channels, "read_only": True,
            "selected_components": active.get("components", []) if active else [],
            "summary": f"Runtime: {active['status'] if active else 'not_selected'}; "
                       f"Alpha: {(alpha or {}).get('version', '—')}; "
                       f"Beta: {', '.join(str(r['version']) for r in beta) or '—'}; "
                       f"Stable: {(stable or {}).get('version', '—')}"}
