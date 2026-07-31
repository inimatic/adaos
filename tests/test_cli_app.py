from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from adaos.apps.cli import app as cli_app


def test_switch_lang_writes_supported_language(monkeypatch, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    monkeypatch.setattr(cli_app, "find_dotenv", lambda *args, **kwargs: str(env_path))
    monkeypatch.setenv("ADAOS_LANG", "en")

    result = CliRunner().invoke(cli_app.switch_app, ["lang", "ru-RU"])

    assert result.exit_code == 0, result.output
    assert "ADAOS_LANG установлен в 'ru'" in result.output
    assert env_path.read_text(encoding="utf-8") == "ADAOS_LANG=ru\n"
    assert os.environ["ADAOS_LANG"] == "ru"


def test_switch_lang_rejects_unsupported_language(monkeypatch, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    monkeypatch.setattr(cli_app, "find_dotenv", lambda *args, **kwargs: str(env_path))
    monkeypatch.setenv("ADAOS_LANG", "ru")

    result = CliRunner().invoke(cli_app.switch_app, ["lang", "zz"])

    assert result.exit_code != 0
    assert "Допустимые языки" in result.output
    assert not env_path.exists()


def test_cli_i18n_preboot_normalizes_and_falls_back(monkeypatch) -> None:
    from adaos.apps.cli import i18n as cli_i18n

    monkeypatch.setenv("ADAOS_LANG", "ru-RU")
    assert cli_i18n._preboot_translate("cli.help_skill") == "Работа с навыками"

    monkeypatch.setenv("ADAOS_LANG", "zz")
    assert cli_i18n._preboot_translate("cli.help_skill") == "Working with skills"


def test_active_slot_manifest_payload_prefers_active_slot_venv(monkeypatch, tmp_path: Path) -> None:
    base_dir = tmp_path / ".adaos"
    slot_dir = base_dir / "state" / "core_slots" / "slots" / "B"
    repo_dir = slot_dir / "repo"
    venv_dir = slot_dir / "venv"
    src_dir = repo_dir / "src"
    python_bin = venv_dir / "bin" / "python"
    manifest_path = slot_dir / "manifest.json"

    (base_dir / "state" / "core_slots").mkdir(parents=True, exist_ok=True)
    (base_dir / "state" / "core_slots" / "active").write_text("B\n", encoding="utf-8")
    src_dir.mkdir(parents=True, exist_ok=True)
    python_bin.parent.mkdir(parents=True, exist_ok=True)
    python_bin.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "repo_dir": str(repo_dir),
                "cwd": str(repo_dir),
                "venv_dir": str(venv_dir),
                "env": {
                    "ADAOS_SLOT_REPO_ROOT": str(repo_dir),
                    "ADAOS_BASE_DIR": str(base_dir),
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ADAOS_BASE_DIR", str(base_dir))
    monkeypatch.setenv("PYTHONPATH", "/existing/pythonpath")

    python, env_map, resolved_repo_dir = cli_app._active_slot_manifest_payload()

    assert python == str(python_bin)
    assert resolved_repo_dir == str(repo_dir)
    assert env_map["ADAOS_BASE_DIR"] == str(base_dir)
    assert env_map["ADAOS_ACTIVE_CORE_SLOT"] == "B"
    assert env_map["ADAOS_ACTIVE_CORE_SLOT_DIR"] == str(slot_dir)
    assert env_map["ADAOS_SLOT_REPO_ROOT"] == str(repo_dir)
    assert env_map["PYTHONPATH"] == f"{src_dir}{os.pathsep}/existing/pythonpath"


def test_should_reexec_active_slot_venv_when_current_python_differs(monkeypatch, tmp_path: Path) -> None:
    base_dir = tmp_path / ".adaos"
    slot_dir = base_dir / "state" / "core_slots" / "slots" / "A"
    venv_dir = slot_dir / "venv"
    python_bin = venv_dir / "bin" / "python"
    manifest_path = slot_dir / "manifest.json"

    (base_dir / "state" / "core_slots").mkdir(parents=True, exist_ok=True)
    (base_dir / "state" / "core_slots" / "active").write_text("A\n", encoding="utf-8")
    python_bin.parent.mkdir(parents=True, exist_ok=True)
    python_bin.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "repo_dir": str(slot_dir / "repo"),
                "cwd": str(slot_dir / "repo"),
                "venv_dir": str(venv_dir),
                "env": {},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ADAOS_BASE_DIR", str(base_dir))
    monkeypatch.delenv("ADAOS_CLI_REEXECED", raising=False)
    monkeypatch.delenv("ADAOS_DISABLE_ACTIVE_SLOT_PYTHON_REEXEC", raising=False)
    monkeypatch.setattr(cli_app.sys, "executable", str(tmp_path / "other-python"))

    assert cli_app._should_reexec_active_slot_venv() is True


def test_api_restart_from_repo_venv_preserves_selected_runtime(monkeypatch, tmp_path: Path) -> None:
    repo_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    slot_python = tmp_path / "slot" / "venv" / "Scripts" / "python.exe"
    repo_python.parent.mkdir(parents=True)
    slot_python.parent.mkdir(parents=True)
    repo_python.write_text("", encoding="utf-8")
    slot_python.write_text("", encoding="utf-8")

    monkeypatch.delenv("ADAOS_CLI_SLOT_BOUND", raising=False)
    monkeypatch.delenv("ADAOS_CLI_REEXECED", raising=False)
    monkeypatch.delenv("ADAOS_DISABLE_ACTIVE_SLOT_PYTHON_REEXEC", raising=False)
    monkeypatch.setattr(cli_app, "_repo_venv_python", lambda: str(repo_python))
    monkeypatch.setattr(cli_app, "_active_slot_manifest_payload", lambda: (str(slot_python), {}, None))
    monkeypatch.setattr(cli_app.sys, "executable", str(repo_python))

    assert cli_app._should_preserve_repo_runtime_for_api_restart(["api", "restart"])
    assert not cli_app._should_reexec_active_slot_venv(["api", "restart"])
    assert cli_app._should_reexec_active_slot_venv(["api", "serve"])


def test_windows_wrapper_reexec_does_not_block_active_slot_reexec(monkeypatch, tmp_path: Path) -> None:
    base_dir = tmp_path / ".adaos"
    slot_dir = base_dir / "state" / "core_slots" / "slots" / "B"
    venv_dir = slot_dir / "venv"
    python_bin = venv_dir / "bin" / "python"
    manifest_path = slot_dir / "manifest.json"

    (base_dir / "state" / "core_slots").mkdir(parents=True, exist_ok=True)
    (base_dir / "state" / "core_slots" / "active").write_text("B\n", encoding="utf-8")
    python_bin.parent.mkdir(parents=True, exist_ok=True)
    python_bin.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "repo_dir": str(slot_dir / "repo"),
                "cwd": str(slot_dir / "repo"),
                "venv_dir": str(venv_dir),
                "env": {},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ADAOS_BASE_DIR", str(base_dir))
    monkeypatch.setenv("ADAOS_CLI_REEXECED", "1")
    monkeypatch.setenv("ADAOS_CLI_REEXEC_REASON", "adaos.exe wrapper")
    monkeypatch.delenv("ADAOS_DISABLE_ACTIVE_SLOT_PYTHON_REEXEC", raising=False)
    monkeypatch.setattr(cli_app.sys, "executable", str(tmp_path / "repo-venv" / "python"))

    assert cli_app._should_reexec_active_slot_venv() is True


def test_active_slot_reexec_stops_after_slot_python_reexec(monkeypatch, tmp_path: Path) -> None:
    base_dir = tmp_path / ".adaos"
    slot_dir = base_dir / "state" / "core_slots" / "slots" / "B"
    venv_dir = slot_dir / "venv"
    python_bin = venv_dir / "bin" / "python"
    manifest_path = slot_dir / "manifest.json"

    (base_dir / "state" / "core_slots").mkdir(parents=True, exist_ok=True)
    (base_dir / "state" / "core_slots" / "active").write_text("B\n", encoding="utf-8")
    python_bin.parent.mkdir(parents=True, exist_ok=True)
    python_bin.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "repo_dir": str(slot_dir / "repo"),
                "cwd": str(slot_dir / "repo"),
                "venv_dir": str(venv_dir),
                "env": {},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ADAOS_BASE_DIR", str(base_dir))
    monkeypatch.setenv("ADAOS_CLI_REEXECED", "1")
    monkeypatch.setenv("ADAOS_CLI_REEXEC_REASON", "active slot .venv")
    monkeypatch.delenv("ADAOS_DISABLE_ACTIVE_SLOT_PYTHON_REEXEC", raising=False)
    monkeypatch.setattr(cli_app.sys, "executable", str(tmp_path / "repo-venv" / "python"))

    assert cli_app._should_reexec_active_slot_venv() is False


def test_slot_shell_required_diagnostic_for_unslotted_state_changing_command(monkeypatch, tmp_path: Path) -> None:
    base_dir = tmp_path / ".adaos"
    slot_dir = base_dir / "state" / "core_slots" / "slots" / "B"
    repo_dir = slot_dir / "repo"
    venv_dir = slot_dir / "venv"
    python_bin = venv_dir / "bin" / "python"
    manifest_path = slot_dir / "manifest.json"

    (base_dir / "state" / "core_slots").mkdir(parents=True, exist_ok=True)
    (base_dir / "state" / "core_slots" / "active").write_text("B\n", encoding="utf-8")
    repo_dir.mkdir(parents=True, exist_ok=True)
    python_bin.parent.mkdir(parents=True, exist_ok=True)
    python_bin.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "repo_dir": str(repo_dir),
                "cwd": str(repo_dir),
                "venv_dir": str(venv_dir),
                "env": {},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ADAOS_BASE_DIR", str(base_dir))
    monkeypatch.setenv("ENV_TYPE", "prod")
    monkeypatch.delenv("ADAOS_ALLOW_UNSLOTTED_CLI", raising=False)
    monkeypatch.delenv("ADAOS_DISABLE_SLOT_CONTEXT_WARNING", raising=False)
    monkeypatch.delenv("ADAOS_CLI_SLOT_BOUND", raising=False)
    monkeypatch.setattr(cli_app.sys, "executable", str(tmp_path / "repo-venv" / "python"))

    diagnostic = cli_app._slot_shell_required_diagnostic(["skill", "push", "infrastate_skill"])

    assert diagnostic["code"] == "slot_shell_required"
    assert diagnostic["command"] == "skill push infrastate_skill"
    assert diagnostic["expected_python"] == str(python_bin)
    assert diagnostic["expected_repo"] == str(repo_dir)


def test_slot_shell_required_diagnostic_skips_read_only_and_dev_commands(monkeypatch, tmp_path: Path) -> None:
    base_dir = tmp_path / ".adaos"
    slot_dir = base_dir / "state" / "core_slots" / "slots" / "B"
    venv_dir = slot_dir / "venv"
    python_bin = venv_dir / "bin" / "python"
    manifest_path = slot_dir / "manifest.json"

    (base_dir / "state" / "core_slots").mkdir(parents=True, exist_ok=True)
    (base_dir / "state" / "core_slots" / "active").write_text("B\n", encoding="utf-8")
    python_bin.parent.mkdir(parents=True, exist_ok=True)
    python_bin.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "repo_dir": str(slot_dir / "repo"),
                "cwd": str(slot_dir / "repo"),
                "venv_dir": str(venv_dir),
                "env": {},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ADAOS_BASE_DIR", str(base_dir))
    monkeypatch.setenv("ENV_TYPE", "prod")
    monkeypatch.delenv("ADAOS_CLI_SLOT_BOUND", raising=False)
    monkeypatch.setattr(cli_app.sys, "executable", str(tmp_path / "repo-venv" / "python"))

    assert cli_app._slot_shell_required_diagnostic(["skill", "status"]) == {}
    assert cli_app._slot_shell_required_diagnostic(["node", "yjs", "status"]) == {}
    assert cli_app._slot_shell_required_diagnostic(["node", "yjs", "describe", "ws1"]) == {}
    assert cli_app._slot_shell_required_diagnostic(["dev", "skill", "push", "demo"]) == {}
    assert cli_app._slot_shell_required_diagnostic(["node", "yjs", "update", "ws1"])["code"] == "slot_shell_required"


def test_slot_shell_required_diagnostic_skips_bound_slot_context(monkeypatch, tmp_path: Path) -> None:
    base_dir = tmp_path / ".adaos"
    slot_dir = base_dir / "state" / "core_slots" / "slots" / "B"
    repo_dir = slot_dir / "repo"
    venv_dir = slot_dir / "venv"
    python_bin = venv_dir / "bin" / "python"
    manifest_path = slot_dir / "manifest.json"

    (base_dir / "state" / "core_slots").mkdir(parents=True, exist_ok=True)
    (base_dir / "state" / "core_slots" / "active").write_text("B\n", encoding="utf-8")
    repo_dir.mkdir(parents=True, exist_ok=True)
    python_bin.parent.mkdir(parents=True, exist_ok=True)
    python_bin.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "repo_dir": str(repo_dir),
                "cwd": str(repo_dir),
                "venv_dir": str(venv_dir),
                "env": {},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(repo_dir)
    monkeypatch.setenv("ADAOS_BASE_DIR", str(base_dir))
    monkeypatch.setenv("ADAOS_CLI_SLOT_BOUND", "1")
    monkeypatch.setattr(cli_app.sys, "executable", str(python_bin))

    assert cli_app._slot_shell_required_diagnostic(["scenario", "push", "web_desktop"]) == {}


def test_apply_active_slot_manifest_environment_when_python_already_slot_bound(monkeypatch, tmp_path: Path) -> None:
    base_dir = tmp_path / ".adaos"
    slot_dir = base_dir / "state" / "core_slots" / "slots" / "B"
    repo_dir = slot_dir / "repo"
    venv_dir = slot_dir / "venv"
    src_dir = repo_dir / "src"
    python_bin = venv_dir / "bin" / "python"
    manifest_path = slot_dir / "manifest.json"

    (base_dir / "state" / "core_slots").mkdir(parents=True, exist_ok=True)
    (base_dir / "state" / "core_slots" / "active").write_text("B\n", encoding="utf-8")
    src_dir.mkdir(parents=True, exist_ok=True)
    python_bin.parent.mkdir(parents=True, exist_ok=True)
    python_bin.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "repo_dir": str(repo_dir),
                "cwd": str(repo_dir),
                "venv_dir": str(venv_dir),
                "env": {},
            }
        ),
        encoding="utf-8",
    )

    start_dir = tmp_path / "start"
    start_dir.mkdir()
    monkeypatch.chdir(start_dir)
    monkeypatch.setenv("ADAOS_BASE_DIR", str(base_dir))
    # Register keys before the helper mutates os.environ directly so teardown
    # cannot leak a deleted temporary slot to later tests.
    monkeypatch.setenv("ADAOS_ACTIVE_CORE_SLOT", "")
    monkeypatch.setenv("ADAOS_ACTIVE_CORE_SLOT_DIR", "")
    monkeypatch.setenv("ADAOS_SLOT_REPO_ROOT", "")
    monkeypatch.setenv("ADAOS_CLI_SLOT_BOUND", "")
    monkeypatch.setattr(cli_app.sys, "executable", str(python_bin))

    assert cli_app._apply_active_slot_manifest_environment_if_current() is True

    assert os.environ["ADAOS_ACTIVE_CORE_SLOT"] == "B"
    assert os.environ["ADAOS_ACTIVE_CORE_SLOT_DIR"] == str(slot_dir)
    assert os.environ["ADAOS_SLOT_REPO_ROOT"] == str(repo_dir)
    assert os.environ["ADAOS_CLI_SLOT_BOUND"] == "1"
    assert Path.cwd() == repo_dir


def test_repo_venv_reexec_is_disabled_after_slot_binding(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_CLI_SLOT_BOUND", "1")
    monkeypatch.delenv("ADAOS_CLI_REEXECED", raising=False)

    assert cli_app._should_reexec_repo_venv() is False
