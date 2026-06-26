from __future__ import annotations

import importlib
import json
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


def test_read_status_marks_stale_refresh_runtime_as_prepare_stall(monkeypatch, tmp_path):
    ctx = SimpleNamespace(
        paths=SimpleNamespace(
            base_dir=lambda: tmp_path,
            workspace_dir=lambda: tmp_path / "workspace",
        )
    )
    status_dir = tmp_path / "state" / "skill_runtime_migration"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "status.json").write_text(
        json.dumps(
            {
                "ok": True,
                "state": "running",
                "phase": "migrate",
                "pending": True,
                "operation_id": "skill-migrate-test",
                "started_at": 1000.0,
                "updated_at": 1000.0,
                "current": {"skill": "new_face_vision_skill", "index": 1, "stage": "refresh_runtime"},
                "skills": [
                    {
                        "skill": "new_face_vision_skill",
                        "stage": "refresh_runtime",
                        "source_path": str(tmp_path / "workspace" / "skills" / "new_face_vision_skill"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(worker, "_now", lambda: 1700.0)
    monkeypatch.setattr(worker, "_io_pressure_snapshot", lambda _ctx, _payload: {"available": True, "pressure": True})

    status = worker.read_status(ctx)

    diagnostics = status["diagnostics"]
    assert diagnostics["stale"] is True
    assert diagnostics["updated_age_s"] == 700.0
    assert diagnostics["current_skill"] == "new_face_vision_skill"
    assert diagnostics["current_stage"] == "refresh_runtime"
    assert diagnostics["suspected_blocker"] == "dependency_install_or_runtime_prepare_stalled"
    assert "inspect runtime prepare/install logs for the current skill" in diagnostics["recommendations"]


def test_read_status_classifies_sqlite_lock_failure(tmp_path):
    ctx = SimpleNamespace(
        paths=SimpleNamespace(
            base_dir=lambda: tmp_path,
            workspace_dir=lambda: tmp_path / "workspace",
        )
    )
    status_dir = tmp_path / "state" / "skill_runtime_migration"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "status.json").write_text(
        json.dumps(
            {
                "ok": False,
                "state": "failed",
                "phase": "migrate",
                "pending": False,
                "operation_id": "skill-migrate-test",
                "updated_at": 1000.0,
                "message": "skill runtime migration failed",
                "current": {"skill": "mediaserver", "index": 1, "stage": "refresh_runtime"},
                "skills": [
                    {
                        "skill": "mediaserver",
                        "stage": "refresh_runtime",
                        "error": "sqlite3.OperationalError: database is locked",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    status = worker.read_status(ctx)

    diagnostics = status["diagnostics"]
    assert diagnostics["state"] == "failed"
    assert diagnostics["stale"] is False
    assert diagnostics["suspected_blocker"] == "sqlite_lock"
    assert "inspect disk usage, /proc/pressure/io, and SQLite lock holders on the stand" in diagnostics["recommendations"]
