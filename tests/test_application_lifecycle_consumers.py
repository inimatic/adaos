"""Qualify exact consumer sources delivered with the Core lifecycle milestone."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
from uuid import uuid4

import pytest
import yaml

from adaos.services.skill.activation_assessment import inspect_handler_activation, recommended_activation


ROOT = Path(os.environ.get("ADAOS_LIFECYCLE_SOURCE_ROOT") or
            Path(__file__).resolve().parents[1] / ".adaos/dev/sn_6acf0c01")


def walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


@pytest.mark.parametrize("name", ["applications", "web_desktop", "builder"])
def test_consumers_share_exact_core_modal_and_no_authoring_runtime_authority(name):
    model = json.loads((ROOT / "scenarios" / name / "webui.json").read_text(encoding="utf-8"))
    ui = model["ui"]["application"]
    assert "core-application-lifecycle" not in ui["desktop"].get("modals", {})
    modal = ui["modals"]["core-application-lifecycle"]
    tools = {n.get("toolId") or n.get("target") for n in walk(modal)}
    assert {"applications.lifecycle.show", "applications.lifecycle.plan", "applications.lifecycle.trials",
            "applications.lifecycle.operations", "applications.lifecycle.recover", "applications.cancel_operation"} <= tools
    assert not any(str(t).startswith("builder.") for t in tools)
    ids = [w["id"] for w in modal["schema"]["widgets"]]
    assert len(ids) == len(set(ids))
    if name == "applications":
        bindings = next(w for w in ui["desktop"]["pageSchema"]["widgets"] if w["id"] == "application-details")["inputs"]["stateBindings"]
        assert bindings["trialAccepted"]["path"] == "lifecycle.candidate.accepted"
    if name == "web_desktop":
        inventory = next(w for w in ui["desktop"]["pageSchema"]["widgets"] if w["id"] == "system-core-lifecycle-inventory")
        assert "isOwnerOrAdministrator" in inventory["visibleIf"]


@pytest.mark.parametrize("name", ["applications", "web_desktop", "adaos_builder"])
def test_lifecycle_permissions_are_owner_only(name):
    model = yaml.safe_load((ROOT / "projects" / name / "project.yaml").read_text(encoding="utf-8"))
    permissions = {p["id"]:p for p in model["permission_profile"]["required"]}
    for permission in ("applications.plan", "applications.apply", "applications.recover", "applications.develop"):
        assert permissions[permission]["approval_policy"] == "owner_only"
    for role in model.get("application_roles", []):
        if any(p.startswith("applications.") for p in role["grants"]):
            assert role["assignable_to"] == ["owner"]


@pytest.mark.parametrize("name", ["builder_sdk_control_skill", "web_desktop_runtime_skill"])
def test_consumer_activation_assessment_matches_actual_handler(name):
    path = ROOT / "skills" / name
    manifest = yaml.safe_load((path / "skill.yaml").read_text(encoding="utf-8"))
    assert manifest["runtime"]["activation"] == recommended_activation(manifest, inspect_handler_activation(path / "handlers/main.py"))


def test_management_keeps_expired_beta_visible_but_not_launchable():
    path = ROOT / "skills/web_desktop_runtime_skill/handlers/main.py"
    package_name = "lifecycle_consumer_" + uuid4().hex
    for name, directory in [(package_name, path.parent.parent), (package_name + ".handlers", path.parent)]:
        package = types.ModuleType(name)
        package.__path__ = [str(directory)]
        sys.modules[name] = package
    spec = importlib.util.spec_from_file_location(package_name + ".handlers.main", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    result = module._project_application({"application":{"application_id":"example","display":{"title":"Example"}},
        "installed":True, "local_beta_active":True, "installed_release":{"version":"0.1.0"},
        "active_release":{"project_release":{"version":"0.2.0"}},
        "runtime_admission":{"admission":"expired","reason":"TrialExpired","expires_at":"2000-01-01T00:00:00Z"}})
    assert result["installed"] and result["effective_version"] == "0.2.0"
    assert result["launchable"] is False
