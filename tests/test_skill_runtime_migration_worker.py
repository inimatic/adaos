from __future__ import annotations

import importlib
from types import SimpleNamespace

worker = importlib.import_module("adaos.services.skill.runtime_migration_worker")


class _FakeManager:
    def __init__(self, versions: dict[str, str]) -> None:
        self._versions = versions

    def runtime_status(self, name: str) -> dict:
        version = self._versions.get(name)
        if not version:
            raise RuntimeError("no versions installed")
        return {"name": name, "version": version, "active_slot": "A"}


def test_migration_candidates_include_only_runtime_behind(monkeypatch, tmp_path):
    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: tmp_path, skills_workspace_dir=lambda: tmp_path / "skills"))
    workspace_versions = {
        "fresh_skill": "1.2.0",
        "old_skill": "1.2.0",
        "missing_runtime_skill": "0.1.0",
    }
    for skill_name in workspace_versions:
        (tmp_path / "skills" / skill_name).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_registered_skill_names", lambda _ctx: sorted(workspace_versions))
    monkeypatch.setattr(worker, "_registry_versions", lambda _ctx: {})
    monkeypatch.setattr(worker, "_workspace_skill_source", lambda _ctx, name: tmp_path / "skills" / name)
    monkeypatch.setattr(worker, "_read_local_artifact_version", lambda path: workspace_versions[path.name])

    result = worker.migration_candidates(
        ctx,
        _FakeManager({"fresh_skill": "1.2.0", "old_skill": "1.1.9"}),
    )

    assert [item["skill"] for item in result] == ["missing_runtime_skill", "old_skill"]
    assert {item["reason"] for item in result} == {"runtime_version_behind"}


def test_migration_candidates_force_includes_requested_name(monkeypatch, tmp_path):
    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: tmp_path, skills_workspace_dir=lambda: tmp_path / "skills"))

    monkeypatch.setattr(worker, "_registered_skill_names", lambda _ctx: [])
    monkeypatch.setattr(worker, "_registry_versions", lambda _ctx: {})
    monkeypatch.setattr(worker, "_workspace_skill_source", lambda _ctx, name: tmp_path / "skills" / name)
    monkeypatch.setattr(worker, "_read_local_artifact_version", lambda path: "1.0.0")
    (tmp_path / "skills" / "target_skill").mkdir(parents=True, exist_ok=True)

    result = worker.migration_candidates(
        ctx,
        _FakeManager({"target_skill": "1.0.0"}),
        name="target_skill",
        force=True,
    )

    assert [item["skill"] for item in result] == ["target_skill"]
    assert result[0]["reason"] == "force"
