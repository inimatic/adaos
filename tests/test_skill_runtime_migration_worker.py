from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
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
                    "attempted_version": version,
                    "attempted_core_identity": worker._core_runtime_identity(),
                }
                if is_deactivated
                else {}
            ),
        }


def test_global_migration_lease_serializes_runtime_processes(tmp_path) -> None:
    ctx = SimpleNamespace(paths=SimpleNamespace(base_dir=lambda: tmp_path))

    first = worker._try_acquire_global_lease(ctx, operation_id="first")
    assert first is not None
    try:
        assert worker._try_acquire_global_lease(ctx, operation_id="second") is None
    finally:
        worker._release_global_lease(first)

    third = worker._try_acquire_global_lease(ctx, operation_id="third")
    assert third is not None
    worker._release_global_lease(third)


def test_status_diagnostics_reports_worker_process_tree(tmp_path) -> None:
    ctx = SimpleNamespace(paths=SimpleNamespace(base_dir=lambda: tmp_path, workspace_dir=lambda: tmp_path))
    payload = {
        "state": "running",
        "phase": "migrate",
        "pending": True,
        "worker_pid": os.getpid(),
        "worker_mode": "subprocess",
        "started_at": worker._now(),
        "updated_at": worker._now(),
    }

    diagnostics = worker._status_diagnostics(ctx, payload)

    process = diagnostics["worker_process"]
    assert process["available"] is True
    assert process["worker_pid"] == os.getpid()
    assert any(item["kind"] == "migration_worker" for item in process["active_workloads"])


def test_isolated_worker_entrypoint_records_terminal_status(tmp_path) -> None:
    env = dict(os.environ)
    env["ADAOS_BASE_DIR"] = str(tmp_path)
    env["ADAOS_TESTING"] = "1"
    env["ADAOS_SKILL_MIGRATION_WORKER_PROCESS"] = "1"
    env.pop("ADAOS_SKILL_MIGRATION_WORKER_PRIORITY", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "adaos.services.skill.runtime_migration_worker",
            "--operation-id",
            "smoke-worker",
            "--webspace-id",
            "desktop",
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    status = json.loads((tmp_path / "state" / "skill_runtime_migration" / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "succeeded"
    assert status["phase"] == "complete"
    assert status["pending"] is False
    assert status["total"] == 0
    assert status["worker_mode"] == "subprocess"
    assert status["worker_priority"] == "normal"
    assert status["worker_pid"] > 0


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


def test_registered_skill_names_includes_selected_runtime_when_sqlite_lost_intent(monkeypatch):
    class _Registry:
        def __init__(self, _sql) -> None:
            pass

        def list(self) -> list:
            return [SimpleNamespace(name="database_skill", installed=True)]

    monkeypatch.setattr(worker, "SqliteSkillRegistry", _Registry)
    monkeypatch.setattr(worker, "selected_runtime_skill_names", lambda _ctx: ["weather_skill"])

    assert worker._registered_skill_names(SimpleNamespace(sql=object())) == ["database_skill", "weather_skill"]


def test_migration_candidates_use_registry_version_for_sparse_placeholder(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    placeholder = skills_root / "weather_skill"
    (placeholder / "handlers").mkdir(parents=True)
    ctx = SimpleNamespace(
        paths=SimpleNamespace(
            workspace_dir=lambda: tmp_path,
            skills_workspace_dir=lambda: skills_root,
        )
    )

    monkeypatch.setattr(worker, "_registered_skill_names", lambda _ctx: ["weather_skill"])
    monkeypatch.setattr(worker, "_registry_versions", lambda _ctx: {"weather_skill": "2.6.23"})

    result = worker.migration_candidates(ctx, _FakeManager({"weather_skill": "2.6.12"}))

    assert result == [
        {
            "skill": "weather_skill",
            "workspace_version": "2.6.23",
            "runtime_version": "2.6.12",
            "source_path": str(placeholder),
            "source_materialized": False,
            "version_source": "workspace_registry",
            "reason": "runtime_version_behind",
            "deactivated": False,
            "deactivation": {},
        }
    ]


def test_migration_candidates_exclude_uninstalled_workspace_artifacts(monkeypatch, tmp_path):
    ctx = SimpleNamespace(
        paths=SimpleNamespace(
            workspace_dir=lambda: tmp_path,
            skills_workspace_dir=lambda: tmp_path / "skills",
        )
    )
    for skill_name in ("installed_skill", "workspace_only_skill"):
        (tmp_path / "skills" / skill_name).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_registered_skill_names", lambda _ctx: ["installed_skill"])
    monkeypatch.setattr(
        worker,
        "_registry_versions",
        lambda _ctx: {"installed_skill": "1.1.0", "workspace_only_skill": "9.0.0"},
    )
    monkeypatch.setattr(worker, "_workspace_skill_source", lambda _ctx, name: tmp_path / "skills" / name)
    monkeypatch.setattr(
        worker,
        "_read_local_artifact_version",
        lambda path: "1.1.0" if path.name == "installed_skill" else "9.0.0",
    )

    result = worker.migration_candidates(ctx, _FakeManager({}))

    assert [item["skill"] for item in result] == ["installed_skill"]


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


def test_background_discovery_retries_quarantine_once_after_core_update(monkeypatch, tmp_path):
    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: tmp_path, skills_workspace_dir=lambda: tmp_path / "skills"))
    skill_dir = tmp_path / "skills" / "infrastate_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)

    class _Manager:
        def runtime_status(self, _name: str) -> dict:
            return {
                "name": "infrastate_skill",
                "version": "0.75.59",
                "active_slot": "A",
                "deactivated": True,
                "deactivation": {
                    "reason": "runtime_migration_failed",
                    "committed_core_switch": False,
                    "attempted_version": "0.75.59",
                    "attempted_core_identity": "core-old",
                },
            }

    monkeypatch.setattr(worker, "_registered_skill_names", lambda _ctx: ["infrastate_skill"])
    monkeypatch.setattr(worker, "_registry_versions", lambda _ctx: {})
    monkeypatch.setattr(worker, "_workspace_skill_source", lambda _ctx, name: tmp_path / "skills" / name)
    monkeypatch.setattr(worker, "_read_local_artifact_version", lambda _path: "0.75.59")

    monkeypatch.setattr(worker, "_core_runtime_identity", lambda: "core-new")
    candidates = worker.migration_candidates(ctx, _Manager())
    assert [item["skill"] for item in candidates] == ["infrastate_skill"]
    assert candidates[0]["reason"] == "recover_after_core_update"

    monkeypatch.setattr(worker, "_core_runtime_identity", lambda: "core-old")
    assert worker.migration_candidates(ctx, _Manager()) == []


def test_background_discovery_recovers_newer_candidate_behind_legacy_quarantine(monkeypatch, tmp_path):
    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: tmp_path, skills_workspace_dir=lambda: tmp_path / "skills"))
    skill_dir = tmp_path / "skills" / "infrastate_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_registered_skill_names", lambda _ctx: ["infrastate_skill"])
    monkeypatch.setattr(worker, "_registry_versions", lambda _ctx: {})
    monkeypatch.setattr(worker, "_workspace_skill_source", lambda _ctx, name: tmp_path / "skills" / name)
    monkeypatch.setattr(worker, "_read_local_artifact_version", lambda _path: "0.75.60")

    result = worker.migration_candidates(
        ctx,
        _FakeManager({"infrastate_skill": "0.75.59"}, deactivated={"infrastate_skill"}),
    )

    assert [item["skill"] for item in result] == ["infrastate_skill"]
    assert result[0]["reason"] == "recover_precommit_migration_failure"


def test_background_discovery_does_not_repeat_same_failed_candidate_and_core(monkeypatch, tmp_path):
    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: tmp_path, skills_workspace_dir=lambda: tmp_path / "skills"))
    skill_dir = tmp_path / "skills" / "infrastate_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)

    class _Manager:
        def runtime_status(self, _name: str) -> dict:
            return {
                "version": "0.75.59",
                "active_slot": "A",
                "deactivated": True,
                "deactivation": {
                    "reason": "runtime_migration_failed",
                    "committed_core_switch": False,
                    "attempted_version": "0.75.60",
                    "attempted_core_identity": "core-current",
                },
            }

    monkeypatch.setattr(worker, "_registered_skill_names", lambda _ctx: ["infrastate_skill"])
    monkeypatch.setattr(worker, "_registry_versions", lambda _ctx: {})
    monkeypatch.setattr(worker, "_workspace_skill_source", lambda _ctx, name: tmp_path / "skills" / name)
    monkeypatch.setattr(worker, "_read_local_artifact_version", lambda _path: "0.75.60")
    monkeypatch.setattr(worker, "_core_runtime_identity", lambda: "core-current")

    assert worker.migration_candidates(ctx, _Manager()) == []


def test_background_discovery_skips_same_rejected_candidate_but_accepts_newer(monkeypatch, tmp_path):
    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: tmp_path, skills_workspace_dir=lambda: tmp_path / "skills"))
    skill_dir = tmp_path / "skills" / "weather_skill"
    skill_dir.mkdir(parents=True)
    workspace_version = {"value": "2.6.18"}

    class _Manager:
        def runtime_status(self, _name: str) -> dict:
            return {
                "version": "2.6.17",
                "active_slot": "B",
                "deactivated": False,
                "deactivation": {
                    "reason": "runtime_migration_failed",
                    "status": "candidate_quarantined",
                    "committed_core_switch": False,
                    "attempted_version": "2.6.18",
                },
            }

    monkeypatch.setattr(worker, "_registered_skill_names", lambda _ctx: ["weather_skill"])
    monkeypatch.setattr(worker, "_registry_versions", lambda _ctx: {})
    monkeypatch.setattr(worker, "_workspace_skill_source", lambda _ctx, name: tmp_path / "skills" / name)
    monkeypatch.setattr(worker, "_read_local_artifact_version", lambda _path: workspace_version["value"])

    assert worker.migration_candidates(ctx, _Manager()) == []

    workspace_version["value"] = "2.6.19"
    result = worker.migration_candidates(ctx, _Manager())

    assert [item["skill"] for item in result] == ["weather_skill"]
    assert result[0]["reason"] == "runtime_version_behind"


def test_precommit_quarantine_recovers_automatically_for_newer_candidate(monkeypatch, tmp_path):
    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: tmp_path, skills_workspace_dir=lambda: tmp_path / "skills"))
    skill_dir = tmp_path / "skills" / "weather_skill"
    skill_dir.mkdir(parents=True)

    class _Manager:
        def runtime_status(self, _name: str) -> dict:
            return {
                "version": "2.6.17",
                "active_slot": "B",
                "deactivated": True,
                "deactivation": {
                    "reason": "runtime_migration_failed",
                    "committed_core_switch": False,
                    "failed_stage": "prepare",
                },
            }

    monkeypatch.setattr(worker, "_registered_skill_names", lambda _ctx: ["weather_skill"])
    monkeypatch.setattr(worker, "_registry_versions", lambda _ctx: {})
    monkeypatch.setattr(worker, "_workspace_skill_source", lambda _ctx, name: tmp_path / "skills" / name)
    monkeypatch.setattr(worker, "_read_local_artifact_version", lambda _path: "2.6.18")

    result = worker.migration_candidates(ctx, _Manager())

    assert [item["skill"] for item in result] == ["weather_skill"]
    assert result[0]["reason"] == "recover_precommit_migration_failure"


def test_explicit_quarantine_recovery_retries_once_and_clears_status(monkeypatch, tmp_path):
    ctx = SimpleNamespace()
    refresh_calls: list[dict] = []

    class _Manager:
        def deactivate_runtime(self, name: str, **kwargs):
            raise AssertionError("migration must not disable the active runtime before prepare")

        def runtime_status(self, name: str):
            return {"version": "0.75.59", "active_slot": "A", "deactivated": True}

    def _refresh(_mgr, name: str, **kwargs):
        assert name == "infrastate_skill"
        refresh_calls.append(kwargs)
        return {
            "ok": True,
            "runtime_migrated": True,
            "active_converged": True,
            "tests": {"pytest": {"status": "passed", "detail": ""}},
        }

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
    monkeypatch.setattr(worker, "failed_candidate_runtimes", lambda *_args: [])
    monkeypatch.setattr(worker, "_installed_runtime_version_records", lambda *_args, **_kwargs: [])
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
    assert result["skills"][0]["disabled_for_migration"] is False
    assert result["skills"][0]["tests"] == {"pytest": {"status": "passed", "detail": ""}}
    assert len(refresh_calls) == 1
    assert refresh_calls[0]["ensure_installed"] is False
    assert refresh_calls[0]["retry_deactivated"] is True
    assert refresh_calls[0]["defer_webspace_rebuild"] is True
    assert refresh_calls[0]["run_candidate_tests"] is True


def test_noop_migration_skips_webspace_rebuild(monkeypatch):
    rebuild_calls: list[dict] = []

    monkeypatch.setattr(worker, "_manager", lambda _ctx: object())
    monkeypatch.setattr(worker, "migration_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(worker, "quarantined_runtimes", lambda *_args: [])
    monkeypatch.setattr(worker, "failed_candidate_runtimes", lambda *_args: [])
    monkeypatch.setattr(worker, "_installed_runtime_version_records", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        worker,
        "rebuild_webspace_projection_sync",
        lambda **kwargs: rebuild_calls.append(dict(kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(worker, "_write_status", lambda _ctx, payload: dict(payload))

    result = worker._run_migration_sync(
        SimpleNamespace(),
        operation_id="skill-migrate-noop",
        webspace_id="desktop",
        force=False,
        run_tests=True,
        name=None,
        sync_workspace=False,
    )

    assert result["ok"] is True
    assert result["state"] == "succeeded"
    assert result["webspace_rebuild"] == {
        "ok": True,
        "skipped": True,
        "reason": "no_runtime_changes",
        "webspace_id": "desktop",
    }
    assert rebuild_calls == []


def test_noop_migration_fails_when_runtime_drift_remains(monkeypatch):
    monkeypatch.setattr(worker, "_manager", lambda _ctx: object())
    monkeypatch.setattr(worker, "migration_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(worker, "quarantined_runtimes", lambda *_args: [])
    monkeypatch.setattr(worker, "failed_candidate_runtimes", lambda *_args: [])
    monkeypatch.setattr(
        worker,
        "_installed_runtime_version_records",
        lambda *_args, **_kwargs: [
            {
                "skill": "weather_skill",
                "workspace_version": "2.6.23",
                "runtime_version": "2.6.12",
                "runtime_behind": True,
            }
        ],
    )
    monkeypatch.setattr(worker, "_write_status", lambda _ctx, payload: dict(payload))

    result = worker._run_migration_sync(
        SimpleNamespace(),
        operation_id="skill-migrate-drift",
        webspace_id="desktop",
        force=False,
        run_tests=True,
        name=None,
        sync_workspace=False,
    )

    assert result["ok"] is False
    assert result["state"] == "failed"
    assert result["remaining_runtime_drift_total"] == 1
    assert "left 1 installed runtime" in result["message"]


def test_owner_finalization_reloads_handlers_before_activation_and_rebuild(monkeypatch, tmp_path):
    order: list[str] = []
    writes: list[dict] = []
    ctx = SimpleNamespace(
        paths=SimpleNamespace(skills_dir=lambda: tmp_path / "skills"),
        bus=object(),
    )

    async def _reload(_ctx, name: str) -> dict:
        order.append(f"reload:{name}")
        return {"ok": True, "skill": name, "handlers": [f"{name}/handlers/main.py"]}

    def _invalidate(_webspace_id: str, *, operation_id: str) -> dict:
        order.append(f"invalidate:{operation_id}")
        return {"ok": True}

    async def _rebuild(**_kwargs) -> dict:
        order.append("rebuild")
        return {"ok": True}

    monkeypatch.setattr(worker, "_manager", lambda _ctx: object())
    monkeypatch.setattr(worker, "_reload_owner_skill_handlers", _reload)
    monkeypatch.setattr(worker, "_invalidate_owner_materialization", _invalidate)
    monkeypatch.setattr(
        worker,
        "bus_emit",
        lambda _bus, topic, payload, _source: order.append(f"emit:{topic}:{payload['skill_name']}"),
    )
    monkeypatch.setattr(worker, "rebuild_webspace_projection", _rebuild)
    monkeypatch.setattr(worker, "_installed_runtime_version_records", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(worker, "_write_status", lambda _ctx, payload: writes.append(dict(payload)) or dict(payload))

    result = asyncio.run(
        worker._finalize_owner_runtime_migration(
            ctx,
            {
                "ok": True,
                "state": "succeeded",
                "phase": "complete",
                "pending": False,
                "operation_id": "op-live",
                "started_at": worker._now(),
                "name": None,
                "skills": [
                    {
                        "skill": "weather_skill",
                        "ok": True,
                        "active_version_before": "2.6.22",
                        "active_slot_before": "A",
                    }
                ],
            },
            operation_id="op-live",
            webspace_id="desktop",
        )
    )

    assert order == [
        "reload:weather_skill",
        "invalidate:op-live",
        "emit:skills.activated:weather_skill",
        "rebuild",
    ]
    assert writes[0]["phase"] == "live_finalize"
    assert result["ok"] is True
    assert result["state"] == "succeeded"
    assert result["live_finalized_total"] == 1
    assert result["skills"][0]["activation_emitted"] is True


def test_owner_finalization_restores_fallback_when_live_reload_fails(monkeypatch, tmp_path):
    reload_results = iter(
        [
            {"ok": False, "reason": "reload_failed", "error": "bad import"},
            {"ok": True, "handlers": ["fallback/handlers/main.py"]},
        ]
    )
    preservation_calls: list[dict] = []
    events: list[str] = []
    ctx = SimpleNamespace(
        paths=SimpleNamespace(skills_dir=lambda: tmp_path / "skills"),
        bus=object(),
    )

    async def _reload(_ctx, _name: str) -> dict:
        return next(reload_results)

    def _preserve(_ctx, _mgr, **kwargs) -> dict:
        preservation_calls.append(kwargs)
        kwargs["entry"]["fallback_preserved"] = True
        return {"status": "candidate_quarantined", "fallback_version": "2.6.22", "fallback_slot": "A"}

    monkeypatch.setattr(worker, "_manager", lambda _ctx: object())
    monkeypatch.setattr(worker, "_reload_owner_skill_handlers", _reload)
    monkeypatch.setattr(worker, "_preserve_runtime_after_candidate_failure", _preserve)
    monkeypatch.setattr(worker, "_installed_runtime_version_records", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(worker, "bus_emit", lambda *_args, **_kwargs: events.append("emit"))
    monkeypatch.setattr(worker, "_write_status", lambda _ctx, payload: dict(payload))

    result = asyncio.run(
        worker._finalize_owner_runtime_migration(
            ctx,
            {
                "ok": True,
                "state": "succeeded",
                "phase": "complete",
                "pending": False,
                "operation_id": "op-fail",
                "started_at": worker._now(),
                "skills": [
                    {
                        "skill": "weather_skill",
                        "workspace_version": "2.6.23",
                        "ok": True,
                        "active_version_before": "2.6.22",
                        "active_slot_before": "A",
                    }
                ],
            },
            operation_id="op-fail",
            webspace_id="desktop",
        )
    )

    assert result["ok"] is False
    assert result["state"] == "failed"
    assert result["handler_reload_failed_total"] == 1
    assert result["skills"][0]["fallback_preserved"] is True
    assert result["skills"][0]["fallback_handler_reload"]["ok"] is True
    assert preservation_calls[0]["reload_fallback_handlers"] is False
    assert events == []


def test_candidate_failure_preserves_only_the_exact_pre_attempt_selection(monkeypatch):
    state = {"version": "1.2.2", "active_slot": "B", "deactivated": True}
    calls: list[str] = []

    class _Manager:
        def runtime_status(self, _name: str) -> dict:
            return dict(state)

        def rollback_runtime(self, _name: str) -> str:
            raise AssertionError("unchanged selection must not use history rollback")

        def record_runtime_migration_failure(self, _name: str, **kwargs) -> dict:
            calls.append("record")
            return {
                "status": "candidate_quarantined",
                "fallback_version": state["version"],
                "fallback_slot": state["active_slot"],
                **kwargs,
            }

    monkeypatch.setattr(worker, "_reload_live_skill_handlers_sync", lambda *_args: {"ok": True})
    entry = {"stage": "tests"}

    marker = worker._preserve_runtime_after_candidate_failure(
        SimpleNamespace(),
        _Manager(),
        name="weather_skill",
        candidate={"workspace_version": "1.2.3"},
        entry=entry,
        before={"version": "1.2.2", "active_slot": "B", "deactivated": True},
        operation_id="op-1",
        error=RuntimeError("candidate tests failed"),
    )

    assert calls == ["record"]
    assert marker["fallback_version"] == "1.2.2"
    assert marker["fallback_slot"] == "B"
    assert marker["attempted_core_identity"] == worker._core_runtime_identity()
    assert entry["rollback_performed"] is False
    assert entry["fallback_preserved"] is True


def test_candidate_failure_corrects_history_rollback_to_exact_fallback(monkeypatch):
    state = {"version": "1.2.3", "active_slot": "A", "deactivated": False}
    calls: list[str] = []

    class _Manager:
        def runtime_status(self, _name: str) -> dict:
            return dict(state)

        def rollback_runtime(self, _name: str) -> str:
            calls.append("history_rollback")
            state.update(version="1.2.1", active_slot="A")
            return "A"

        def restore_runtime_selection_exact(self, _name: str, *, version: str, slot: str) -> dict:
            calls.append(f"exact_restore:{version}/{slot}")
            state.update(version=version, active_slot=slot)
            return {"ok": True, "restored_active_version": version, "restored_active_slot": slot}

        def record_runtime_migration_failure(self, _name: str, **kwargs) -> dict:
            calls.append("record")
            return {
                "status": "candidate_quarantined",
                "fallback_version": state["version"],
                "fallback_slot": state["active_slot"],
                **kwargs,
            }

    monkeypatch.setattr(worker, "_reload_live_skill_handlers_sync", lambda *_args: {"ok": True})
    entry = {"stage": "activate"}

    marker = worker._preserve_runtime_after_candidate_failure(
        SimpleNamespace(),
        _Manager(),
        name="weather_skill",
        candidate={"workspace_version": "1.2.3"},
        entry=entry,
        before={"version": "1.2.2", "active_slot": "B", "deactivated": False},
        operation_id="op-2",
        error=RuntimeError("candidate activation failed"),
    )

    assert calls == ["history_rollback", "exact_restore:1.2.2/B", "record"]
    assert marker["fallback_version"] == "1.2.2"
    assert marker["fallback_slot"] == "B"
    assert entry["rollback_performed"] is True
    assert entry["fallback_preserved"] is True


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
