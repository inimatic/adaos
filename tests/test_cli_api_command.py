import asyncio
import json
import types

from adaos.apps.cli.commands.api import (
    _advertise_base,
    _ensure_api_serve_dev_sidecar,
    _find_matching_server_pids,
    _is_local_url,
    _process_matches_bind,
    _resolve_stop_bind,
    _resolve_bind,
    _write_pidfile,
    app,
)
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
