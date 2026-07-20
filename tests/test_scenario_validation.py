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
    (target / "scenario.json").write_text(
        json.dumps(
            {
                "id": name,
                "version": "0.1.0",
                "depends": depends,
                "steps": [{"name": "run", "call": route}],
            }
        ),
        encoding="utf-8",
    )
    return target


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


def test_scenario_json_is_canonical_when_legacy_yaml_is_also_present(tmp_path: Path) -> None:
    scenario = _write_scenario(
        tmp_path,
        "dual_manifest",
        depends=["missing_skill"],
        route="missing_skill.check",
    )
    (scenario / "scenario.yaml").write_text(
        "id: dual_manifest\nversion: 0.1.0\nsteps: []\n",
        encoding="utf-8",
    )

    report = validate_scenario_path(scenario)

    assert report.ok is False
    assert any(issue.code == "scenario.dependency.missing" for issue in report.issues)


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
    }.issubset(codes)


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
