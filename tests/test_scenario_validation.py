from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from adaos.services.scenario.validation import validate_scenario_path
from adaos.services.root.service import RootDeveloperService, RootServiceError


def _write_skill(root: Path, name: str, tool: str = "check") -> None:
    target = root / "skills" / name
    target.mkdir(parents=True)
    (target / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "version": "0.1.0",
                "entry": "handlers/main.py",
                "exports": {"tools": [tool]},
                "tools": [{"name": tool, "entry": f"handlers.main:{tool}"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_scenario(root: Path, name: str, *, depends: list[str], route: str) -> Path:
    target = root / "scenarios" / name
    target.mkdir(parents=True)
    (target / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "id": name,
                "version": "0.1.0",
                "depends": depends,
                "steps": [{"name": "run", "call": route}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return target


def _write_toolless_skill(root: Path, name: str) -> None:
    target = root / "skills" / name
    target.mkdir(parents=True)
    (target / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "version": "0.1.0",
                "entry": "handlers/main.py",
                "events": {"subscribe": ["desktop.toggleInstall"]},
                "exports": {"tools": []},
                "tools": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_scenario_validation_resolves_declared_dev_skill_tools(tmp_path: Path) -> None:
    _write_skill(tmp_path, "smoke_skill", "check")
    scenario = _write_scenario(
        tmp_path,
        "smoke",
        depends=["smoke_skill"],
        route="smoke_skill.check",
    )

    report = validate_scenario_path(scenario)

    assert report.ok is True
    assert report.errors == []


def test_scenario_validation_admits_toolless_ui_event_dependency(tmp_path: Path) -> None:
    _write_toolless_skill(tmp_path, "desktop_shell")
    _write_skill(tmp_path, "smoke_skill", "check")
    scenario = _write_scenario(
        tmp_path,
        "smoke",
        depends=["desktop_shell", "smoke_skill"],
        route="smoke_skill.check",
    )

    report = validate_scenario_path(scenario)

    assert report.ok is True
    assert report.errors == []


def test_scenario_validation_rejects_unknown_route_on_toolless_dependency(tmp_path: Path) -> None:
    _write_toolless_skill(tmp_path, "desktop_shell")
    scenario = _write_scenario(
        tmp_path,
        "smoke",
        depends=["desktop_shell"],
        route="desktop_shell.missing",
    )

    report = validate_scenario_path(scenario)

    assert report.ok is False
    assert {issue.code for issue in report.issues} == {"scenario.route.unknown"}


def test_scenario_validation_admits_declared_conversational_package(tmp_path: Path) -> None:
    _write_skill(tmp_path, "smoke_skill", "check")
    scenario = _write_scenario(
        tmp_path,
        "smoke",
        depends=["smoke_skill"],
        route="smoke_skill.check",
    )
    manifest_path = scenario / "scenario.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["conversational"] = {"manifest": "conversational/manifest.yaml"}
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    report = validate_scenario_path(scenario)

    assert report.ok is False
    assert "conversational.manifest.missing" in {issue.code for issue in report.issues}


def test_scenario_validation_rejects_undeclared_or_missing_routes(tmp_path: Path) -> None:
    scenario = _write_scenario(
        tmp_path,
        "broken",
        depends=["missing_skill"],
        route="missing_skill.check",
    )

    report = validate_scenario_path(scenario)

    assert report.ok is False
    assert {issue.code for issue in report.issues} == {
        "scenario.dependency.missing",
        "scenario.route.unknown",
    }


def test_scenario_validation_rejects_legacy_scenario_json_manifest(tmp_path: Path) -> None:
    scenario = tmp_path / "scenarios" / "legacy_manifest"
    scenario.mkdir(parents=True)
    (scenario / "scenario.json").write_text(
        json.dumps({"id": "legacy_manifest", "version": "0.1.0", "steps": []}),
        encoding="utf-8",
    )

    report = validate_scenario_path(scenario)

    assert report.ok is False
    assert {issue.code for issue in report.issues} == {"scenario.manifest.invalid"}


def test_root_push_preflight_uses_dependency_aware_scenario_validation(tmp_path: Path) -> None:
    _write_skill(tmp_path, "smoke_skill", "check")
    valid = _write_scenario(
        tmp_path,
        "valid",
        depends=["smoke_skill"],
        route="smoke_skill.check",
    )
    invalid = _write_scenario(
        tmp_path,
        "invalid",
        depends=["smoke_skill"],
        route="smoke_skill.missing",
    )
    service = object.__new__(RootDeveloperService)
    service.ctx = SimpleNamespace(paths=SimpleNamespace(skills_dir=lambda: tmp_path / "public-skills"))

    service._validate_artifact_preflight("scenarios", "valid", valid)
    try:
        service._validate_artifact_preflight("scenarios", "invalid", invalid)
    except RootServiceError as exc:
        assert "scenario.route.unknown" in str(exc)
    else:
        raise AssertionError("invalid scenario route must block push preflight")


def test_scenario_validation_cross_checks_webui_skill_data_sources(tmp_path: Path) -> None:
    _write_skill(tmp_path, "smoke_skill", "check")
    scenario = _write_scenario(
        tmp_path,
        "webui_broken",
        depends=["smoke_skill"],
        route="smoke_skill.check",
    )
    (scenario / "webui.json").write_text(
        json.dumps(
            {
                "widgets": [
                    {
                        "id": "status",
                        "type": "item.details",
                        "dataSource": {"kind": "skill", "name": "smoke_skill.missing"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = validate_scenario_path(scenario)

    assert report.ok is False
    assert "scenario.webui.skill_tool_unknown" in {issue.code for issue in report.issues}


def test_scenario_validation_reports_implicit_browser_read_policy(tmp_path: Path) -> None:
    _write_skill(tmp_path, "smoke_skill", "check")
    skill_manifest = tmp_path / "skills" / "smoke_skill" / "skill.yaml"
    skill = yaml.safe_load(skill_manifest.read_text(encoding="utf-8"))
    skill["data_routes"] = [
        {
            "surface": "widget:status",
            "route": "tool/details",
            "tool": "check",
        }
    ]
    skill_manifest.write_text(yaml.safe_dump(skill, sort_keys=False), encoding="utf-8")
    scenario = _write_scenario(
        tmp_path,
        "webui_policy",
        depends=["smoke_skill"],
        route="smoke_skill.check",
    )
    (scenario / "webui.json").write_text(
        json.dumps(
            {
                "widgets": [
                    {
                        "id": "status",
                        "type": "item.details",
                        "dataSource": {"kind": "skill", "name": "smoke_skill.check"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = validate_scenario_path(scenario)

    codes = {issue.code for issue in report.issues}
    assert report.ok is True
    assert {
        "scenario.webui.cache_policy_implicit",
        "scenario.webui.invalidation_tags_missing",
        "scenario.webui.preserve_last_value_implicit",
        "scenario.webui.max_request_hz_implicit",
    }.issubset(codes)


def test_scenario_validation_rejects_data_route_policy_drift(tmp_path: Path) -> None:
    _write_skill(tmp_path, "smoke_skill", "check")
    skill_manifest = tmp_path / "skills" / "smoke_skill" / "skill.yaml"
    skill = yaml.safe_load(skill_manifest.read_text(encoding="utf-8"))
    skill["data_routes"] = [{
        "surface": "widget:status",
        "route": "tool/details",
        "tool": "check",
        "read_policy": {
            "mode": "explicit",
            "triggers": ["mount", "targeted_invalidation"],
            "invalidation_tags": ["status"],
            "preserve_last_value": True,
            "max_request_hz": 2,
        },
    }]
    skill_manifest.write_text(yaml.safe_dump(skill, sort_keys=False), encoding="utf-8")
    scenario = _write_scenario(tmp_path, "webui_policy_drift", depends=["smoke_skill"], route="smoke_skill.check")
    scenario_manifest = scenario / "scenario.yaml"
    scenario_payload = yaml.safe_load(scenario_manifest.read_text(encoding="utf-8"))
    scenario_payload["runtime_data_policy"] = {"enforcement": "strict"}
    scenario_manifest.write_text(yaml.safe_dump(scenario_payload, sort_keys=False), encoding="utf-8")
    (scenario / "webui.json").write_text(json.dumps({"widgets": [{
        "id": "status",
        "type": "item.details",
        "dataSource": {
            "kind": "skill",
            "name": "smoke_skill.check",
            "cacheTtlMs": 0,
            "invalidationTags": ["other"],
            "preserveLastValue": False,
            "maxRequestHz": 1,
        },
    }]}), encoding="utf-8")

    report = validate_scenario_path(scenario)

    assert report.ok is False
    codes = {issue.code for issue in report.issues}
    assert {
        "scenario.webui.invalidation_tags_mismatch",
        "scenario.webui.preserve_last_value_mismatch",
        "scenario.webui.max_request_hz_mismatch",
    }.issubset(codes)


def test_strict_scenario_rejects_skill_datasource_without_data_route(tmp_path: Path) -> None:
    _write_skill(tmp_path, "smoke_skill", "check")
    scenario = _write_scenario(
        tmp_path,
        "webui_missing_route",
        depends=["smoke_skill"],
        route="smoke_skill.check",
    )
    scenario_manifest = scenario / "scenario.yaml"
    scenario_payload = yaml.safe_load(scenario_manifest.read_text(encoding="utf-8"))
    scenario_payload["runtime_data_policy"] = {"enforcement": "strict"}
    scenario_manifest.write_text(
        yaml.safe_dump(scenario_payload, sort_keys=False),
        encoding="utf-8",
    )
    (scenario / "webui.json").write_text(
        json.dumps(
            {
                "widgets": [
                    {
                        "id": "status",
                        "type": "item.details",
                        "dataSource": {
                            "kind": "skill",
                            "name": "smoke_skill.check",
                            "cacheTtlMs": 0,
                            "invalidationTags": ["status"],
                            "preserveLastValue": True,
                            "maxRequestHz": 1,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = validate_scenario_path(scenario)

    assert report.ok is False
    issue = next(item for item in report.issues if item.code == "scenario.webui.data_route_missing")
    assert issue.level == "error"


def test_scenario_validation_does_not_let_incomplete_dev_skill_shadow_workspace_skill(tmp_path: Path) -> None:
    dev_root = tmp_path / "dev" / "subnet"
    (dev_root / "skills" / "builder_skill").mkdir(parents=True)
    (dev_root / "skills" / "builder_skill" / "prompt_state.json").write_text("{}", encoding="utf-8")
    workspace_root = tmp_path / "workspace"
    _write_skill(workspace_root, "builder_skill", "chat")
    scenario = _write_scenario(
        dev_root,
        "prototype",
        depends=["builder_skill"],
        route="builder_skill.chat",
    )

    report = validate_scenario_path(scenario, dependency_roots=[workspace_root / "skills"])

    assert report.ok is True
    assert report.errors == []
