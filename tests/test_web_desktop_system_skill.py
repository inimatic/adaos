from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from uuid import uuid4


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / ".adaos"
        / "workspace"
        / "skills"
        / "web_desktop_runtime_skill"
        / "handlers"
        / "main.py"
    )
    package_name = f"test_web_desktop_system_{uuid4().hex}"
    package = types.ModuleType(package_name)
    package.__path__ = [str(path.parent.parent)]
    handlers_name = f"{package_name}.handlers"
    handlers = types.ModuleType(handlers_name)
    handlers.__path__ = [str(path.parent)]
    sys.modules[package_name] = package
    sys.modules[handlers_name] = handlers
    module_name = f"{handlers_name}.main"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _snapshot() -> dict:
    return {
        "subject": {
            "id": "node:hub-1",
            "title": "Node0",
            "status": "online",
            "relations": {"subnet": ["subnet:sn-1"]},
        },
        "capacity": {
            "resources": {"active_skill_total": 7, "active_scenario_total": 3}
        },
        "subnet": {"subnet_id": "sn-1", "display_name": "devhub"},
        "members": [{"id": "node:hub-1", "title": "Node0", "status": "online"}],
        "member_summary": {"online": 1, "total": 1},
        "applications": [{"id": "app-1", "title": "Application"}],
        "application_updates": {"available": 0, "total": 1},
        "development_delivery": {"delivered": 2, "accepted": 1},
        "update": {"state": "succeeded", "runtime_channel": "dev"},
        "update_controls": {"core_autoupdate": False},
        "resources": {
            "available": True,
            "freshness": "current",
            "cpu": {"percent": 12.5},
        },
        "incidents": [{"id": "incident-1", "title": "Attention"}],
        "skills": [{"id": "skill-1", "title": "Skill"}],
        "skill_summary": {"installed": 1},
        "activity": [{"id": "activity-1", "title": "Updated"}],
        "technical": {"runtime": {"status": "online"}},
    }


def _run(monkeypatch, section: str):
    module = _load_module()
    requested: list[set[str]] = []
    monkeypatch.setattr(module.sdk_access, "require", lambda _permission: None)

    def get_snapshot(*, sections, **_kwargs):
        requested.append(set(sections))
        return _snapshot()

    monkeypatch.setattr(module.sdk_system, "get_operational_snapshot", get_snapshot)
    return module.get_system_overview(section=section, webspace_id="desktop"), requested[0]


def test_dashboard_projects_the_fields_rendered_by_system_landing(monkeypatch):
    result, requested = _run(monkeypatch, "dashboard")

    assert requested == {"summary", "members", "applications", "development"}
    assert result["subnet"]["display_name"] == "devhub"
    assert result["members"][0]["id"] == "node:hub-1"
    assert result["applications"][0]["id"] == "app-1"
    assert result["development_delivery"]["delivered"] == 2


def test_node_dashboard_keeps_update_hardware_skills_and_attention(monkeypatch):
    result, requested = _run(monkeypatch, "node_dashboard")

    assert requested == {
        "summary",
        "update",
        "applications",
        "resources",
        "skills",
        "incidents",
    }
    assert result["update"]["runtime_channel"] == "dev"
    assert result["update_controls"]["core_autoupdate"] is False
    assert result["resources"]["cpu"]["percent"] == 12.5
    assert result["skills"][0]["id"] == "skill-1"
    assert result["skill_summary"]["installed"] == 1
    assert result["incidents"][0]["id"] == "incident-1"


def test_activity_and_technical_are_not_discarded(monkeypatch):
    activity, activity_requested = _run(monkeypatch, "activity")
    technical, technical_requested = _run(monkeypatch, "technical")

    assert activity_requested == {"summary", "activity"}
    assert activity["activity"][0]["id"] == "activity-1"
    assert technical_requested == {"summary", "technical"}
    assert technical["technical"]["runtime"]["status"] == "online"
