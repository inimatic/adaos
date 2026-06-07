from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from adaos.apps.autostart_runner import _slot_launch_spec
from adaos.services.autostart import default_spec, disable, enable, status


class _FakePaths:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def base_dir(self) -> Path:
        return self._base_dir


class _FakeSettings:
    profile = "default"


class _FakeCtx:
    def __init__(self, base_dir: Path) -> None:
        self.paths = _FakePaths(base_dir)
        self.settings = _FakeSettings()


class _FakeProc:
    def __init__(
        self,
        pid: int,
        *,
        cmdline: list[str],
        env: dict[str, str] | None = None,
        parent: "_FakeProc | None" = None,
    ) -> None:
        self.pid = pid
        self._cmdline = cmdline
        self._env = env or {}
        self._parent = parent

    def cmdline(self) -> list[str]:
        return self._cmdline

    def environ(self) -> dict[str, str]:
        return self._env

    def parent(self) -> "_FakeProc | None":
        return self._parent


def test_default_autostart_spec_uses_runner(tmp_path: Path) -> None:
    spec = default_spec(_FakeCtx(tmp_path), host="127.0.0.1", port=8779, token="t1")
    assert spec.argv[:3] == (spec.argv[0], "-m", "adaos.apps.supervisor")
    assert "--host" in spec.argv
    assert "--port" in spec.argv
    assert spec.env["ADAOS_BASE_DIR"] == str(tmp_path)
    assert spec.env["ADAOS_PROFILE"] == "default"
    assert spec.env["ADAOS_AUTOSTART_MANAGED"] == "1"
    assert spec.env["ADAOS_TOKEN"] == "t1"


def test_shell_wrapper_sources_dotenv_before_managed_exports(tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    wrapper = tmp_path / "adaos-autostart.sh"
    shared_dotenv = tmp_path / ".env"

    autostart._write_wrapper_sh(
        wrapper,
        argv=["/venv/bin/python", "-m", "adaos.apps.supervisor"],
        env={
            "ADAOS_BASE_DIR": "/var/lib/adaos",
            "ADAOS_SHARED_DOTENV_PATH": str(shared_dotenv),
            "ADAOS_SUPERVISOR_PORT": "8776",
        },
    )

    text = wrapper.read_text(encoding="utf-8")

    assert text.index('. "${ADAOS_SHARED_DOTENV_PATH}"') < text.rindex("export ADAOS_BASE_DIR='/var/lib/adaos'")
    assert text.index('. "${ADAOS_SHARED_DOTENV_PATH}"') < text.rindex("export ADAOS_SUPERVISOR_PORT='8776'")


def test_windows_disable_stops_live_autostart_wrapper_tree(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    wrapper = tmp_path / "bin" / "adaos-autostart.ps1"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("", encoding="utf-8")
    wrapper_proc = _FakeProc(100, cmdline=["powershell.exe", "-File", str(wrapper)])
    supervisor_proc = _FakeProc(
        200,
        cmdline=["python.exe", "-m", "adaos.apps.supervisor", "--host", "127.0.0.1", "--port", "8777"],
        env={"ADAOS_AUTOSTART_MANAGED": "1", "ADAOS_BASE_DIR": str(tmp_path)},
        parent=wrapper_proc,
    )
    runner_proc = _FakeProc(
        300,
        cmdline=["python.exe", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8777"],
        env={"ADAOS_AUTOSTART_MANAGED": "1", "ADAOS_BASE_DIR": str(tmp_path)},
        parent=supervisor_proc,
    )

    class _Proc:
        def __init__(self, returncode: int = 0) -> None:
            self.returncode = returncode
            self.stdout = ""
            self.stderr = ""

    calls: list[list[str]] = []

    monkeypatch.setattr(autostart, "_is_windows", lambda: True)
    monkeypatch.setattr(autostart, "_is_linux", lambda: False)
    monkeypatch.setattr(autostart, "_is_macos", lambda: False)
    monkeypatch.setattr(autostart, "_current_process_family_pids", lambda: {999})
    monkeypatch.setattr(autostart.psutil, "process_iter", lambda attrs=None: [wrapper_proc, supervisor_proc, runner_proc])
    monkeypatch.setattr(autostart, "_run", lambda cmd: calls.append(cmd) or _Proc())

    result = disable(_FakeCtx(tmp_path))

    assert ["schtasks", "/Delete", "/F", "/TN", "AdaOS"] in calls
    assert ["taskkill", "/PID", "100", "/T", "/F"] in calls
    assert result["worker"]["status"] == "stopped"
    assert result["worker"]["pids"] == [100]
    assert result["worker"]["stopped_pids"] == [100]
    assert not wrapper.exists()


def test_windows_disable_reports_no_live_autostart_worker(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    manual_api_proc = _FakeProc(
        400,
        cmdline=["adaos", "api", "serve", "--host", "127.0.0.1", "--port", "8777"],
    )

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    calls: list[list[str]] = []

    monkeypatch.setattr(autostart, "_is_windows", lambda: True)
    monkeypatch.setattr(autostart, "_is_linux", lambda: False)
    monkeypatch.setattr(autostart, "_is_macos", lambda: False)
    monkeypatch.setattr(autostart, "_current_process_family_pids", lambda: {999})
    monkeypatch.setattr(autostart.psutil, "process_iter", lambda attrs=None: [manual_api_proc])
    monkeypatch.setattr(autostart, "_run", lambda cmd: calls.append(cmd) or _Proc())

    result = disable(_FakeCtx(tmp_path))

    assert ["schtasks", "/Delete", "/F", "/TN", "AdaOS"] in calls
    assert not any(call[:1] == ["taskkill"] for call in calls)
    assert result["worker"] == {"status": "not_found", "pids": []}


def test_default_autostart_spec_falls_back_to_loaded_runtime_token(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    monkeypatch.delenv("ADAOS_TOKEN", raising=False)
    monkeypatch.delenv("ADAOS_HUB_TOKEN", raising=False)
    monkeypatch.delenv("HUB_TOKEN", raising=False)
    monkeypatch.setattr(autostart, "load_config", lambda: SimpleNamespace(token="runtime-token"))

    spec = default_spec(_FakeCtx(tmp_path), host="127.0.0.1", port=8779)

    assert spec.env["ADAOS_TOKEN"] == "runtime-token"


def test_default_autostart_spec_omits_token_when_no_runtime_or_env_token(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    monkeypatch.delenv("ADAOS_TOKEN", raising=False)
    monkeypatch.delenv("ADAOS_HUB_TOKEN", raising=False)
    monkeypatch.delenv("HUB_TOKEN", raising=False)
    monkeypatch.setattr(autostart, "load_config", lambda: SimpleNamespace(token=""))

    spec = default_spec(_FakeCtx(tmp_path), host="127.0.0.1", port=8779)

    assert "ADAOS_TOKEN" not in spec.env


def test_default_autostart_spec_keeps_context_base_dir_when_shared_dotenv_is_dev(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    shared_dotenv = repo_root / ".env"
    shared_dotenv.write_text("ENV_TYPE=dev\nADAOS_PROFILE=from-dotenv\n", encoding="utf-8")

    monkeypatch.setattr(autostart, "_shared_dotenv_path", lambda ctx: shared_dotenv)

    ctx = _FakeCtx(tmp_path / "active-base")
    spec = default_spec(ctx, host="127.0.0.1", port=8779, token="t1")

    assert spec.env["ADAOS_BASE_DIR"] == str((tmp_path / "active-base").resolve())
    assert spec.env["ADAOS_PROFILE"] == "from-dotenv"
    assert spec.env["ADAOS_SHARED_DOTENV_PATH"] == str(shared_dotenv.resolve())


def test_default_autostart_spec_respects_explicit_base_dir_from_shared_dotenv(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    shared_dotenv = repo_root / ".env"
    shared_dotenv.write_text(
        f"ENV_TYPE=dev\nADAOS_PROFILE=from-dotenv\nADAOS_BASE_DIR={tmp_path / 'service-base'}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(autostart, "_shared_dotenv_path", lambda ctx: shared_dotenv)

    ctx = _FakeCtx(tmp_path / "active-base")
    spec = default_spec(ctx, host="127.0.0.1", port=8779, token="t1")

    assert spec.env["ADAOS_BASE_DIR"] == str((tmp_path / "service-base").resolve())
    assert spec.env["ADAOS_PROFILE"] == "from-dotenv"
    assert spec.env["ADAOS_SHARED_DOTENV_PATH"] == str(shared_dotenv.resolve())


def test_default_autostart_spec_preserves_sidecar_env_from_shared_dotenv(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    shared_dotenv = repo_root / ".env"
    shared_dotenv.write_text(
        "ADAOS_REALTIME_ENABLE=1\n"
        "ADAOS_REALTIME_ROUTE_PROXY_ENABLE=1\n"
        "ADAOS_REALTIME_ALLOW_API_FALLBACK=1\n"
        "HUB_NATS_TRANSPORT=ws\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(autostart, "_shared_dotenv_path", lambda ctx: shared_dotenv)

    spec = default_spec(_FakeCtx(tmp_path / "base"), host="127.0.0.1", port=8779, token="t1")

    assert spec.env["ADAOS_REALTIME_ENABLE"] == "1"
    assert spec.env["ADAOS_REALTIME_ROUTE_PROXY_ENABLE"] == "1"
    assert spec.env["ADAOS_REALTIME_ALLOW_API_FALLBACK"] == "1"
    assert spec.env["HUB_NATS_TRANSPORT"] == "ws"


def test_default_autostart_spec_prefers_stable_root_venv_over_slot_context(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    base_dir = tmp_path / "base"
    project_root = tmp_path / "adaos"
    (project_root / "src" / "adaos").mkdir(parents=True)
    shared_dotenv = project_root / ".env"
    shared_dotenv.write_text("ADAOS_PROFILE=from-dotenv\n", encoding="utf-8")
    python_rel = Path("Scripts") / "python.exe" if os.name == "nt" else Path("bin") / "python"
    root_python = project_root / ".venv" / python_rel
    root_python.parent.mkdir(parents=True)
    root_python.write_text("", encoding="utf-8")

    slot_repo = base_dir / "state" / "core_slots" / "slots" / "A" / "repo"
    (slot_repo / "src" / "adaos").mkdir(parents=True)

    class _Paths(_FakePaths):
        def repo_root(self) -> Path:
            return slot_repo

    class _Ctx(_FakeCtx):
        def __init__(self, base_dir: Path) -> None:
            self.paths = _Paths(base_dir)
            self.settings = _FakeSettings()

    monkeypatch.setenv("ADAOS_SHARED_DOTENV_PATH", str(shared_dotenv))
    monkeypatch.setenv("ADAOS_ROOT_REPO_ROOT", str(slot_repo))
    monkeypatch.setattr(autostart, "load_config", lambda: SimpleNamespace(token=""))

    spec = default_spec(_Ctx(base_dir), host="127.0.0.1", port=8779, token="t1")

    assert Path(spec.argv[0]).resolve() == root_python.resolve()
    assert spec.env["ADAOS_ROOT_REPO_ROOT"] == str(project_root.resolve())
    assert spec.env["ADAOS_SHARED_DOTENV_PATH"] == str(shared_dotenv.resolve())
    assert str(project_root / "src") in spec.env["PYTHONPATH"].split(os.pathsep)


def test_slot_launch_spec_formats_placeholders() -> None:
    argv, command = _slot_launch_spec(
        {
            "slot": "B",
            "argv": ["python", "-m", "adaos.apps.autostart_runner", "--host", "{host}", "--port", "{port}"],
        },
        host="127.0.0.1",
        port=8777,
        token="tok",
    )
    assert command is None
    assert argv is not None
    assert argv[-1] == "8777"


def test_parse_wrapper_python_reports_core_slot_source(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    base_dir = tmp_path / "base"
    wrapper = base_dir / "bin" / "adaos-autostart.sh"
    wrapper.parent.mkdir(parents=True)
    slot_python = base_dir / "state" / "core_slots" / "slots" / "A" / "venv" / "bin" / "python"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"exec '{slot_python}' '-m' 'adaos.apps.supervisor' '--host' '127.0.0.1' '--port' '8777'\n",
        encoding="utf-8",
    )

    payload = autostart._wrapper_control_plane_payload(wrapper, base_dir=base_dir)

    assert payload["wrapper_python"] == str(slot_python)
    assert payload["wrapper_python_is_core_slot"] is True


def test_linux_status_without_user_bus_uses_service_file(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    monkeypatch.setattr(autostart, "_is_windows", lambda: False)
    monkeypatch.setattr(autostart, "_is_macos", lambda: False)
    monkeypatch.setattr(autostart, "_is_linux", lambda: True)
    monkeypatch.setattr(autostart, "_home", lambda: tmp_path)
    monkeypatch.setattr(autostart, "shutil_which", lambda cmd: "/bin/systemctl" if cmd == "systemctl" else None)
    monkeypatch.setattr(autostart, "_linux_user_bus_path", lambda: None)

    calls: list[list[str]] = []

    def _boom(cmd: list[str]):
        calls.append(cmd)
        raise AssertionError("status() must not call systemctl --user when the user bus is missing")

    monkeypatch.setattr(autostart, "_run", _boom)

    service_path = tmp_path / ".config" / "systemd" / "user" / "adaos.service"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text("[Unit]\nDescription=test\n", encoding="utf-8")

    payload = status(_FakeCtx(tmp_path))
    assert payload["enabled"] is True
    assert payload["active"] is None
    assert calls == []


def test_restart_service_uses_unit_name_on_linux(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    monkeypatch.setattr(autostart.sys, "platform", "linux")
    monkeypatch.setattr(
        autostart,
        "status",
        lambda ctx: {
            "scope": "system",
            "service": "/etc/systemd/system/adaos.service",
            "host": "127.0.0.1",
            "port": 8778,
            "service_main_pid": 111,
        },
    )

    captured_calls: list[dict[str, object]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _run(cmd, capture_output, text, timeout, encoding=None, errors=None):
        captured_calls.append(
            {
                "cmd": cmd,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "encoding": encoding,
                "errors": errors,
            }
        )
        return _Proc()

    monkeypatch.setattr(autostart.subprocess, "run", _run)
    active_calls = {"count": 0}

    class _RunProc:
        def __init__(self, *, returncode=0, stdout="", stderr="") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(cmd: list[str]):
        if cmd == ["systemctl", "is-active", "adaos.service"]:
            active_calls["count"] += 1
            if active_calls["count"] == 1:
                return _RunProc(returncode=3, stdout="activating")
            return _RunProc(returncode=0, stdout="active")
        raise AssertionError(f"unexpected _run command: {cmd}")

    monkeypatch.setattr(autostart, "_run", _fake_run)
    pid_values = iter([111, 222])
    monkeypatch.setattr(autostart, "_linux_service_main_pid", lambda scope: next(pid_values))
    monkeypatch.setattr(autostart, "_discover_live_control_bind", lambda host, port: ("127.0.0.1", 8778))
    monkeypatch.setattr(autostart.time, "sleep", lambda _: None)

    payload = autostart.restart_service(_FakeCtx(tmp_path))

    assert captured_calls[0]["cmd"] == ["systemctl", "restart", "adaos.service", "--no-block"]
    assert captured_calls[1]["cmd"] == [
        "systemctl",
        "show",
        "adaos.service",
        "-p",
        "TimeoutStopUSec",
        "-p",
        "TimeoutStartUSec",
        "-p",
        "RestartUSec",
        "--value",
    ]
    assert payload["service"] == "adaos.service"
    assert payload["service_ref"] == "/etc/systemd/system/adaos.service"
    assert captured_calls[0]["encoding"] == "utf-8"
    assert captured_calls[0]["errors"] == "replace"
    assert payload["service_main_pid"] == 222
    assert payload["listening"] is True
    assert payload["url"] == "http://127.0.0.1:8778"


def test_linux_enable_without_user_bus_raises_helpful_error(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    monkeypatch.setattr(autostart, "_bootstrap_core_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr(autostart, "_is_windows", lambda: False)
    monkeypatch.setattr(autostart, "_is_macos", lambda: False)
    monkeypatch.setattr(autostart, "_is_linux", lambda: True)
    monkeypatch.setattr(autostart, "_home", lambda: tmp_path)
    monkeypatch.setattr(autostart, "shutil_which", lambda cmd: "/bin/systemctl" if cmd == "systemctl" else None)
    monkeypatch.setattr(autostart, "_linux_user_bus_path", lambda: None)
    monkeypatch.setattr(autostart, "_linux_has_systemd_pid1", lambda: False)
    monkeypatch.setattr(autostart, "_linux_is_root", lambda: False)

    spec = default_spec(_FakeCtx(tmp_path), host="127.0.0.1", port=8777, token="t1")
    try:
        enable(_FakeCtx(tmp_path), spec, scope="user")
    except RuntimeError as exc:
        msg = str(exc)
        assert "systemctl --user is not available" in msg
        assert "Generated files" in msg
    else:
        raise AssertionError("expected enable() to raise when systemctl --user is unavailable")

    assert (tmp_path / "bin" / "adaos-autostart.sh").exists()
    assert (tmp_path / ".config" / "systemd" / "user" / "adaos.service").exists()


def test_linux_enable_root_falls_back_to_system_service(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    monkeypatch.setattr(autostart, "_bootstrap_core_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr(autostart, "_is_windows", lambda: False)
    monkeypatch.setattr(autostart, "_is_macos", lambda: False)
    monkeypatch.setattr(autostart, "_is_linux", lambda: True)
    monkeypatch.setattr(autostart, "_home", lambda: tmp_path)
    monkeypatch.setattr(autostart, "shutil_which", lambda cmd: "/bin/systemctl" if cmd == "systemctl" else None)
    monkeypatch.setattr(autostart, "_linux_user_bus_path", lambda: None)
    monkeypatch.setattr(autostart, "_linux_has_systemd_pid1", lambda: True)
    monkeypatch.setattr(autostart, "_linux_is_root", lambda: True)
    monkeypatch.setattr(autostart, "_linux_service_path_system", lambda: (tmp_path / "etc" / "systemd" / "system" / "adaos.service").resolve())

    calls: list[list[str]] = []

    class _Proc:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    def _run(cmd: list[str]):
        calls.append(cmd)
        return _Proc()

    monkeypatch.setattr(autostart, "_run", _run)

    spec = default_spec(_FakeCtx(tmp_path), host="127.0.0.1", port=8777, token="t1")
    res = enable(_FakeCtx(tmp_path), spec)
    assert res["scope"] == "system"
    assert (tmp_path / "etc" / "systemd" / "system" / "adaos.service").exists()
    assert ["systemctl", "enable", "--now", "adaos.service"] in calls
    shim = tmp_path / "bin" / "adaos"
    assert res["cli_shim"]["ok"] is True
    assert res["cli_shim"]["path"] == str(shim.resolve())
    shim_text = shim.read_text(encoding="utf-8")
    assert "Managed by AdaOS autostart" in shim_text
    assert "adaos.apps.cli.app" in shim_text
    assert ' "$@"' in shim_text
    assert "ADAOS_TOKEN" not in shim_text


def test_linux_refresh_wrapper_updates_cli_shim(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    wrapper = tmp_path / "bin" / "adaos-autostart.sh"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text("exec '/old/python'\n", encoding="utf-8")

    monkeypatch.setattr(autostart, "_is_windows", lambda: False)
    monkeypatch.setattr(autostart, "_is_macos", lambda: False)
    monkeypatch.setattr(autostart, "_is_linux", lambda: True)
    monkeypatch.setattr(autostart, "_linux_is_root", lambda: True)
    monkeypatch.setattr(autostart, "_home", lambda: tmp_path)
    monkeypatch.setattr(autostart, "status", lambda ctx: {"wrapper": str(wrapper), "scope": "system"})

    spec = default_spec(_FakeCtx(tmp_path), host="127.0.0.1", port=8777, token="t1")
    res = autostart.refresh_wrapper(_FakeCtx(tmp_path), spec)

    shim = tmp_path / "bin" / "adaos"
    assert shim.exists()
    assert res["cli_shim"]["install"]["ok"] is True
    assert res["cli_shim"]["changed"] is True
    assert "adaos.apps.cli.app" in shim.read_text(encoding="utf-8")


def test_linux_enable_root_prefers_system_service_even_with_user_bus(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    monkeypatch.setattr(autostart, "_bootstrap_core_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr(autostart, "_is_windows", lambda: False)
    monkeypatch.setattr(autostart, "_is_macos", lambda: False)
    monkeypatch.setattr(autostart, "_is_linux", lambda: True)
    monkeypatch.setattr(autostart, "_home", lambda: tmp_path)
    monkeypatch.setattr(autostart, "shutil_which", lambda cmd: "/bin/systemctl" if cmd == "systemctl" else None)
    monkeypatch.setattr(autostart, "_linux_user_bus_path", lambda: tmp_path / "bus")
    monkeypatch.setattr(autostart, "_linux_has_systemd_pid1", lambda: True)
    monkeypatch.setattr(autostart, "_linux_is_root", lambda: True)
    monkeypatch.setattr(autostart, "_linux_service_path_system", lambda: (tmp_path / "etc" / "systemd" / "system" / "adaos.service").resolve())

    stale_user_service = tmp_path / ".config" / "systemd" / "user" / "adaos.service"
    stale_user_service.parent.mkdir(parents=True, exist_ok=True)
    stale_user_service.write_text("[Unit]\nDescription=stale\n", encoding="utf-8")

    calls: list[list[str]] = []

    class _Proc:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    monkeypatch.setattr(autostart, "_run", lambda cmd: calls.append(cmd) or _Proc())

    spec = default_spec(_FakeCtx(tmp_path), host="127.0.0.1", port=8777, token="t1")
    res = enable(_FakeCtx(tmp_path), spec)

    assert res["scope"] == "system"
    assert ["systemctl", "enable", "--now", "adaos.service"] in calls
    assert ["systemctl", "--user", "disable", "--now", "adaos.service"] in calls
    assert not stale_user_service.exists()


def test_linux_enable_system_run_as_user_rejects_root_paths(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    monkeypatch.setattr(autostart, "_bootstrap_core_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr(autostart, "_is_windows", lambda: False)
    monkeypatch.setattr(autostart, "_is_macos", lambda: False)
    monkeypatch.setattr(autostart, "_is_linux", lambda: True)
    monkeypatch.setattr(autostart, "_home", lambda: tmp_path)
    monkeypatch.setattr(autostart, "shutil_which", lambda cmd: "/bin/systemctl" if cmd == "systemctl" else None)
    monkeypatch.setattr(autostart, "_linux_user_bus_path", lambda: None)
    monkeypatch.setattr(autostart, "_linux_has_systemd_pid1", lambda: True)
    monkeypatch.setattr(autostart, "_linux_is_root", lambda: True)

    spec = default_spec(_FakeCtx(tmp_path), host="127.0.0.1", port=8777, token="t1")
    spec = type(spec)(  # keep dataclass type but override fields
        name=spec.name,
        argv=("/root/adaos/.venv/bin/python3",) + tuple(spec.argv[1:]),
        env={**spec.env, "ADAOS_BASE_DIR": "/root/adaos/.adaos"},
    )

    try:
        enable(_FakeCtx(tmp_path), spec, scope="system", run_as="adaos", create_user=True)
    except RuntimeError as exc:
        assert "paths point to /root" in str(exc)
    else:
        raise AssertionError("expected enable() to reject running as user with /root paths")


def test_linux_enable_system_can_create_user(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.autostart as autostart

    monkeypatch.setattr(autostart, "_bootstrap_core_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr(autostart, "_is_windows", lambda: False)
    monkeypatch.setattr(autostart, "_is_macos", lambda: False)
    monkeypatch.setattr(autostart, "_is_linux", lambda: True)
    monkeypatch.setattr(autostart, "_home", lambda: tmp_path)
    monkeypatch.setattr(autostart, "shutil_which", lambda cmd: "/bin/systemctl" if cmd == "systemctl" else None)
    monkeypatch.setattr(autostart, "_linux_user_bus_path", lambda: None)
    monkeypatch.setattr(autostart, "_linux_has_systemd_pid1", lambda: True)
    monkeypatch.setattr(autostart, "_linux_is_root", lambda: True)
    monkeypatch.setattr(autostart, "_linux_service_path_system", lambda: (tmp_path / "etc" / "systemd" / "system" / "adaos.service").resolve())

    created: list[str] = []
    monkeypatch.setattr(autostart, "_linux_user_exists", lambda u: False)
    monkeypatch.setattr(autostart, "_linux_create_system_user", lambda u: created.append(u))

    calls: list[list[str]] = []

    class _Proc:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    monkeypatch.setattr(autostart, "_run", lambda cmd: calls.append(cmd) or _Proc())

    spec = default_spec(_FakeCtx(tmp_path), host="127.0.0.1", port=8777, token="t1")
    res = enable(_FakeCtx(tmp_path), spec, scope="system", run_as="adaos", create_user=True)
    assert res["scope"] == "system"
    assert res["run_as"] == "adaos"
    assert created == ["adaos"]
