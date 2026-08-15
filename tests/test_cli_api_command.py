import asyncio
import json
import os
import types

import pytest

from adaos.apps.cli.commands.api import (
    ManagedRuntimeConflict,
    _advertise_base,
    _configure_runtime_endpoint_env,
    _ensure_api_serve_dev_sidecar,
    _find_matching_server_pids,
    _is_local_url,
    _missing_runtime_preflight_required_files,
    _parse_windows_dynamic_port_range,
    _parse_windows_tcp_excluded_ranges,
    _probe_api_bind_availability,
    _process_matches_bind,
    _run_api_pre_stop_preflight,
    _resolve_stop_bind,
    _resolve_bind,
    _resolve_implicit_api_port_fallback,
    _stop_previous_server,
    _write_pidfile,
    app,
)
from adaos.apps.cli.commands import api as api_cmd
from adaos.apps.cli.commands import dev as dev_cmd
from adaos.services.runtime_dotenv import merged_runtime_dotenv_env
from adaos.services.node_config import NodeConfig
from typer.testing import CliRunner


def test_advertise_base_uses_loopback_for_wildcard_bind():
    assert _advertise_base("0.0.0.0", 8779) == "http://127.0.0.1:8779"
    assert _advertise_base("::", 8779) == "http://127.0.0.1:8779"


def test_write_pidfile_records_server_owner(tmp_path):
    path = tmp_path / "serve.json"

    _write_pidfile(path, host="127.0.0.1", port=8778, advertised_base="http://127.0.0.1:8778", owner="autostart")

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["owner"] == "autostart"
    assert data["port"] == 8778


def test_resolve_bind_prefers_saved_local_hub_port():
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="hub",
        hub_url="http://127.0.0.1:8779",
        local_api_url="http://127.0.0.1:8779",
        token="t1",
    )
    assert _resolve_bind(conf, "127.0.0.1", 8777) == ("127.0.0.1", 8779)


def test_resolve_bind_ignores_remote_hub_url_for_local_bind():
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="hub",
        hub_url="https://api.inimatic.com/hubs/sn_1",
        token="t1",
    )
    assert _resolve_bind(conf, "127.0.0.1", 8777) == ("127.0.0.1", 8777)
    assert not _is_local_url(conf.hub_url)


def test_resolve_bind_keeps_explicit_slot_port_for_supervisor_managed_runtime(monkeypatch):
    monkeypatch.setenv("ADAOS_SUPERVISOR_ENABLED", "1")
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="hub",
        hub_url="http://127.0.0.1:8778",
        token="t1",
    )
    assert _resolve_bind(conf, "127.0.0.1", 8777) == ("127.0.0.1", 8777)


def test_resolve_stop_bind_uses_local_hub_url():
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="hub",
        hub_url="http://127.0.0.1:8779",
        local_api_url="http://127.0.0.1:8779",
        token="t1",
    )
    assert _resolve_stop_bind(conf) == ("127.0.0.1", 8779)


def test_runtime_preflight_requires_redevice_sdk(tmp_path):
    missing = _missing_runtime_preflight_required_files(tmp_path)

    assert "src/adaos/sdk/redevice.py" in missing
    assert "src/adaos/apps/api/server.py" in missing


def test_parse_windows_tcp_excluded_ranges():
    output = """
Protocol tcp Port Exclusion Ranges

Start Port    End Port
----------    --------
      8730        8829
     50000       50059     *
"""

    assert _parse_windows_tcp_excluded_ranges(output) == [(8730, 8829), (50000, 50059)]


def test_parse_windows_dynamic_port_range():
    output = """
Protocol tcp Dynamic Port Range
---------------------------------
Start Port      : 1024
Number of Ports : 13977
"""

    assert _parse_windows_dynamic_port_range(output) == {"start": 1024, "count": 13977, "end": 15000}


def test_bind_preflight_reports_windows_excluded_port(monkeypatch):
    monkeypatch.setattr("adaos.apps.cli.commands.api._tcp_exclusion_for_port", lambda host, port: (8730, 8829))

    result = _probe_api_bind_availability("127.0.0.1", 8777)

    assert result["ok"] is False
    assert result["error"] == "PortExcluded"
    assert result["range"] == [8730, 8829]


def test_implicit_api_port_repair_precedes_fallback(monkeypatch):
    monkeypatch.setattr(
        api_cmd,
        "_probe_api_bind_availability",
        lambda host, port: {"ok": False, "error": "PortExcluded"} if int(port) == 8777 else {"ok": True},
    )
    monkeypatch.setattr(api_cmd, "_repo_root_for_runtime_preflight", lambda: types.SimpleNamespace())
    monkeypatch.setattr(
        api_cmd,
        "_repair_windows_api_port_exclusion",
        lambda host, port, probe, repo_root: {"attempted": True, "repaired": True},
    )

    assert _resolve_implicit_api_port_fallback("127.0.0.1", 8777, explicit_port=False) == ("127.0.0.1", 8777)


def test_implicit_api_port_fallback_skips_windows_reserved_port(monkeypatch):
    def _probe(host, port):
        if int(port) == 8779:
            return {"ok": False, "error": "PortExcluded"}
        if int(port) == 8877:
            return {"ok": True}
        return {"ok": False, "error": "PermissionDenied"}

    monkeypatch.setattr("adaos.apps.cli.commands.api._probe_api_bind_availability", _probe)

    assert _resolve_implicit_api_port_fallback("127.0.0.1", 8779, explicit_port=False) == ("127.0.0.1", 8877)
    assert _resolve_implicit_api_port_fallback("127.0.0.1", 8779, explicit_port=True) == ("127.0.0.1", 8779)


def test_api_preflight_uses_successful_port_repair(monkeypatch, tmp_path):
    calls = {"probe": 0}

    def _probe(host, port):
        calls["probe"] += 1
        if calls["probe"] == 1:
            return {"ok": False, "error": "PortExcluded"}
        return {"ok": True}

    monkeypatch.setattr(api_cmd, "_repo_root_for_runtime_preflight", lambda: tmp_path)
    monkeypatch.setattr(api_cmd, "_missing_runtime_preflight_required_files", lambda repo_root: [])
    monkeypatch.setattr(api_cmd, "_probe_api_bind_availability", _probe)
    monkeypatch.setattr(
        api_cmd,
        "_repair_windows_api_port_exclusion",
        lambda host, port, probe, repo_root: {"attempted": True, "repaired": True},
    )
    monkeypatch.setattr(api_cmd, "_skills_root_for_runtime_preflight", lambda repo_root: tmp_path / "skills")
    monkeypatch.setattr(api_cmd, "_run_runtime_import_preflight", lambda repo_root, skills_root: {"ok": True})

    result = _run_api_pre_stop_preflight("127.0.0.1", 8777)

    assert result["ok"] is True
    assert calls["probe"] == 2


def test_api_serve_preflight_failure_keeps_previous_server(monkeypatch):
    runner = CliRunner()
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="hub",
        hub_url="http://127.0.0.1:8779",
        local_api_url="http://127.0.0.1:8779",
        token="t1",
    )
    stopped: list[tuple[str, int]] = []

    monkeypatch.setattr("adaos.apps.cli.commands.api.load_config", lambda: conf)
    monkeypatch.setattr(
        "adaos.apps.cli.commands.api._run_api_pre_stop_preflight",
        lambda host, port: {
            "ok": False,
            "errors": [
                {
                    "phase": "skill_handler_import",
                    "error": "ModuleNotFoundError",
                    "message": "No module named 'adaos.sdk.redevice'",
                    "path": ".adaos/workspace/skills/.runtime/redevice_list/handlers/main.py",
                }
            ],
        },
    )
    monkeypatch.setattr("adaos.apps.cli.commands.api._stop_previous_server", lambda host, port: stopped.append((host, port)))
    monkeypatch.setattr("adaos.apps.cli.commands.api._write_pidfile", lambda *args, **kwargs: None)
    monkeypatch.setattr("adaos.apps.cli.commands.api._ensure_api_serve_dev_sidecar", lambda *args, **kwargs: None)
    monkeypatch.setattr("adaos.apps.cli.commands.api.uvicorn.run", lambda *args, **kwargs: None)

    result = runner.invoke(app, ["serve", "--host", "127.0.0.1", "--port", "8779"])

    assert result.exit_code == 1
    assert stopped == []
    assert "preflight failed" in result.stdout
    assert "keeping existing API server running" in result.stdout


def test_api_restart_preflight_failure_keeps_previous_server(monkeypatch):
    runner = CliRunner()
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="hub",
        hub_url="http://127.0.0.1:8779",
        local_api_url="http://127.0.0.1:8779",
        token="t1",
    )
    shutdowns: list[tuple[str, int]] = []
    forced: list[tuple[str, int]] = []

    monkeypatch.setattr("adaos.apps.cli.commands.api.load_config", lambda: conf)
    monkeypatch.setattr(
        "adaos.apps.cli.commands.api._run_api_pre_stop_preflight",
        lambda host, port: {
            "ok": False,
            "errors": [{"phase": "required_files", "error": "MissingRequiredFiles", "message": "bad checkout"}],
        },
    )
    monkeypatch.setattr(
        "adaos.apps.cli.commands.api._request_graceful_shutdown",
        lambda host, port, token=None, reason="cli.stop": shutdowns.append((host, port)) or True,
    )
    monkeypatch.setattr("adaos.apps.cli.commands.api._stop_previous_server", lambda host, port: forced.append((host, port)))

    result = runner.invoke(app, ["restart"])

    assert result.exit_code == 1
    assert shutdowns == []
    assert forced == []
    assert "preflight failed" in result.stdout


def test_api_detached_restart_uses_root_cli_bootstrap(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _Process:
        pid = 4321

    monkeypatch.setattr(api_cmd, "merged_runtime_dotenv_env", lambda env: dict(env))
    log_path = tmp_path / "restart.log"
    monkeypatch.setattr(api_cmd, "_restart_log_path", lambda _host, _port: log_path)
    monkeypatch.setattr(
        api_cmd.subprocess,
        "Popen",
        lambda **kwargs: captured.update(kwargs) or _Process(),
    )

    launch = api_cmd._spawn_detached_server("127.0.0.1", 8777, token="t1", reload=True)

    assert captured["args"] == [
        api_cmd.sys.executable,
        "-m",
        "adaos",
        "api",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "8777",
        "--reload",
        "--token",
        "t1",
    ]
    assert launch.pid == 4321
    assert launch.log_path == log_path
    assert getattr(captured["stdout"], "closed", False) is True
    assert captured["stderr"] is api_cmd.subprocess.STDOUT


def test_api_detached_restart_preserves_repo_runtime_environment(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    repo_root = tmp_path / "repo"
    repo_python = repo_root / ".venv" / "Scripts" / "python.exe"
    (repo_root / "src").mkdir(parents=True)
    repo_python.parent.mkdir(parents=True)
    repo_python.write_text("", encoding="utf-8")

    class _Process:
        pid = 4321

    monkeypatch.delenv("ADAOS_CLI_SLOT_BOUND", raising=False)
    monkeypatch.setenv("ADAOS_ACTIVE_CORE_SLOT", "A")
    monkeypatch.setenv("ADAOS_SLOT_REPO_ROOT", "stale-slot")
    monkeypatch.setattr(api_cmd.sys, "executable", str(repo_python))
    monkeypatch.setattr(api_cmd, "_repo_root_for_runtime_preflight", lambda: repo_root)
    monkeypatch.setattr(api_cmd, "_repo_runtime_git_commit", lambda: "repo-commit")
    monkeypatch.setattr(api_cmd, "merged_runtime_dotenv_env", lambda env: dict(env))
    monkeypatch.setattr(api_cmd, "_restart_log_path", lambda _host, _port: tmp_path / "restart.log")
    monkeypatch.setattr(
        api_cmd.subprocess,
        "Popen",
        lambda **kwargs: captured.update(kwargs) or _Process(),
    )

    api_cmd._spawn_detached_server("127.0.0.1", 8777, token=None)

    env = captured["env"]
    assert isinstance(env, dict)
    assert env["ADAOS_DISABLE_ACTIVE_SLOT_PYTHON_REEXEC"] == "1"
    assert env["ADAOS_DISABLE_ACTIVE_SLOT_ENV_APPLY"] == "1"
    assert env["ADAOS_DISABLE_PREFERRED_PYTHON_REEXEC"] == "1"
    assert env["ADAOS_GIT_COMMIT"] == "repo-commit"
    assert env["PYTHONPATH"] == str(repo_root / "src")
    assert "ADAOS_ACTIVE_CORE_SLOT" not in env
    assert "ADAOS_SLOT_REPO_ROOT" not in env
    assert captured["cwd"] == str(repo_root.resolve())


def test_api_restart_expected_commit_uses_repo_identity(monkeypatch):
    monkeypatch.setattr(api_cmd, "_api_restart_preserves_repo_runtime", lambda: True)
    monkeypatch.setattr(api_cmd, "_repo_runtime_git_commit", lambda: "repo-commit")

    assert api_cmd._api_restart_expected_git_commit() == "repo-commit"


def test_api_restart_start_timeout_is_bounded_and_configurable(monkeypatch):
    monkeypatch.delenv("ADAOS_API_RESTART_START_TIMEOUT_SEC", raising=False)
    assert api_cmd._api_restart_start_timeout_seconds() == 90.0

    monkeypatch.setenv("ADAOS_API_RESTART_START_TIMEOUT_SEC", "90")
    assert api_cmd._api_restart_start_timeout_seconds() == 90.0

    monkeypatch.setenv("ADAOS_API_RESTART_START_TIMEOUT_SEC", "1")
    assert api_cmd._api_restart_start_timeout_seconds() == 20.0

    monkeypatch.setenv("ADAOS_API_RESTART_START_TIMEOUT_SEC", "invalid")
    assert api_cmd._api_restart_start_timeout_seconds() == 90.0

    monkeypatch.delenv("ADAOS_API_RESTART_STABILITY_SEC", raising=False)
    assert api_cmd._api_restart_stability_seconds() == 10.0
    monkeypatch.setenv("ADAOS_API_RESTART_STABILITY_SEC", "120")
    assert api_cmd._api_restart_stability_seconds() == 60.0

    monkeypatch.delenv("ADAOS_API_RESTART_READINESS_GRACE_SEC", raising=False)
    assert api_cmd._api_restart_readiness_grace_seconds() == 60.0
    monkeypatch.setenv("ADAOS_API_RESTART_READINESS_GRACE_SEC", "300")
    assert api_cmd._api_restart_readiness_grace_seconds() == 120.0


def test_wait_for_server_start_requires_expected_pid_and_ready_health(monkeypatch):
    class _ReadyResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "adaos": {"git_commit": "abc123"}}

    monkeypatch.setattr(api_cmd, "_find_listening_server_pid", lambda _host, _port: 4321)
    monkeypatch.setattr(api_cmd.requests, "get", lambda *_args, **_kwargs: _ReadyResponse())

    assert api_cmd._wait_for_server_start(
        "127.0.0.1",
        8777,
        timeout=0.5,
        expected_git_commit="abc123",
        stability=0,
    )
    assert not api_cmd._wait_for_server_start(
        "127.0.0.1",
        8777,
        timeout=0.01,
        expected_git_commit="other-build",
        stability=0,
        readiness_grace=0,
    )


def test_wait_for_server_start_rejects_listener_without_ready_health(monkeypatch):
    class _UnreadyResponse:
        status_code = 503

        @staticmethod
        def json():
            return {"detail": "not ready", "adaos": {"git_commit": "abc123"}}

    monkeypatch.setattr(api_cmd, "_find_listening_server_pid", lambda _host, _port: 4321)
    monkeypatch.setattr(api_cmd.requests, "get", lambda *_args, **_kwargs: _UnreadyResponse())

    assert not api_cmd._wait_for_server_start(
        "127.0.0.1",
        8777,
        timeout=0.01,
        expected_git_commit="abc123",
        stability=0,
        readiness_grace=0,
    )


def test_wait_for_server_start_grants_bounded_pre_listener_process_grace(monkeypatch):
    clock = {"now": 0.0}

    monkeypatch.setattr(api_cmd.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        api_cmd.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )
    monkeypatch.setattr(api_cmd.psutil, "pid_exists", lambda pid: pid == 4321)
    monkeypatch.setattr(api_cmd, "_find_listening_server_pid", lambda _host, _port: None)

    assert not api_cmd._wait_for_server_start(
        "127.0.0.1",
        8777,
        timeout=0.8,
        expected_pid=4321,
        stability=0,
        readiness_grace=0.2,
    )
    assert clock["now"] >= 1.0


def test_api_restart_uses_configured_start_timeout_and_reports_launch_log(
    monkeypatch,
    tmp_path,
):
    runner = CliRunner()
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="hub",
        hub_url="http://127.0.0.1:8779",
        local_api_url="http://127.0.0.1:8779",
        token="t1",
    )
    observed: dict[str, float] = {}
    launch = api_cmd.DetachedServerLaunch(pid=4321, log_path=tmp_path / "restart.log")

    monkeypatch.setattr(api_cmd, "load_config", lambda: conf)
    monkeypatch.setattr(api_cmd, "_ensure_api_pre_stop_preflight_or_exit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api_cmd, "_restart_autostart_service_for_current_base_dir", lambda _base: None)
    monkeypatch.setattr(api_cmd, "_current_base_dir", lambda: tmp_path)
    monkeypatch.setattr(api_cmd, "_restart_marker_path", lambda _host, _port: tmp_path / "marker.json")
    monkeypatch.setattr(api_cmd, "_write_restart_marker", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api_cmd, "_clear_restart_marker", lambda _path: None)
    monkeypatch.setattr(api_cmd, "_request_graceful_shutdown", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(api_cmd, "_spawn_detached_server", lambda *_args, **_kwargs: launch)
    monkeypatch.setattr(api_cmd, "_api_restart_start_timeout_seconds", lambda: 75.0)
    monkeypatch.setattr(api_cmd, "_api_restart_expected_git_commit", lambda: "abc123")
    monkeypatch.setattr(
        api_cmd,
        "_wait_for_server_start",
        lambda _host, _port, *, timeout, expected_git_commit, expected_pid: observed.update(
            timeout=timeout,
            expected_git_commit=expected_git_commit,
            expected_pid=expected_pid,
        )
        or False,
    )
    monkeypatch.setattr(api_cmd.psutil, "pid_exists", lambda pid: pid == 4321)

    result = runner.invoke(app, ["restart"])

    assert result.exit_code == 1
    assert observed == {"timeout": 75.0, "expected_git_commit": "abc123", "expected_pid": 4321}
    assert "base_timeout=75s" in result.stdout
    assert "readiness_grace=60s" in result.stdout
    assert "alive=true" in result.stdout
    assert "expected_git_commit=abc123" in result.stdout
    assert str(launch.log_path) in result.stdout


def test_resolve_stop_bind_rejects_remote_hub_url():
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="member",
        hub_url="https://api.inimatic.com/hubs/sn_1",
        token="t1",
    )
    assert _resolve_stop_bind(conf) is None


def test_process_matches_bind_with_split_flags():
    proc = types.SimpleNamespace(cmdline=lambda: ["python", "-m", "adaos", "api", "serve", "--host", "127.0.0.1", "--port", "8778"])
    assert _process_matches_bind(proc, "127.0.0.1", 8778)
    assert not _process_matches_bind(proc, "127.0.0.1", 8777)


def test_process_matches_bind_with_equals_flags():
    proc = types.SimpleNamespace(cmdline=lambda: ["python", "-m", "adaos", "api", "serve", "--host=localhost", "--port=8778"])
    assert _process_matches_bind(proc, "127.0.0.1", 8778)


def test_process_matches_bind_defaults_to_loopback_and_8777():
    proc = types.SimpleNamespace(cmdline=lambda: ["python", "-m", "adaos", "api", "serve"])
    assert _process_matches_bind(proc, "127.0.0.1", 8777)
    assert not _process_matches_bind(proc, "127.0.0.1", 8778)


def test_process_matches_bind_for_autostart_runner():
    proc = types.SimpleNamespace(cmdline=lambda: ["python", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8778"])
    assert _process_matches_bind(proc, "127.0.0.1", 8778)


def test_api_serve_uses_api_launch_mode(monkeypatch):
    runner = CliRunner()
    called: dict[str, object] = {}

    def _run(ctx, **kwargs):
        called.update(kwargs)

    monkeypatch.setattr("adaos.apps.cli.commands.api.run_api_runtime", _run)

    result = runner.invoke(app, ["serve", "--host", "127.0.0.1", "--port", "8779"])

    assert result.exit_code == 0
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 8779
    assert called["launch_mode"] == "api_serve"
    assert called["pidfile_owner"] == "api"


def test_configure_runtime_endpoint_env_uses_actual_api_bind(monkeypatch):
    monkeypatch.delenv("ADAOS_SELF_BASE_URL", raising=False)
    monkeypatch.delenv("ADAOS_RUNTIME_HOST", raising=False)
    monkeypatch.delenv("ADAOS_RUNTIME_PORT", raising=False)
    monkeypatch.delenv("ADAOS_RUNTIME_LAUNCH_MODE", raising=False)

    _configure_runtime_endpoint_env(
        advertised_base="http://127.0.0.1:8779",
        launch_mode="api_serve",
    )

    assert os.environ["ADAOS_SELF_BASE_URL"] == "http://127.0.0.1:8779"
    assert os.environ["ADAOS_RUNTIME_HOST"] == "127.0.0.1"
    assert os.environ["ADAOS_RUNTIME_PORT"] == "8779"
    assert os.environ["ADAOS_RUNTIME_LAUNCH_MODE"] == "api_serve"


def test_api_serve_reports_autostart_conflict_without_traceback(monkeypatch):
    runner = CliRunner()
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="hub",
        hub_url="http://127.0.0.1:8777",
        local_api_url="http://127.0.0.1:8777",
        token="t1",
    )

    def _raise_conflict(host, port):
        raise ManagedRuntimeConflict(host=host, port=port, pids=[222])

    monkeypatch.setattr(api_cmd, "load_config", lambda: conf)
    monkeypatch.setattr(api_cmd, "_ensure_api_pre_stop_preflight_or_exit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api_cmd, "_stop_previous_server", _raise_conflict)

    result = runner.invoke(app, ["serve", "--host", "127.0.0.1", "--port", "8777"])

    assert result.exit_code == 1
    assert "autostart is already running" in result.stdout
    assert "adaos autostart disable" in result.stdout
    assert "Traceback" not in result.stdout


def test_stop_previous_server_refuses_autostart_pidfile_owner(monkeypatch, tmp_path):
    pidfile = tmp_path / "serve.json"
    pidfile.write_text(json.dumps({"pid": 123, "owner": "autostart"}), encoding="utf-8")

    monkeypatch.setattr(api_cmd, "_pidfile_path", lambda _host, _port: pidfile)
    monkeypatch.setattr(api_cmd, "_current_process_family_pids", lambda: set())
    monkeypatch.setattr(api_cmd, "_current_launch_owner", lambda: "api")
    monkeypatch.setattr(api_cmd, "_current_base_dir", lambda: tmp_path)
    monkeypatch.setattr(api_cmd, "_find_listening_server_pid", lambda _host, _port: None)
    monkeypatch.setattr(api_cmd, "_process_running", lambda pid: int(pid) == 123)
    monkeypatch.setattr(
        api_cmd,
        "_env_flag",
        lambda name, default=False: False if name == "ADAOS_API_TAKEOVER_AUTOSTART" else default,
    )

    with pytest.raises(ManagedRuntimeConflict) as excinfo:
        _stop_previous_server("127.0.0.1", 8777)

    assert excinfo.value.pids == (123,)
    assert "autostart disable" in str(excinfo.value)


def test_dev_serve_uses_dev_launch_mode(monkeypatch):
    runner = CliRunner()
    called: dict[str, object] = {}

    def _run(ctx, **kwargs):
        called.update(kwargs)

    monkeypatch.setattr("adaos.apps.cli.commands.api.run_api_runtime", _run)

    result = runner.invoke(dev_cmd.app, ["serve", "--host", "127.0.0.1", "--port", "8779"])

    assert result.exit_code == 0
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 8779
    assert called["launch_mode"] == "dev_serve"
    assert called["pidfile_owner"] == "dev"


def test_api_serve_dev_sidecar_adopts_existing_listener(monkeypatch):
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="hub",
        hub_url="http://127.0.0.1:8777",
        token="t1",
    )
    monkeypatch.delenv("ADAOS_AUTOSTART_MANAGED", raising=False)
    monkeypatch.delenv("ADAOS_SUPERVISOR_ENABLED", raising=False)
    monkeypatch.delenv("ADAOS_AUTOSTART_MODE", raising=False)
    monkeypatch.setattr("adaos.services.realtime_sidecar.realtime_sidecar_enabled", lambda role=None: True)
    monkeypatch.setattr("adaos.services.realtime_sidecar.resolve_realtime_remote_candidates", lambda: ["wss://root/nats"])
    monkeypatch.setattr(
        "adaos.services.realtime_sidecar.realtime_sidecar_listener_snapshot",
        lambda proc=None, role=None: {
            "listener_running": True,
            "listener_pid": 1234,
            "local_url": "nats://127.0.0.1:7422",
        },
    )

    async def _unexpected_start(*_args, **_kwargs):
        raise AssertionError("existing sidecar listener should be adopted, not replaced")

    monkeypatch.setattr("adaos.services.realtime_sidecar.start_realtime_sidecar_subprocess", _unexpected_start)

    assert asyncio.run(_ensure_api_serve_dev_sidecar(conf, launch_mode="dev_serve")) is None


def test_api_serve_dev_sidecar_leaves_repo_root_to_context(monkeypatch):
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="hub",
        hub_url="http://127.0.0.1:8777",
        token="t1",
    )
    monkeypatch.delenv("ADAOS_AUTOSTART_MANAGED", raising=False)
    monkeypatch.delenv("ADAOS_SUPERVISOR_ENABLED", raising=False)
    monkeypatch.delenv("ADAOS_AUTOSTART_MODE", raising=False)
    monkeypatch.setattr("adaos.services.realtime_sidecar.realtime_sidecar_enabled", lambda role=None: True)
    monkeypatch.setattr("adaos.services.realtime_sidecar.resolve_realtime_remote_candidates", lambda: ["wss://root/nats"])
    monkeypatch.setattr(
        "adaos.services.realtime_sidecar.realtime_sidecar_listener_snapshot",
        lambda proc=None, role=None: {"listener_running": False},
    )

    class FakeProc:
        pid = 4321

        def terminate(self):
            return None

    calls: dict[str, object] = {}

    async def _start(**kwargs):
        calls["start_kwargs"] = dict(kwargs)
        return FakeProc()

    async def _stop(_proc):
        calls["stopped"] = True

    monkeypatch.setattr("adaos.services.realtime_sidecar.start_realtime_sidecar_subprocess", _start)
    monkeypatch.setattr("adaos.services.realtime_sidecar.stop_realtime_sidecar_subprocess", _stop)

    assert asyncio.run(_ensure_api_serve_dev_sidecar(conf, launch_mode="dev_serve")) is not None
    assert calls["start_kwargs"] == {"role": "hub"}


def test_find_matching_server_pids_skips_protected_wrappers(monkeypatch):
    class FakeProc:
        def __init__(self, pid: int, cmdline: list[str]):
            self.info = {"pid": pid, "cmdline": cmdline}
            self._cmdline = cmdline

        def cmdline(self):
            return list(self._cmdline)

    procs = [
        FakeProc(100, ["D:\\git\\adaos\\.venv\\Scripts\\python.exe", "-m", "adaos", "api", "serve", "--host", "127.0.0.1", "--port", "8778"]),
        FakeProc(200, ["C:\\Python311\\python.exe", "-m", "adaos", "api", "serve", "--host", "127.0.0.1", "--port", "8778"]),
        FakeProc(300, ["python", "-m", "adaos", "api", "serve", "--host", "127.0.0.1", "--port", "8779"]),
    ]

    monkeypatch.setattr("adaos.apps.cli.commands.api.psutil.process_iter", lambda *_args, **_kwargs: procs)
    monkeypatch.setattr("adaos.apps.cli.commands.api.os.getpid", lambda: 999)

    assert _find_matching_server_pids("127.0.0.1", 8778, protected_pids={100}) == [200]


def test_api_stop_uses_hub_url_from_node_config(monkeypatch):
    runner = CliRunner()
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="hub",
        hub_url="http://127.0.0.1:8779",
        local_api_url="http://127.0.0.1:8779",
        token="t1",
    )
    called: list[tuple[str, int]] = []

    monkeypatch.setattr("adaos.apps.cli.commands.api.load_config", lambda: conf)
    monkeypatch.setattr("adaos.apps.cli.commands.api._stop_previous_server", lambda host, port: called.append((host, port)))
    monkeypatch.setattr("adaos.apps.cli.commands.api._pidfile_path", lambda host, port: types.SimpleNamespace(exists=lambda: False))
    monkeypatch.setattr("adaos.apps.cli.commands.api._find_listening_server_pid", lambda host, port: None)
    monkeypatch.setattr("adaos.apps.cli.commands.api._find_matching_server_pids", lambda host, port, protected_pids=None: [])
    monkeypatch.setattr("adaos.apps.cli.commands.api._current_process_family_pids", lambda: set())

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
    assert called == [("127.0.0.1", 8779)]
    assert "No AdaOS API server running at http://127.0.0.1:8779" in result.stdout


def test_api_stop_prefers_graceful_shutdown(monkeypatch):
    runner = CliRunner()
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="hub",
        hub_url="http://127.0.0.1:8779",
        local_api_url="http://127.0.0.1:8779",
        token="t1",
    )
    forced: list[tuple[str, int]] = []

    monkeypatch.setattr("adaos.apps.cli.commands.api.load_config", lambda: conf)
    monkeypatch.setattr("adaos.apps.cli.commands.api._pidfile_path", lambda host, port: types.SimpleNamespace(exists=lambda: True))
    owner_state = {"calls": 0}

    def _owner_pid(host, port):
        owner_state["calls"] += 1
        return 1234 if owner_state["calls"] == 1 else None

    monkeypatch.setattr("adaos.apps.cli.commands.api._find_listening_server_pid", _owner_pid)
    monkeypatch.setattr("adaos.apps.cli.commands.api._find_matching_server_pids", lambda host, port, protected_pids=None: [])
    monkeypatch.setattr("adaos.apps.cli.commands.api._current_process_family_pids", lambda: set())
    monkeypatch.setattr("adaos.apps.cli.commands.api._request_graceful_shutdown", lambda host, port, token, reason='cli.stop': True)
    monkeypatch.setattr("adaos.apps.cli.commands.api._stop_previous_server", lambda host, port: forced.append((host, port)))

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
    assert forced == []
    assert "Stopped AdaOS API gracefully at http://127.0.0.1:8779" in result.stdout


def test_takeover_shutdown_uses_runtime_retire_scope(monkeypatch):
    captured: dict[str, object] = {}

    class _Response:
        status_code = 202

    def _post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(api_cmd.requests, "post", _post)
    monkeypatch.setattr(api_cmd, "_wait_for_server_exit", lambda host, port, timeout: True)

    stopped = api_cmd._request_graceful_shutdown(
        "127.0.0.1",
        8778,
        token="t1",
        reason="autostart.takeover",
        lifecycle_scope="runtime_retire",
    )

    assert stopped is True
    assert captured["json"]["lifecycle_scope"] == "runtime_retire"


def test_api_stop_fails_for_non_local_hub_url(monkeypatch):
    runner = CliRunner()
    conf = NodeConfig(
        node_id="n1",
        subnet_id="sn_1",
        role="member",
        hub_url="https://api.inimatic.com/hubs/sn_1",
        token="t1",
    )

    monkeypatch.setattr("adaos.apps.cli.commands.api.load_config", lambda: conf)

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 1
    assert "does not contain a local_api_url" in result.stdout


def test_merged_runtime_dotenv_env_prefers_repo_ws_runtime_keys(tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "\n".join(
            [
                "HUB_NATS_WS_IMPL=websockets",
                "ADAOS_WIN_SELECTOR_LOOP=0",
                "HUB_NATS_WS_DIAG_FILE=.adaos/diagnostics/nats_ws_diag.jsonl",
                "UNRELATED_KEY=from-dotenv",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    merged = merged_runtime_dotenv_env(
        {
            "HUB_NATS_WS_IMPL": "aiohttp",
            "ADAOS_WIN_SELECTOR_LOOP": "1",
            "UNRELATED_KEY": "from-env",
        },
        dotenv_path=dotenv_path,
    )

    assert merged["HUB_NATS_WS_IMPL"] == "websockets"
    assert merged["ADAOS_WIN_SELECTOR_LOOP"] == "0"
    assert merged["HUB_NATS_WS_DIAG_FILE"] == ".adaos/diagnostics/nats_ws_diag.jsonl"
    assert merged["UNRELATED_KEY"] == "from-env"
