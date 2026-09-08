from types import SimpleNamespace

import pytest

from adaos.services.scenario import webspace_runtime


@pytest.mark.asyncio
async def test_removed_scenario_recovers_current_and_home_webspaces(monkeypatch) -> None:
    rows = [
        SimpleNamespace(workspace_id="home-missing"),
        SimpleNamespace(workspace_id="current-missing"),
        SimpleNamespace(workspace_id="unaffected"),
    ]
    states = {
        "home-missing": SimpleNamespace(
            current_scenario="working",
            effective_home_scenario="removed",
            source_mode="dev",
        ),
        "current-missing": SimpleNamespace(
            current_scenario="removed",
            effective_home_scenario="home",
            source_mode="dev",
        ),
        "unaffected": SimpleNamespace(
            current_scenario="other",
            effective_home_scenario="other",
            source_mode="dev",
        ),
    }
    manifest_updates = []
    switches = []
    rebuilds = []
    monkeypatch.setattr(webspace_runtime.workspace_index, "list_workspaces", lambda: rows)
    monkeypatch.setattr(
        webspace_runtime.workspace_index,
        "set_workspace_manifest",
        lambda webspace_id, **values: manifest_updates.append((webspace_id, values)),
    )

    async def describe(webspace_id):
        return states[webspace_id]

    async def switch(webspace_id, scenario_id, **options):
        switches.append((webspace_id, scenario_id, options))
        return {"ok": True, "accepted": True}

    async def rebuild(webspace_id, **options):
        rebuilds.append((webspace_id, options))
        return {"ok": True, "accepted": True}

    monkeypatch.setattr(webspace_runtime, "describe_webspace_operational_state", describe)
    monkeypatch.setattr(webspace_runtime, "switch_webspace_scenario", switch)
    monkeypatch.setattr(webspace_runtime, "rebuild_webspace_from_sources", rebuild)
    monkeypatch.setattr(webspace_runtime, "_scenario_exists_for_switch", lambda *_args, **_kwargs: True)

    result = await webspace_runtime._recover_webspaces_after_scenario_removed(
        "removed",
        {},
    )

    assert manifest_updates == [("home-missing", {"home_scenario": "working"})]
    assert [(item[0], item[1]) for item in switches] == [
        ("home-missing", "working"),
        ("current-missing", "home"),
    ]
    assert all(item[2]["request_source"] == "scenario.removed" for item in switches)
    assert rebuilds == [
        (
            "desktop",
            {
                "action": "scenario_uninstall_sync",
                "source_of_truth": "scenario_projection",
            },
        )
    ]
    assert result["ok"] is True
    assert len(result["recovered"]) == 2


@pytest.mark.asyncio
async def test_removed_only_home_uses_builtin_desktop_fallback(monkeypatch) -> None:
    row = SimpleNamespace(workspace_id="orphaned")
    state = SimpleNamespace(
        current_scenario="removed",
        effective_home_scenario="removed",
        source_mode="workspace",
    )
    manifest_updates = []
    switches = []
    rebuilds = []
    monkeypatch.setattr(webspace_runtime.workspace_index, "list_workspaces", lambda: [row])
    monkeypatch.setattr(
        webspace_runtime.workspace_index,
        "set_workspace_manifest",
        lambda webspace_id, **values: manifest_updates.append((webspace_id, values)),
    )
    monkeypatch.setattr(
        webspace_runtime,
        "describe_webspace_operational_state",
        lambda *_args: _async_value(state),
    )
    monkeypatch.setattr(
        webspace_runtime,
        "switch_webspace_scenario",
        lambda webspace_id, scenario_id, **options: _async_value(
            switches.append((webspace_id, scenario_id, options))
            or {"ok": True, "accepted": True}
        ),
    )
    monkeypatch.setattr(
        webspace_runtime,
        "rebuild_webspace_from_sources",
        lambda webspace_id, **options: _async_value(
            rebuilds.append((webspace_id, options))
            or {"ok": True, "accepted": True}
        ),
    )

    result = await webspace_runtime._recover_webspaces_after_scenario_removed("removed", {})

    assert manifest_updates == [("orphaned", {"home_scenario": "web_desktop"})]
    assert switches[0][1] == "web_desktop"
    assert rebuilds[0][0] == "desktop"
    assert result["recovered"][0]["fallback_scenario_id"] == "web_desktop"


async def _async_value(value):
    return value
