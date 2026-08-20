from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.adapters.db import SqliteScenarioRegistry, SqliteSkillRegistry
from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    ProjectRelease,
    canonical_payload_digest,
)
from adaos.services import workspace_registry as workspace_registry_module
from adaos.services import workspace_sync as workspace_sync_module
from adaos.services.workspace_sync import (
    audit_workspace_materialization,
    reconcile_workspace_db_to_materialized,
    resolve_scenario_requirements,
    runtime_required_scenario_refs,
    selected_runtime_skill_names,
    sync_workspace_sparse_to_registry,
)
from adaos.services.workspace_registry import (
    build_registry_entry,
    load_workspace_registry_git_ref,
    list_workspace_registry_entries,
    load_workspace_registry,
    rebuild_workspace_registry,
    registry_pattern_set,
    resolve_registry_payload_install_name,
    set_workspace_registry_channel,
    upsert_workspace_registry_entry,
    WorkspaceRegistryError,
    write_workspace_registry,
    workspace_registry_path,
)


def _capture_registry_errors(monkeypatch) -> list[str]:
    messages: list[str] = []

    def _capture(message: str, *args, **_kwargs) -> None:
        messages.append(message % args if args else message)

    monkeypatch.setattr(workspace_registry_module._LOG, "error", _capture)
    return messages


def test_remote_registry_resolution_uses_cached_tracking_ref_when_fetch_fails(tmp_path: Path):
    payload = {
        "version": 2,
        "updated_at": "2026-08-15T00:00:00+00:00",
        "skills": [],
        "scenarios": [
            {
                "kind": "scenario",
                "id": "adaos_drive",
                "name": "adaos_drive",
                "version": "0.1.0",
                "install": {"kind": "scenario", "name": "adaos_drive", "id": "adaos_drive"},
            }
        ],
    }

    class _Git:
        def fetch(self, *_args, **_kwargs):
            raise RuntimeError("network unavailable")

        def show(self, _root, spec):
            assert spec == "origin/main:registry.json"
            return json.dumps(payload)

    observed = load_workspace_registry_git_ref(_Git(), tmp_path, remote="origin", branch="main")
    resolved, entry = resolve_registry_payload_install_name(
        observed,
        kind="scenarios",
        name_or_id="adaos_drive",
    )

    assert resolved == "adaos_drive"
    assert entry is not None
    assert entry["version"] == "0.1.0"


def test_rebuild_workspace_registry_reads_skill_and_scenario_manifests(tmp_path: Path):
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "weather_skill"
    scenario_dir = workspace / "scenarios" / "greet_on_boot"
    skill_dir.mkdir(parents=True)
    scenario_dir.mkdir(parents=True)

    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "id: weather_skill",
                "name: Weather",
                "version: '1.2.3'",
                "description: Forecast provider",
                "entry: handlers/main.py",
                "runtime:",
                "  python: '3.11'",
                "tools:",
                "  - name: weather.get",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (scenario_dir / "scenario.yaml").write_text(
        "\n".join(
            [
                "id: greet_on_boot",
                "name: Greeting",
                "version: '0.4.0'",
                "trigger: manual",
                "io:",
                "  input:",
                "    - text",
                "  output:",
                "    - text",
                "    - voice",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = rebuild_workspace_registry(workspace)

    assert payload["version"] == 2
    assert payload["skills"][0]["name"] == "weather_skill"
    assert payload["skills"][0]["id"] == "weather_skill"
    assert payload["skills"][0]["title"] == "Weather"
    assert payload["skills"][0]["runtime_python"] == "3.11"
    assert payload["skills"][0]["tools_count"] == 1
    assert payload["skills"][0]["install"]["kind"] == "skill"
    assert payload["scenarios"][0]["name"] == "greet_on_boot"
    assert payload["scenarios"][0]["id"] == "greet_on_boot"
    assert payload["scenarios"][0]["trigger"] == "manual"
    assert payload["scenarios"][0]["io"]["output"] == ["text", "voice"]


def test_rebuild_workspace_registry_prefers_scenario_yaml_title_and_i18n(tmp_path: Path):
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios" / "prototype_app"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.yaml").write_text(
        "\n".join(
            [
                "id: prototype_app",
                "name: prototype_app",
                "title: Prototype App",
                "title_i18n:",
                "  key: scenario.prototype_app.title",
                "  fallback: Prototype App",
                "version: '0.3.2'",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = rebuild_workspace_registry(workspace)

    entry = payload["scenarios"][0]
    assert entry["name"] == "prototype_app"
    assert entry["id"] == "prototype_app"
    assert entry["title"] == "Prototype App"
    assert entry["title_i18n"] == {"key": "scenario.prototype_app.title", "fallback": "Prototype App"}
    assert entry["version"] == "0.3.2"


def test_rebuild_workspace_registry_accepts_scenario_json_as_derived_runtime_projection(tmp_path: Path, caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("adaos"), "propagate", True)
    caplog.set_level(logging.WARNING, logger="adaos.workspace_registry")
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios" / "web_desktop"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.yaml").write_text(
        "\n".join(
            [
                "id: web_desktop",
                "version: '0.3.10'",
                "updated_at: '2026-07-05T04:58:01+00:00'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (scenario_dir / "scenario.json").write_text(
        json.dumps({"id": "web_desktop", "version": "0.0.1", "ui": {"application": {}}}) + "\n",
        encoding="utf-8",
    )

    payload = rebuild_workspace_registry(workspace)

    entry = payload["scenarios"][0]
    assert entry["name"] == "web_desktop"
    assert entry["id"] == "web_desktop"
    assert entry["manifest"] == "scenarios/web_desktop/scenario.yaml"
    assert entry["version"] == "0.3.10"
    assert entry["updated_at"] == "2026-07-05T04:58:01+00:00"
    assert "unsupported declaration files" not in caplog.text


def test_rebuild_workspace_registry_rejects_scenario_without_scenario_yaml(tmp_path: Path, monkeypatch):
    errors = _capture_registry_errors(monkeypatch)
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios" / "legacy_scene"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.json").write_text(
        json.dumps({"id": "legacy_scene", "version": "9.9.9"}) + "\n",
        encoding="utf-8",
    )

    payload = rebuild_workspace_registry(workspace)

    assert payload["scenarios"] == []
    assert any("required declaration is missing" in message for message in errors)
    assert any("unsupported_present=scenario.json" in message for message in errors)


def test_rebuild_workspace_registry_skips_sparse_placeholder_dirs(tmp_path: Path, caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("adaos"), "propagate", True)
    caplog.set_level(logging.ERROR, logger="adaos.workspace_registry")
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "sparse_skill"
    scenario_dir = workspace / "scenarios" / "sparse_scene"
    skill_dir.mkdir(parents=True)
    scenario_dir.mkdir(parents=True)
    (skill_dir / ".gitignore").write_text("*\n", encoding="utf-8")
    (scenario_dir / ".gitignore").write_text("*\n", encoding="utf-8")

    payload = rebuild_workspace_registry(workspace)

    assert payload["skills"] == []
    assert payload["scenarios"] == []
    assert "required declaration is missing" not in caplog.text


def test_rebuild_workspace_registry_skips_empty_sparse_placeholder_dirs(tmp_path: Path, caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("adaos"), "propagate", True)
    caplog.set_level(logging.ERROR, logger="adaos.workspace_registry")
    workspace = tmp_path / "workspace"
    (workspace / "skills" / "sparse_skill").mkdir(parents=True)
    (workspace / "scenarios" / "sparse_scene").mkdir(parents=True)

    payload = rebuild_workspace_registry(workspace)

    assert payload["skills"] == []
    assert payload["scenarios"] == []
    assert "required declaration is missing" not in caplog.text


def test_build_registry_entry_skips_non_materialized_catalog_path(tmp_path: Path, caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("adaos"), "propagate", True)
    caplog.set_level(logging.ERROR, logger="adaos.workspace_registry")

    entry = build_registry_entry("skills", tmp_path / "workspace" / "skills" / "catalog_only")

    assert entry is None
    assert "required declaration is missing" not in caplog.text


def test_authoritative_registry_keeps_sparse_placeholder_entries_without_enrichment_errors(
    tmp_path: Path,
    caplog,
    monkeypatch,
):
    monkeypatch.setattr(logging.getLogger("adaos"), "propagate", True)
    caplog.set_level(logging.ERROR, logger="adaos.workspace_registry")
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "remote_only"
    skill_dir.mkdir(parents=True)
    (skill_dir / ".gitignore").write_text("*\n", encoding="utf-8")
    workspace_registry_path(workspace).write_text(
        json.dumps(
            {
                "version": 2,
                "updated_at": "2026-08-18T00:00:00+00:00",
                "skills": [
                    {
                        "kind": "skill",
                        "id": "remote_only",
                        "name": "remote_only",
                        "version": "9.0.0",
                        "path": "skills/remote_only",
                        "manifest": "skills/remote_only/skill.yaml",
                    }
                ],
                "scenarios": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = load_workspace_registry(workspace, fallback_to_scan=False)

    assert payload["skills"] == [
        {
            "kind": "skill",
            "id": "remote_only",
            "name": "remote_only",
            "version": "9.0.0",
            "path": "skills/remote_only",
            "manifest": "skills/remote_only/skill.yaml",
        }
    ]
    assert "required declaration is missing" not in caplog.text


def test_load_workspace_registry_rejects_entries_with_unsupported_manifest(tmp_path: Path, monkeypatch):
    errors = _capture_registry_errors(monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    workspace_registry_path(workspace).write_text(
        json.dumps(
            {
                "version": 1,
                "skills": [
                    {
                        "kind": "skill",
                        "id": "weather_skill",
                        "name": "weather_skill",
                        "manifest": "skills/weather_skill/skill.yaml",
                    }
                ],
                "scenarios": [
                    {
                        "kind": "scenario",
                        "id": "legacy_scene",
                        "name": "legacy_scene",
                        "manifest": "scenarios/legacy_scene/scenario.json",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    payload = load_workspace_registry(workspace, fallback_to_scan=False)

    assert [item["name"] for item in payload["skills"]] == ["weather_skill"]
    assert payload["scenarios"] == []
    assert any("workspace registry entry rejected" in message for message in errors)
    assert any("required=scenario.yaml" in message for message in errors)


def test_upsert_workspace_registry_entry_preserves_existing_entries(tmp_path: Path):
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "weather_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "name: Weather",
                "version: '2.0.0'",
                "description: Fresh forecast",
                "",
            ]
        ),
        encoding="utf-8",
    )
    workspace.mkdir(parents=True, exist_ok=True)
    workspace_registry_path(workspace).write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-03-06T00:00:00+00:00",
                "skills": [{"kind": "skill", "name": "alarm_skill", "version": "1.0.0"}],
                "scenarios": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    entry = upsert_workspace_registry_entry(
        workspace,
        "skills",
        skill_dir,
        version="2.0.0",
        updated_at="2026-03-06T10:00:00+00:00",
    )

    assert entry["name"] == "weather_skill"
    items = list_workspace_registry_entries(workspace, kind="skills", fallback_to_scan=False)
    names = [item["name"] for item in items]
    assert names == ["alarm_skill", "weather_skill"]
    assert items[1]["version"] == "2.0.0"


def test_upsert_workspace_registry_entry_rejects_missing_required_declaration(tmp_path: Path, monkeypatch):
    errors = _capture_registry_errors(monkeypatch)
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "browsers_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "handlers").mkdir(parents=True)
    (skill_dir / "handlers" / "main.py").write_text("def handle():\n    return {'ok': True}\n", encoding="utf-8")
    workspace.mkdir(parents=True, exist_ok=True)
    workspace_registry_path(workspace).write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-03-06T00:00:00+00:00",
                "skills": [
                    {
                        "kind": "skill",
                        "id": "browsers_skill",
                        "name": "browsers_skill",
                        "version": "0.4.0",
                        "updated_at": "2026-03-06T10:00:00+00:00",
                        "path": "skills/browsers_skill",
                        "manifest": "skills/browsers_skill/skill.yaml",
                        "install": {
                            "kind": "skill",
                            "name": "browsers_skill",
                            "id": "browsers_skill",
                        },
                        "entry": "handlers/main.py",
                    }
                ],
                "scenarios": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError):
        upsert_workspace_registry_entry(workspace, "skills", skill_dir)

    assert any("required declaration is missing" in message for message in errors)


def test_registry_entry_includes_tags_and_publisher(tmp_path: Path):
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "infra_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "id: infra_skill",
                "name: Infra",
                "version: '1.0.0'",
                "tags:",
                "  - infra",
                "  - ops",
                "publisher:",
                "  owner_id: owner-1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    entry = upsert_workspace_registry_entry(
        workspace,
        "skills",
        skill_dir,
        extra={"publisher": {"owner_id": "owner-1", "node_id": "hub-1"}},
    )

    assert entry["tags"] == ["infra", "ops"]
    assert entry["publisher"]["owner_id"] == "owner-1"
    assert entry["publisher"]["node_id"] == "hub-1"


def test_registry_entry_normalizes_skill_activation_policy(tmp_path: Path):
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "infrascope_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "name: Infrascope",
                "version: '1.0.0'",
                "runtime:",
                "  python: '3.11'",
                "  activation:",
                "    mode: lazy",
                "    startup_allowed: false",
                "    background_refresh: false",
                "    when:",
                "      scenarios_active:",
                "        - infrascope",
                "      client_presence: true",
                "      webspace_scope: active",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = rebuild_workspace_registry(workspace)

    assert payload["skills"][0]["activation"] == {
        "mode": "lazy",
        "startup_allowed": False,
        "background_refresh": False,
        "when": {
            "scenarios_active": ["infrascope"],
            "client_presence": True,
            "webspace_scope": "active",
        },
    }


def test_registry_entry_normalizes_scenario_skill_bindings(tmp_path: Path):
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios" / "infrascope"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.yaml").write_text(
        "\n".join(
            [
                "id: infrascope",
                "version: '0.6.0'",
                "depends:",
                "  - legacy_skill",
                "runtime:",
                "  skills:",
                "    required:",
                "      - infrascope_skill",
                "    optional:",
                "      - telemetry_skill",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = rebuild_workspace_registry(workspace)

    assert payload["scenarios"][0]["skills"] == {
        "required": ["legacy_skill", "infrascope_skill"],
        "optional": ["telemetry_skill"],
    }


def test_registry_pattern_set_keeps_registry_json_first():
    patterns = registry_pattern_set(["skills/weather_skill", "registry.json", "scenarios/greet_on_boot"])
    assert patterns[0] == "registry.json"
    assert patterns.count("registry.json") == 1


def test_runtime_required_scenarios_include_bootstrap_home_current_and_reference(monkeypatch):
    workspaces = [
        SimpleNamespace(
            effective_home_scenario="media_center",
            current_scenario_overlay="adaos_drive",
            home_scenario_ref_overlay={"scenario_id": "remote_dashboard"},
        )
    ]
    monkeypatch.setattr("adaos.services.workspaces.index.list_workspaces", lambda: workspaces)

    assert runtime_required_scenario_refs() == [
        "adaos_drive",
        "media_center",
        "remote_dashboard",
        "web_desktop",
    ]


def test_scenario_requirements_resolve_aliases_and_required_skill_closure():
    payload = {
        "version": 2,
        "skills": [],
        "scenarios": [
            {
                "kind": "scenario",
                "id": "desktop-shell",
                "name": "web_desktop",
                "install": {"kind": "scenario", "id": "desktop-shell", "name": "web_desktop"},
                "skills": {"required": ["web_desktop_skill", "voice_chat_skill"]},
            }
        ],
    }

    scenarios, skills, unresolved = resolve_scenario_requirements(
        payload,
        ["desktop-shell", "missing_scenario", "../unsafe"],
    )

    assert scenarios == ["missing_scenario", "web_desktop"]
    assert skills == ["voice_chat_skill", "web_desktop_skill"]
    assert unresolved == ["../unsafe", "missing_scenario"]


def test_registry_v1_is_readable_and_rewritten_as_v2_without_losing_fields(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = workspace_registry_path(workspace)
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-07-20T00:00:00Z",
                "skills": [],
                "scenarios": [
                    {
                        "kind": "scenario",
                        "id": "recipes",
                        "name": "recipes",
                        "version": "1.2.3",
                        "path": "scenarios/recipes",
                        "manifest": "scenarios/recipes/scenario.yaml",
                        "custom_legacy_field": "preserve-me",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_workspace_registry(workspace, fallback_to_scan=False)
    assert loaded["version"] == 2
    assert loaded["scenarios"][0]["custom_legacy_field"] == "preserve-me"

    write_workspace_registry(workspace, loaded)
    rewritten = json.loads(registry.read_text(encoding="utf-8"))
    assert rewritten["version"] == 2
    assert rewritten["scenarios"][0]["custom_legacy_field"] == "preserve-me"


def test_historical_registry_and_incomplete_manifests_migrate_deterministically(tmp_path: Path):
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "artifact_migration"
        / "workspace_v1_incomplete"
    )
    workspace = tmp_path / "workspace"
    shutil.copytree(fixture, workspace)

    first = load_workspace_registry(workspace, fallback_to_scan=False)
    second = load_workspace_registry(workspace, fallback_to_scan=False)

    assert first == second
    assert first["version"] == 2
    scenario = first["scenarios"][0]
    assert scenario["name"] == "recipes_legacy"
    assert scenario["id"] == "recipes"
    assert scenario["version"].startswith("0.0.0-legacy.")
    assert scenario["version"] != "9.9.9"
    assert scenario["custom_legacy_field"] == "keep-scenario"
    assert scenario["install"] == {
        "kind": "scenario",
        "name": "recipes_legacy",
        "id": "recipes",
    }
    assert scenario["compatibility"] == {
        "schema": "adaos.workspace.artifact_compatibility.v1",
        "status": "migration_required",
        "reason": "canonical_manifest_version_missing",
        "version_source": "canonical_manifest_digest",
        "manifest_digest": scenario["compatibility"]["manifest_digest"],
        "publishable": False,
    }
    assert scenario["compatibility"]["manifest_digest"].startswith("sha256:")

    skill = first["skills"][0]
    assert skill["name"] == "weather_legacy"
    assert skill["id"] == "weather_skill"
    assert skill["version"].startswith("0.0.0-legacy.")
    assert skill["custom_legacy_field"] == "keep-skill"
    assert skill["install"] == {
        "kind": "skill",
        "name": "weather_legacy",
        "id": "weather_skill",
    }
    assert skill["compatibility"]["publishable"] is False

    write_workspace_registry(workspace, first)
    rewritten = json.loads(workspace_registry_path(workspace).read_text(encoding="utf-8"))
    assert rewritten == first


@pytest.mark.parametrize("payload", ["{", "[]", '{"version": 999}'])
def test_workspace_registry_load_fails_closed_for_untrusted_payload(
    tmp_path: Path,
    payload: str,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_registry_path(workspace).write_text(payload, encoding="utf-8")

    with pytest.raises(WorkspaceRegistryError):
        load_workspace_registry(workspace, fallback_to_scan=True)


def test_workspace_registry_atomic_write_preserves_previous_file_on_replace_failure(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = workspace_registry_path(workspace)
    original = b'{"version": 1, "skills": [], "scenarios": []}\n'
    registry.write_bytes(original)

    def fail_atomic_write(path, payload):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(
        workspace_registry_module,
        "_atomic_write_registry_json",
        fail_atomic_write,
    )

    with pytest.raises(OSError, match="simulated replace failure"):
        write_workspace_registry(
            workspace,
            {"version": 2, "skills": [], "scenarios": []},
        )

    assert registry.read_bytes() == original
    assert list(workspace.glob(".registry.json.*.tmp")) == []


def test_registry_v2_load_keeps_catalog_read_bounded_to_registry_file(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_registry_path(workspace).write_text(
        json.dumps(
            {
                "version": 2,
                "skills": [],
                "scenarios": [
                    {
                        "kind": "scenario",
                        "id": "recipes",
                        "name": "recipes",
                        "version": "1.2.3",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def unexpected_manifest_read(*args, **kwargs):
        raise AssertionError("registry v2 must not rescan canonical manifests")

    monkeypatch.setattr(
        workspace_registry_module,
        "build_registry_entry",
        unexpected_manifest_read,
    )

    loaded = load_workspace_registry(workspace, fallback_to_scan=False)

    assert loaded["scenarios"][0]["version"] == "1.2.3"


def test_registry_upsert_serializes_the_complete_read_modify_write_cycle(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_workspace_registry(
        workspace,
        {"version": 2, "skills": [], "scenarios": []},
    )
    first_dir = workspace / "scenarios" / "first"
    second_dir = workspace / "scenarios" / "second"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)
    (first_dir / "scenario.yaml").write_text(
        "id: first\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    (second_dir / "scenario.yaml").write_text(
        "id: second\nversion: 1.0.0\n",
        encoding="utf-8",
    )

    original_load = workspace_registry_module.load_workspace_registry
    first_has_read = threading.Event()
    allow_first_write = threading.Event()
    second_finished = threading.Event()
    errors: list[BaseException] = []

    def delayed_load(*args, **kwargs):
        payload = original_load(*args, **kwargs)
        if threading.current_thread().name == "registry-first":
            first_has_read.set()
            assert allow_first_write.wait(timeout=2)
        return payload

    def upsert(directory: Path, *, finished: threading.Event | None = None):
        try:
            upsert_workspace_registry_entry(
                workspace,
                "scenarios",
                directory,
            )
        except BaseException as exc:  # surfaced in the parent test thread
            errors.append(exc)
        finally:
            if finished is not None:
                finished.set()

    monkeypatch.setattr(
        workspace_registry_module,
        "load_workspace_registry",
        delayed_load,
    )
    first = threading.Thread(
        target=upsert,
        args=(first_dir,),
        name="registry-first",
    )
    second = threading.Thread(
        target=upsert,
        args=(second_dir,),
        kwargs={"finished": second_finished},
        name="registry-second",
    )
    first.start()
    assert first_has_read.wait(timeout=2)
    second.start()
    assert second_finished.wait(timeout=0.1) is False
    allow_first_write.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert errors == []
    assert not first.is_alive()
    assert not second.is_alive()
    loaded = original_load(workspace, fallback_to_scan=False)
    assert [item["id"] for item in loaded["scenarios"]] == ["first", "second"]


@pytest.mark.parametrize(
    "entries",
    [
        [
            {"id": "recipes", "name": "recipes", "version": "1.0.0"},
            {"id": "Recipes", "name": "recipes_copy", "version": "1.0.0"},
        ],
        [{"id": "escape", "name": "../escape", "version": "1.0.0"}],
    ],
)
def test_workspace_registry_rejects_ambiguous_or_unsafe_install_aliases(
    tmp_path: Path,
    entries: list[dict[str, str]],
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_registry_path(workspace).write_text(
        json.dumps({"version": 2, "skills": [], "scenarios": entries}),
        encoding="utf-8",
    )

    with pytest.raises(WorkspaceRegistryError):
        load_workspace_registry(workspace, fallback_to_scan=False)


def test_registry_channel_points_to_sealed_immutable_release(tmp_path: Path):
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios" / "recipes"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.2.3\ntitle: Recipes\n",
        encoding="utf-8",
    )
    upsert_workspace_registry_entry(workspace, "scenarios", scenario_dir)
    source = ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("scenarios/recipes/",),
    )
    package = ArtifactPackageRef(
        kind="scenario",
        artifact_id="recipes",
        version="1.2.3",
        digest="sha256:" + "a" * 64,
        manifest_digest="sha256:" + "b" * 64,
        source_ref=source,
    )
    release = ProjectRelease(
        project_id="recipes",
        version="1.2.3",
        source_ref=source,
        components=(package,),
    ).seal()

    set_workspace_registry_channel(
        workspace,
        "scenarios",
        "recipes",
        channel="stable",
        release=release,
    )

    entry = load_workspace_registry(workspace, fallback_to_scan=False)["scenarios"][0]
    assert entry["channels"]["stable"] == {
        "release": "recipes@1.2.3",
        "release_digest": release.release_digest,
        "source_revision": source.revision,
        "package_digest": package.digest,
        "version": "1.2.3",
    }
    assert entry["source"]["path"] == "scenarios/recipes"
    assert entry["source"]["revision"] == source.revision


def test_registry_channel_compare_and_swap_rejects_changed_entry(tmp_path: Path):
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios" / "recipes"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.2.3\n",
        encoding="utf-8",
    )
    upsert_workspace_registry_entry(workspace, "scenarios", scenario_dir)
    observed = load_workspace_registry(workspace, fallback_to_scan=False)["scenarios"][0]
    observed_digest = canonical_payload_digest(observed)

    registry = load_workspace_registry(workspace, fallback_to_scan=False)
    registry["scenarios"][0]["operator_note"] = "changed-after-review"
    write_workspace_registry(workspace, registry)

    source = ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("scenarios/recipes/",),
    )
    package = ArtifactPackageRef(
        kind="scenario",
        artifact_id="recipes",
        version="1.2.3",
        digest="sha256:" + "a" * 64,
        manifest_digest="sha256:" + "b" * 64,
        source_ref=source,
    )
    release = ProjectRelease(
        project_id="recipes",
        version="1.2.3",
        source_ref=source,
        components=(package,),
    ).seal()

    with pytest.raises(WorkspaceRegistryError, match="changed after review"):
        set_workspace_registry_channel(
            workspace,
            "scenarios",
            "recipes",
            channel="stable",
            release=release,
            expected_entry_digest=observed_digest,
        )

    entry = load_workspace_registry(workspace, fallback_to_scan=False)["scenarios"][0]
    assert entry["operator_note"] == "changed-after-review"
    assert "channels" not in entry


class _Sql:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self):
        return sqlite3.connect(self.path)


def test_sparse_sync_keeps_runtime_scenarios_and_materializes_required_skills(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_workspace_registry(
        workspace,
        {
            "version": 2,
            "skills": [],
            "scenarios": [
                {
                    "kind": "scenario",
                    "id": "web_desktop",
                    "name": "web_desktop",
                    "install": {"kind": "scenario", "id": "web_desktop", "name": "web_desktop"},
                    "skills": {"required": ["web_desktop_skill", "voice_chat_skill"]},
                },
                {
                    "kind": "scenario",
                    "id": "media_center",
                    "name": "media_center",
                    "install": {"kind": "scenario", "id": "media_center", "name": "media_center"},
                    "skills": {"required": ["media_center_skill", "mediaserver"]},
                },
            ],
        },
    )
    sql = _Sql(tmp_path / "adaos.db")
    SqliteScenarioRegistry(sql).register("media_center", active_version="0.1.0")

    class _Git:
        def __init__(self):
            self.pulls = 0

        def changed_files(self, _root):
            return []

        def pull(self, _root):
            self.pulls += 1

    class _Sparse:
        patterns = ["registry.json", "scenarios/media_center"]

        def __init__(self, _git, _root):
            pass

        def read_patterns(self):
            return list(self.patterns)

        def update(self, *, add=(), remove=()):
            self.patterns = [item for item in self.patterns if item not in remove]
            for item in add:
                if item not in self.patterns:
                    self.patterns.append(item)
            return list(self.patterns)

    git = _Git()
    ctx = SimpleNamespace(
        paths=SimpleNamespace(workspace_dir=lambda: workspace),
        sql=sql,
        git=git,
        settings=SimpleNamespace(base_dir=tmp_path),
    )
    monkeypatch.setattr(workspace_sync_module, "SparseWorkspace", _Sparse)
    monkeypatch.setattr(workspace_sync_module, "runtime_required_scenario_refs", lambda: ["web_desktop"])
    monkeypatch.setattr(
        workspace_sync_module,
        "reconcile_workspace_db_to_materialized",
        lambda _ctx: {"ok": True},
    )
    monkeypatch.setattr(
        "adaos.services.git.availability.get_git_availability",
        lambda **_kwargs: SimpleNamespace(enabled=True),
    )

    result = sync_workspace_sparse_to_registry(ctx)

    assert result["ok"] is True
    assert result["runtime_scenario_refs"] == ["web_desktop"]
    assert result["scenarios"] == ["media_center", "web_desktop"]
    assert result["scenario_required_skills"] == [
        "media_center_skill",
        "mediaserver",
        "voice_chat_skill",
        "web_desktop_skill",
    ]
    assert result["unresolved_runtime_scenarios"] == []
    assert result["patterns"] == [
        "registry.json",
        "skills/media_center_skill",
        "skills/mediaserver",
        "skills/voice_chat_skill",
        "skills/web_desktop_skill",
        "scenarios/media_center",
        "scenarios/web_desktop",
    ]
    assert git.pulls == 1


def test_selected_runtime_skill_names_requires_valid_selection(tmp_path: Path):
    skills_root = tmp_path / "workspace" / "skills"
    runtime_root = skills_root / ".runtime"
    selected = runtime_root / "weather_skill" / "current_runtime.json"
    selected.parent.mkdir(parents=True)
    selected.write_text(json.dumps({"version": "2.6.23", "slot": "B"}), encoding="utf-8")
    invalid = runtime_root / "broken_skill" / "current_runtime.json"
    invalid.parent.mkdir(parents=True)
    invalid.write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
    corrupt = runtime_root / "corrupt_skill" / "current_runtime.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("not-json", encoding="utf-8")
    ctx = SimpleNamespace(paths=SimpleNamespace(skills_dir=lambda: skills_root))

    assert selected_runtime_skill_names(ctx) == ["weather_skill"]


def test_selected_runtime_skill_names_preserves_legacy_selection(tmp_path: Path):
    skills_root = tmp_path / "workspace" / "skills"
    runtime_root = skills_root / ".runtime"
    selected = runtime_root / "legacy_skill"
    selected.mkdir(parents=True)
    (selected / "current_version").write_text("1.2.3", encoding="utf-8")
    active = selected / "v1.2" / "active"
    active.parent.mkdir(parents=True)
    active.write_text("B", encoding="utf-8")

    invalid = runtime_root / "invalid_legacy_skill"
    invalid.mkdir(parents=True)
    (invalid / "current_version").write_text("2.0.0", encoding="utf-8")
    invalid_active = invalid / "v2.0" / "active"
    invalid_active.parent.mkdir(parents=True)
    invalid_active.write_text("pending", encoding="utf-8")

    ctx = SimpleNamespace(paths=SimpleNamespace(skills_dir=lambda: skills_root))

    assert selected_runtime_skill_names(ctx) == ["legacy_skill"]


def test_workspace_materialization_audit_is_read_only(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios" / "media_center"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.yaml").write_text(
        "id: media_center\nversion: '0.1.0'\n",
        encoding="utf-8",
    )
    write_workspace_registry(
        workspace,
        {
            "version": 2,
            "skills": [],
            "scenarios": [
                {
                    "kind": "scenario",
                    "id": "media_center",
                    "name": "media_center",
                    "install": {"kind": "scenario", "id": "media_center", "name": "media_center"},
                },
                {
                    "kind": "scenario",
                    "id": "web_desktop",
                    "name": "web_desktop",
                    "install": {"kind": "scenario", "id": "web_desktop", "name": "web_desktop"},
                    "skills": {"required": ["web_desktop_skill"]},
                },
            ],
        },
    )
    sql = _Sql(tmp_path / "adaos.db")
    SqliteSkillRegistry(sql).register("database_only_skill", active_version="9.9.9")
    SqliteScenarioRegistry(sql).register("database_only_scenario", active_version="9.9.9")
    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: workspace), sql=sql)
    monkeypatch.setattr(workspace_sync_module, "workspace_registry_is_git_tracked", lambda _root: True)
    monkeypatch.setattr(workspace_sync_module, "runtime_required_scenario_refs", lambda: ["web_desktop"])

    result = audit_workspace_materialization(ctx)

    assert result["skills"] == []
    assert result["scenarios"] == ["media_center"]
    assert result["runtime_requirements"]["missing_scenarios"] == ["web_desktop"]
    assert result["runtime_requirements"]["missing_skills"] == ["web_desktop_skill"]
    assert [row.name for row in SqliteSkillRegistry(sql).list()] == ["database_only_skill"]
    assert [row.name for row in SqliteScenarioRegistry(sql).list()] == ["database_only_scenario"]


def test_reconcile_workspace_db_to_materialized_updates_sqlite(tmp_path: Path):
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "weather_skill"
    scenario_dir = workspace / "scenarios" / "greet_on_boot"
    skill_dir.mkdir(parents=True, exist_ok=True)
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text("id: weather_skill\nversion: '1.2.3'\n", encoding="utf-8")
    (scenario_dir / "scenario.yaml").write_text("id: greet_on_boot\nversion: '0.4.0'\n", encoding="utf-8")

    sql = _Sql(tmp_path / "adaos.db")
    skill_registry = SqliteSkillRegistry(sql)
    scenario_registry = SqliteScenarioRegistry(sql)
    skill_registry.register("ghost_skill", active_version="9.9.9")
    scenario_registry.register("ghost_scene", active_version="8.8.8")

    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: workspace), sql=sql)

    result = reconcile_workspace_db_to_materialized(ctx)

    assert result["ok"] is True
    assert result["skills"] == ["weather_skill"]
    assert result["scenarios"] == ["greet_on_boot"]
    assert result["skills_removed"] == ["ghost_skill"]
    assert result["scenarios_removed"] == ["ghost_scene"]

    registry_payload = json.loads(workspace_registry_path(workspace).read_text(encoding="utf-8"))
    assert [item["id"] for item in registry_payload["skills"]] == ["weather_skill"]
    assert [item["manifest"] for item in registry_payload["scenarios"]] == ["scenarios/greet_on_boot/scenario.yaml"]

    skill_rows = {row.name: row for row in skill_registry.list()}
    scenario_rows = {row.name: row for row in scenario_registry.list()}
    assert list(skill_rows) == ["weather_skill"]
    assert skill_rows["weather_skill"].active_version == "1.2.3"
    assert list(scenario_rows) == ["greet_on_boot"]
    assert scenario_rows["greet_on_boot"].active_version == "0.4.0"


def test_reconcile_workspace_db_preserves_git_authoritative_catalog(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "weather_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text("id: weather_skill\nversion: '1.2.3'\n", encoding="utf-8")
    catalog = {
        "version": 2,
        "updated_at": "2026-08-14T00:00:00+00:00",
        "skills": [
            {"kind": "skill", "id": "weather_skill", "name": "weather_skill", "version": "1.2.3"},
            {"kind": "skill", "id": "remote_only", "name": "remote_only", "version": "9.0.0"},
        ],
        "scenarios": [],
    }
    catalog_path = workspace_registry_path(workspace)
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    original = catalog_path.read_bytes()

    sql = _Sql(tmp_path / "adaos.db")
    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: workspace), sql=sql)
    monkeypatch.setattr(workspace_sync_module, "workspace_registry_is_git_tracked", lambda _root: True)

    result = reconcile_workspace_db_to_materialized(ctx)

    assert result["registry_persisted"] is False
    assert result["registry_authority"] == "git"
    assert catalog_path.read_bytes() == original
    assert [row.name for row in SqliteSkillRegistry(sql).list()] == ["weather_skill"]
