from __future__ import annotations

import asyncio
import contextlib
import socket
from pathlib import Path

import pytest

from adaos.apps.cli.commands import realtime as realtime_cmd
from adaos.services import realtime_sidecar as realtime_sidecar_mod
from adaos.services.realtime_sidecar import (
    RealtimeSidecarServer,
    build_sidecar_lifecycle_report,
    classify_realtime_sidecar_transport,
    realtime_sidecar_enablement_policy,
    realtime_sidecar_enabled,
    realtime_sidecar_local_url,
)


@pytest.fixture(autouse=True)
def _isolate_public_nats_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADAOS_ZONE_ID", raising=False)
    monkeypatch.delenv("ROOT_BASE_URL", raising=False)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_sidecar_lifecycle_report_compacts_durable_state_and_transport(tmp_path: Path) -> None:
    (tmp_path / "state" / "supervisor").mkdir(parents=True)
    (tmp_path / "state" / "core_update").mkdir(parents=True)
    (tmp_path / "state" / "supervisor" / "runtime.json").write_text(
        '{"runtime_state":"ready","runtime_api_ready":true,"managed_alive":true,'
        '"desired_running":true,"runtime_instance_id":"runtime-a",'
        '"runtime_url":"http://127.0.0.1:8777","secret":"must-not-leak"}',
        encoding="utf-8",
    )
    (tmp_path / "state" / "supervisor" / "update_attempt.json").write_text(
        '{"state":"active","target_version":"next","private":"drop"}',
        encoding="utf-8",
    )
    (tmp_path / "state" / "core_update" / "status.json").write_text(
        '{"state":"running","phase":"prepare","message":"updating","private":"drop"}',
        encoding="utf-8",
    )

    payload = build_sidecar_lifecycle_report(
        base_dir=tmp_path,
        transport_snapshot={
            "listen": "127.0.0.1:7422",
            "active_session": True,
            "remote_connected_ago_s": 1.25,
            "session_id": "transport-a",
            "last_error": None,
        },
        runtime_listener_ready=True,
        source_epoch="epoch-a",
        revision=3,
        reported_at=100.0,
    )

    assert payload["schema"] == "adaos.hub.lifecycle.sidecar.v1"
    assert payload["transport"]["state"] == "ready"
    assert payload["transport"]["ready"] is True
    assert payload["supervisor"]["runtime"]["runtime_api_ready"] is True
    assert payload["supervisor"]["runtime"]["runtime_instance_id"] == "runtime-a"
    assert "secret" not in payload["supervisor"]["runtime"]
    assert "private" not in payload["supervisor"]["status"]
    assert "private" not in payload["supervisor"]["attempt"]


def test_sidecar_transport_requires_a_current_active_session() -> None:
    classification = classify_realtime_sidecar_transport(
        {
            "active_session": False,
            "active_session_total": 0,
            "remote_connected_ago_s": 52.0,
            "last_error": None,
        },
        diag_fresh=True,
    )

    assert classification["transport_ready"] is False
    assert classification["remote_session_state"] == "down"
    assert classification["session_state"] == "local_only"


def test_sidecar_transport_accepts_fresh_active_remote_session() -> None:
    classification = classify_realtime_sidecar_transport(
        {
            "active_session": True,
            "active_session_total": 1,
            "remote_connected_ago_s": 0.2,
            "last_error": None,
        },
        diag_fresh=True,
    )

    assert classification["transport_ready"] is True
    assert classification["remote_session_state"] == "ready"
    assert classification["session_state"] == "remote_ready"


def test_sidecar_lifecycle_fingerprint_ignores_observation_heartbeat(tmp_path: Path) -> None:
    first = build_sidecar_lifecycle_report(
        base_dir=tmp_path,
        transport_snapshot={"listen": "127.0.0.1:7422", "active_session": False},
        runtime_listener_ready=False,
        source_epoch="epoch-a",
        revision=1,
        reported_at=100.0,
    )
    second = build_sidecar_lifecycle_report(
        base_dir=tmp_path,
        transport_snapshot={"listen": "127.0.0.1:7422", "active_session": False},
        runtime_listener_ready=False,
        source_epoch="epoch-a",
        revision=2,
        reported_at=115.0,
    )

    assert (
        realtime_sidecar_mod._sidecar_lifecycle_semantic_fingerprint(first)
        == realtime_sidecar_mod._sidecar_lifecycle_semantic_fingerprint(second)
    )


def test_sidecar_lifecycle_report_marks_a_planned_shutdown(tmp_path: Path) -> None:
    payload = build_sidecar_lifecycle_report(
        base_dir=tmp_path,
        transport_snapshot={"listen": "127.0.0.1:7422", "active_session": True},
        runtime_listener_ready=False,
        source_epoch="epoch-a",
        revision=4,
        reported_at=120.0,
        shutdown_kind="planned",
        shutdown_reason="service_stop",
    )

    assert payload["shutdown"] == {
        "kind": "planned",
        "reason": "service_stop",
        "observed_at": 120.0,
    }


def test_sidecar_lifecycle_report_marks_previously_ready_runtime_crashed_after_probe_grace(
    tmp_path: Path,
) -> None:
    (tmp_path / "state" / "supervisor").mkdir(parents=True)
    (tmp_path / "state" / "supervisor" / "runtime.json").write_text(
        '{"runtime_state":"ready","runtime_api_ready":true,"managed_alive":true,'
        '"desired_running":true,"runtime_instance_id":"runtime-a"}',
        encoding="utf-8",
    )

    payload = build_sidecar_lifecycle_report(
        base_dir=tmp_path,
        transport_snapshot={"listen": "127.0.0.1:7422", "active_session": True},
        runtime_listener_ready=False,
        runtime_listener_unavailable_for_s=6.0,
        runtime_crash_grace_s=6.0,
        source_epoch="epoch-a",
        revision=5,
        reported_at=130.0,
    )

    runtime = payload["supervisor"]["runtime"]
    assert runtime["managed_alive"] is False
    assert runtime["runtime_state"] == "crashed"
    assert runtime["runtime_api_ready"] is False
    assert runtime["listener_evidence"] == "unreachable_after_grace"


def test_sidecar_lifecycle_report_does_not_call_update_restart_a_crash(tmp_path: Path) -> None:
    (tmp_path / "state" / "supervisor").mkdir(parents=True)
    (tmp_path / "state" / "core_update").mkdir(parents=True)
    (tmp_path / "state" / "supervisor" / "runtime.json").write_text(
        '{"runtime_state":"ready","runtime_api_ready":true,"managed_alive":true,'
        '"desired_running":true,"runtime_instance_id":"runtime-a"}',
        encoding="utf-8",
    )
    (tmp_path / "state" / "core_update" / "status.json").write_text(
        '{"state":"running","phase":"restart"}',
        encoding="utf-8",
    )

    payload = build_sidecar_lifecycle_report(
        base_dir=tmp_path,
        transport_snapshot={"listen": "127.0.0.1:7422", "active_session": True},
        runtime_listener_ready=False,
        runtime_listener_unavailable_for_s=60.0,
        runtime_crash_grace_s=6.0,
        source_epoch="epoch-a",
        revision=6,
        reported_at=140.0,
    )

    runtime = payload["supervisor"]["runtime"]
    assert runtime["managed_alive"] is True
    assert runtime["runtime_state"] == "unavailable"
    assert "listener_evidence" not in runtime


def test_sidecar_lifecycle_tls_appends_hub_ca_to_system_trust(monkeypatch, tmp_path: Path) -> None:
    ca_path = tmp_path / "adaos-ca.pem"
    ca_path.write_text("test-ca", encoding="utf-8")
    create_calls: list[dict[str, object]] = []
    loaded_ca: list[str] = []

    class _Context:
        def load_verify_locations(self, *, cafile: str) -> None:
            loaded_ca.append(cafile)

    def _create_default_context(**kwargs):
        create_calls.append(dict(kwargs))
        return _Context()

    monkeypatch.delenv("ADAOS_ROOT_CA_MODE", raising=False)
    monkeypatch.setattr(realtime_sidecar_mod.ssl, "create_default_context", _create_default_context)

    context = realtime_sidecar_mod._sidecar_lifecycle_ssl_context(ca_path)

    assert isinstance(context, _Context)
    assert create_calls == [{}]
    assert loaded_ca == [str(ca_path)]


def test_realtime_sidecar_rotates_diag_and_log_with_five_backups(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    diag_path = tmp_path / "realtime_sidecar.jsonl"
    log_path = tmp_path / "realtime_sidecar.log"
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_MAX_BYTES", "4")
    monkeypatch.setenv("ADAOS_REALTIME_LOG_MAX_BYTES", "4")
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_BACKUPS", "5")
    monkeypatch.setenv("ADAOS_REALTIME_LOG_BACKUPS", "5")

    for path in (diag_path, log_path):
        path.write_text("active-too-large", encoding="utf-8")
        for index in range(1, 7):
            path.with_name(f"{path.name}.{index}").write_text(f"backup-{index}", encoding="utf-8")

    assert realtime_sidecar_mod._rotate_realtime_sidecar_diag_if_needed(diag_path) is True
    assert realtime_sidecar_mod._rotate_realtime_sidecar_log_if_needed(log_path) is True

    for path in (diag_path, log_path):
        assert not path.exists()
        assert path.with_name(f"{path.name}.1").read_text(encoding="utf-8") == "active-too-large"
        assert path.with_name(f"{path.name}.5").read_text(encoding="utf-8") == "backup-4"
        assert not path.with_name(f"{path.name}.6").exists()


class _FakeRemoteWS:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.recv_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.closed = False
        self.transport = None

    async def recv(self) -> bytes:
        return await self.recv_queue.get()

    async def send(self, payload: bytes) -> None:
        self.sent.append(bytes(payload))

    async def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _FakeInfoRemoteWS(_FakeRemoteWS):
    def __init__(self) -> None:
        super().__init__()
        self.recv_queue.put_nowait(b'INFO {"server_id":"test","proto":1}\r\n')


class ConnectionClosedOK(Exception):
    pass


class _FakeNormalCloseRemoteWS(_FakeRemoteWS):
    async def recv(self) -> bytes:
        raise ConnectionClosedOK("normal close")


class _FakeAbnormalCloseRemoteWS(_FakeRemoteWS):
    async def recv(self) -> bytes:
        raise ConnectionResetError("remote connection reset")


class _FakeAuthRemoteWS(_FakeRemoteWS):
    def __init__(self) -> None:
        super().__init__()
        self.recv_queue.put_nowait(
            b'INFO {"server_id":"test","version":"2.10.29","proto":1,"auth_required":true,"max_payload":1048576}\r\n'
        )

    async def send(self, payload: bytes) -> None:
        await super().send(payload)
        if bytes(payload).startswith(b"CONNECT "):
            await self.recv_queue.put(b"-ERR 'Authorization Violation'\r\n")


class _FakeSocket:
    def __init__(self) -> None:
        self.sockopts: list[tuple[int, int, int]] = []
        self.keepalive_vals = None

    def setsockopt(self, level: int, optname: int, value: int) -> None:
        self.sockopts.append((level, optname, value))

    def ioctl(self, code, value) -> None:
        self.keepalive_vals = (code, value)


class _FakeTransport:
    def __init__(self, sock: _FakeSocket) -> None:
        self._sock = sock

    def get_extra_info(self, name: str):
        if name == "socket":
            return self._sock
        return None


class _FakeProcess:
    def __init__(self, cmdline: list[str]) -> None:
        self._cmdline = cmdline

    def cmdline(self) -> list[str]:
        return list(self._cmdline)


def test_realtime_sidecar_enabled_defaults_to_hub_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADAOS_REALTIME_ENABLE", raising=False)
    monkeypatch.delenv("HUB_REALTIME_ENABLE", raising=False)

    assert realtime_sidecar_enabled(role="hub", os_name="nt") is True
    assert realtime_sidecar_enabled(role="hub", os_name="posix") is True
    assert realtime_sidecar_enabled(role="member", os_name="nt") is False
    assert realtime_sidecar_enabled(role="root", os_name="nt") is False


def test_realtime_sidecar_paths_survive_deleted_runtime_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "base"
    monkeypatch.setenv("ADAOS_BASE_DIR", str(base_dir))
    monkeypatch.delenv("ADAOS_REALTIME_LOG", raising=False)
    monkeypatch.delenv("ADAOS_REALTIME_DIAG_FILE", raising=False)

    def missing_cwd() -> Path:
        raise FileNotFoundError("deleted cwd")

    monkeypatch.setattr(realtime_sidecar_mod.Path, "cwd", staticmethod(missing_cwd))

    assert realtime_sidecar_mod.realtime_sidecar_log_path() == base_dir / "diagnostics" / "realtime_sidecar.log"
    assert realtime_sidecar_mod.realtime_sidecar_diag_path() == base_dir / "diagnostics" / "realtime_sidecar.jsonl"


def test_realtime_sidecar_enabled_respects_explicit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")

    assert realtime_sidecar_enabled(role="hub", os_name="nt") is True


def test_realtime_sidecar_enabled_allows_explicit_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "0")

    assert realtime_sidecar_enabled(role="hub", os_name="nt") is False


def test_realtime_sidecar_enablement_policy_reports_default_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADAOS_REALTIME_ENABLE", raising=False)
    monkeypatch.delenv("HUB_REALTIME_ENABLE", raising=False)

    policy = realtime_sidecar_enablement_policy(role="hub")
    assert policy == {
        "role": "hub",
        "enabled": True,
        "default_enabled": True,
        "explicit": False,
        "source": "role_default",
        "env_var": None,
        "env_value": None,
        "reason": "hub runtimes use sidecar as the default realtime transport",
    }

    monkeypatch.setenv("HUB_REALTIME_ENABLE", "0")
    policy = realtime_sidecar_enablement_policy(role="hub")
    assert policy["enabled"] is False
    assert policy["default_enabled"] is True
    assert policy["explicit"] is True
    assert policy["source"] == "env_override"
    assert policy["env_var"] == "HUB_REALTIME_ENABLE"
    assert policy["env_value"] == "0"


def test_realtime_sidecar_process_detection_accepts_module_launch() -> None:
    assert realtime_sidecar_mod._process_looks_like_adaos_realtime(
        _FakeProcess(["python", "-m", "adaos.services.realtime_sidecar"])
    )
    assert realtime_sidecar_mod._process_looks_like_adaos_realtime(
        _FakeProcess(["adaos", "realtime", "serve", "--port", "7422"])
    )
    assert not realtime_sidecar_mod._process_looks_like_adaos_realtime(
        _FakeProcess(["python", "-m", "adaos.apps.api.server"])
    )


def test_realtime_sidecar_local_url_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_HOST", "127.0.0.7")
    monkeypatch.setenv("ADAOS_REALTIME_PORT", "9234")

    assert realtime_sidecar_local_url() == "nats://127.0.0.7:9234"


def test_realtime_sidecar_route_tunnel_contract_reflects_enabled_supervisor_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_ENABLED", "1")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8777")
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_WS_PORT", str(_free_port()))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_YWS_PORT", str(_free_port()))

    contract = realtime_sidecar_mod.realtime_sidecar_route_tunnel_contract()

    assert contract["current_support"] == "planned"
    assert contract["lifecycle_manager"] == "supervisor"
    assert contract["ownership_boundary"] == "transport_only"
    assert contract["ws"]["current_owner"] == "runtime"
    assert contract["ws"]["planned_owner"] == "sidecar"
    assert contract["ws"]["delegation_mode"] == "local_ws_proxy"
    assert contract["ws"]["listener"]["url"].endswith("/ws")
    assert contract["yws"]["planned_owner"] == "sidecar"
    assert contract["yws"]["handoff_ready"] is False
    assert contract["yws"]["listener"]["url"].endswith("/yws")


def test_realtime_sidecar_route_tunnel_discovers_runtime_from_persisted_supervisor_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "base"
    state_dir = base_dir / "state" / "supervisor"
    state_dir.mkdir(parents=True)
    (state_dir / "runtime.json").write_text(
        '{"runtime_url":"http://127.0.0.1:8788","desired_running":true,"managed_alive":true}',
        encoding="utf-8",
    )
    monkeypatch.setenv("ADAOS_BASE_DIR", str(base_dir))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_ENABLED", "1")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8777")

    def _unexpected_http(*args, **kwargs):
        raise AssertionError("route tunnel discovery should not require supervisor HTTP when state is available")

    monkeypatch.setattr(realtime_sidecar_mod, "urlopen", _unexpected_http)

    listeners = realtime_sidecar_mod.realtime_sidecar_route_tunnel_listeners()

    assert listeners["ws"]["upstream_port"] == 8788
    assert listeners["yws"]["upstream_url"] == "ws://127.0.0.1:8788/yws"


def test_realtime_sidecar_parent_snapshot_does_not_http_probe_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADAOS_REALTIME_CHILD", raising=False)
    monkeypatch.setattr(realtime_sidecar_mod, "_route_tunnel_supervisor_state_endpoint", lambda: None)
    monkeypatch.setattr(
        realtime_sidecar_mod,
        "_route_tunnel_supervisor_http_endpoint",
        lambda: (_ for _ in ()).throw(AssertionError("parent status snapshot must not call supervisor HTTP")),
    )

    assert realtime_sidecar_mod._route_tunnel_supervisor_runtime_endpoint() is None


def test_realtime_sidecar_child_can_fallback_to_supervisor_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_CHILD", "1")
    monkeypatch.setattr(realtime_sidecar_mod, "_route_tunnel_supervisor_state_endpoint", lambda: None)
    monkeypatch.setattr(realtime_sidecar_mod, "_route_tunnel_supervisor_http_endpoint", lambda: ("127.0.0.1", 8778))

    assert realtime_sidecar_mod._route_tunnel_supervisor_runtime_endpoint() == ("127.0.0.1", 8778)


def test_realtime_sidecar_route_tunnel_refreshes_upstream_after_slot_switch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "base"
    state_dir = base_dir / "state" / "supervisor"
    state_dir.mkdir(parents=True)
    runtime_state = state_dir / "runtime.json"
    runtime_state.write_text(
        '{"runtime_url":"http://127.0.0.1:8777","desired_running":true,"managed_alive":true}',
        encoding="utf-8",
    )
    monkeypatch.setenv("ADAOS_BASE_DIR", str(base_dir))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_ENABLED", "1")
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_WS_PORT", str(_free_port()))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_YWS_PORT", str(_free_port()))

    realtime_sidecar_mod._reset_route_tunnel_runtime_state()
    assert realtime_sidecar_mod.realtime_sidecar_route_tunnel_listeners()["ws"]["upstream_port"] == 8777

    runtime_state.write_text(
        '{"runtime_url":"http://127.0.0.1:8778","desired_running":true,"managed_alive":true}',
        encoding="utf-8",
    )

    listeners = realtime_sidecar_mod.realtime_sidecar_route_tunnel_listeners()

    assert listeners["ws"]["upstream_port"] == 8778
    assert listeners["ws"]["upstream_url"] == "ws://127.0.0.1:8778/ws"
    assert listeners["yws"]["upstream_url"] == "ws://127.0.0.1:8778/yws"


def test_realtime_sidecar_listener_snapshot_includes_route_tunnel_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_ENABLED", "1")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8777")
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_WS_PORT", str(_free_port()))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_YWS_PORT", str(_free_port()))
    monkeypatch.setattr(realtime_sidecar_mod, "_find_realtime_listener_pid", lambda host, port: 7422)

    snapshot = realtime_sidecar_mod.realtime_sidecar_listener_snapshot()

    assert snapshot["listener_running"] is True
    assert snapshot["listener_pid"] == 7422
    assert snapshot["enablement_policy"]["enabled"] is True
    assert snapshot["enablement_policy"]["source"] == "env_override"
    assert snapshot["route_tunnel_contract"]["current_support"] == "planned"
    assert snapshot["route_tunnel_contract"]["ws"]["planned_owner"] == "sidecar"
    assert snapshot["route_tunnel_contract"]["yws"]["delegation_mode"] == "local_ws_proxy"
    assert snapshot["media_proxy_contract"]["current_support"] == "disabled"
    assert snapshot["media_proxy_contract"]["planned_owner"] == "sidecar"


def test_realtime_sidecar_media_proxy_contract_reflects_explicit_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_port = _free_port()
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_MEDIA_PROXY_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_MEDIA_PROXY_HOST", "0.0.0.0")
    monkeypatch.setenv("ADAOS_REALTIME_MEDIA_PROXY_PORT", str(media_port))
    monkeypatch.setenv("ADAOS_REALTIME_MEDIA_PUBLIC_BASES", f"http://192.0.2.10:{media_port}")

    contract = realtime_sidecar_mod.realtime_sidecar_media_proxy_contract()

    assert contract["current_support"] == "planned"
    assert contract["ownership_boundary"] == "media_content_read_only"
    assert contract["delegation_mode"] == "local_http_media_proxy"
    assert contract["listener"]["host"] == "0.0.0.0"
    assert contract["listener"]["port"] == media_port
    assert contract["public_bases"] == [f"http://192.0.2.10:{media_port}"]
    assert "/media/media-indexer/content/{playback_id}" in contract["route_paths"]
    assert contract["range_requests"] is True


def test_realtime_sidecar_media_proxy_resolves_runtime_media_without_agent_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "base"
    files_dir = base_dir / "workspace" / "skills" / ".runtime" / "mediaserver" / "v0.8" / "data" / "files"
    files_dir.mkdir(parents=True)
    media_file = files_dir / "frame.jpg"
    media_file.write_bytes(b"frame")
    current_version = base_dir / "workspace" / "skills" / ".runtime" / "mediaserver" / "current_version"
    current_version.write_text("0.8.0", encoding="utf-8")
    monkeypatch.setenv("ADAOS_BASE_DIR", str(base_dir))

    def _missing_ctx(_filename: str) -> Path:
        raise RuntimeError("AgentContext is not initialized. Call set_ctx(...) during app bootstrap.")

    monkeypatch.setattr("adaos.services.media_library.media_file_path", _missing_ctx)

    assert realtime_sidecar_mod._media_proxy_file_path("frame.jpg") == media_file


def test_realtime_sidecar_media_proxy_serves_token_protected_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media_port = _free_port()
    media_file = tmp_path / "frame.jpg"
    media_file.write_bytes(b"abcdef")
    monkeypatch.setenv("ADAOS_TOKEN", "dev-token")
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_MEDIA_PROXY_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_MEDIA_PROXY_HOST", "127.0.0.1")
    monkeypatch.setenv("ADAOS_REALTIME_MEDIA_PROXY_PORT", str(media_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_WS_PORT", str(_free_port()))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_YWS_PORT", str(_free_port()))
    def _media_proxy_path(filename: str) -> Path:
        if filename == "song.mp3":
            raise ValueError("unsupported_extension:.mp3")
        return media_file

    monkeypatch.setattr(realtime_sidecar_mod, "_media_proxy_file_path", _media_proxy_path)
    monkeypatch.setattr(realtime_sidecar_mod, "_media_proxy_guess_media_type", lambda filename: "image/jpeg")
    monkeypatch.setattr(
        "adaos.services.media_indexer_library.resolve_media_indexer_content",
        lambda playback_id: (media_file, {"mime_type": "audio/mpeg"}),
    )
    monkeypatch.setattr(
        "adaos.services.media_indexer_library.resolve_media_indexer_content_by_name",
        lambda filename: (media_file, {"mime_type": "audio/mpeg"}),
    )

    async def _request(raw: bytes) -> bytes:
        reader, writer = await asyncio.open_connection("127.0.0.1", media_port)
        writer.write(raw)
        await writer.drain()
        chunks: list[bytes] = []
        while True:
            chunk = await reader.read(1024)
            if not chunk:
                break
            chunks.append(chunk)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return b"".join(chunks)

    async def _run() -> None:
        server = RealtimeSidecarServer(host="127.0.0.1", port=0)
        await server.start()
        try:
            contract = realtime_sidecar_mod.realtime_sidecar_media_proxy_contract()
            assert contract["current_support"] == "ready"
            assert contract["listener_ready"] is True

            ok = await _request(
                b"GET /api/node/media/files/content/frame.jpg?token=dev-token HTTP/1.1\r\nHost: local\r\n\r\n"
            )
            assert b"HTTP/1.1 200 OK" in ok
            assert b"Content-Type: image/jpeg" in ok
            assert ok.endswith(b"abcdef")

            ranged = await _request(
                b"GET /media/files/content/frame.jpg?token=dev-token HTTP/1.1\r\n"
                b"Host: local\r\nRange: bytes=1-3\r\n\r\n"
            )
            assert b"HTTP/1.1 206 Partial Content" in ranged
            assert b"Content-Range: bytes 1-3/6" in ranged
            assert ranged.endswith(b"bcd")

            indexer_media = await _request(
                b"GET /media/media-indexer/content/abc123?token=dev-token HTTP/1.1\r\n"
                b"Host: local\r\nRange: bytes=2-5\r\n\r\n"
            )
            assert b"HTTP/1.1 206 Partial Content" in indexer_media
            assert b"Content-Type: audio/mpeg" in indexer_media
            assert b"Content-Range: bytes 2-5/6" in indexer_media
            assert indexer_media.endswith(b"cdef")

            indexer_compat = await _request(
                b"GET /media/files/content/song.mp3?token=dev-token HTTP/1.1\r\n"
                b"Host: local\r\nRange: bytes=0-2\r\n\r\n"
            )
            assert b"HTTP/1.1 206 Partial Content" in indexer_compat
            assert b"Content-Type: audio/mpeg" in indexer_compat
            assert indexer_compat.endswith(b"abc")

            denied = await _request(
                b"GET /media/files/content/frame.jpg?token=bad HTTP/1.1\r\nHost: local\r\n\r\n"
            )
            assert b"HTTP/1.1 401 Unauthorized" in denied
        finally:
            await server.close()

    asyncio.run(_run())


def test_realtime_sidecar_route_tunnel_contract_marks_local_websocket_handoffs_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_ENABLED", "1")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8777")
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_WS_PORT", str(_free_port()))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_YWS_PORT", str(_free_port()))

    async def _run() -> None:
        server = RealtimeSidecarServer(host="127.0.0.1", port=0)
        await server.start()
        try:
            contract = realtime_sidecar_mod.realtime_sidecar_route_tunnel_contract()

            assert contract["current_support"] == "ready"
            assert contract["ws"]["listener_ready"] is True
            assert contract["yws"]["listener_ready"] is True
            assert contract["ws"]["current_owner"] == "sidecar"
            assert contract["ws"]["handoff_ready"] is True
            assert contract["ws"]["blockers"] == []
            assert contract["yws"]["current_owner"] == "sidecar"
            assert contract["yws"]["handoff_ready"] is True
            assert contract["yws"]["blockers"] == []
            assert contract["ws"]["listener"]["url"].endswith("/ws")
            assert contract["yws"]["listener"]["url"].endswith("/yws")
        finally:
            await server.close()

    asyncio.run(_run())


def test_realtime_sidecar_route_tunnel_ws_bases_return_path_specific_listener_when_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8777")
    ws_proxy_port = _free_port()
    yws_proxy_port = _free_port()
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_WS_PORT", str(ws_proxy_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_YWS_PORT", str(yws_proxy_port))

    async def _run() -> None:
        server = RealtimeSidecarServer(host="127.0.0.1", port=0)
        await server.start()
        try:
            assert realtime_sidecar_mod.realtime_sidecar_route_tunnel_ws_bases(path="/ws?token=x") == [
                f"ws://127.0.0.1:{ws_proxy_port}"
            ]
            assert realtime_sidecar_mod.realtime_sidecar_route_tunnel_ws_bases(path="/yws?token=x") == [
                f"ws://127.0.0.1:{yws_proxy_port}"
            ]
            assert realtime_sidecar_mod.realtime_sidecar_route_tunnel_ws_bases(path="/other") == []
        finally:
            await server.close()

    asyncio.run(_run())


def test_realtime_sidecar_route_proxy_relays_local_websocket_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_port = _free_port()
    ws_proxy_port = _free_port()
    yws_proxy_port = _free_port()
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", str(runtime_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_WS_PORT", str(ws_proxy_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_YWS_PORT", str(yws_proxy_port))

    async def _run() -> None:
        websockets = pytest.importorskip("websockets")

        async def _echo(websocket, _path=None):
            async for message in websocket:
                await websocket.send(message)

        upstream = await websockets.serve(_echo, "127.0.0.1", runtime_port, max_size=None, compression=None)
        server = RealtimeSidecarServer(host="127.0.0.1", port=0)
        await server.start()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{ws_proxy_port}/ws?token=dev", max_size=None) as client:
                await client.send("hello-through-sidecar")
                echoed = await asyncio.wait_for(client.recv(), timeout=1.0)
                assert echoed == "hello-through-sidecar"
        finally:
            await server.close()
            upstream.close()
            await upstream.wait_closed()

    asyncio.run(_run())


def test_realtime_sidecar_yws_route_proxy_accepts_room_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_port = _free_port()
    ws_proxy_port = _free_port()
    yws_proxy_port = _free_port()
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", str(runtime_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_WS_PORT", str(ws_proxy_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_YWS_PORT", str(yws_proxy_port))

    async def _run() -> None:
        websockets = pytest.importorskip("websockets")
        upstream_paths: list[str] = []

        async def _echo(websocket, _path=None):
            request = getattr(websocket, "request", None)
            upstream_paths.append(
                str(getattr(websocket, "path", None) or getattr(request, "path", None) or _path or "")
            )
            async for message in websocket:
                await websocket.send(message)

        upstream = await websockets.serve(_echo, "127.0.0.1", runtime_port, max_size=None, compression=None)
        server = RealtimeSidecarServer(host="127.0.0.1", port=0)
        await server.start()
        try:
            async with websockets.connect(
                f"ws://127.0.0.1:{yws_proxy_port}/yws/default?token=dev",
                max_size=None,
            ) as client:
                await client.send(b"hello-yws-room")
                echoed = await asyncio.wait_for(client.recv(), timeout=1.0)
                assert echoed == b"hello-yws-room"
            assert upstream_paths == ["/yws/default?token=dev"]
        finally:
            await server.close()
            upstream.close()
            await upstream.wait_closed()

    asyncio.run(_run())


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("ws", "/ws?token=dev"),
        ("yws", "/yws/default?token=dev"),
    ],
)
def test_realtime_sidecar_route_proxy_keeps_browser_socket_across_upstream_restart(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    path: str,
) -> None:
    runtime_port = _free_port()
    ws_proxy_port = _free_port()
    yws_proxy_port = _free_port()
    proxy_port = ws_proxy_port if kind == "ws" else yws_proxy_port
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", str(runtime_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_WS_PORT", str(ws_proxy_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_YWS_PORT", str(yws_proxy_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_RECONNECT_DELAY_S", "0.05")

    async def _run() -> None:
        websockets = pytest.importorskip("websockets")
        first_seen = asyncio.Event()

        async def _close_after_first(websocket, _path=None):
            message = await websocket.recv()
            first_seen.set()
            await websocket.send(f"first:{message}")
            await websocket.close()

        async def _echo(websocket, _path=None):
            async for message in websocket:
                await websocket.send(f"second:{message}")

        upstream = await websockets.serve(
            _close_after_first,
            "127.0.0.1",
            runtime_port,
            max_size=None,
            compression=None,
        )
        server = RealtimeSidecarServer(host="127.0.0.1", port=0)
        await server.start()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{proxy_port}{path}", max_size=None) as client:
                await client.send("subscribe:node.status")
                assert await asyncio.wait_for(client.recv(), timeout=1.0) == "first:subscribe:node.status"
                await asyncio.wait_for(first_seen.wait(), timeout=1.0)
                upstream.close()
                await upstream.wait_closed()
                upstream = await websockets.serve(_echo, "127.0.0.1", runtime_port, max_size=None, compression=None)
                replayed = await asyncio.wait_for(client.recv(), timeout=2.0)
                assert replayed == "second:subscribe:node.status"
                await client.send("after-restart")
                assert await asyncio.wait_for(client.recv(), timeout=1.0) == "second:after-restart"
        finally:
            await server.close()
            upstream.close()
            await upstream.wait_closed()

    asyncio.run(_run())


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("ws", "/ws?token=dev"),
        ("yws", "/yws/default?token=dev"),
    ],
)
def test_realtime_sidecar_route_proxy_rediscovers_active_runtime_port(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    path: str,
) -> None:
    old_runtime_port = _free_port()
    new_runtime_port = _free_port()
    current_runtime_port = old_runtime_port
    ws_proxy_port = _free_port()
    yws_proxy_port = _free_port()
    proxy_port = ws_proxy_port if kind == "ws" else yws_proxy_port
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", str(old_runtime_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_WS_PORT", str(ws_proxy_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_YWS_PORT", str(yws_proxy_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_RECONNECT_DELAY_S", "0.05")
    monkeypatch.setattr(
        realtime_sidecar_mod,
        "_route_tunnel_supervisor_runtime_endpoint",
        lambda: ("127.0.0.1", current_runtime_port),
    )

    async def _run() -> None:
        nonlocal current_runtime_port
        websockets = pytest.importorskip("websockets")

        async def _old_runtime(websocket, _path=None):
            message = await websocket.recv()
            await websocket.send(f"old:{message}")
            await websocket.close()

        async def _new_runtime(websocket, _path=None):
            async for message in websocket:
                await websocket.send(f"new:{message}")

        old_upstream = await websockets.serve(
            _old_runtime,
            "127.0.0.1",
            old_runtime_port,
            max_size=None,
            compression=None,
        )
        server = RealtimeSidecarServer(host="127.0.0.1", port=0)
        new_upstream = None
        await server.start()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{proxy_port}{path}", max_size=None) as client:
                await client.send("subscribe")
                assert await asyncio.wait_for(client.recv(), timeout=1.0) == "old:subscribe"
                old_upstream.close()
                await old_upstream.wait_closed()
                current_runtime_port = new_runtime_port
                new_upstream = await websockets.serve(
                    _new_runtime,
                    "127.0.0.1",
                    new_runtime_port,
                    max_size=None,
                    compression=None,
                )
                assert await asyncio.wait_for(client.recv(), timeout=2.0) == "new:subscribe"
                await client.send("after-ab")
                assert await asyncio.wait_for(client.recv(), timeout=1.0) == "new:after-ab"
        finally:
            await server.close()
            old_upstream.close()
            await old_upstream.wait_closed()
            if new_upstream is not None:
                new_upstream.close()
                await new_upstream.wait_closed()

    asyncio.run(_run())


def test_realtime_sidecar_loop_defaults_to_proactor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADAOS_REALTIME_WIN_LOOP", raising=False)

    assert realtime_sidecar_mod._sidecar_loop_mode() == "proactor"


def test_realtime_sidecar_ws_heartbeat_defaults_to_transport_keepalive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADAOS_REALTIME_WS_HEARTBEAT_S", raising=False)

    assert realtime_sidecar_mod._realtime_ws_heartbeat_s() == 20.0


@pytest.mark.asyncio
async def test_probe_realtime_sidecar_ready_accepts_open_listener() -> None:
    async def _handle(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    try:
        sock = server.sockets[0].getsockname()
        assert await realtime_sidecar_mod.probe_realtime_sidecar_ready(host=sock[0], port=sock[1], timeout_s=1.0)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_probe_realtime_sidecar_ready_rejects_closed_port() -> None:
    port = _free_port()

    assert not await realtime_sidecar_mod.probe_realtime_sidecar_ready(host="127.0.0.1", port=port, timeout_s=0.2)


@pytest.mark.asyncio
async def test_realtime_sidecar_probe_does_not_supersede_active_local_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    fake_ws = _FakeRemoteWS()

    async def _fake_connect(*args, **kwargs):
        return fake_ws

    import websockets  # type: ignore

    monkeypatch.setattr(websockets, "connect", _fake_connect)
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "diag.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", "wss://example.invalid/nats")
    monkeypatch.setenv("ADAOS_REALTIME_PROBE_GRACE_S", "0.05")

    server = RealtimeSidecarServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection(server.listen_host, server.listen_port)
        writer.write(b"PING\r\n")
        await writer.drain()
        await asyncio.sleep(0.05)
        assert fake_ws.sent == [b"PING\r\n"]

        assert await realtime_sidecar_mod.probe_realtime_sidecar_ready(
            host=server.listen_host,
            port=server.listen_port,
            timeout_s=1.0,
        )
        await asyncio.sleep(0.1)

        writer.write(b"PING\r\n")
        await writer.drain()
        await asyncio.sleep(0.05)

        assert fake_ws.sent == [b"PING\r\n", b"PING\r\n"]
        assert server._stats.superseded_total == 0
        writer.close()
        await writer.wait_closed()
        with contextlib.suppress(Exception):
            await reader.read()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_realtime_sidecar_keeps_two_local_nats_sessions_during_runtime_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    remote_sessions: list[_FakeInfoRemoteWS] = []

    async def _fake_connect(*args, **kwargs):
        session = _FakeInfoRemoteWS()
        remote_sessions.append(session)
        return session

    import websockets  # type: ignore

    monkeypatch.setattr(websockets, "connect", _fake_connect)
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "diag.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", "wss://example.invalid/nats")
    monkeypatch.setenv("ADAOS_REALTIME_PROBE_GRACE_S", "0.02")

    server = RealtimeSidecarServer(host="127.0.0.1", port=0)
    await server.start()
    first_reader = first_writer = second_reader = second_writer = None
    try:
        first_reader, first_writer = await asyncio.open_connection(server.listen_host, server.listen_port)
        assert await asyncio.wait_for(first_reader.readuntil(b"\r\n"), timeout=1.0) == (
            b'INFO {"server_id":"test","proto":1}\r\n'
        )

        second_reader, second_writer = await asyncio.open_connection(server.listen_host, server.listen_port)
        assert await asyncio.wait_for(second_reader.readuntil(b"\r\n"), timeout=1.0) == (
            b'INFO {"server_id":"test","proto":1}\r\n'
        )

        first_writer.write(b"PING\r\n")
        second_writer.write(b"PONG\r\n")
        await asyncio.gather(first_writer.drain(), second_writer.drain())
        for _ in range(50):
            if len(remote_sessions) == 2 and remote_sessions[0].sent and remote_sessions[1].sent:
                break
            await asyncio.sleep(0.01)

        assert len(remote_sessions) == 2
        assert remote_sessions[0].sent == [b"PING\r\n"]
        assert remote_sessions[1].sent == [b"PONG\r\n"]
        assert remote_sessions[0].closed is False
        assert server._stats.superseded_total == 0
        assert server._stats.overlap_admitted_total == 1
        assert len(server._live_session_tasks()) == 2
    finally:
        for writer in (first_writer, second_writer):
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        await server.close()


@pytest.mark.asyncio
async def test_realtime_sidecar_probe_does_not_break_immediate_nats_connect(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    nats = pytest.importorskip("nats")
    if not hasattr(nats, "aio"):
        pytest.skip("nats-py aio client is not available in this environment")
    import websockets  # type: ignore

    async def _fake_connect(*args, **kwargs):
        return _FakeAuthRemoteWS()

    monkeypatch.setattr(websockets, "connect", _fake_connect)
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "diag.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", "wss://example.invalid/nats")

    server = RealtimeSidecarServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        assert await realtime_sidecar_mod.probe_realtime_sidecar_ready(
            host=server.listen_host,
            port=server.listen_port,
            timeout_s=1.0,
        )

        nc = nats.aio.client.Client()
        try:
            with pytest.raises(nats.errors.Error, match="Authorization Violation"):
                await asyncio.wait_for(
                    nc.connect(
                        servers=[f"nats://{server.listen_host}:{server.listen_port}"],
                        user="hub_test",
                        password="bad",
                        allow_reconnect=False,
                        connect_timeout=1.0,
                        ping_interval=3600,
                        max_outstanding_pings=10,
                    ),
                    timeout=2.0,
                )
        finally:
            await nc.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_realtime_sidecar_relays_bytes_between_local_nats_and_remote_ws(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    fake_ws = _FakeRemoteWS()

    async def _fake_connect(*args, **kwargs):
        return fake_ws

    import websockets  # type: ignore

    monkeypatch.setattr(websockets, "connect", _fake_connect)
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "diag.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", "wss://example.invalid/nats")

    server = RealtimeSidecarServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection(server.listen_host, server.listen_port)
        writer.write(b"PING\r\n")
        await writer.drain()
        await asyncio.sleep(0.05)

        assert fake_ws.sent == [b"PING\r\n"]

        await fake_ws.recv_queue.put(b"INFO {}\r\n")
        data = await asyncio.wait_for(reader.readexactly(len(b"INFO {}\r\n")), timeout=1.0)

        assert data == b"INFO {}\r\n"

        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_realtime_sidecar_retries_remote_connect_without_dropping_local_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    fake_ws = _FakeRemoteWS()
    attempts = 0

    async def _fake_connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary dns failure")
        return fake_ws

    import websockets  # type: ignore

    monkeypatch.setattr(websockets, "connect", _fake_connect)
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "diag.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", "wss://example.invalid/nats")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_CONNECT_RETRY_INITIAL_S", "0.05")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_CONNECT_RETRY_MAX_S", "0.05")

    server = RealtimeSidecarServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        _reader, writer = await asyncio.open_connection(server.listen_host, server.listen_port)
        writer.write(b"PING\r\n")
        await writer.drain()

        for _ in range(40):
            if fake_ws.sent:
                break
            await asyncio.sleep(0.01)

        assert attempts >= 2
        assert fake_ws.sent == [b"PING\r\n"]
        assert server._stats.remote_connect_fail_total == 1
        assert server._stats.remote_connect_retry_total >= 1
        assert server._stats.remote_connect_retrying is False
        assert server._stats.last_error is None

        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_realtime_sidecar_treats_normal_remote_ws_close_as_session_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    fake_ws = _FakeNormalCloseRemoteWS()

    async def _fake_connect(*args, **kwargs):
        return fake_ws

    import websockets  # type: ignore

    monkeypatch.setattr(websockets, "connect", _fake_connect)
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "diag.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", "wss://example.invalid/nats")

    server = RealtimeSidecarServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection(server.listen_host, server.listen_port)
        await asyncio.wait_for(reader.read(), timeout=1.0)

        assert fake_ws.closed is True
        assert server._stats.session_close_total == 1
        assert server._stats.last_error is None

        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_realtime_sidecar_closes_broken_local_session_and_accepts_reconnect(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    broken_ws = _FakeAbnormalCloseRemoteWS()
    recovered_ws = _FakeRemoteWS()
    remote_sessions = [broken_ws, recovered_ws]
    attempts = 0

    async def _fake_connect(*args, **kwargs):
        nonlocal attempts
        session = remote_sessions[min(attempts, len(remote_sessions) - 1)]
        attempts += 1
        return session

    import websockets  # type: ignore

    monkeypatch.setattr(websockets, "connect", _fake_connect)
    monkeypatch.setattr(realtime_sidecar_mod, "_realtime_remote_quarantine_until", {})
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "diag.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", "wss://example.invalid/nats")

    server = RealtimeSidecarServer(host="127.0.0.1", port=0)
    await server.start()
    first_writer = second_writer = None
    try:
        first_reader, first_writer = await asyncio.open_connection(server.listen_host, server.listen_port)
        assert await asyncio.wait_for(first_reader.read(), timeout=1.0) == b""

        for _ in range(50):
            if not server._live_session_tasks():
                break
            await asyncio.sleep(0.01)
        assert not server._live_session_tasks()

        _second_reader, second_writer = await asyncio.open_connection(server.listen_host, server.listen_port)
        second_writer.write(b"PING\r\n")
        await second_writer.drain()
        for _ in range(50):
            if recovered_ws.sent:
                break
            await asyncio.sleep(0.01)

        assert attempts == 2
        assert broken_ws.closed is True
        assert recovered_ws.sent == [b"PING\r\n"]
        assert server._stats.session_open_total == 2
        assert server._stats.remote_quarantine_total == 1
        assert server._stats.active_session is True
    finally:
        for writer in (first_writer, second_writer):
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        await server.close()


@pytest.mark.asyncio
async def test_realtime_sidecar_remote_connect_uses_ws_ping_and_tcp_keepalive(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    recorded: dict[str, object] = {}
    fake_ws = _FakeRemoteWS()
    fake_sock = _FakeSocket()
    fake_ws.transport = _FakeTransport(fake_sock)

    async def _fake_connect(*args, **kwargs):
        recorded["args"] = args
        recorded["kwargs"] = dict(kwargs)
        return fake_ws

    import websockets  # type: ignore

    monkeypatch.setattr(websockets, "connect", _fake_connect)
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "diag.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", "wss://example.invalid/nats")
    monkeypatch.setenv("ADAOS_REALTIME_WS_HEARTBEAT_S", "20")

    server = RealtimeSidecarServer(host="127.0.0.1", port=0)
    ws, target = await server._connect_remote(session_id="rt-test")
    try:
        assert ws is fake_ws
        assert target.startswith("wss://example.invalid/nats")
        kwargs = dict(recorded["kwargs"])
        assert kwargs["ping_interval"] == 20.0
        assert kwargs["ping_timeout"] is None
        assert kwargs["subprotocols"] == ["nats"]
        assert kwargs["compression"] is None
        assert any(opt[1] == socket.SO_KEEPALIVE for opt in fake_sock.sockopts)
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_realtime_sidecar_remote_connect_uses_own_default_instead_of_global_ws_heartbeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    recorded: dict[str, object] = {}
    fake_ws = _FakeRemoteWS()

    async def _fake_connect(*args, **kwargs):
        recorded["kwargs"] = dict(kwargs)
        return fake_ws

    import websockets  # type: ignore

    monkeypatch.setattr(websockets, "connect", _fake_connect)
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "diag.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", "wss://example.invalid/nats")
    monkeypatch.setenv("HUB_NATS_WS_HEARTBEAT_S", "37")
    monkeypatch.delenv("ADAOS_REALTIME_WS_HEARTBEAT_S", raising=False)

    server = RealtimeSidecarServer(host="127.0.0.1", port=0)
    ws, _target = await server._connect_remote(session_id="rt-test")
    try:
        kwargs = dict(recorded["kwargs"])
        assert kwargs["ping_interval"] == 20.0
        assert kwargs["ping_timeout"] is None
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_realtime_sidecar_remote_connect_allows_disabling_sidecar_ws_heartbeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    recorded: dict[str, object] = {}
    fake_ws = _FakeRemoteWS()

    async def _fake_connect(*args, **kwargs):
        recorded["kwargs"] = dict(kwargs)
        return fake_ws

    import websockets  # type: ignore

    monkeypatch.setattr(websockets, "connect", _fake_connect)
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "diag.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", "wss://example.invalid/nats")
    monkeypatch.setenv("ADAOS_REALTIME_WS_HEARTBEAT_S", "0")

    server = RealtimeSidecarServer(host="127.0.0.1", port=0)
    ws, _target = await server._connect_remote(session_id="rt-test")
    try:
        kwargs = dict(recorded["kwargs"])
        assert kwargs["ping_interval"] is None
        assert kwargs["ping_timeout"] is None
    finally:
        await ws.close()


def test_realtime_sidecar_prefers_api_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUB_NATS_PREFER_DEDICATED", raising=False)
    monkeypatch.delenv("ADAOS_REALTIME_PREFER_DEDICATED", raising=False)
    monkeypatch.delenv("ADAOS_REALTIME_ALLOW_API_FALLBACK", raising=False)
    monkeypatch.delenv("ADAOS_REALTIME_REMOTE_WS_URL", raising=False)

    ordered = realtime_sidecar_mod.resolve_realtime_remote_candidates()

    assert ordered == ["wss://api.inimatic.com/nats"]


def test_realtime_sidecar_does_not_inherit_hub_prefer_dedicated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_NATS_PREFER_DEDICATED", "0")
    monkeypatch.delenv("ADAOS_REALTIME_PREFER_DEDICATED", raising=False)
    monkeypatch.delenv("ADAOS_REALTIME_ALLOW_API_FALLBACK", raising=False)
    monkeypatch.delenv("ADAOS_REALTIME_REMOTE_WS_URL", raising=False)

    ordered = realtime_sidecar_mod.resolve_realtime_remote_candidates()

    assert ordered == ["wss://api.inimatic.com/nats"]


def test_realtime_sidecar_keeps_api_ingress_when_no_custom_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUB_NATS_PREFER_DEDICATED", raising=False)
    monkeypatch.delenv("ADAOS_REALTIME_PREFER_DEDICATED", raising=False)
    monkeypatch.setenv("ADAOS_REALTIME_ALLOW_API_FALLBACK", "0")
    monkeypatch.delenv("ADAOS_REALTIME_REMOTE_WS_URL", raising=False)

    ordered = realtime_sidecar_mod.resolve_realtime_remote_candidates()

    assert ordered == ["wss://api.inimatic.com/nats"]


def test_realtime_sidecar_can_explicitly_prefer_dedicated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_NATS_PREFER_DEDICATED", "0")
    monkeypatch.setenv("ADAOS_REALTIME_PREFER_DEDICATED", "1")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_ALT", "wss://nats.inimatic.com/nats")
    monkeypatch.delenv("ADAOS_REALTIME_ALLOW_API_FALLBACK", raising=False)
    monkeypatch.delenv("ADAOS_REALTIME_REMOTE_WS_URL", raising=False)

    ordered = realtime_sidecar_mod.resolve_realtime_remote_candidates()

    assert ordered == ["wss://nats.inimatic.com/nats", "wss://api.inimatic.com/nats"]


def test_realtime_sidecar_uses_ws_fallback_for_direct_tcp_node_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADAOS_REALTIME_REMOTE_WS_URL", raising=False)
    monkeypatch.delenv("ADAOS_REALTIME_ALLOW_TCP_FALLBACK", raising=False)
    monkeypatch.setattr(
        realtime_sidecar_mod,
        "_load_node_yaml",
        lambda: {"nats": {"ws_url": "nats://nats.inimatic.com:4222"}},
    )

    ordered = realtime_sidecar_mod.resolve_realtime_remote_candidates()

    assert ordered == ["wss://api.inimatic.com/nats"]


def test_realtime_sidecar_can_append_tcp_fallback_for_direct_tcp_node_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADAOS_REALTIME_REMOTE_WS_URL", raising=False)
    monkeypatch.setenv("ADAOS_REALTIME_ALLOW_TCP_FALLBACK", "1")
    monkeypatch.setattr(
        realtime_sidecar_mod,
        "_load_node_yaml",
        lambda: {"nats": {"ws_url": "nats://nats.inimatic.com:4222"}},
    )

    ordered = realtime_sidecar_mod.resolve_realtime_remote_candidates()

    assert ordered == [
        "wss://api.inimatic.com/nats",
        "nats://nats.inimatic.com:4222",
    ]


def test_realtime_sidecar_respects_explicit_public_ws_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", "wss://api.inimatic.com/nats")
    monkeypatch.delenv("ADAOS_REALTIME_REMOTE_WS_ALT", raising=False)
    monkeypatch.delenv("ADAOS_REALTIME_ALLOW_API_FALLBACK", raising=False)

    ordered = realtime_sidecar_mod.resolve_realtime_remote_candidates()

    assert ordered == ["wss://api.inimatic.com/nats"]


@pytest.mark.asyncio
async def test_realtime_sidecar_subprocess_forces_dedicated_direct_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    popen_env: dict[str, str] = {}
    popen_args: list[str] = []
    popen_creationflags = 0

    class _FakeProc:
        def poll(self):
            return None

        def terminate(self) -> None:
            return None

    async def _fake_is_port_open(_host: str, _port: int) -> bool:
        return False

    async def _fake_wait_bound(*, host: str, port: int, timeout_s: float = 10.0) -> bool:
        return True

    async def _unexpected_probe(**kwargs) -> bool:
        raise AssertionError("subprocess startup must not open the NATS listener as a health probe")

    def _fake_popen(*args, **kwargs):
        nonlocal popen_args, popen_creationflags, popen_env
        popen_args = list(args[0])
        popen_env = dict(kwargs["env"])
        popen_creationflags = int(kwargs.get("creationflags") or 0)
        return _FakeProc()

    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_BASE_DIR", "")
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setattr(realtime_sidecar_mod, "_is_port_open", _fake_is_port_open)
    monkeypatch.setattr(realtime_sidecar_mod, "wait_realtime_sidecar_bound", _fake_wait_bound)
    monkeypatch.setattr(realtime_sidecar_mod, "probe_realtime_sidecar_ready", _unexpected_probe)
    monkeypatch.setattr(realtime_sidecar_mod.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(realtime_sidecar_mod, "current_base_dir", lambda: tmp_path / "base")

    proc = await realtime_sidecar_mod.start_realtime_sidecar_subprocess(role="hub")

    assert proc is not None
    assert popen_args[:3] == [realtime_sidecar_mod.sys.executable, "-m", "adaos.services.realtime_sidecar"]
    assert "realtime" not in popen_args[:5]
    assert popen_env["ADAOS_REALTIME_PREFER_DEDICATED"] == "0"
    assert popen_env["ADAOS_REALTIME_ALLOW_API_FALLBACK"] == "0"
    assert popen_env["ADAOS_REALTIME_WIN_LOOP"] == "proactor"
    assert popen_env["ADAOS_BASE_DIR"] == str(tmp_path / "base")
    if realtime_sidecar_mod.os.name == "nt":
        assert popen_creationflags & int(realtime_sidecar_mod.subprocess.CREATE_NEW_PROCESS_GROUP)


@pytest.mark.asyncio
async def test_realtime_sidecar_subprocess_requests_graceful_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[object] = []

    class _FakeProc:
        exit_code = None
        terminated = False
        killed = False

        def poll(self):
            return self.exit_code

        def send_signal(self, signum) -> None:
            sent.append(signum)
            self.exit_code = 0

        def terminate(self) -> None:
            self.terminated = True
            self.exit_code = 1

        def kill(self) -> None:
            self.killed = True
            self.exit_code = 2

    proc = _FakeProc()
    graceful_signal = object()
    async def _request_control_shutdown(*, timeout_s: float = 2.0) -> bool:
        proc.exit_code = 0
        return True

    monkeypatch.setattr(
        realtime_sidecar_mod,
        "_request_realtime_sidecar_graceful_shutdown",
        _request_control_shutdown,
    )
    monkeypatch.setattr(realtime_sidecar_mod, "_realtime_sidecar_shutdown_signal", lambda: graceful_signal)
    monkeypatch.setattr(realtime_sidecar_mod, "_find_realtime_listener_pid", lambda _host, _port: None)

    await realtime_sidecar_mod.stop_realtime_sidecar_subprocess(proc)

    assert sent == []
    assert proc.terminated is False
    assert proc.killed is False


@pytest.mark.asyncio
async def test_realtime_sidecar_subprocess_uses_signal_when_control_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[object] = []

    class _FakeProc:
        exit_code = None

        def poll(self):
            return self.exit_code

        def send_signal(self, signum) -> None:
            sent.append(signum)
            self.exit_code = 0

        def terminate(self) -> None:
            self.exit_code = 1

        def kill(self) -> None:
            self.exit_code = 2

    async def _control_unavailable(*, timeout_s: float = 2.0) -> bool:
        return False

    proc = _FakeProc()
    graceful_signal = object()
    monkeypatch.setattr(
        realtime_sidecar_mod,
        "_request_realtime_sidecar_graceful_shutdown",
        _control_unavailable,
    )
    monkeypatch.setattr(realtime_sidecar_mod, "_realtime_sidecar_shutdown_signal", lambda: graceful_signal)
    monkeypatch.setattr(realtime_sidecar_mod, "_find_realtime_listener_pid", lambda _host, _port: None)

    await realtime_sidecar_mod.stop_realtime_sidecar_subprocess(proc)

    assert sent == [graceful_signal]


@pytest.mark.asyncio
async def test_realtime_sidecar_subprocess_falls_back_when_graceful_signal_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeProc:
        exit_code = None
        terminated = False
        killed = False

        def poll(self):
            return self.exit_code

        def send_signal(self, _signum) -> None:
            raise OSError("signal unavailable")

        def terminate(self) -> None:
            self.terminated = True
            self.exit_code = 1

        def kill(self) -> None:
            self.killed = True
            self.exit_code = 2

    proc = _FakeProc()
    async def _control_unavailable(*, timeout_s: float = 2.0) -> bool:
        return False

    monkeypatch.setattr(
        realtime_sidecar_mod,
        "_request_realtime_sidecar_graceful_shutdown",
        _control_unavailable,
    )
    monkeypatch.setattr(realtime_sidecar_mod, "_realtime_sidecar_shutdown_signal", lambda: object())
    monkeypatch.setattr(realtime_sidecar_mod, "_find_realtime_listener_pid", lambda _host, _port: None)

    await realtime_sidecar_mod.stop_realtime_sidecar_subprocess(proc)

    assert proc.terminated is True
    assert proc.killed is False


@pytest.mark.asyncio
async def test_realtime_sidecar_local_control_accepts_graceful_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    nats_port = _free_port()
    control_port = _free_port()
    monkeypatch.setenv("ADAOS_REALTIME_PORT", str(nats_port))
    monkeypatch.setenv("ADAOS_REALTIME_CONTROL_PORT", str(control_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_PROXY_ENABLE", "0")
    monkeypatch.setenv("ADAOS_REALTIME_MEDIA_PROXY_ENABLE", "0")
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "sidecar.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    server = RealtimeSidecarServer(host="127.0.0.1", port=nats_port)
    await server.start()

    try:
        accepted = await realtime_sidecar_mod._request_realtime_sidecar_graceful_shutdown()
        await asyncio.wait_for(server._shutdown_requested.wait(), timeout=1.0)
    finally:
        await server.close()

    assert accepted is True


@pytest.mark.asyncio
async def test_realtime_sidecar_control_shutdown_sends_normal_websocket_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    websockets = pytest.importorskip("websockets")
    remote_port = _free_port()
    nats_port = _free_port()
    control_port = _free_port()
    remote_connected = asyncio.Event()
    remote_closed = asyncio.Event()
    close_codes: list[int | None] = []

    async def _remote(websocket, _path=None):
        remote_connected.set()
        try:
            async for _message in websocket:
                pass
        finally:
            close_codes.append(getattr(websocket, "close_code", None))
            remote_closed.set()

    upstream = await websockets.serve(
        _remote,
        "127.0.0.1",
        remote_port,
        max_size=None,
        compression=None,
    )
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_PORT", str(nats_port))
    monkeypatch.setenv("ADAOS_REALTIME_CONTROL_PORT", str(control_port))
    monkeypatch.setenv("ADAOS_REALTIME_ROUTE_PROXY_ENABLE", "0")
    monkeypatch.setenv("ADAOS_REALTIME_MEDIA_PROXY_ENABLE", "0")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", f"ws://127.0.0.1:{remote_port}/nats")
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "sidecar.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    run_task = asyncio.create_task(
        realtime_sidecar_mod.run_realtime_sidecar(host="127.0.0.1", port=nats_port)
    )
    reader = None
    writer = None

    try:
        deadline = asyncio.get_running_loop().time() + 2.0
        while writer is None and asyncio.get_running_loop().time() < deadline:
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", nats_port)
            except OSError:
                await asyncio.sleep(0.02)
        assert reader is not None and writer is not None
        writer.write(b"PING\r\n")
        await writer.drain()
        await asyncio.wait_for(remote_connected.wait(), timeout=1.0)

        accepted = await realtime_sidecar_mod._request_realtime_sidecar_graceful_shutdown()
        await asyncio.wait_for(run_task, timeout=2.0)
        await asyncio.wait_for(remote_closed.wait(), timeout=1.0)
    finally:
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        if not run_task.done():
            run_task.cancel()
            with contextlib.suppress(BaseException):
                await run_task
        upstream.close()
        await upstream.wait_closed()

    assert accepted is True
    assert close_codes == [1000]


@pytest.mark.asyncio
async def test_realtime_sidecar_subprocess_starts_for_direct_tcp_node_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    popen_env: dict[str, str] = {}

    class _FakeProc:
        def poll(self):
            return None

        def terminate(self) -> None:
            return None

    async def _fake_is_port_open(_host: str, _port: int) -> bool:
        return False

    async def _fake_wait_bound(*, host: str, port: int, timeout_s: float = 10.0) -> bool:
        return True

    def _fake_popen(*args, **kwargs):
        nonlocal popen_env
        popen_env = dict(kwargs["env"])
        return _FakeProc()

    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setattr(
        realtime_sidecar_mod,
        "_load_node_yaml",
        lambda: {"nats": {"ws_url": "nats://nats.inimatic.com:4222"}},
    )
    monkeypatch.setattr(realtime_sidecar_mod, "_is_port_open", _fake_is_port_open)
    monkeypatch.setattr(realtime_sidecar_mod, "wait_realtime_sidecar_bound", _fake_wait_bound)
    monkeypatch.setattr(realtime_sidecar_mod.subprocess, "Popen", _fake_popen)

    proc = await realtime_sidecar_mod.start_realtime_sidecar_subprocess(role="hub")

    assert proc is not None
    assert popen_env["ADAOS_REALTIME_PREFER_DEDICATED"] == "0"
    assert popen_env["ADAOS_REALTIME_ALLOW_API_FALLBACK"] == "0"
    assert popen_env["ADAOS_REALTIME_WIN_LOOP"] == "proactor"


@pytest.mark.asyncio
async def test_realtime_sidecar_subprocess_replaces_stale_listener(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    popen_env: dict[str, str] = {}
    replace_calls: list[tuple[str, int]] = []

    class _FakeProc:
        def poll(self):
            return None

        def terminate(self) -> None:
            return None

    async def _fake_is_port_open(_host: str, _port: int) -> bool:
        return not replace_calls

    async def _fake_wait_bound(*, host: str, port: int, timeout_s: float = 10.0) -> bool:
        return True

    def _fake_popen(*args, **kwargs):
        nonlocal popen_env
        popen_env = dict(kwargs["env"])
        return _FakeProc()

    def _fake_replace_existing_realtime_listener(host: str, port: int) -> bool:
        replace_calls.append((host, port))
        return True

    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setattr(realtime_sidecar_mod, "_is_port_open", _fake_is_port_open)
    monkeypatch.setattr(
        realtime_sidecar_mod,
        "_replace_existing_realtime_listener",
        _fake_replace_existing_realtime_listener,
    )
    monkeypatch.setattr(realtime_sidecar_mod, "wait_realtime_sidecar_bound", _fake_wait_bound)
    monkeypatch.setattr(realtime_sidecar_mod.subprocess, "Popen", _fake_popen)

    proc = await realtime_sidecar_mod.start_realtime_sidecar_subprocess(role="hub")

    assert proc is not None
    assert replace_calls == [("127.0.0.1", 7422)]
    assert popen_env["ADAOS_REALTIME_WIN_LOOP"] == "proactor"


def test_realtime_sidecar_never_injects_nats_keepalive_into_byte_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADAOS_REALTIME_NATS_PING_S", raising=False)
    monkeypatch.delenv("ADAOS_REALTIME_UPSTREAM_NATS_PING_S", raising=False)
    assert realtime_sidecar_mod._realtime_nats_ping_interval_s() is None

    monkeypatch.setenv("ADAOS_REALTIME_NATS_PING_S", "5")
    assert realtime_sidecar_mod._realtime_nats_ping_interval_s() is None


def test_realtime_sidecar_filters_quarantined_remote_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    dedicated = "wss://nats.inimatic.com/nats"
    api = "wss://api.inimatic.com/nats"
    quarantine = {
        realtime_sidecar_mod._realtime_remote_quarantine_key(dedicated): realtime_sidecar_mod.time.monotonic() + 60.0
    }
    monkeypatch.setattr(realtime_sidecar_mod, "_realtime_remote_quarantine_until", quarantine)
    monkeypatch.setattr(realtime_sidecar_mod, "resolve_realtime_remote_candidates", lambda: [dedicated, api])

    assert realtime_sidecar_mod._available_realtime_remote_candidates() == [api]


def test_realtime_sidecar_orders_all_quarantined_candidates_by_oldest_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dedicated = "wss://nats.inimatic.com/nats"
    api = "wss://api.inimatic.com/nats"
    now_m = realtime_sidecar_mod.time.monotonic()
    quarantine = {
        realtime_sidecar_mod._realtime_remote_quarantine_key(dedicated): now_m + 30.0,
        realtime_sidecar_mod._realtime_remote_quarantine_key(api): now_m + 60.0,
    }
    monkeypatch.setattr(realtime_sidecar_mod, "_realtime_remote_quarantine_until", quarantine)
    monkeypatch.setattr(realtime_sidecar_mod, "resolve_realtime_remote_candidates", lambda: [api, dedicated])

    assert realtime_sidecar_mod._available_realtime_remote_candidates() == [dedicated, api]


def test_realtime_sidecar_does_not_quarantine_one_close_after_stable_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_STABLE_SESSION_S", "30")
    details = "ConnectionClosedError: no close frame received or sent code=1006"

    assert realtime_sidecar_mod._should_quarantine_realtime_remote(details, connected_for_s=2.0) is True
    assert realtime_sidecar_mod._should_quarantine_realtime_remote(details, connected_for_s=337.0) is False


def test_realtime_cli_applies_loop_policy_before_asyncio_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(realtime_cmd, "apply_realtime_loop_policy", lambda: calls.append("policy"))

    def _fake_run(coro):
        calls.append("run")
        try:
            coro.close()
        except Exception:
            pass
        return 0

    monkeypatch.setattr(realtime_cmd.asyncio, "run", _fake_run)

    with pytest.raises(SystemExit) as exc:
        realtime_cmd.serve(host="127.0.0.1", port=7422)

    assert exc.value.code == 0
    assert calls == ["policy", "run"]


@pytest.mark.asyncio
async def test_realtime_sidecar_does_not_insert_ping_between_pub_payload_fragments(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    fake_ws = _FakeRemoteWS()

    async def _fake_connect(*args, **kwargs):
        return fake_ws

    import websockets  # type: ignore

    monkeypatch.setattr(websockets, "connect", _fake_connect)
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "diag.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", "wss://example.invalid/nats")
    monkeypatch.setenv("ADAOS_REALTIME_NATS_PING_S", "0.01")

    server = RealtimeSidecarServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        _reader, writer = await asyncio.open_connection(server.listen_host, server.listen_port)
        payload = b'{"enabled":false,"items":[' + (b'"x",' * 20_000) + b'"end"]}'
        header = f"PUB state.snapshot {len(payload)}\r\n".encode("ascii")
        midpoint = len(payload) // 2
        first = header + payload[:midpoint]
        second = payload[midpoint:] + b"\r\n"

        writer.write(first)
        await writer.drain()
        await asyncio.sleep(0.1)
        writer.write(second)
        await writer.drain()
        for _ in range(50):
            if sum(map(len, fake_ws.sent)) >= len(first) + len(second):
                break
            await asyncio.sleep(0.01)

        assert b"".join(fake_ws.sent) == first + second
        assert server._stats.sidecar_nats_ping_interval_s is None
        assert server._stats.sidecar_nats_pings_tx == 0
        assert server._stats.sidecar_nats_pongs_rx == 0
        assert server._stats.sidecar_nats_pings_outstanding == 0
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_realtime_sidecar_forwards_runtime_nats_ping_pong_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    fake_ws = _FakeRemoteWS()

    async def _fake_connect(*args, **kwargs):
        return fake_ws

    import websockets  # type: ignore

    monkeypatch.setattr(websockets, "connect", _fake_connect)
    monkeypatch.setenv("ADAOS_REALTIME_DIAG_FILE", str(tmp_path / "diag.jsonl"))
    monkeypatch.setenv("ADAOS_REALTIME_LOG", str(tmp_path / "sidecar.log"))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_REALTIME_REMOTE_WS_URL", "wss://example.invalid/nats")

    server = RealtimeSidecarServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection(server.listen_host, server.listen_port)
        writer.write(b"PING\r\n")
        await writer.drain()
        for _ in range(50):
            if fake_ws.sent:
                break
            await asyncio.sleep(0.01)

        await fake_ws.recv_queue.put(b"PONG\r\n")
        data = await asyncio.wait_for(reader.readexactly(len(b"PONG\r\n")), timeout=1.0)

        assert fake_ws.sent == [b"PING\r\n"]
        assert data == b"PONG\r\n"
        assert server._stats.local_nats_pings_tx == 1
        assert server._stats.client_nats_pings_outstanding == 0
        assert server._stats.sidecar_nats_pings_tx == 0
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()
