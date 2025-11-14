from __future__ import annotations

import y_py as Y
from ypy_websocket.ystore import SQLiteYStore

from .seed import SEED
from adaos.services.scenarios.loader import read_content


async def ensure_workspace_seeded_from_scenario(
    ystore: SQLiteYStore, workspace_id: str, default_scenario_id: str = "web_desktop"
) -> None:
    """
    If the YDoc has no ui.application yet, try to seed it from a scenario
    package (.adaos/workspace/scenarios/<id>/scenario.json). If not found or
    invalid, fall back to the static SEED.

    Writes (when seeding from scenario):
      - ui.scenarios.<id>.application
      - registry.scenarios.<id>
      - data.scenarios.<id>.catalog
      - ui.current_scenario (if missing)
    """
    # Ensure the underlying SQLite DB is initialised so read/write work.
    try:
        await ystore.start()
    except Exception:
        # If start fails, leave early; the room can still operate in-memory.
        return

    ydoc = Y.YDoc()
    try:
        await ystore.apply_updates(ydoc)
    except Exception:
        # Treat any read error as "no state yet".
        pass

    ui_map = ydoc.get_map("ui")
    data_map = ydoc.get_map("data")

    # If ui.application already exists, assume workspace is seeded.
    if ui_map.get("application") is not None or len(ui_map) or len(data_map):
        return

    # 1) Try scenario content first.
    content = read_content(default_scenario_id)
    seeded = False
    if content:
        ui_content = (content.get("ui") or {}).get("application") or {}
        registry_content = content.get("registry") or {}
        catalog_content = content.get("catalog") or {}

        with ydoc.begin_transaction() as txn:
            ui = ydoc.get_map("ui")
            registry = ydoc.get_map("registry")
            data = ydoc.get_map("data")

            # ui.scenarios.<id>.application
            scenarios_ui = ui.get("scenarios") or {}
            if not isinstance(scenarios_ui, dict):
                scenarios_ui = {}
            scenarios_ui = dict(scenarios_ui)
            scenarios_ui[default_scenario_id] = {"application": ui_content}
            ui.set(txn, "scenarios", scenarios_ui)

            # ui.current_scenario (only if missing)
            if not ui.get("current_scenario"):
                ui.set(txn, "current_scenario", default_scenario_id)

            # registry.scenarios.<id>
            if registry_content:
                reg_scen = registry.get("scenarios") or {}
                if not isinstance(reg_scen, dict):
                    reg_scen = {}
                reg_scen = dict(reg_scen)
                reg_scen[default_scenario_id] = registry_content
                registry.set(txn, "scenarios", reg_scen)

            # data.scenarios.<id>.catalog
            if catalog_content:
                data_scen = data.get("scenarios") or {}
                if not isinstance(data_scen, dict):
                    data_scen = {}
                data_scen = dict(data_scen)
                entry = data_scen.get(default_scenario_id) or {}
                if not isinstance(entry, dict):
                    entry = {}
                entry = dict(entry)
                entry["catalog"] = catalog_content
                data_scen[default_scenario_id] = entry
                data.set(txn, "scenarios", data_scen)

        seeded = True

    # 2) Fallback: SEED as in Stage A1.
    if not seeded:
        with ydoc.begin_transaction() as txn:
            ui = ydoc.get_map("ui")
            data = ydoc.get_map("data")

            ui.set(txn, "application", SEED["ui"]["application"])
            data.set(txn, "catalog", SEED["data"]["catalog"])
            data.set(txn, "installed", SEED["data"]["installed"])
            data.set(txn, "weather", SEED["data"]["weather"])

    try:
        await ystore.encode_state_as_update(ydoc)
    except Exception:
        pass


async def bootstrap_seed_if_empty(ystore: SQLiteYStore) -> None:
    """
    Backwards-compatible wrapper: seed workspace using the default scenario
    (web_desktop) or SEED if scenario content is not available.
    """
    await ensure_workspace_seeded_from_scenario(ystore, workspace_id="default")
