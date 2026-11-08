from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

import yaml

from adaos.adapters.db import SqliteSkillRegistry
from adaos.domain.workspace_manifest import parse_scenario_skill_bindings
from adaos.services.agent_context import get_ctx
from adaos.services.skill.manager import SkillManager
from adaos.services.skill.declarations import runtime_skill_declarations_snapshot


_LOG = logging.getLogger("adaos.builder.dev_dependencies")
_BINDING_RECEIPT_SCHEMA = "adaos.builder.materialized_data_binding_receipt.v1"
_BINDING_RECEIPT_LOCK = threading.RLock()


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

    workflow = BuilderWorkflowService.from_context()
    state = workflow.describe("scenario", scenario_id)
    binding = state.get("data_binding") if isinstance(state, Mapping) else {}
    binding = dict(binding) if isinstance(binding, Mapping) else {}
    profile_id = str(binding.get("selected_profile_id") or "").strip()
    selection_generation = 0
    source_webspace_id = None
    if webspace_id:
        from adaos.services.builder.preview_data_profiles import (
            BuilderPreviewDataProfileService,
        )

        selection_service = BuilderPreviewDataProfileService.from_context()
        project_from_state = getattr(
            selection_service,
            "projection_from_workflow_state",
            None,
        )
        if callable(project_from_state):
            selection = project_from_state(
                scenario_id,
                webspace_id=webspace_id,
                workflow_state=state,
            )
        else:  # Compatibility for third-party adapters during the ABI cutover.
            selection = selection_service.projection(
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
        "sensitivity": str(profile.get("sensitivity") or "").strip() or None,
        "redaction": str(profile.get("redaction") or "").strip() or None,
        "expires_at": str(profile.get("expires_at") or "").strip() or None,
        "owner": str(profile.get("owner") or "").strip() or None,
        "adapter": "exact_cbs_mapping" if external else "prototype_data",
        "external_provider_selected": external,
        "provider_opened": False,
        "ready": ready,
        "implementation_mappings": mappings,
    }
    contract["fingerprint"] = _fingerprint(contract)
    return contract


def data_binding_applies_to_skill(
    binding: Mapping[str, Any], skill_name: str
) -> bool:
    """Return whether an admitted profile maps data owned by ``skill_name``."""

    name = str(skill_name or "").strip()
    if not name:
        return False
    for item in binding.get("implementation_mappings") or []:
        if not isinstance(item, Mapping) or str(item.get("status") or "") not in {
            "mapped",
            "fixture_only",
        }:
            continue
        implementation = str(item.get("implementation_ref") or "").strip()
        if not implementation.startswith("skill:"):
            continue
        token = implementation.removeprefix("skill:")
        if token == name or any(
            token.startswith(name + separator) for separator in (":", "/", "@", ".")
        ):
            return True
    return False


def _preview_data_path_token(value: Any) -> str:
    raw = str(value or "").strip()
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-._")[:48] or "default"
    witness = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{witness}"


def _path_stamp(path: Path) -> dict[str, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "ctime_ns": int(stat.st_ctime_ns),
    }


def _binding_receipt_path(state_dir: Path, webspace_id: str) -> Path:
    return (
        state_dir
        / "builder"
        / "workbench"
        / "materialized-data-bindings"
        / f"{_preview_data_path_token(webspace_id)}.json"
    )


def _binding_authority_paths(
    scenario_id: str,
    webspace_id: str,
    *,
    source_webspace_id: str | None = None,
) -> tuple[Path, Path, str]:
    from adaos.services.builder.preview_data_profiles import (
        BuilderPreviewDataProfileService,
    )
    from adaos.services.builder.workflow import BuilderWorkflowService

    workflow = BuilderWorkflowService.from_context()
    workflow_path = workflow._state_path("scenario", scenario_id)
    profiles = BuilderPreviewDataProfileService.from_context()
    # Always resolve the current relationship.  ``source_webspace_id`` is only
    # the receipt's witness; accepting it as input authority would let an old
    # receipt survive a Preview host rebind.
    source = profiles._source(webspace_id)
    return workflow_path, profiles.path(source), source


def remember_materialized_data_binding(
    scenario_id: str,
    *,
    webspace_id: str,
    binding: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Persist the binding admitted by materialization for cheap tool reads."""

    scenario = str(scenario_id or "").strip()
    webspace = str(webspace_id or "").strip()
    if not scenario or not webspace or not isinstance(binding, Mapping):
        return None
    try:
        state_dir = Path(get_ctx().paths.state_dir())
        workflow_path, selection_path, source = _binding_authority_paths(
            scenario,
            webspace,
            source_webspace_id=str(binding.get("source_webspace_id") or "").strip()
            or None,
        )
        receipt = {
            "schema": _BINDING_RECEIPT_SCHEMA,
            "scenario_id": scenario,
            "webspace_id": webspace,
            "source_webspace_id": source,
            "workflow_path": str(workflow_path),
            "workflow_stamp": _path_stamp(workflow_path),
            "selection_path": str(selection_path),
            "selection_stamp": _path_stamp(selection_path),
            "binding": dict(binding),
            "materialized_at": time.time(),
        }
        path = _binding_receipt_path(state_dir, webspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with _BINDING_RECEIPT_LOCK:
            temporary.write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        return receipt
    except Exception:
        _LOG.warning(
            "failed to persist materialized Preview data binding scenario=%s webspace=%s",
            scenario,
            webspace,
            exc_info=True,
        )
        return None


def _read_materialized_data_binding(
    scenario_id: str,
    *,
    webspace_id: str,
) -> dict[str, Any] | None:
    """Read a receipt only while every mutable authority stamp still matches."""

    scenario = str(scenario_id or "").strip()
    webspace = str(webspace_id or "").strip()
    if not scenario or not webspace:
        return None
    try:
        state_dir = Path(get_ctx().paths.state_dir())
        path = _binding_receipt_path(state_dir, webspace)
        with _BINDING_RECEIPT_LOCK:
            receipt = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(receipt, Mapping):
            return None
        if (
            receipt.get("schema") != _BINDING_RECEIPT_SCHEMA
            or str(receipt.get("scenario_id") or "") != scenario
            or str(receipt.get("webspace_id") or "") != webspace
        ):
            return None
        workflow_path = Path(str(receipt.get("workflow_path") or "")).resolve()
        selection_path = Path(str(receipt.get("selection_path") or "")).resolve()
        authority_root = state_dir.expanduser().resolve()
        allowed_workflow_roots = [authority_root]
        try:
            allowed_workflow_roots.append(
                Path(get_ctx().paths.dev_scenarios_dir()).expanduser().resolve()
            )
        except (AttributeError, OSError, TypeError):
            pass
        if not any(
            workflow_path.is_relative_to(root) for root in allowed_workflow_roots
        ):
            return None
        if not selection_path.is_relative_to(authority_root):
            return None
        if _path_stamp(workflow_path) != receipt.get("workflow_stamp"):
            return None
        if _path_stamp(selection_path) != receipt.get("selection_stamp"):
            return None
        binding = receipt.get("binding")
        return dict(binding) if isinstance(binding, Mapping) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def resolve_preview_data_binding(
    ctx: Any,
    *,
    scenario_id: str,
    webspace_id: str,
    skill_name: str,
) -> dict[str, Any] | None:
    """Resolve the Core-owned physical route for one Preview skill.

    The selected profile is Builder workflow state, while the physical root is
    derived by Core.  Neither a scenario handler nor a browser may provide a
    filesystem path.  A binding which is not ready is returned without opening
    an external provider so each transport can project the same typed failure.
    """

    scenario = str(scenario_id or "").strip()
    webspace = str(webspace_id or "").strip()
    skill = str(skill_name or "").strip()
    if not scenario or not webspace or not skill:
        return None
    binding = _read_materialized_data_binding(
        scenario,
        webspace_id=webspace,
    )
    if binding is None:
        binding = selected_data_binding(scenario, webspace_id=webspace)
        remember_materialized_data_binding(
            scenario,
            webspace_id=webspace,
            binding=binding,
        )
    if not data_binding_applies_to_skill(binding, skill):
        return None

    mode = str(binding.get("mode") or "mock").strip()
    data_root: Path | None = None
    runtime_source = "isolated_preview"
    provider_opened = False
    if bool(binding.get("ready")) and mode in {"live_readonly", "live"}:
        from adaos.services.skill.data_paths import resolve_installed_skill_data_root

        data_root = resolve_installed_skill_data_root(ctx, skill)
        runtime_source = "workspace_live"
        provider_opened = True
    elif mode not in {"live_readonly", "live"}:
        state_dir = Path(
            getattr(ctx, "authority_state_dir", None) or ctx.paths.state_dir()
        )
        data_root = (
            state_dir
            / "builder"
            / "preview-data"
            / "v1"
            / _preview_data_path_token(binding.get("source_webspace_id") or webspace)
            / _preview_data_path_token(scenario)
            / _preview_data_path_token(binding.get("profile_id"))
            / _preview_data_path_token(skill)
            / "data"
        )

    verified = dict(binding)
    verified.update(
        {
            "scenario_id": scenario,
            "webspace_id": webspace,
            "runtime_source": runtime_source,
            "provider_opened": provider_opened,
        }
    )
    if data_root is not None:
        verified["_data_root"] = str(data_root.expanduser().resolve())
    return verified


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
    refs = [
        str(item.get("implementation_ref") or "").strip()
        for item in binding.get("implementation_mappings") or []
        if isinstance(item, Mapping)
        and str(item.get("status") or "").strip() == "mapped"
        and str(item.get("implementation_ref") or "").strip().startswith("skill:")
    ]
    available = _available_skill_names() if refs else ()
    for ref in refs:
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


def _reload_dev_skill_handler(
    skills_root: Path,
    skill_name: str,
    *,
    expected_version: str | None,
    expected_slot: str,
) -> dict[str, Any]:
    """Replace the process handler when materialization mounts a new DEV slot.

    Builder automation may prepare and activate a DEV runtime in a worker
    process.  The API process still owns event subscriptions, so seeing an
    active slot on disk is not enough: its handler generation must be mounted
    here as well.  Materialization normally calls this function from its
    filesystem worker thread.  The fallback thread keeps the contract safe for
    diagnostic callers which happen to invoke the synchronous preflight from
    an active event loop.
    """

    from adaos.services.skills_loader_importlib import ImportlibSkillsLoader

    async def reload() -> dict[str, Any]:
        return await ImportlibSkillsLoader().reload_skill_handlers(
            skills_root,
            skill_name,
            expected_version=expected_version,
            expected_slot=expected_slot,
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(reload())
    copied = contextvars.copy_context()
    with ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="builder-dev-handler-mount",
    ) as executor:
        return executor.submit(copied.run, lambda: asyncio.run(reload())).result()


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
    remember_materialized_data_binding(
        scenario_id,
        webspace_id=webspace_id,
        binding=binding,
    )
    if not bool(binding.get("ready")):
        return {
            "ok": False,
            "error": "builder_data_binding_incomplete",
            "scenario_id": scenario_id,
            "webspace_id": webspace_id,
            "data_binding": binding,
            "skills": [],
        }
    manager: SkillManager | None = None
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
                if manager is None:
                    manager = _skill_manager()
                declaration_before = runtime_skill_declarations_snapshot(name)
                slot = manager.mount_dev_runtime(name)
                declaration_after = runtime_skill_declarations_snapshot(name)
                mounted_root = str(declaration_after.get("artifact_root") or "").strip()
                previous_root = str(declaration_before.get("artifact_root") or "").strip()
                reactivation: dict[str, Any] | None = None
                if not mounted_root:
                    raise RuntimeError(
                        f"DEV skill {name!r} did not publish its mounted declaration"
                    )
                if previous_root != mounted_root:
                    reactivation = _reload_dev_skill_handler(
                        dev_skills,
                        name,
                        expected_version=str(manifest.get("version") or "").strip()
                        or None,
                        expected_slot=str(slot or "").strip().upper(),
                    )
                    _LOG.info(
                        "DEV Preview handler mount skill=%s version=%s slot=%s "
                        "ok=%s skipped=%s reason=%s handlers=%s",
                        name,
                        str(manifest.get("version") or "").strip() or "-",
                        str(slot or "").strip().upper() or "-",
                        bool(reactivation.get("ok")),
                        bool(reactivation.get("skipped")),
                        str(reactivation.get("reason") or ""),
                        len(reactivation.get("handlers") or []),
                    )
                    allowed_declaration_only = (
                        bool(reactivation.get("skipped"))
                        and str(reactivation.get("reason") or "")
                        == "reactivation_preflight_no_in_process_handlers"
                    )
                    if not bool(reactivation.get("ok")) and not allowed_declaration_only:
                        raise RuntimeError(
                            str(
                                reactivation.get("reason")
                                or "builder_dev_handler_reactivation_failed"
                            )
                        )
                item.update(
                    {
                        "source": "dev",
                        "ready": True,
                        "version": str(manifest.get("version") or "").strip() or None,
                        "slot": slot,
                        "admission": "process_handler_reactivated"
                        if reactivation is not None
                        else "process_handler_current",
                        "artifact_root": mounted_root,
                        **(
                            {"reactivation": reactivation}
                            if reactivation is not None
                            else {}
                        ),
                    }
                )
            else:
                if manager is None:
                    manager = _skill_manager()
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


__all__ = [
    "data_binding_applies_to_skill",
    "prepare_dev_prototype_dependencies",
    "remember_materialized_data_binding",
    "resolve_preview_data_binding",
    "selected_data_binding",
]
