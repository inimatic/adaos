from __future__ import annotations

import os
from pathlib import Path

from adaos.apps import autostart_runner
from adaos.services import agent_context
from adaos.services import autostart, core_slots, core_update, hub_root_outbox_store, hub_root_protocol_store


class _FakePaths:
    def __init__(self, base_dir: Path, package_dir: Path) -> None:
        self._base_dir = base_dir
        self._package_dir = package_dir

    def base_dir(self) -> Path:
        return self._base_dir

    def package_path(self) -> Path:
        return self._package_dir

    def state_dir(self) -> Path:
        return self._base_dir / "state"

    def logs_dir(self) -> Path:
        return self._base_dir / "logs"


class _FakeCtx:
    def __init__(self, base_dir: Path, package_dir: Path) -> None:
        self.paths = _FakePaths(base_dir, package_dir)


def test_core_slots_prefers_context_base_dir(monkeypatch, tmp_path: Path) -> None:
    ctx = _FakeCtx(tmp_path / "custom-base", tmp_path / "repo" / "src" / "adaos")
    monkeypatch.setattr(agent_context, "get_ctx", lambda: ctx)

    assert core_slots.slot_dir("A") == (tmp_path / "custom-base" / "state" / "core_slots" / "slots" / "A").resolve()


def test_core_update_prefers_context_paths(monkeypatch, tmp_path: Path) -> None:
    ctx = _FakeCtx(tmp_path / "custom-base", tmp_path / "repo" / "src" / "adaos")
    monkeypatch.setattr(agent_context, "get_ctx", lambda: ctx)
    monkeypatch.setattr(core_update, "get_ctx", lambda: ctx)

    assert core_update._base_dir() == (tmp_path / "custom-base").resolve()
    assert core_update._repo_root() == (tmp_path / "repo").resolve()


def test_core_update_prefers_stable_root_over_slot_repo_env(monkeypatch, tmp_path: Path) -> None:
    base_dir = tmp_path / "custom-base"
    project_root = tmp_path / "adaos"
    (project_root / "src" / "adaos").mkdir(parents=True)
    shared_dotenv = project_root / ".env"
    shared_dotenv.write_text("ADAOS_TOKEN=test\n", encoding="utf-8")
    slot_repo = base_dir / "state" / "core_slots" / "slots" / "A" / "repo"
    (slot_repo / "src" / "adaos").mkdir(parents=True)
    python_rel = Path("Scripts") / "python.exe" if os.name == "nt" else Path("bin") / "python"
    root_python = project_root / ".venv" / python_rel
    root_python.parent.mkdir(parents=True)
    root_python.write_text("", encoding="utf-8")

    ctx = _FakeCtx(base_dir, slot_repo / "src" / "adaos")
    monkeypatch.setattr(agent_context, "get_ctx", lambda: ctx)
    monkeypatch.setattr(core_update, "get_ctx", lambda: ctx)
    monkeypatch.setenv("ADAOS_ROOT_REPO_ROOT", str(slot_repo))
    monkeypatch.setenv("ADAOS_SHARED_DOTENV_PATH", str(shared_dotenv))

    assert core_update._repo_root() == project_root.resolve()
    command = core_update.configured_update_command({"target_slot": "B", "target_version": "abc123"})
    assert command is not None
    assert str(root_python.resolve()) in command
    assert str(project_root.resolve()) in command


def test_autostart_runner_slot_launch_spec_uses_context_base_dir(monkeypatch, tmp_path: Path) -> None:
    ctx = _FakeCtx(tmp_path / "custom-base", tmp_path / "repo" / "src" / "adaos")
    monkeypatch.setattr(agent_context, "get_ctx", lambda: ctx)
    monkeypatch.setattr(autostart_runner, "slot_dir", lambda slot: (tmp_path / "slots" / slot).resolve())

    argv, command = autostart_runner._slot_launch_spec(
        {
            "slot": "B",
            "argv": ["python", "-m", "adaos.apps.autostart_runner", "--base-dir", "{base_dir}", "--port", "{port}"],
        },
        host="127.0.0.1",
        port=8777,
        token=None,
    )

    assert command is None
    assert argv is not None
    assert str((tmp_path / "custom-base").resolve()) in argv


def test_autostart_runner_validation_logs_use_context_logs_dir(monkeypatch, tmp_path: Path) -> None:
    ctx = _FakeCtx(tmp_path / "custom-base", tmp_path / "repo" / "src" / "adaos")
    monkeypatch.setattr(agent_context, "get_ctx", lambda: ctx)

    stdout_path, stderr_path = autostart_runner._validation_log_paths("B")

    assert stdout_path == (tmp_path / "custom-base" / "logs" / "autostart-slot-B.out.log").resolve()
    assert stderr_path == (tmp_path / "custom-base" / "logs" / "autostart-slot-B.err.log").resolve()


def test_autostart_state_dir_prefers_context(monkeypatch, tmp_path: Path) -> None:
    ctx = _FakeCtx(tmp_path / "custom-base", tmp_path / "repo" / "src" / "adaos")
    monkeypatch.setattr(agent_context, "get_ctx", lambda: ctx)

    assert autostart._state_dir() == (tmp_path / "custom-base" / "state").resolve()


def test_hub_root_protocol_store_prefers_context_state_dir(monkeypatch, tmp_path: Path) -> None:
    ctx = _FakeCtx(tmp_path / "custom-base", tmp_path / "repo" / "src" / "adaos")
    monkeypatch.setattr(agent_context, "get_ctx", lambda: ctx)

    assert hub_root_protocol_store._state_root() == (tmp_path / "custom-base" / "state" / "hub_root_protocol").resolve()


def test_hub_root_outbox_store_prefers_context_state_dir(monkeypatch, tmp_path: Path) -> None:
    ctx = _FakeCtx(tmp_path / "custom-base", tmp_path / "repo" / "src" / "adaos")
    monkeypatch.setattr(agent_context, "get_ctx", lambda: ctx)

    assert hub_root_outbox_store.outbox_store_path("main") == (
        tmp_path / "custom-base" / "state" / "hub_root_outboxes" / "main.json"
    ).resolve()
