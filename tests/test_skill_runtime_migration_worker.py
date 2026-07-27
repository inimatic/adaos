from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

worker = importlib.import_module("adaos.services.skill.runtime_migration_worker")


class _FakeManager:
    def __init__(self, versions: dict[str, str], *, deactivated: set[str] | None = None) -> None:
        self._versions = versions
        self._deactivated = set(deactivated or ())

    def runtime_status(self, name: str) -> dict:
        version = self._versions.get(name)
        if not version:
            raise RuntimeError("no versions installed")
        is_deactivated = name in self._deactivated
        return {
            "name": name,
            "version": version,
            "active_slot": "A",
            "deactivated": is_deactivated,
            "deactivation": (
                {
                    "reason": "runtime_migration_failed",
                    "failed_stage": "tests",
                    "failure_kind": "migration",
                    "comment": "pytest exit code -15",
                    "operation_id": "skill-migrate-old",
                }
                if is_deactivated
                else {}
            ),
        }


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


def test_migration_candidates_explicitly_recovers_same_version_quarantine(monkeypatch, tmp_path):
    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: tmp_path, skills_workspace_dir=lambda: tmp_path / "skills"))
    skill_dir = tmp_path / "skills" / "infrastate_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_registered_skill_names", lambda _ctx: ["infrastate_skill"])
    monkeypatch.setattr(worker, "_registry_versions", lambda _ctx: {})
    monkeypatch.setattr(worker, "_workspace_skill_source", lambda _ctx, name: tmp_path / "skills" / name)
    monkeypatch.setattr(worker, "_read_local_artifact_version", lambda _path: "0.75.59")

    result = worker.migration_candidates(
        ctx,
        _FakeManager({"infrastate_skill": "0.75.59"}, deactivated={"infrastate_skill"}),
        name="infrastate_skill",
    )

    assert [item["skill"] for item in result] == ["infrastate_skill"]
    assert result[0]["reason"] == "explicit_quarantine_recovery"
    assert result[0]["deactivated"] is True


def test_background_discovery_reports_quarantine_without_retrying_it(monkeypatch, tmp_path):
    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: tmp_path, skills_workspace_dir=lambda: tmp_path / "skills"))
    skill_dir = tmp_path / "skills" / "infrastate_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    manager = _FakeManager({"infrastate_skill": "0.75.59"}, deactivated={"infrastate_skill"})

    monkeypatch.setattr(worker, "_registered_skill_names", lambda _ctx: ["infrastate_skill"])
    monkeypatch.setattr(worker, "_registry_versions", lambda _ctx: {})
    monkeypatch.setattr(worker, "_workspace_skill_source", lambda _ctx, name: tmp_path / "skills" / name)
    monkeypatch.setattr(worker, "_read_local_artifact_version", lambda _path: "0.75.59")

    assert worker.migration_candidates(ctx, manager) == []
    assert worker.quarantined_runtimes(ctx, manager) == [
        {
            "skill": "infrastate_skill",
            "version": "0.75.59",
            "active_slot": "A",
            "reason": "runtime_migration_failed",
            "failed_stage": "tests",
            "failure_kind": "migration",
            "comment": "pytest exit code -15",
            "operation_id": "skill-migrate-old",
        }
    ]


def test_explicit_quarantine_recovery_retries_once_and_clears_status(monkeypatch, tmp_path):
    ctx = SimpleNamespace()
    refresh_calls: list[dict] = []

    class _Manager:
        def deactivate_runtime(self, name: str, **kwargs):
            assert name == "infrastate_skill"
            assert kwargs["reason"] == "runtime_migration_in_progress"
            return {"deactivated": True, "transient": True}

        def run_skill_tests(self, name: str, *, source: str):
            assert (name, source) == ("infrastate_skill", "installed")
            return {"pytest": SimpleNamespace(status="passed", detail=None)}

    def _refresh(_mgr, name: str, **kwargs):
        assert name == "infrastate_skill"
        refresh_calls.append(kwargs)
        return {"ok": True, "runtime_migrated": True, "active_converged": True}

    writes: list[dict] = []
    monkeypatch.setattr(worker, "_manager", lambda _ctx: _Manager())
    monkeypatch.setattr(
        worker,
        "migration_candidates",
        lambda *_args, **_kwargs: [
            {
                "skill": "infrastate_skill",
                "workspace_version": "0.75.59",
                "runtime_version": "0.75.59",
                "deactivated": True,
                "reason": "explicit_quarantine_recovery",
            }
        ],
    )
    quarantine_snapshots = iter([[{"skill": "infrastate_skill"}], []])
    monkeypatch.setattr(worker, "quarantined_runtimes", lambda *_args: next(quarantine_snapshots))
    monkeypatch.setattr(worker, "refresh_skill_runtime", _refresh)
    monkeypatch.setattr(worker, "_reload_live_skill_handlers_sync", lambda *_args: {"ok": True})
    monkeypatch.setattr(worker, "rebuild_webspace_projection_sync", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(worker, "_write_status", lambda _ctx, payload: writes.append(dict(payload)) or dict(payload))

    result = worker._run_migration_sync(
        ctx,
        operation_id="skill-migrate-new",
        webspace_id="desktop",
        force=False,
        run_tests=True,
        name="infrastate_skill",
        sync_workspace=False,
    )

    assert result["ok"] is True
    assert result["state"] == "succeeded"
    assert result["quarantined_total"] == 0
    assert result["skills"][0]["deactivation_cleared"] is True
    assert result["skills"][0]["tests"] == {"pytest": {"status": "passed", "detail": ""}}
    assert len(refresh_calls) == 1
    assert refresh_calls[0]["retry_deactivated"] is True


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
