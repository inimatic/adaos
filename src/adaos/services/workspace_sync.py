from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from adaos.adapters.db import SqliteScenarioRegistry, SqliteSkillRegistry
from adaos.adapters.git.workspace import SparseWorkspace
from adaos.services.git.workspace_guard import ensure_clean
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
    return {
        "scenario_refs": runtime_scenario_refs,
        "scenarios": runtime_scenarios,
        "scenario_skills": runtime_required_skills,
        "unresolved_scenarios": unresolved_runtime_scenarios,
        "missing_scenarios": sorted(set(runtime_scenarios) - materialized_scenarios),
        "missing_skills": sorted(set(runtime_required_skills) - materialized_skills),
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

    for name, entry in materialized_skills.items():
        skill_registry.register(name, active_version=str(entry.get("version") or "").strip() or None)
    for name in sorted(set(current_skills) - set(materialized_skills)):
        skill_registry.unregister(name)

    for name, entry in materialized_scenarios.items():
        scenario_registry.register(name, active_version=str(entry.get("version") or "").strip() or None)
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
        "skills_removed": sorted(set(current_skills) - set(materialized_skills)),
        "scenarios_removed": sorted(set(current_scenarios) - set(materialized_scenarios)),
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
    skill_rows = SqliteSkillRegistry(ctx.sql).list()
    scenario_rows = SqliteScenarioRegistry(ctx.sql).list()
    registry_skills = installed_names(skill_rows)
    registry_scenarios = installed_names(scenario_rows)
    skills, skills_fallback = effective_registry_names(ctx, registry_skills, workspace_root, "skills")
    scenarios, scenarios_fallback = effective_registry_names(ctx, registry_scenarios, workspace_root, "scenarios")
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

    try:
        from adaos.services.git.availability import get_git_availability

        av = get_git_availability(base_dir=ctx.settings.base_dir)
    except Exception:
        av = None

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
        reconcile_result: dict[str, Any] | None = None
        try:
            reconcile_result = reconcile_workspace_db_to_materialized(ctx)
        except Exception as exc:
            errors.append(f"reconcile: {exc}")
        return {
            "ok": len(errors) == 0,
            "mode": "archive",
            "skills": skills,
            "scenarios": scenarios,
            "registry_skills": registry_skills,
            "registry_scenarios": registry_scenarios,
            "runtime_scenario_refs": runtime_scenario_refs,
            "scenario_required_skills": scenario_required_skills,
            "unresolved_runtime_scenarios": unresolved_runtime_scenarios,
            "fallback_used": fallback_used,
            "errors": errors,
            "reconcile": reconcile_result,
            "patterns": desired,
        }

    sparse = SparseWorkspace(ctx.git, workspace_root)
    current = sparse.read_patterns()
    to_remove = [pattern for pattern in current if pattern not in desired]

    ensure_clean(ctx.git, str(workspace_root), desired)
    sparse.update(add=desired, remove=to_remove)
    try:
        ctx.git.pull(str(workspace_root))
    except Exception as exc:
        return {
            "ok": False,
            "skills": skills,
            "scenarios": scenarios,
            "registry_skills": registry_skills,
            "registry_scenarios": registry_scenarios,
            "runtime_scenario_refs": runtime_scenario_refs,
            "scenario_required_skills": scenario_required_skills,
            "unresolved_runtime_scenarios": unresolved_runtime_scenarios,
            "fallback_used": fallback_used,
            "error": str(exc),
            "patterns": desired,
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
            "runtime_scenario_refs": runtime_scenario_refs,
            "scenario_required_skills": scenario_required_skills,
            "unresolved_runtime_scenarios": unresolved_runtime_scenarios,
            "fallback_used": fallback_used,
            "error": f"workspace requirements failed after pull: {exc}",
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
            "runtime_scenario_refs": runtime_scenario_refs,
            "scenario_required_skills": scenario_required_skills,
            "unresolved_runtime_scenarios": unresolved_runtime_scenarios,
            "fallback_used": fallback_used,
            "error": f"workspace reconcile failed after pull: {exc}",
            "patterns": desired,
        }

    return {
        "ok": True,
        "skills": skills,
        "scenarios": scenarios,
        "registry_skills": registry_skills,
        "registry_scenarios": registry_scenarios,
        "runtime_scenario_refs": runtime_scenario_refs,
        "scenario_required_skills": scenario_required_skills,
        "unresolved_runtime_scenarios": unresolved_runtime_scenarios,
        "fallback_used": fallback_used,
        "reconcile": reconcile_result,
        "patterns": desired,
    }


__all__ = [
    "audit_workspace_materialization",
    "effective_registry_names",
    "installed_names",
    "reconcile_workspace_db_to_materialized",
    "resolve_scenario_requirements",
    "runtime_required_scenario_refs",
    "sync_workspace_sparse_to_registry",
    "workspace_kind_names",
]
