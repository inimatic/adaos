from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

if "y_py" not in sys.modules:
    sys.modules["y_py"] = types.SimpleNamespace(
        YDoc=type("YDoc", (), {}),
        encode_state_vector=lambda *args, **kwargs: b"",
        encode_state_as_update=lambda *args, **kwargs: b"",
        apply_update=lambda *args, **kwargs: None,
    )
if "ypy_websocket.ystore" not in sys.modules:
    ystore_module = types.ModuleType("ypy_websocket.ystore")
    ystore_module.BaseYStore = type("BaseYStore", (), {})
    ystore_module.YDocNotFound = type("YDocNotFound", (Exception,), {})
    sys.modules["ypy_websocket.ystore"] = ystore_module
if "ypy_websocket" not in sys.modules:
    pkg = types.ModuleType("ypy_websocket")
    pkg.ystore = sys.modules["ypy_websocket.ystore"]
    sys.modules["ypy_websocket"] = pkg

from adaos.services.scenario.manager import ScenarioManager
from adaos.services.skill.manager import SkillManager


class _FakeCaps:
    def require(self, *args, **kwargs) -> None:
        return None


class _FakeGit:
    def __init__(self) -> None:
        self.commit_calls: list[dict[str, object]] = []
        self.push_calls: list[str] = []
        self.pull_calls: list[str] = []
        self.sparse_add_calls: list[tuple[str, str]] = []

    def changed_files(self, root: str, *, subpath: str):
        if subpath == "registry.json":
            return ["registry.json"]
        return [subpath]

    def sparse_add(self, root: str, path: str) -> None:
        self.sparse_add_calls.append((root, path))

    def pull(self, root: str) -> None:
        self.pull_calls.append(root)

    def commit_subpath(self, root: str, *, subpath, message: str, author_name: str, author_email: str, signoff: bool = False):
        self.commit_calls.append(
            {
                "root": root,
                "subpath": subpath,
                "message": message,
                "author_name": author_name,
                "author_email": author_email,
                "signoff": signoff,
            }
        )
        return "rev-1"

    def push(self, root: str) -> None:
        self.push_calls.append(root)


class _FakeRootClient:
    def __init__(self) -> None:
        self.manifest: dict[str, object] = {}
        self.uploads: list[dict[str, object]] = []

    def get_skill_model_manifest(self, **kwargs) -> dict[str, object]:
        return dict(self.manifest)

    def upload_skill_model_artifact(self, **kwargs) -> dict[str, object]:
        self.uploads.append(dict(kwargs))
        return {"manifest": {"version_id": "v-test"}}


class _FakeTxn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeMap(dict):
    def set(self, txn, key: str, value: object) -> None:  # noqa: ARG002
        self[key] = value


class _FakeDoc:
    def __init__(self, state: dict[str, _FakeMap]) -> None:
        self._state = state

    def begin_transaction(self) -> _FakeTxn:
        return _FakeTxn()

    def get_map(self, name: str) -> _FakeMap:
        return self._state.setdefault(name, _FakeMap())


def _workspace_ctx(workspace: Path, git: _FakeGit) -> SimpleNamespace:
    return SimpleNamespace(
        git=git,
        paths=SimpleNamespace(workspace_dir=lambda: workspace),
        settings=SimpleNamespace(
            base_dir=str(workspace),
            git_author_name="Ada Tester",
            git_author_email="tester@adaos.local",
        ),
    )


def test_skill_push_updates_registry_and_commits_it(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    skill_dir = workspace / "skills" / "demo_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "id: demo_skill",
                "name: Demo Skill",
                "version: '1.0.0'",
                "description: Initial skill",
                "",
            ]
        ),
        encoding="utf-8",
    )

    git = _FakeGit()
    monkeypatch.setattr("adaos.services.skill.manager.get_git_availability", lambda base_dir=None: SimpleNamespace(enabled=True), raising=False)

    manager = object.__new__(SkillManager)
    manager.caps = _FakeCaps()
    manager.settings = SimpleNamespace(git_author_name="Ada Tester", git_author_email="tester@adaos.local")
    manager.ctx = _workspace_ctx(workspace, git)

    revision = manager.push("demo_skill", "publish demo skill")

    registry = json.loads((workspace / "registry.json").read_text(encoding="utf-8"))
    assert revision == "rev-1"
    assert [item["id"] for item in registry["skills"]] == ["demo_skill"]
    assert git.sparse_add_calls == [(str(workspace), "skills/demo_skill")]
    assert git.pull_calls == []
    assert git.commit_calls[0]["subpath"] == ["skills/demo_skill", "registry.json"]
    assert git.push_calls == [str(workspace)]


def test_skill_push_without_bump_catches_registry_up_to_manifest(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    skill_dir = workspace / "skills" / "weather_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "id: weather_skill",
                "name: Weather Skill",
                "version: '2.6.5'",
                "description: Weather",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (workspace / "registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-05-26T00:00:00+00:00",
                "skills": [{"kind": "skill", "id": "weather_skill", "name": "weather_skill", "version": "2.6.4"}],
                "scenarios": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    git = _FakeGit()
    monkeypatch.setattr("adaos.services.skill.manager.get_git_availability", lambda base_dir=None: SimpleNamespace(enabled=True), raising=False)

    manager = object.__new__(SkillManager)
    manager.caps = _FakeCaps()
    manager.settings = SimpleNamespace(git_author_name="Ada Tester", git_author_email="tester@adaos.local")
    manager.ctx = _workspace_ctx(workspace, git)
    manager.reg = None

    revision = manager.push("weather_skill", "catch registry up", bump=False)

    registry = json.loads((workspace / "registry.json").read_text(encoding="utf-8"))
    skill_yaml = (skill_dir / "skill.yaml").read_text(encoding="utf-8")
    assert revision == "rev-1"
    assert "version: '2.6.5'" in skill_yaml
    assert registry["skills"][0]["version"] == "2.6.5"
    assert git.commit_calls[0]["subpath"] == ["skills/weather_skill", "registry.json"]


def test_skill_push_uses_existing_registry_entry_when_manifest_is_missing(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    skill_dir = workspace / "skills" / "browsers_skill"
    (skill_dir / "handlers").mkdir(parents=True)
    (skill_dir / "handlers" / "main.py").write_text("def tool():\n    return {'ok': True}\n", encoding="utf-8")
    (workspace / "registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-05-06T20:56:18+00:00",
                "skills": [
                    {
                        "kind": "skill",
                        "id": "browsers_skill",
                        "name": "browsers_skill",
                        "version": "0.4.0",
                        "updated_at": "2026-05-06T20:56:18+00:00",
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

    git = _FakeGit()
    monkeypatch.setattr("adaos.services.skill.manager.get_git_availability", lambda base_dir=None: SimpleNamespace(enabled=True), raising=False)

    manager = object.__new__(SkillManager)
    manager.caps = _FakeCaps()
    manager.settings = SimpleNamespace(git_author_name="Ada Tester", git_author_email="tester@adaos.local")
    manager.ctx = _workspace_ctx(workspace, git)

    revision = manager.push("browsers_skill", "publish browsers skill")

    registry = json.loads((workspace / "registry.json").read_text(encoding="utf-8"))
    assert revision == "rev-1"
    assert [item["id"] for item in registry["skills"]] == ["browsers_skill"]
    assert registry["skills"][0]["entry"] == "handlers/main.py"
    assert git.sparse_add_calls == [(str(workspace), "skills/browsers_skill")]
    assert git.pull_calls == [str(workspace)]
    assert git.commit_calls[0]["subpath"] == ["skills/browsers_skill", "registry.json"]
    assert git.push_calls == [str(workspace)]


def test_private_model_artifacts_are_not_uploaded_by_default(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo_skill"
    (skill_dir / "models").mkdir(parents=True)
    (skill_dir / "models" / "model.pt").write_bytes(b"private-weights")
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "name: demo_skill",
                "models:",
                "  private: true",
                "  artifacts:",
                "    weights:",
                "      path: models/model.pt",
                "",
            ]
        ),
        encoding="utf-8",
    )

    manager = object.__new__(SkillManager)
    manager._root_client = lambda: (_ for _ in ()).throw(AssertionError("private models should not contact root"))

    result = manager._push_declared_model_artifacts(skill_dir, skill_name="demo_skill")

    assert result == [
        {
            "key": "weights",
            "artifact": "model.pt",
            "private": True,
            "skipped": True,
            "reason": "private_model",
        }
    ]


def test_private_model_artifacts_upload_with_explicit_override(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo_skill"
    (skill_dir / "models").mkdir(parents=True)
    (skill_dir / "models" / "model.pt").write_bytes(b"private-weights")
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "name: demo_skill",
                "models:",
                "  private: true",
                "  artifacts:",
                "    weights:",
                "      path: models/model.pt",
                "",
            ]
        ),
        encoding="utf-8",
    )

    client = _FakeRootClient()
    manager = object.__new__(SkillManager)
    manager._root_client = lambda: client

    result = manager._push_declared_model_artifacts(
        skill_dir,
        skill_name="demo_skill",
        publish_private=True,
    )

    assert result[0]["key"] == "weights"
    assert result[0]["skipped"] is False
    assert client.uploads[0]["name"] == "demo_skill"
    assert client.uploads[0]["artifact"] == "model.pt"


def test_scenario_push_updates_registry_and_commits_it(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    scenario_dir = workspace / "scenarios" / "welcome_scene"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.json").write_text(
        json.dumps(
            {
                "id": "welcome_scene",
                "name": "Welcome Scene",
                "version": "0.1.0",
                "description": "Initial scenario",
            }
        ),
        encoding="utf-8",
    )

    git = _FakeGit()
    ctx = _workspace_ctx(workspace, git)
    monkeypatch.setattr("adaos.services.scenario.manager.get_git_availability", lambda base_dir=None: SimpleNamespace(enabled=True), raising=False)
    monkeypatch.setattr("adaos.services.scenario.manager.get_ctx", lambda: ctx)

    manager = object.__new__(ScenarioManager)
    manager.caps = _FakeCaps()
    manager.git = git
    manager.ctx = ctx

    revision = manager.push("welcome_scene", "publish welcome scenario")

    registry = json.loads((workspace / "registry.json").read_text(encoding="utf-8"))
    assert revision == "rev-1"
    assert [item["id"] for item in registry["scenarios"]] == ["welcome_scene"]
    assert git.sparse_add_calls == [(str(workspace), "scenarios/welcome_scene")]
    assert git.pull_calls == []
    assert git.commit_calls[0]["subpath"] == ["scenarios/welcome_scene", "registry.json"]
    assert git.push_calls == [str(workspace)]


def test_scenario_push_uses_existing_registry_entry_when_manifest_is_missing(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    scenario_dir = workspace / "scenarios" / "welcome_scene"
    (scenario_dir / "docs").mkdir(parents=True)
    (scenario_dir / "docs" / "note.md").write_text("# hello\n", encoding="utf-8")
    (workspace / "registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-05-06T20:56:18+00:00",
                "skills": [],
                "scenarios": [
                    {
                        "kind": "scenario",
                        "id": "welcome_scene",
                        "name": "welcome_scene",
                        "version": "0.1.0",
                        "updated_at": "2026-05-06T20:56:18+00:00",
                        "path": "scenarios/welcome_scene",
                        "manifest": "scenarios/welcome_scene/scenario.yaml",
                        "install": {
                            "kind": "scenario",
                            "name": "welcome_scene",
                            "id": "welcome_scene",
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    git = _FakeGit()
    ctx = _workspace_ctx(workspace, git)
    monkeypatch.setattr("adaos.services.scenario.manager.get_git_availability", lambda base_dir=None: SimpleNamespace(enabled=True), raising=False)
    monkeypatch.setattr("adaos.services.scenario.manager.get_ctx", lambda: ctx)

    manager = object.__new__(ScenarioManager)
    manager.caps = _FakeCaps()
    manager.git = git
    manager.ctx = ctx

    revision = manager.push("welcome_scene", "publish welcome scenario")

    registry = json.loads((workspace / "registry.json").read_text(encoding="utf-8"))
    assert revision == "rev-1"
    assert [item["id"] for item in registry["scenarios"]] == ["welcome_scene"]
    assert git.sparse_add_calls == [(str(workspace), "scenarios/welcome_scene")]
    assert git.pull_calls == [str(workspace)]
    assert git.commit_calls[0]["subpath"] == ["scenarios/welcome_scene", "registry.json"]
    assert git.push_calls == [str(workspace)]


def test_scenario_project_to_doc_keeps_runtime_owned_effective_data_under_rebuild_ownership(monkeypatch) -> None:
    manager = object.__new__(ScenarioManager)
    manager.caps = _FakeCaps()
    monkeypatch.setattr("adaos.services.scenario.manager._local_node_id", lambda: "node-1")

    state = {
        "ui": _FakeMap(
            {
                "application": {
                    "desktop": {
                        "pageSchema": {"id": "live-page"},
                    }
                }
            }
        ),
        "registry": _FakeMap({"merged": {"modals": ["live-modal"]}}),
        "data": _FakeMap(
            {
                "catalog": {"apps": [{"id": "live-app"}]},
                "installed": {"apps": ["scenario:web_desktop"], "widgets": []},
                "desktop": {"pageSchema": {"id": "live-desktop"}},
                "routing": {"routes": {"home": "/"}},
            }
        ),
    }

    manager._project_to_doc(
        _FakeDoc(state),
        "prompt_engineer_scenario",
        ui_section={"desktop": {"pageSchema": {"id": "legacy-page"}}},
        registry_section={"modals": ["legacy-modal"]},
        catalog_section={"apps": [{"id": "legacy-app"}]},
        data_section={
            "catalog": {"apps": [{"id": "should-not-overwrite"}]},
            "installed": {"apps": ["should-not-overwrite"]},
            "desktop": {"pageSchema": {"id": "should-not-overwrite"}},
            "routing": {"routes": {"home": "/should-not-overwrite"}},
            "weather": {"city": "Moscow"},
        },
    )

    assert state["ui"]["application"]["desktop"]["pageSchema"]["id"] == "live-page"
    assert state["registry"]["merged"]["modals"] == ["live-modal"]
    assert state["data"]["catalog"]["apps"] == [{"id": "live-app"}]
    assert state["data"]["installed"]["apps"] == ["scenario:web_desktop"]
    assert state["data"]["desktop"]["pageSchema"]["id"] == "live-desktop"
    assert state["data"]["routing"]["routes"]["home"] == "/"
    assert state["ui"]["current_scenario"] == "prompt_engineer_scenario"
    assert state["ui"]["scenarios"]["node-1"]["prompt_engineer_scenario"]["application"]["desktop"]["pageSchema"]["id"] == "legacy-page"
    assert state["registry"]["scenarios"]["node-1"]["prompt_engineer_scenario"]["modals"] == ["legacy-modal"]
    assert state["data"]["scenarios"]["node-1"]["prompt_engineer_scenario"]["catalog"]["apps"] == [{"id": "legacy-app"}]
    assert state["data"]["weather"] == {"city": "Moscow"}
