from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adaos.adapters.db import SqliteScenarioRegistry, SqliteSkillRegistry
from adaos.adapters.git.workspace import SparseWorkspace
from adaos.services.git.workspace_guard import ensure_clean
from adaos.services.project_deployment.materialization import (
    restore_project_owned_materializations,
)
from adaos.services.skill.runtime_env import SkillRuntimeEnvironment
from adaos.services.workspace_registry import (
    load_workspace_registry,
    rebuild_workspace_registry,
    registry_pattern_set,
    resolve_registry_payload_install_name,
    workspace_registry_is_git_tracked,
    write_workspace_registry,
)


_LOG = logging.getLogger("adaos.workspace_sync")
_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_BOOTSTRAP_SCENARIOS = ("web_desktop",)


def _import_semantic_registry(ctx, workspace_root: Path) -> dict[str, Any]:
    from adaos.services.capability_binding_state.registry_projection import (
        SemanticRegistryProjection,
    )

    state_dir_resolver = getattr(ctx.paths, "state_dir", None)
    state_dir = (
        Path(state_dir_resolver())
        if callable(state_dir_resolver)
        else Path(getattr(ctx.settings, "base_dir")) / ".adaos" / "state"
    )
    config = getattr(ctx, "config", None)
    subnet_id = str(
        getattr(config, "subnet_id_value", None)
        or getattr(config, "subnet_id", None)
        or ""
    ).strip()
    local_publisher_ref = (
        subnet_id if subnet_id.startswith("subnet:") else f"subnet:{subnet_id}"
    ) if subnet_id else None
    return SemanticRegistryProjection(
        registry_root=workspace_root,
        state_dir=state_dir,
    ).import_to_local_catalog(local_publisher_ref=local_publisher_ref)


def _apply_automatic_application_updates(
    ctx,
    semantic_registry: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply safe auto-compatible updates after the registry import commits."""

    if not isinstance(semantic_registry, dict):
        return {
            "schema": "adaos.application.auto_update_run.v1",
            "status": "skipped",
            "reason": "semantic_registry_unavailable",
        }
    application_catalog = semantic_registry.get("application_catalog")
    if (
        not isinstance(application_catalog, dict)
        or application_catalog.get("status") != "imported"
    ):
        return {
            "schema": "adaos.application.auto_update_run.v1",
            "status": "skipped",
            "reason": "application_catalog_unavailable",
        }
    from adaos.services.applications import get_application_service
    from adaos.services.applications.auto_update import ApplicationAutoUpdateService

    state_dir_resolver = getattr(ctx.paths, "state_dir", None)
    state_dir = (
        Path(state_dir_resolver())
        if callable(state_dir_resolver)
        else Path(getattr(ctx.settings, "base_dir")) / ".adaos" / "state"
    )
    authority_state_dir = getattr(ctx, "authority_state_dir", None)
    if authority_state_dir:
        state_dir = Path(authority_state_dir)
    config = getattr(ctx, "config", None)
    subnet_id = str(
        getattr(config, "subnet_id_value", None)
        or getattr(config, "subnet_id", None)
        or ""
    ).strip()
    if not subnet_id:
        return {
            "schema": "adaos.application.auto_update_run.v1",
            "status": "skipped",
            "reason": "subnet_identity_unavailable",
        }
    subnet_ref = subnet_id if subnet_id.startswith("subnet:") else f"subnet:{subnet_id}"
    return ApplicationAutoUpdateService(
        state_dir,
        get_application_service(state_dir),
    ).run(
        subnet_ref=subnet_ref,
        trigger="registry_sync",
        registry_index_digest=str(
            application_catalog.get("index_digest")
            or semantic_registry.get("index_digest")
            or ""
        )
        or None,
    )


def _automatic_application_update_result(
    ctx,
    semantic_registry: dict[str, Any] | None,
) -> dict[str, Any]:
    try:
        return _apply_automatic_application_updates(ctx, semantic_registry)
    except Exception as exc:
        _LOG.warning("automatic Application update failed after registry import", exc_info=True)
        return {
            "schema": "adaos.application.auto_update_run.v1",
            "status": "failed",
            "reason": "auto_update_runner_failed",
            "error": {"type": type(exc).__name__, "message": str(exc)[:500]},
        }


def _reconcile_after_application_auto_update(
    ctx,
    application_auto_update: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Refresh legacy registries after an Application changed the workspace."""

    result = (
        application_auto_update
        if isinstance(application_auto_update, Mapping)
        else {}
    )
    if (
        str(result.get("status") or "").strip().lower() != "completed"
        or int(result.get("applied_count") or 0) <= 0
    ):
        return None
    return reconcile_workspace_db_to_materialized(ctx)


def _environment_type() -> str:
    return str(os.getenv("ENV_TYPE") or os.getenv("ADAOS_ENV_TYPE") or "prod").strip().lower()


def _configured_workspace_branch(ctx) -> str:
    override = str(os.getenv("ADAOS_WORKSPACE_REGISTRY_BRANCH") or "").strip()
    if override:
        return override
    settings = getattr(ctx, "settings", None)
    skills_branch = str(getattr(settings, "skills_monorepo_branch", None) or "").strip()
    scenarios_branch = str(getattr(settings, "scenarios_monorepo_branch", None) or "").strip()
    configured = {value for value in (skills_branch, scenarios_branch) if value}
    if len(configured) > 1:
        raise RuntimeError(
            "shared workspace requires the same skills and scenarios registry branch: "
            f"skills={skills_branch} scenarios={scenarios_branch}"
        )
    return next(iter(configured), "main")


def _align_production_workspace_source(ctx, workspace_root: Path) -> dict[str, Any]:
    if _environment_type() == "dev":
        return {"ok": True, "skipped": True, "reason": "development_workspace"}
    align = getattr(ctx.git, "align_to_remote_branch", None)
    if not callable(align):
        return {"ok": True, "skipped": True, "reason": "git_adapter_unsupported"}
    remote = str(os.getenv("ADAOS_WORKSPACE_SYNC_REMOTE") or "origin").strip() or "origin"
    branch = _configured_workspace_branch(ctx)
    result = align(str(workspace_root), remote=remote, branch=branch)
    return {"ok": True, "skipped": False, **dict(result or {})}


def runtime_required_scenario_refs() -> list[str]:
    """Return scenarios that must remain materialized for runtime webspaces."""

    names = set(_BOOTSTRAP_SCENARIOS)
    try:
        from adaos.services.workspaces.index import list_workspaces

        for workspace in list_workspaces():
            candidates = [
                getattr(workspace, "effective_home_scenario", None),
                getattr(workspace, "current_scenario_overlay", None),
            ]
            home_ref = getattr(workspace, "home_scenario_ref_overlay", None)
            if isinstance(home_ref, dict):
                candidates.append(home_ref.get("scenario_id"))
            for candidate in candidates:
                token = str(candidate or "").strip()
                if token:
                    names.add(token)
    except Exception:
        _LOG.warning("failed to enumerate webspace scenario requirements", exc_info=True)
    return sorted(names)


def resolve_scenario_requirements(
    registry_payload: dict[str, Any],
    scenario_refs: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Resolve scenario aliases and their required skill dependency closure."""

    scenarios: set[str] = set()
    required_skills: set[str] = set()
    unresolved: set[str] = set()
    entries: dict[str, dict[str, Any]] = {}

    for raw in scenario_refs:
        ref = str(raw or "").strip()
        if not ref or not _ARTIFACT_NAME_RE.fullmatch(ref):
            if ref:
                unresolved.add(ref)
            continue
        resolved, entry = resolve_registry_payload_install_name(
            registry_payload,
            kind="scenarios",
            name_or_id=ref,
        )
        resolved = str(resolved or "").strip()
        if not resolved or not _ARTIFACT_NAME_RE.fullmatch(resolved):
            unresolved.add(ref)
            continue
        scenarios.add(resolved)
        if entry is None:
            unresolved.add(ref)
            continue
        entries[resolved] = entry

    for scenario in scenarios:
        entry = entries.get(scenario)
        if not isinstance(entry, dict):
            _, entry = resolve_registry_payload_install_name(
                registry_payload,
                kind="scenarios",
                name_or_id=scenario,
            )
        skills = entry.get("skills") if isinstance(entry, dict) else None
        required = skills.get("required") if isinstance(skills, dict) else None
        if not isinstance(required, list):
            continue
        for raw in required:
            name = str(raw or "").strip()
            if name and _ARTIFACT_NAME_RE.fullmatch(name):
                required_skills.add(name)

    return sorted(scenarios), sorted(required_skills), sorted(unresolved)


def _runtime_requirement_status(
    workspace_root: Path,
    *,
    materialized_skills: set[str],
    materialized_scenarios: set[str],
    registry_payload: dict[str, Any],
) -> dict[str, Any]:
    runtime_scenario_refs = runtime_required_scenario_refs()
    runtime_scenarios, runtime_required_skills, unresolved_runtime_scenarios = resolve_scenario_requirements(
        registry_payload,
        runtime_scenario_refs,
    )
    startup_scenario_refs: list[str] = []
    for raw in runtime_scenario_refs:
        ref = str(raw or "").strip()
        resolved, entry = resolve_registry_payload_install_name(
            registry_payload,
            kind="scenarios",
            name_or_id=ref,
        )
        activation = entry.get("activation") if isinstance(entry, dict) else None
        if ref in _BOOTSTRAP_SCENARIOS or (
            isinstance(activation, dict) and activation.get("startup_allowed") is True
        ):
            startup_scenario_refs.append(str(resolved or ref).strip())
    startup_scenarios, startup_required_skills, unresolved_startup_scenarios = resolve_scenario_requirements(
        registry_payload,
        sorted(set(startup_scenario_refs)),
    )
    return {
        "scenario_refs": runtime_scenario_refs,
        "scenarios": runtime_scenarios,
        "scenario_skills": runtime_required_skills,
        "unresolved_scenarios": unresolved_runtime_scenarios,
        "missing_scenarios": sorted(set(runtime_scenarios) - materialized_scenarios),
        "missing_skills": sorted(set(runtime_required_skills) - materialized_skills),
        "startup_scenario_refs": sorted(set(startup_scenario_refs)),
        "startup_scenarios": startup_scenarios,
        "startup_scenario_skills": startup_required_skills,
        "unresolved_startup_scenarios": unresolved_startup_scenarios,
        "startup_missing_scenarios": sorted(set(startup_scenarios) - materialized_scenarios),
        "startup_missing_skills": sorted(set(startup_required_skills) - materialized_skills),
        "workspace_root": str(workspace_root),
    }


def audit_workspace_materialization(ctx) -> dict[str, Any]:
    """Inspect sparse runtime requirements without mutating SQLite or Git."""

    workspace_root = Path(ctx.paths.workspace_dir())
    materialized_payload = rebuild_workspace_registry(workspace_root)
    materialized_skills = {
        str(entry.get("name") or entry.get("id") or "").strip()
        for entry in (materialized_payload.get("skills") or [])
        if isinstance(entry, dict) and str(entry.get("name") or entry.get("id") or "").strip()
    }
    materialized_scenarios = {
        str(entry.get("name") or entry.get("id") or "").strip()
        for entry in (materialized_payload.get("scenarios") or [])
        if isinstance(entry, dict) and str(entry.get("name") or entry.get("id") or "").strip()
    }
    registry_is_authoritative = workspace_registry_is_git_tracked(workspace_root)
    registry_payload = (
        load_workspace_registry(workspace_root, fallback_to_scan=False)
        if registry_is_authoritative
        else materialized_payload
    )
    return {
        "ok": True,
        "skills": sorted(materialized_skills),
        "scenarios": sorted(materialized_scenarios),
        "registry_authority": "git" if registry_is_authoritative else "materialized_workspace",
        "runtime_requirements": _runtime_requirement_status(
            workspace_root,
            materialized_skills=materialized_skills,
            materialized_scenarios=materialized_scenarios,
            registry_payload=registry_payload,
        ),
    }


def installed_names(rows: list[object]) -> list[str]:
    names: list[str] = []
    for row in rows:
        if not bool(getattr(row, "installed", True)):
            continue
        name = getattr(row, "name", None) or getattr(row, "id", None)
        if not name:
            continue
        names.append(str(name))
    return sorted(set(names))


def selected_runtime_skill_names(ctx) -> list[str]:
    """Return skills with an authoritative selected immutable runtime."""

    try:
        skills_dir_attr = getattr(ctx.paths, "skills_dir", None)
        if skills_dir_attr is None:
            skills_root = Path(ctx.paths.workspace_dir()) / "skills"
        else:
            skills_root = Path(skills_dir_attr() if callable(skills_dir_attr) else skills_dir_attr)
    except Exception:
        return []
    runtime_root = skills_root / ".runtime"
    if not runtime_root.is_dir():
        return []
    names: set[str] = set()
    for skill_root in runtime_root.iterdir():
        if not skill_root.is_dir():
            continue
        name = skill_root.name
        if not _ARTIFACT_NAME_RE.fullmatch(name):
            continue
        env = SkillRuntimeEnvironment(skills_root=skills_root, skill_name=name)
        payload = env.read_runtime_selection()
        version = str(payload.get("version") or "").strip() if payload else ""
        slot = str(payload.get("slot") or payload.get("active_slot") or "").strip().upper() if payload else ""
        if version and slot in {"A", "B"}:
            names.add(name)
            continue

        # Legacy runtimes selected their immutable version and slot with two
        # text markers before current_runtime.json was introduced.
        version_marker = env.active_version_marker()
        if not version_marker.is_file():
            continue
        try:
            legacy_version = str(version_marker.read_text(encoding="utf-8") or "").strip()
            active_marker = env.active_marker(legacy_version)
            legacy_slot = str(active_marker.read_text(encoding="utf-8") or "").strip().upper()
        except Exception:
            continue
        if legacy_version and legacy_slot in {"A", "B"}:
            names.add(name)
    return sorted(names)


def workspace_kind_names(ctx, workspace_root: Path, kind: str) -> list[str]:
    names: set[str] = set()
    prefix = f"{kind}/"
    workspace_root = workspace_root.resolve()

    try:
        sparse = SparseWorkspace(ctx.git, workspace_root)
        for pattern in sparse.read_patterns():
            value = str(pattern or "").strip()
            if not value.startswith(prefix):
                continue
            tail = value[len(prefix) :].strip().strip("/")
            if tail:
                names.add(tail)
    except Exception:
        pass

    try:
        kind_root = workspace_root / kind
        if kind_root.exists():
            for child in kind_root.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    names.add(child.name)
    except Exception:
        pass

    return sorted(names)


def effective_registry_names(ctx, registry_names: list[str], workspace_root: Path, kind: str) -> tuple[list[str], bool]:
    names = sorted(set(str(name) for name in (registry_names or []) if str(name).strip()))
    if names:
        return names, False
    fallback = workspace_kind_names(ctx, workspace_root, kind)
    if fallback:
        return fallback, True
    return [], False


def reconcile_workspace_db_to_materialized(ctx) -> dict[str, Any]:
    workspace_root = Path(ctx.paths.workspace_dir())
    payload = rebuild_workspace_registry(workspace_root)
    registry_is_authoritative = workspace_registry_is_git_tracked(workspace_root)
    if not registry_is_authoritative:
        write_workspace_registry(workspace_root, payload)

    skill_entries = payload.get("skills") if isinstance(payload.get("skills"), list) else []
    scenario_entries = payload.get("scenarios") if isinstance(payload.get("scenarios"), list) else []

    skill_registry = SqliteSkillRegistry(ctx.sql)
    scenario_registry = SqliteScenarioRegistry(ctx.sql)

    current_skills = {str(row.name or "").strip(): row for row in skill_registry.list() if str(getattr(row, "name", "") or "").strip()}
    current_scenarios = {
        str(row.name or "").strip(): row
        for row in scenario_registry.list()
        if str(getattr(row, "name", "") or "").strip()
    }
    selected_runtime_skills = set(selected_runtime_skill_names(ctx))

    materialized_skills: dict[str, dict[str, Any]] = {}
    for entry in skill_entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("id") or "").strip()
        if name:
            materialized_skills[name] = dict(entry)

    materialized_scenarios: dict[str, dict[str, Any]] = {}
    for entry in scenario_entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("id") or "").strip()
        if name:
            materialized_scenarios[name] = dict(entry)

    skills_unchanged: list[str] = []
    skills_registered: list[str] = []
    for name, entry in materialized_skills.items():
        active_version = str(entry.get("version") or "").strip() or None
        current = current_skills.get(name)
        if current is not None and (active_version is None or current.active_version == active_version):
            skills_unchanged.append(name)
            continue
        skill_registry.register(name, active_version=active_version)
        skills_registered.append(name)
    removed_skills = set(current_skills) - set(materialized_skills) - selected_runtime_skills
    for name in sorted(removed_skills):
        skill_registry.unregister(name)

    scenarios_unchanged: list[str] = []
    scenarios_registered: list[str] = []
    for name, entry in materialized_scenarios.items():
        active_version = str(entry.get("version") or "").strip() or None
        current = current_scenarios.get(name)
        if current is not None and (active_version is None or current.active_version == active_version):
            scenarios_unchanged.append(name)
            continue
        scenario_registry.register(name, active_version=active_version)
        scenarios_registered.append(name)
    for name in sorted(set(current_scenarios) - set(materialized_scenarios)):
        scenario_registry.unregister(name)

    try:
        authoritative_registry = (
            load_workspace_registry(workspace_root, fallback_to_scan=False)
            if registry_is_authoritative
            else payload
        )
        runtime_requirements = _runtime_requirement_status(
            workspace_root,
            materialized_skills=set(materialized_skills),
            materialized_scenarios=set(materialized_scenarios),
            registry_payload=authoritative_registry,
        )
    except Exception:
        _LOG.warning("failed to evaluate materialized runtime requirements", exc_info=True)
        runtime_requirements = {
            "scenario_refs": list(_BOOTSTRAP_SCENARIOS),
            "scenarios": [],
            "scenario_skills": [],
            "unresolved_scenarios": [],
            "missing_scenarios": [],
            "missing_skills": [],
            "workspace_root": str(workspace_root),
        }

    return {
        "ok": True,
        "skills": sorted(materialized_skills),
        "scenarios": sorted(materialized_scenarios),
        "skills_registered": sorted(skills_registered),
        "skills_unchanged": sorted(skills_unchanged),
        "skills_removed": sorted(removed_skills),
        "skills_preserved_by_runtime": sorted(
            (set(current_skills) - set(materialized_skills)) & selected_runtime_skills
        ),
        "scenarios_removed": sorted(set(current_scenarios) - set(materialized_scenarios)),
        "scenarios_registered": sorted(scenarios_registered),
        "scenarios_unchanged": sorted(scenarios_unchanged),
        "registry_updated_at": payload.get("updated_at"),
        "registry_persisted": not registry_is_authoritative,
        "registry_authority": "git" if registry_is_authoritative else "materialized_workspace",
        "runtime_requirements": runtime_requirements,
    }


def sync_workspace_sparse_to_registry(ctx) -> dict[str, Any]:
    """
    Skills and scenarios share the same workspace monorepo checkout; sparse
    patterns must be applied as a union, otherwise one sync overwrites the other.
    """

    workspace_root = Path(ctx.paths.workspace_dir())
    source_alignment: dict[str, Any] = {"ok": True, "skipped": True, "reason": "git_unavailable"}
    try:
        from adaos.services.git.availability import get_git_availability

        av = get_git_availability(base_dir=ctx.settings.base_dir)
    except Exception:
        av = None

    if av is None or av.enabled:
        try:
            source_alignment = _align_production_workspace_source(ctx, workspace_root)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"workspace source alignment failed: {exc}",
                "source_alignment": {"ok": False, "error": str(exc)},
            }

    skill_rows = SqliteSkillRegistry(ctx.sql).list()
    scenario_rows = SqliteScenarioRegistry(ctx.sql).list()
    registry_skills = installed_names(skill_rows)
    registry_scenarios = installed_names(scenario_rows)
    selected_runtime_skills = selected_runtime_skill_names(ctx)
    skills, skills_fallback = effective_registry_names(ctx, registry_skills, workspace_root, "skills")
    scenarios, scenarios_fallback = effective_registry_names(ctx, registry_scenarios, workspace_root, "scenarios")
    skills = sorted(set(skills) | set(selected_runtime_skills))
    runtime_scenario_refs = runtime_required_scenario_refs()

    try:
        registry_payload = load_workspace_registry(workspace_root, fallback_to_scan=False)
    except Exception as exc:
        return {
            "ok": False,
            "skills": skills,
            "scenarios": scenarios,
            "registry_skills": registry_skills,
            "registry_scenarios": registry_scenarios,
            "selected_runtime_skills": selected_runtime_skills,
            "runtime_scenario_refs": runtime_scenario_refs,
            "error": f"workspace registry unavailable: {exc}",
        }

    resolved_scenarios, scenario_required_skills, unresolved_runtime_scenarios = resolve_scenario_requirements(
        registry_payload,
        sorted(set(scenarios) | set(runtime_scenario_refs)),
    )
    scenarios = sorted(set(scenarios) | set(resolved_scenarios))
    skills = sorted(set(skills) | set(scenario_required_skills))
    desired = registry_pattern_set([*(f"skills/{n}" for n in skills), *(f"scenarios/{n}" for n in scenarios)])
    fallback_used: dict[str, list[str]] = {}
    if skills_fallback:
        fallback_used["skills"] = skills
    if scenarios_fallback:
        fallback_used["scenarios"] = scenarios

    if av is not None and not av.enabled:
        errors: list[str] = []
        for name in skills:
            try:
                ctx.skills_repo.install(name)
            except Exception as exc:
                errors.append(f"skills/{name}: {exc}")
        for name in scenarios:
            try:
                ctx.scenarios_repo.install(name)
            except Exception as exc:
                errors.append(f"scenarios/{name}: {exc}")
        project_materialization = restore_project_owned_materializations(ctx)
        if project_materialization.get("ok") is not True:
            errors.append("project materialization restore failed")
        reconcile_result: dict[str, Any] | None = None
        try:
            reconcile_result = reconcile_workspace_db_to_materialized(ctx)
        except Exception as exc:
            errors.append(f"reconcile: {exc}")
        semantic_registry: dict[str, Any] | None = None
        try:
            semantic_registry = _import_semantic_registry(ctx, workspace_root)
        except Exception as exc:
            errors.append(f"semantic registry: {exc}")
        application_auto_update = _automatic_application_update_result(
            ctx,
            semantic_registry,
        )
        post_application_reconcile: dict[str, Any] | None = None
        try:
            post_application_reconcile = _reconcile_after_application_auto_update(
                ctx,
                application_auto_update,
            )
        except Exception as exc:
            errors.append(f"post-Application-update reconcile: {exc}")
        return {
            "ok": len(errors) == 0,
            "mode": "archive",
            "skills": skills,
            "scenarios": scenarios,
            "registry_skills": registry_skills,
            "registry_scenarios": registry_scenarios,
            "selected_runtime_skills": selected_runtime_skills,
            "runtime_scenario_refs": runtime_scenario_refs,
            "scenario_required_skills": scenario_required_skills,
            "unresolved_runtime_scenarios": unresolved_runtime_scenarios,
            "fallback_used": fallback_used,
            "project_materialization": project_materialization,
            "errors": errors,
            "reconcile": reconcile_result,
            "semantic_registry": semantic_registry,
            "application_auto_update": application_auto_update,
            "post_application_reconcile": post_application_reconcile,
            "patterns": desired,
            "source_alignment": source_alignment,
        }

    sparse = SparseWorkspace(ctx.git, workspace_root)
    current = sparse.read_patterns()
    to_remove = [pattern for pattern in current if pattern not in desired]

    ensure_clean(ctx.git, str(workspace_root), desired)
    sparse.update(add=desired, remove=to_remove)
    try:
        if bool(source_alignment.get("skipped")):
            ctx.git.pull(str(workspace_root))
    except Exception as exc:
        return {
            "ok": False,
            "skills": skills,
            "scenarios": scenarios,
            "registry_skills": registry_skills,
            "registry_scenarios": registry_scenarios,
            "selected_runtime_skills": selected_runtime_skills,
            "runtime_scenario_refs": runtime_scenario_refs,
            "scenario_required_skills": scenario_required_skills,
            "unresolved_runtime_scenarios": unresolved_runtime_scenarios,
            "fallback_used": fallback_used,
            "error": str(exc),
            "patterns": desired,
            "source_alignment": source_alignment,
        }

    # Pull may advance registry.json. Recompute aliases and dependency closure
    # before reconciling SQLite so newly declared requirements materialize in
    # the same update operation.
    try:
        refreshed_payload = load_workspace_registry(workspace_root, fallback_to_scan=False)
        resolved_scenarios, scenario_required_skills, unresolved_runtime_scenarios = resolve_scenario_requirements(
            refreshed_payload,
            sorted(set(registry_scenarios) | set(scenarios) | set(runtime_scenario_refs)),
        )
        scenarios = sorted(set(scenarios) | set(resolved_scenarios))
        skills = sorted(set(skills) | set(scenario_required_skills))
        desired = registry_pattern_set(
            [*(f"skills/{n}" for n in skills), *(f"scenarios/{n}" for n in scenarios)]
        )
        current = sparse.read_patterns()
        ensure_clean(ctx.git, str(workspace_root), desired)
        sparse.update(
            add=desired,
            remove=[pattern for pattern in current if pattern not in desired],
        )
    except Exception as exc:
        return {
            "ok": False,
            "skills": skills,
            "scenarios": scenarios,
            "registry_skills": registry_skills,
            "registry_scenarios": registry_scenarios,
            "selected_runtime_skills": selected_runtime_skills,
            "runtime_scenario_refs": runtime_scenario_refs,
            "scenario_required_skills": scenario_required_skills,
            "unresolved_runtime_scenarios": unresolved_runtime_scenarios,
            "fallback_used": fallback_used,
            "error": f"workspace requirements failed after pull: {exc}",
            "patterns": desired,
        }

    project_materialization = restore_project_owned_materializations(ctx)
    if project_materialization.get("ok") is not True:
        return {
            "ok": False,
            "skills": skills,
            "scenarios": scenarios,
            "registry_skills": registry_skills,
            "registry_scenarios": registry_scenarios,
            "selected_runtime_skills": selected_runtime_skills,
            "runtime_scenario_refs": runtime_scenario_refs,
            "scenario_required_skills": scenario_required_skills,
            "unresolved_runtime_scenarios": unresolved_runtime_scenarios,
            "fallback_used": fallback_used,
            "project_materialization": project_materialization,
            "error": "project materialization restore failed",
            "patterns": desired,
        }

    try:
        reconcile_result = reconcile_workspace_db_to_materialized(ctx)
    except Exception as exc:
        return {
            "ok": False,
            "skills": skills,
            "scenarios": scenarios,
            "registry_skills": registry_skills,
            "registry_scenarios": registry_scenarios,
            "selected_runtime_skills": selected_runtime_skills,
            "runtime_scenario_refs": runtime_scenario_refs,
            "scenario_required_skills": scenario_required_skills,
            "unresolved_runtime_scenarios": unresolved_runtime_scenarios,
            "fallback_used": fallback_used,
            "error": f"workspace reconcile failed after pull: {exc}",
            "patterns": desired,
        }

    try:
        semantic_registry = _import_semantic_registry(ctx, workspace_root)
    except Exception as exc:
        return {
            "ok": False,
            "skills": skills,
            "scenarios": scenarios,
            "registry_skills": registry_skills,
            "registry_scenarios": registry_scenarios,
            "selected_runtime_skills": selected_runtime_skills,
            "runtime_scenario_refs": runtime_scenario_refs,
            "scenario_required_skills": scenario_required_skills,
            "unresolved_runtime_scenarios": unresolved_runtime_scenarios,
            "fallback_used": fallback_used,
            "error": f"semantic registry import failed after pull: {exc}",
            "patterns": desired,
        }

    application_auto_update = _automatic_application_update_result(
        ctx,
        semantic_registry,
    )
    try:
        post_application_reconcile = _reconcile_after_application_auto_update(
            ctx,
            application_auto_update,
        )
    except Exception as exc:
        return {
            "ok": False,
            "skills": skills,
            "scenarios": scenarios,
            "registry_skills": registry_skills,
            "registry_scenarios": registry_scenarios,
            "selected_runtime_skills": selected_runtime_skills,
            "runtime_scenario_refs": runtime_scenario_refs,
            "scenario_required_skills": scenario_required_skills,
            "unresolved_runtime_scenarios": unresolved_runtime_scenarios,
            "fallback_used": fallback_used,
            "project_materialization": project_materialization,
            "reconcile": reconcile_result,
            "semantic_registry": semantic_registry,
            "application_auto_update": application_auto_update,
            "error": f"workspace reconcile failed after Application update: {exc}",
            "patterns": desired,
            "source_alignment": source_alignment,
        }

    return {
        "ok": True,
        "skills": skills,
        "scenarios": scenarios,
        "registry_skills": registry_skills,
        "registry_scenarios": registry_scenarios,
        "selected_runtime_skills": selected_runtime_skills,
        "runtime_scenario_refs": runtime_scenario_refs,
        "scenario_required_skills": scenario_required_skills,
        "unresolved_runtime_scenarios": unresolved_runtime_scenarios,
        "fallback_used": fallback_used,
        "project_materialization": project_materialization,
        "reconcile": reconcile_result,
        "semantic_registry": semantic_registry,
        "application_auto_update": application_auto_update,
        "post_application_reconcile": post_application_reconcile,
        "patterns": desired,
        "source_alignment": source_alignment,
    }


__all__ = [
    "audit_workspace_materialization",
    "effective_registry_names",
    "installed_names",
    "reconcile_workspace_db_to_materialized",
    "resolve_scenario_requirements",
    "runtime_required_scenario_refs",
    "selected_runtime_skill_names",
    "sync_workspace_sparse_to_registry",
    "workspace_kind_names",
]
