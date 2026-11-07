from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping

import yaml

from adaos.adapters.db import SqliteSkillRegistry
from adaos.domain.workspace_manifest import parse_scenario_skill_bindings
from adaos.services.agent_context import get_ctx
from adaos.services.skill.manager import SkillManager
from adaos.services.skill.declarations import runtime_skill_declarations_snapshot


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def selected_data_binding(
    scenario_id: str,
    *,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    """Return the admitted Builder data adapter without opening its provider."""

    from adaos.services.builder.workflow import BuilderWorkflowService

    state = BuilderWorkflowService.from_context().describe("scenario", scenario_id)
    binding = state.get("data_binding") if isinstance(state, Mapping) else {}
    binding = dict(binding) if isinstance(binding, Mapping) else {}
    profile_id = str(binding.get("selected_profile_id") or "").strip()
    selection_generation = 0
    source_webspace_id = None
    if webspace_id:
        from adaos.services.builder.preview_data_profiles import (
            BuilderPreviewDataProfileService,
        )

        selection = BuilderPreviewDataProfileService.from_context().projection(
            scenario_id,
            webspace_id=webspace_id,
        )
        profile_id = str(selection.get("profile_id") or profile_id).strip()
        selection_generation = max(0, int(selection.get("generation") or 0))
        source_webspace_id = str(selection.get("source_webspace_id") or "").strip() or None
    profile = next(
        (
            dict(item)
            for item in binding.get("profiles") or []
            if isinstance(item, Mapping)
            and str(item.get("profile_id") or "").strip() == profile_id
        ),
        {},
    )
    mode = str(profile.get("mode") or binding.get("selected_mode") or "mock").strip()
    capabilities = [
        str(item).strip()
        for item in profile.get("capabilities") or []
        if str(item).strip()
    ]
    mappings = [
        {
            "logical_ref": str(item.get("logical_ref") or "").strip(),
            "implementation_ref": str(item.get("implementation_ref") or "").strip() or None,
            "status": str(item.get("status") or "missing").strip(),
        }
        for item in profile.get("implementation_mappings") or []
        if isinstance(item, Mapping)
    ]
    external = mode not in {"mock", "fixture"}
    ready = not external or (
        (bool(mappings) or not capabilities)
        and all(
            item["status"] == "mapped" and item["implementation_ref"]
            for item in mappings
        )
    )
    contract = {
        "schema": "adaos.builder.materialized_data_binding.v1",
        "profile_id": profile_id or "default-mock",
        "mode": mode or "mock",
        "generation": max(0, int(binding.get("generation") or 0)),
        "selection_generation": selection_generation,
        "source_webspace_id": source_webspace_id,
        "logical_schema_ref": str(profile.get("logical_schema_ref") or "").strip()
        or None,
        "source_ref": str(profile.get("source_ref") or "").strip() or None,
        "capabilities": capabilities,
        "read_policy": str(profile.get("read_policy") or "").strip() or None,
        "write_policy": str(profile.get("write_policy") or "").strip() or None,
        "adapter": "exact_cbs_mapping" if external else "prototype_data",
        "external_provider_selected": external,
        "provider_opened": False,
        "ready": ready,
        "implementation_mappings": mappings,
    }
    contract["fingerprint"] = _fingerprint(contract)
    return contract


def _scenario_manifest(scenario_id: str) -> dict[str, Any]:
    root = Path(get_ctx().paths.dev_scenarios_dir()) / scenario_id
    for name in ("scenario.yaml", "scenario.yml", "scenario.json"):
        path = root / name
        if not path.is_file():
            continue
        value = (
            json.loads(path.read_text(encoding="utf-8-sig"))
            if path.suffix == ".json"
            else yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        )
        if isinstance(value, Mapping):
            return dict(value)
    raise FileNotFoundError(f"DEV scenario manifest is unavailable: {scenario_id}")


def _available_skill_names() -> tuple[str, ...]:
    paths = get_ctx().paths
    names: set[str] = set()
    for root in (Path(paths.dev_skills_dir()), Path(paths.skills_dir())):
        if not root.is_dir():
            continue
        names.update(
            item.name
            for item in root.iterdir()
            if item.is_dir() and (item / "skill.yaml").is_file()
        )
    return tuple(sorted(names, key=lambda item: (-len(item), item)))


def _implementation_skill_name(ref: str, available: tuple[str, ...]) -> str:
    raw = str(ref or "").strip().removeprefix("skill:")
    for name in available:
        if raw == name or any(raw.startswith(name + separator) for separator in (".", ":", "/", "@")):
            return name
    base = raw.split("@", 1)[0].split(":", 1)[0].split("/", 1)[0].strip()
    return base


def _skill_names(scenario_id: str, binding: Mapping[str, Any]) -> list[str]:
    manifest = _scenario_manifest(scenario_id)
    names = list(parse_scenario_skill_bindings(manifest).required)
    available = _available_skill_names()
    for item in binding.get("implementation_mappings") or []:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "").strip() != "mapped":
            continue
        ref = str(item.get("implementation_ref") or "").strip()
        if not ref.startswith("skill:"):
            continue
        name = _implementation_skill_name(ref, available)
        if name:
            names.append(name)
    return list(dict.fromkeys(str(item).strip() for item in names if str(item).strip()))


def _skill_manager() -> SkillManager:
    ctx = get_ctx()
    return SkillManager(
        repo=ctx.skills_repo,
        registry=SqliteSkillRegistry(ctx.sql),
        git=ctx.git,
        paths=ctx.paths,
        bus=ctx.bus,
        caps=ctx.caps,
    )


def prepare_dev_prototype_dependencies(
    scenario_id: str,
    *,
    webspace_id: str,
) -> dict[str, Any]:
    """Prepare declared DEV skills and pin the selected mock/CBS data adapter.

    Provider preparation is deliberately separate from provider use.  A mock
    or fixture profile never opens a live CBS, even when the corresponding
    implementation is installed locally.
    """

    binding = selected_data_binding(scenario_id, webspace_id=webspace_id)
    if not bool(binding.get("ready")):
        return {
            "ok": False,
            "error": "builder_data_binding_incomplete",
            "scenario_id": scenario_id,
            "webspace_id": webspace_id,
            "data_binding": binding,
            "skills": [],
        }
    manager = _skill_manager()
    dev_skills = Path(get_ctx().paths.dev_skills_dir())
    skills: list[dict[str, Any]] = []
    for name in _skill_names(scenario_id, binding):
        item: dict[str, Any] = {"name": name, "source": None, "ready": False}
        started = time.perf_counter()
        try:
            dev_skill = dev_skills / name
            if dev_skill.is_dir():
                manifest = yaml.safe_load(
                    (dev_skill / "skill.yaml").read_text(encoding="utf-8-sig")
                ) or {}
                declaration = runtime_skill_declarations_snapshot(name)
                slot = None
                if not declaration:
                    slot = manager.admit_dev_catalog_dependency(name)
                item.update(
                    {
                        "source": "dev",
                        "ready": True,
                        "version": str(manifest.get("version") or "").strip() or None,
                        "slot": slot,
                        "admission": (
                            "process_declaration" if declaration else "prepared_runtime"
                        ),
                    }
                )
            else:
                status = manager.runtime_status(name)
                item.update(
                    {
                        "source": "workspace",
                        "ready": bool(status.get("ready")),
                        "version": status.get("version"),
                        "slot": status.get("active_slot"),
                    }
                )
                if not item["ready"]:
                    item["error"] = "declared_dependency_runtime_unavailable"
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        item["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        skills.append(item)
    ok = all(bool(item.get("ready")) for item in skills)
    result = {
        "ok": ok,
        "scenario_id": scenario_id,
        "webspace_id": webspace_id,
        "skills": skills,
        "data_binding": binding,
        "provider_opened": False,
    }
    if not ok:
        result["error"] = "builder_dev_dependency_preflight_failed"
    result["fingerprint"] = _fingerprint(
        {
            "skills": [
                {key: item.get(key) for key in ("name", "source", "version", "slot", "ready")}
                for item in skills
            ],
            "data_binding": binding.get("fingerprint"),
        }
    )
    return result


__all__ = ["prepare_dev_prototype_dependencies", "selected_data_binding"]
