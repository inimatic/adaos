import asyncio
import json
import types

from adaos.apps.cli.commands.api import (
    _advertise_base,
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


def test_api_detached_restart_uses_root_cli_bootstrap(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(api_cmd, "merged_runtime_dotenv_env", lambda env: dict(env))
    monkeypatch.setattr(api_cmd.subprocess, "Popen", lambda **kwargs: captured.update(kwargs))

    api_cmd._spawn_detached_server("127.0.0.1", 8777, token="t1", reload=True)

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
