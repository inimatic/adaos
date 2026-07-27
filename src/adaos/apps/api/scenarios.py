from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from adaos.apps.api.auth import require_token
from adaos.services.agent_context import get_ctx, AgentContext
from adaos.services.node_config import load_config
from adaos.services.node_display import node_display_from_config, node_display_from_directory_node
from adaos.services.registry.subnet_directory import get_directory
from adaos.services.scenario.manager import (
    ScenarioDependencyLifecycleError,
    ScenarioManager,
    dependency_failure_blocks_scenario_activation,
    dependency_failure_message,
)
from adaos.services.scenario.webspace_runtime import rebuild_webspace_from_sources
from adaos.services.scenarios import loader as scenarios_loader
from adaos.services.workspaces import index as workspace_index
from adaos.adapters.db import SqliteScenarioRegistry
from adaos.services.operations import submit_install_operation, submit_update_operation
from adaos.services.artifact_subscription_update import (
    ArtifactSubscriptionUpdateCoordinator,
    ArtifactSubscriptionUpdateError,
)
from adaos.services.workspace_registry import list_workspace_registry_entries
from adaos.services.yjs.webspace import default_webspace_id


router = APIRouter(tags=["scenarios"], dependencies=[Depends(require_token)])
log = logging.getLogger(__name__)


# --- DI: получаем менеджер так же, как в CLI ---------------------------------
def _get_manager(ctx: AgentContext = Depends(get_ctx)) -> ScenarioManager:
    repo = ctx.scenarios_repo
    reg = SqliteScenarioRegistry(ctx.sql)
    return ScenarioManager(repo=repo, registry=reg, git=ctx.git, paths=ctx.paths, bus=ctx.bus, caps=ctx.caps)


# --- helpers -----------------------------------------------------------------
def _to_mapping(obj: Any) -> Dict[str, Any]:
    # sqlite3.Row, NamedTuple, dataclass, simple objects — мягкая нормализация
    try:
        return dict(obj)
    except Exception:
        pass
    try:
        return obj._asdict()  # type: ignore[attr-defined]
    except Exception:
        pass
    d: Dict[str, Any] = {}
    for k in ("name", "pin", "last_updated", "id", "path", "version", "active_version"):
        if hasattr(obj, k):
            v = getattr(obj, k)
            # id может быть сложным типом
            if k == "id":
                if hasattr(v, "value"):
                    v = getattr(v, "value")
                else:
                    v = str(v)
            d[k] = v
    return d or {"repr": repr(obj)}


def _meta_id(meta: Any) -> str:
    mid = getattr(meta, "id", None)
    if mid is None:
        return str(meta)
    return getattr(mid, "value", str(mid))


def _local_node_id() -> str:
    try:
        conf = load_config()
        node_id = str(getattr(conf, "node_id", "") or "").strip()
        if node_id:
            return node_id
    except Exception:
        pass
    return "hub"


def _local_node_label() -> str:
    try:
        conf = load_config()
        return str(node_display_from_config(conf).get("node_label") or "").strip() or _local_node_id()
    except Exception:
        return _local_node_id()


def _node_label_from_directory(node: Dict[str, Any]) -> str:
    return str(node_display_from_directory_node(node).get("node_label") or "").strip() or str(node.get("node_id") or "").strip() or "hub"


def _ctx_path(ctx: AgentContext, attr_name: str) -> Path | None:
    try:
        attr = getattr(ctx.paths, attr_name, None)
        value = attr() if callable(attr) else attr
        if value:
            return Path(value).expanduser().resolve()
    except Exception:
        return None
    return None


def _repo_workspace_scenarios_root(ctx: AgentContext) -> Path | None:
    try:
        repo_root_attr = getattr(ctx.paths, "repo_root", None)
        repo_root = repo_root_attr() if callable(repo_root_attr) else repo_root_attr
        if not repo_root:
            return None
        candidate = Path(repo_root).expanduser().resolve() / ".adaos" / "workspace" / "scenarios"
        if candidate.exists():
            return candidate
    except Exception:
        return None
    return None


def _workspace_scenario_manifest_exists(ctx: AgentContext, scenario_id: str) -> bool:
    token = str(scenario_id or "").strip()
    if not token:
        return False

    roots: list[Path] = []
    for attr_name in ("scenarios_workspace_dir", "scenarios_dir"):
        root = _ctx_path(ctx, attr_name)
        if root is not None and root not in roots:
            roots.append(root)

    repo_root = _repo_workspace_scenarios_root(ctx)
    if repo_root is not None and repo_root not in roots:
        roots.append(repo_root)

    if not roots:
        return True
    return any((root / token / "scenario.yaml").is_file() for root in roots)


def _workspace_scenario_registry_by_name(ctx: AgentContext) -> dict[str, dict[str, Any]]:
    workspace_root = _ctx_path(ctx, "workspace_dir")
    if workspace_root is None:
        return {}
    try:
        registry_items = list_workspace_registry_entries(workspace_root, kind="scenarios", fallback_to_scan=True)
    except Exception:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for item in registry_items:
        if not isinstance(item, dict):
            continue
        item_name = str(item.get("name") or item.get("id") or "").strip()
        if item_name:
            result[item_name] = item
    return result


def _webspace_uses_dev_scenarios(webspace_id: str | None) -> bool:
    token = str(webspace_id or "").strip()
    if not token:
        return False
    try:
        row = workspace_index.get_workspace(token)
    except Exception:
        return False
    return bool(
        row
        and (
            bool(getattr(row, "is_dev", False))
            or str(getattr(row, "effective_source_mode", "") or "").strip().lower() == "dev"
        )
    )


def _local_dev_scenario_items(
    ctx: AgentContext,
    *,
    node_id: str,
    node_display: Dict[str, Any],
) -> list[Dict[str, Any]]:
    try:
        root = Path(ctx.paths.dev_scenarios_dir())
        entries = sorted((entry for entry in root.iterdir() if entry.is_dir()), key=lambda entry: entry.name.lower())
    except Exception:
        return []

    items: list[Dict[str, Any]] = []
    for entry in entries:
        manifest = scenarios_loader.read_manifest(entry.name, space="dev")
        if not manifest:
            continue
        scenario_id = str(manifest.get("id") or manifest.get("name") or entry.name).strip()
        if not scenario_id:
            continue
        raw_title = manifest.get("title") or manifest.get("display_name") or scenario_id
        title = str(raw_title).strip() if isinstance(raw_title, str) else scenario_id
        items.append(
            {
                "id": scenario_id,
                "name": scenario_id,
                "title": title or scenario_id,
                "version": str(manifest.get("version") or "").strip() or None,
                "updated_at": str(manifest.get("updated_at") or "").strip() or None,
                "path": str(entry),
                "node_id": node_id,
                "node_label": str(node_display.get("node_label") or _local_node_label()),
                "node_compact_label": node_display.get("node_compact_label"),
                "node_index": node_display.get("node_index"),
                "node_color": node_display.get("node_color"),
                "source": "local_dev",
                "source_mode": "dev",
                "space": "dev",
                "dev": True,
            }
        )
    return items


# --- API (тонкий фасад CLI) --------------------------------------------------
class InstallReq(BaseModel):
    name: str
    pin: Optional[str] = None
    async_operation: bool = False
    webspace_id: str | None = None


class UpdateReq(BaseModel):
    name: str
    async_operation: bool = False
    webspace_id: str | None = None
    dry_run: bool = False
    expected_plan_digest: str | None = None
    permission_decision: dict[str, Any] | None = None
    idempotency_key: str | None = None


class PushReq(BaseModel):
    name: str
    message: str
    signoff: bool = False

class UninstallReq(BaseModel):
    name: str
    webspace_id: str | None = None


@router.get("/list")
async def list_scenarios(
    fs: bool = False,
    webspace_id: str | None = None,
    mgr: ScenarioManager = Depends(_get_manager),
    ctx: AgentContext = Depends(get_ctx),
):
    rows = mgr.list_installed()
    items: list[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    positions: dict[tuple[str, str], int] = {}
    local_node_id = _local_node_id()
    local_node_display = node_display_from_config(load_config())
    workspace_registry_by_name = _workspace_scenario_registry_by_name(ctx)
    for row in rows or []:
        item = _to_mapping(row)
        scenario_id = str(item.get("name") or item.get("id") or item.get("repr") or "").strip()
        if not scenario_id:
            continue
        if not _workspace_scenario_manifest_exists(ctx, scenario_id):
            log.error(
                "installed scenario hidden: required declaration is missing name=%s required=scenario.yaml",
                scenario_id,
            )
            continue
        key = (local_node_id, scenario_id)
        if key in seen:
            continue
        seen.add(key)
        registry_meta = workspace_registry_by_name.get(scenario_id)
        item_version = str(
            item.get("version")
            or item.get("active_version")
            or ((registry_meta or {}).get("version") if isinstance(registry_meta, dict) else "")
            or ""
        ).strip()
        item["id"] = scenario_id
        item["name"] = scenario_id
        item["version"] = item_version or None
        item["node_id"] = local_node_id
        item["node_label"] = str(local_node_display.get("node_label") or _local_node_label())
        item["node_compact_label"] = local_node_display.get("node_compact_label")
        item["node_index"] = local_node_display.get("node_index")
        item["node_color"] = local_node_display.get("node_color")
        item["source"] = "local_installed"
        positions[key] = len(items)
        items.append(item)
    if _webspace_uses_dev_scenarios(webspace_id):
        for item in _local_dev_scenario_items(ctx, node_id=local_node_id, node_display=local_node_display):
            scenario_id = str(item.get("id") or "").strip()
            key = (local_node_id, scenario_id)
            if key in positions:
                items[positions[key]] = item
                continue
            seen.add(key)
            positions[key] = len(items)
            items.append(item)
    try:
        conf = load_config()
        if str(getattr(conf, "role", "") or "").strip().lower() == "hub":
            for node in get_directory().list_known_nodes():
                node_id = str(node.get("node_id") or "").strip()
                if not node_id:
                    continue
                node_display = node_display_from_directory_node(node)
                node_label = str(node_display.get("node_label") or _node_label_from_directory(node))
                capacity = node.get("capacity") if isinstance(node.get("capacity"), dict) else {}
                scenarios = capacity.get("scenarios") if isinstance(capacity.get("scenarios"), list) else []
                for scenario in scenarios:
                    if not isinstance(scenario, dict):
                        continue
                    scenario_id = str(scenario.get("name") or scenario.get("id") or "").strip()
                    if not scenario_id:
                        continue
                    key = (node_id, scenario_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    positions[key] = len(items)
                    items.append({
                        **scenario,
                        "id": scenario_id,
                        "name": scenario_id,
                        "node_id": node_id,
                        "node_label": node_label,
                        "node_compact_label": node_display.get("node_compact_label"),
                        "node_index": node_display.get("node_index"),
                        "node_color": node_display.get("node_color"),
                        "source": "subnet_capacity",
                    })
    except Exception:
        pass
    result: Dict[str, Any] = {"items": items}
    if fs:
        present = {_meta_id(m) for m in mgr.list_present()}
        desired = {
            (i.get("name") or i.get("id") or i.get("repr"))
            for i in items
            if i.get("source") != "local_dev"
        }
        missing = sorted(desired - present)
        extra = sorted(present - desired)
        result["fs"] = {
            "present": sorted(present),
            "missing": missing,
            "extra": extra,
        }
    return result


@router.post("/sync")
async def sync(mgr: ScenarioManager = Depends(_get_manager)):
    await asyncio.to_thread(mgr.sync)
    return {"ok": True}


def _install_scenario_sync(body: InstallReq, mgr: ScenarioManager, webspace_id: str) -> Dict[str, Any]:
    try:
        meta = mgr.install_with_deps(body.name, pin=body.pin, webspace_id=webspace_id)
    except ScenarioDependencyLifecycleError as exc:
        raise _dependency_failure_http_exception(exc.result) from exc
    return {
        "ok": True,
        "scenario": {
            "id": _meta_id(meta),
            "version": getattr(meta, "version", None),
            "path": str(getattr(meta, "path", "")),
        },
        "dependency_bootstrap": getattr(mgr, "last_dependency_bootstrap_result", None),
    }


@router.post("/install")
async def install(body: InstallReq, mgr: ScenarioManager = Depends(_get_manager)):
    if body.async_operation:
        operation = submit_install_operation(
            target_kind="scenario",
            target_id=body.name,
            webspace_id=body.webspace_id,
        )
        return {
            "ok": True,
            "accepted": True,
            "operation_id": operation["operation_id"],
            "operation": operation,
        }
    webspace_id = body.webspace_id or default_webspace_id()
    payload = await asyncio.to_thread(_install_scenario_sync, body, mgr, webspace_id)
    try:
        await rebuild_webspace_from_sources(
            webspace_id,
            action="scenario_install_sync",
            scenario_id=body.name,
            source_of_truth="scenario_projection",
        )
    except Exception:
        pass
    return payload


@router.post("/update")
async def update(
    body: UpdateReq,
    mgr: ScenarioManager = Depends(_get_manager),
    ctx: AgentContext = Depends(get_ctx),
):
    coordinator = ArtifactSubscriptionUpdateCoordinator(ctx, scenario_manager=mgr)
    try:
        update_route = coordinator.select_route(body.name)
    except ArtifactSubscriptionUpdateError as exc:
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    if update_route.package_required:
        if body.async_operation:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "artifact_update_async_not_supported",
                    "message": "package activation already has a durable operation journal; review and activate it directly",
                },
            )
        try:
            return await coordinator.update(
                "scenario",
                body.name,
                dry_run=body.dry_run,
                expected_plan_digest=body.expected_plan_digest,
                permission_decision=body.permission_decision,
                idempotency_key=body.idempotency_key,
                webspace_id=body.webspace_id,
            )
        except ArtifactSubscriptionUpdateError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if body.async_operation:
        operation = submit_update_operation(
            target_kind="scenario",
            target_id=body.name,
            webspace_id=body.webspace_id,
        )
        return {
            "ok": True,
            "accepted": True,
            "operation_id": operation["operation_id"],
            "operation": operation,
        }
    webspace_id = body.webspace_id or default_webspace_id()
    mgr.sync()
    try:
        dependency_bootstrap = mgr.bootstrap_dependencies(body.name, webspace_id=webspace_id)
    except Exception as exc:
        dependency_bootstrap = {
            "ok": False,
            "scenario_id": body.name,
            "webspace_id": webspace_id,
            "required": [],
            "items": [],
            "succeeded": [],
            "failed": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    if dependency_failure_blocks_scenario_activation(dependency_bootstrap):
        raise _dependency_failure_http_exception(dependency_bootstrap)
    meta = None
    try:
        for item in list(mgr.list_present() or []):
            if _meta_id(item) == body.name:
                meta = item
                break
    except Exception:
        meta = None
    try:
        mgr.sync_to_yjs(body.name, webspace_id=webspace_id, emit_event=False)
    except Exception:
        pass
    try:
        await rebuild_webspace_from_sources(
            webspace_id,
            action="scenario_update_sync",
            scenario_id=body.name,
            source_of_truth="scenario_projection",
        )
    except Exception:
        pass
    return {
        "ok": True,
        "scenario": {
            "id": _meta_id(meta) if meta is not None else body.name,
            "version": getattr(meta, "version", None),
            "path": str(getattr(meta, "path", "")),
        },
        "dependency_bootstrap": dependency_bootstrap,
        "mode": "legacy_source_pull",
        "update_route": update_route.to_dict(),
        "legacy_materialization": True,
        "warning": "no stable package subscription; compatibility workspace sync was used",
    }


def _dependency_failure_http_exception(result: dict[str, Any] | None) -> HTTPException:
    payload = dict(result or {})
    return HTTPException(
        status_code=409,
        detail={
            "code": "scenario_dependency_lifecycle_failed",
            "message": dependency_failure_message(payload),
            "dependency_bootstrap": payload,
        },
    )


@router.delete("/{name}")
async def remove(name: str, mgr: ScenarioManager = Depends(_get_manager)):
    mgr.uninstall(name)
    try:
        await rebuild_webspace_from_sources(
            default_webspace_id(),
            action="scenario_uninstall_sync",
            source_of_truth="scenario_projection",
        )
    except Exception:
        pass
    return {"ok": True}

@router.post("/uninstall")
async def uninstall(body: UninstallReq, mgr: ScenarioManager = Depends(_get_manager)):
    mgr.uninstall(body.name)
    try:
        await rebuild_webspace_from_sources(
            body.webspace_id or default_webspace_id(),
            action="scenario_uninstall_sync",
            source_of_truth="scenario_projection",
        )
    except Exception:
        pass
    return {"ok": True}


@router.post("/push")
async def push(body: PushReq, mgr: ScenarioManager = Depends(_get_manager)):
    revision = mgr.push(body.name, body.message, signoff=body.signoff)
    return {"ok": True, "revision": revision}
