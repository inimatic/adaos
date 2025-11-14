from __future__ import annotations

import time
from typing import Any, Dict, Optional

import y_py as Y
from ypy_websocket.ystore import SQLiteYStore

from adaos.domain import Event
from adaos.services.agent_context import get_ctx
from adaos.apps.yjs.y_store import ystore_path_for_workspace
from .loader import read_manifest, read_content
from . import index as scenario_index
from adaos.services.skills.service import SkillService


class ScenarioService:
    """
    Scenario operations: manifest/content install and Yjs sync.
    """

    def __init__(self, skills: Optional[SkillService] = None) -> None:
        self._skills = skills or SkillService()

    async def install(self, scenario_id: str, workspace_id: str) -> None:
        """
        Install a scenario into the workspace index and ensure its skill
        dependencies are marked as installed.
        """
        manifest = read_manifest(scenario_id)
        name = manifest.get("name") or scenario_id
        version = str(manifest.get("version") or "0.0.0")

        # 1) Install dependent skills (best-effort).
        depends = manifest.get("depends") or []
        if isinstance(depends, (list, tuple)):
            for dep in depends:
                if isinstance(dep, str) and dep:
                    await self._skills.install(dep)

        # 2) Upsert scenario row into sqlite index.
        scenario_index.upsert(workspace_id=workspace_id, scenario_id=name, version=version)

        # 3) Emit event (dev-only).
        try:
            ctx = get_ctx()
            ev = Event(
                type="scenarios.installed",
                payload={"workspace_id": workspace_id, "scenario_id": name, "version": version},
                source="scenarios.service",
                ts=time.time(),
            )
            ctx.bus.publish(ev)
        except Exception:
            pass

    async def _load_ydoc(self, workspace_id: str) -> tuple[SQLiteYStore, Y.YDoc]:
        """
        Helper: open the workspace's Y store and load its YDoc.
        """
        path = ystore_path_for_workspace(workspace_id)
        ystore = SQLiteYStore(str(path))
        try:
            await ystore.start()
        except Exception:
            # If the store cannot start, we still return a fresh in-memory doc.
            pass

        ydoc = Y.YDoc()
        try:
            await ystore.apply_updates(ydoc)
        except Exception:
            # Treat as empty.
            pass
        return ystore, ydoc

    async def sync_to_yjs(self, scenario_id: str, workspace_id: str) -> None:
        """
        Apply scenario.json content into the workspace YDoc under per-scenario
        branches and set ui.current_scenario if missing.
        """
        content = read_content(scenario_id)
        if not content:
            return

        ystore, ydoc = await self._load_ydoc(workspace_id)

        ui_content: Dict[str, Any] = (content.get("ui") or {}).get("application") or {}
        registry_content: Dict[str, Any] = content.get("registry") or {}
        catalog_content: Dict[str, Any] = content.get("catalog") or {}

        with ydoc.begin_transaction() as txn:
            ui = ydoc.get_map("ui")
            registry = ydoc.get_map("registry")
            data = ydoc.get_map("data")

            # ui.scenarios.<id>.application
            scenarios_ui = ui.get("scenarios") or {}
            if not isinstance(scenarios_ui, dict):
                scenarios_ui = {}
            scenarios_ui = dict(scenarios_ui)
            scenarios_ui[scenario_id] = {"application": ui_content}
            ui.set(txn, "scenarios", scenarios_ui)

            # ui.current_scenario (only if empty)
            if not ui.get("current_scenario"):
                ui.set(txn, "current_scenario", scenario_id)

            # registry.scenarios.<id>
            if registry_content:
                reg_scen = registry.get("scenarios") or {}
                if not isinstance(reg_scen, dict):
                    reg_scen = {}
                reg_scen = dict(reg_scen)
                reg_scen[scenario_id] = registry_content
                registry.set(txn, "scenarios", reg_scen)

            # data.scenarios.<id>.catalog (apps/widgets)
            if catalog_content:
                data_scen = data.get("scenarios") or {}
                if not isinstance(data_scen, dict):
                    data_scen = {}
                data_scen = dict(data_scen)
                entry = data_scen.get(scenario_id) or {}
                if not isinstance(entry, dict):
                    entry = {}
                entry = dict(entry)
                entry["catalog"] = catalog_content
                data_scen[scenario_id] = entry
                data.set(txn, "scenarios", data_scen)

        try:
            await ystore.encode_state_as_update(ydoc)
        except Exception:
            pass

        # Emit event: scenarios.synced
        try:
            ctx = get_ctx()
            ev = Event(
                type="scenarios.synced",
                payload={"workspace_id": workspace_id, "scenario_id": scenario_id},
                source="scenarios.service",
                ts=time.time(),
            )
            ctx.bus.publish(ev)
        except Exception:
            pass

    async def set_current(self, scenario_id: str, workspace_id: str) -> None:
        """
        Force ui.current_scenario to a specific scenario id.
        """
        ystore, ydoc = await self._load_ydoc(workspace_id)
        with ydoc.begin_transaction() as txn:
            ui = ydoc.get_map("ui")
            ui.set(txn, "current_scenario", scenario_id)
        try:
            await ystore.encode_state_as_update(ydoc)
        except Exception:
            pass

    async def current(self, workspace_id: str) -> Optional[str]:
        """
        Read ui.current_scenario from Yjs for the given workspace.
        """
        _, ydoc = await self._load_ydoc(workspace_id)
        ui = ydoc.get_map("ui")
        value = ui.get("current_scenario")
        return str(value) if value else None


__all__ = ["ScenarioService"]

