from __future__ import annotations

import gc
import importlib.util
import json
import socket
import sqlite3
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import y_py as Y
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect
from websockets.sync.server import serve


BOOTSTRAP_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "adaos"
    / "integrations"
    / "android-node"
    / "app"
    / "src"
    / "main"
    / "python"
    / "adaos"
    / "android"
    / "bootstrap.py"
)
MEMBER_FIXTURE_PATH = BOOTSTRAP_PATH.parents[6] / "verify_member_link.py"
ANDROID_PYTHON_ROOT = BOOTSTRAP_PATH.parents[2]
PORTABLE_RASA_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "adaos"
    / "services"
    / "nlu"
    / "portable_rasa.py"
)


def _post_json(url: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Origin": "https://inimatic.com"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def _control_command(websocket, command_id: str, kind: str, payload: dict) -> tuple[dict, list[dict]]:
    websocket.send(
        json.dumps(
            {"ch": "events", "t": "cmd", "id": command_id, "kind": kind, "payload": payload}
        )
    )
    events: list[dict] = []
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        message = json.loads(websocket.recv(timeout=5))
        if message.get("t") == "ack" and message.get("id") == command_id:
            return message, events
        events.append(message)
    raise AssertionError(f"control command {kind} was not acknowledged")


def _load_bootstrap():
    _install_portable_rasa_module()
    package_name = "_adaos_android_bootstrap_test"
    package = sys.modules.get(package_name)
    if package is None:
        package = types.ModuleType(package_name)
        package.__path__ = [str(BOOTSTRAP_PATH.parent)]
        sys.modules[package_name] = package
    module_name = f"{package_name}.bootstrap_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(module_name, BOOTSTRAP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _install_portable_rasa_module() -> None:
    """Expose only the shared inference module to the isolated Android host test."""

    module_name = "adaos.services.nlu.portable_rasa"
    if module_name in sys.modules:
        return
    packages = (
        ("adaos", ANDROID_PYTHON_ROOT / "adaos"),
        ("adaos.services", ANDROID_PYTHON_ROOT / "adaos" / "services"),
        ("adaos.services.nlu", ANDROID_PYTHON_ROOT / "adaos" / "services" / "nlu"),
    )
    for package_name, package_path in packages:
        if package_name in sys.modules:
            continue
        package = types.ModuleType(package_name)
        package.__package__ = package_name
        package.__path__ = [str(package_path)]
        sys.modules[package_name] = package
        parent_name, _, child_name = package_name.rpartition(".")
        if parent_name and parent_name in sys.modules:
            setattr(sys.modules[parent_name], child_name, package)

    spec = importlib.util.spec_from_file_location(module_name, PORTABLE_RASA_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    setattr(sys.modules["adaos.services.nlu"], "portable_rasa", module)


def _load_member_fixture():
    module_name = f"_adaos_android_member_fixture_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(module_name, MEMBER_FIXTURE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _wait_until(predicate, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition did not become true before timeout")


def test_loopback_runtime_persists_identity_and_reports_member_status(tmp_path: Path) -> None:
    bootstrap = _load_bootstrap()
    first = json.loads(bootstrap.start(str(tmp_path), "test", 0))
    try:
        assert first["runtime_profile"] == "android_poc"
        assert first["host"] == "127.0.0.1"
        with urllib.request.urlopen(
            f"http://127.0.0.1:{first['port']}/api/node/status",
            timeout=2,
        ) as response:
            status = json.load(response)
        assert status["node_id"] == first["node_id"]
        assert status["subnet_id"] == first["subnet_id"]
        assert status["role"] == "member"
        assert status["environment"]["local_auth_required"] is False
        assert status["runtime"]["yjs_ready"] is True
        assert status["runtime"]["yjs_mode"] == "native_y_py_sqlite_ystore"
        assert status["runtime"]["yjs_seed_ready"] is True
        assert status["runtime"]["ystore_backend"] == "sqlite_snapshot_log"
        assert status["runtime"]["yjs_revision"] >= 1
        assert status["runtime"]["yjs_generation"] >= 1
        assert status["runtime"]["yjs_snapshot_bytes"] > 0
        assert status["runtime"]["yjs_snapshot_pressure"] == "ready"
        assert status["runtime"]["skill_descriptors_ready"] is True
        assert status["runtime"]["nlu"]["status"] == "ready"
        assert status["runtime"]["nlu"]["provider"] == "rasa"
        assert status["runtime"]["nlu"]["mode"] == "always"
        assert status["runtime"]["nlu"]["training"] == "off_device"
        assert status["runtime"]["nlu"]["model_id"] == (
            "362b6f47acb743658d8cd4bb8f538a41"
        )
        assert status["environment"]["nlu"] == status["runtime"]["nlu"]
        assert status["runtime"]["startup_duration_ms"] >= 0
        assert status["runtime"]["resource_bounds"]["loopback"] == {
            "active_request_threads": 1,
            "peak_request_threads": 1,
            "request_thread_limit": 32,
            "rejected_requests": 0,
            "accept_backlog_limit": 16,
        }
        assert status["runtime"]["resource_bounds"]["ystore"]["task_queue_limit"] == 64
        assert status["runtime"]["resource_bounds"]["ystore"]["task_queue_rejected"] == 0
        assert status["runtime"]["resource_bounds"]["skills"]["note_count_limit"] == 256
        assert status["runtime"]["resource_bounds"]["skills"][
            "note_content_chars_limit"
        ] == 16 * 1024
        assert status["runtime"]["resources"]["policy"] == {
            "large_heap_requested": False,
            "sampler": "procfs_no_psutil",
        }
        with urllib.request.urlopen(
            f"http://127.0.0.1:{first['port']}/api/ping",
            timeout=2,
        ) as response:
            ping = json.load(response)
        assert ping["node_id"] == first["node_id"]
        assert ping["subnet_id"] == first["subnet_id"]
        assert ping["environment"]["local_auth_required"] is False
    finally:
        json.loads(bootstrap.stop())

    second = json.loads(bootstrap.start(str(tmp_path), "test", 0))
    try:
        assert second["node_id"] == first["node_id"]
        assert second["subnet_id"] == first["subnet_id"]
    finally:
        bootstrap.stop()


def test_android_resource_sampler_reads_procfs_and_retains_peaks(tmp_path: Path) -> None:
    bootstrap = _load_bootstrap()
    self_root = tmp_path / "self"
    self_root.mkdir()
    (self_root / "status").write_text(
        "VmRSS:\t102400 kB\nVmHWM:\t110000 kB\nVmSwap:\t64 kB\nThreads:\t7\n",
        encoding="utf-8",
    )
    (self_root / "smaps_rollup").write_text(
        "Pss:\t90000 kB\nPrivate_Dirty:\t70000 kB\nSwapPss:\t32 kB\n",
        encoding="utf-8",
    )
    (tmp_path / "meminfo").write_text("MemTotal:\t2097152 kB\n", encoding="utf-8")
    sampler = bootstrap.AndroidResourceSampler(tmp_path)

    first = sampler.sample()
    assert first["process"]["pss_kib"] == 90000
    assert first["process"]["peak_rss_kib"] == 110000
    assert first["process"]["threads"] == 7
    assert first["device"]["memory_total_kib"] == 2097152
    assert first["budgets"]["pressure"] == "ready"

    (self_root / "smaps_rollup").write_text("Pss:\t225000 kB\n", encoding="utf-8")
    second = sampler.sample()
    assert second["process"]["peak_pss_kib"] == 225000
    assert second["budgets"]["pressure"] == "warning"

    (self_root / "smaps_rollup").write_text("Pss:\t100000 kB\n", encoding="utf-8")
    third = sampler.sample()
    assert third["process"]["peak_pss_kib"] == 225000
    assert third["sample_total"] == 3


def test_android_notebook_enforces_projection_content_and_count_bounds(
    tmp_path: Path,
) -> None:
    bootstrap = _load_bootstrap()
    bootstrap.start(str(tmp_path), "test", 0)
    try:
        created = bootstrap._skills.call_tool(
            "notebook_skill:create_note",
            {"content": "x" * (20 * 1024)},
        )
        assert len(created["editor"]["content"]) == 16 * 1024
        bounds = bootstrap._skills.status()["resource_bounds"]
        assert bounds["note_content_chars_limit"] == 16 * 1024
        assert bounds["projected_note_count_limit"] == 32

        database = bootstrap._skills._database
        current = int(database.execute("SELECT COUNT(*) FROM notebook_notes").fetchone()[0])
        now = "2026-01-01T00:00:00Z"
        database.executemany(
            "INSERT INTO notebook_notes VALUES (?, ?, ?, ?, ?)",
            [
                (f"bounded-note-{index}", "", now, now, 1)
                for index in range(256 - current)
            ],
        )
        database.commit()
        with pytest.raises(bootstrap.AndroidSkillError, match="notebook_note_limit_reached"):
            bootstrap._skills.call_tool(
                "notebook_skill:create_note",
                {"content": "one too many"},
            )
        assert len(bootstrap._skills._notebook_snapshot()["items"]) == 32
    finally:
        bootstrap.stop()


def test_android_stop_drains_inflight_status_requests_without_traceback(
    tmp_path: Path,
) -> None:
    bootstrap = _load_bootstrap()
    runtime = json.loads(bootstrap.start(str(tmp_path), "test", 0))
    url = f"http://127.0.0.1:{runtime['port']}/api/node/status"
    polling = threading.Event()
    polling.set()
    server_failures: list[BaseException] = []

    def poll_status() -> None:
        while polling.is_set():
            try:
                with urllib.request.urlopen(url, timeout=0.5) as response:
                    json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code >= 500:
                    server_failures.append(exc)
            except (OSError, ValueError):
                pass

    pollers = [threading.Thread(target=poll_status) for _ in range(8)]
    for poller in pollers:
        poller.start()
    try:
        time.sleep(0.1)
        bootstrap.stop()
    finally:
        polling.clear()
        for poller in pollers:
            poller.join(timeout=2)

    assert server_failures == []
    assert bootstrap._node_status()["node_state"] == "stopped"
    assert bootstrap._node_status()["ready"] is False


def test_android_dialog_uses_rasa_teacher_and_canonical_hub_companion(
    tmp_path: Path,
) -> None:
    bootstrap = _load_bootstrap()
    bootstrap.start(str(tmp_path), "test", 0)

    class FakeMemberLink:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict, str]] = []
            self.calls: list[tuple[str, dict, float]] = []
            self.order: list[str] = []

        def send_bus_event(
            self, event_type: str, payload: dict, *, source: str = ""
        ) -> bool:
            self.order.append("teacher")
            self.events.append((event_type, payload, source))
            return True

        def call_hub_tool(
            self, tool: str, arguments: dict, *, timeout: float
        ) -> dict:
            self.order.append("rpc")
            self.calls.append((tool, arguments, timeout))
            return {"message": "Canonical Hub companion response", "used_llm": True}

    member_link = FakeMemberLink()
    try:
        assert bootstrap._skills is not None
        bootstrap._skills.member_link = member_link
        selected = bootstrap._skills.select_dialog_agent(
            {"agent_id": "agent:conversation_companions:arseni"}
        )
        assert selected["active_agent"]["implementation"] == "hub_delegated"
        assert selected["active_agent"]["model_backed"] is True

        result = bootstrap._skills.handle_dialog_message(
            {"text": "Why should one runtime stay canonical?", "webspace_id": "desktop"}
        )

        assert result["response"] == "Canonical Hub companion response"
        assert result["response_source"] == "hub_skill_llm"
        assert result["used_llm"] is True
        assert result["nlu"]["provider"] == "rasa"
        assert result["nlu"]["mode"] == "always"
        assert result["nlu"]["teacher_dispatched"] is True
        assert member_link.calls[0][0] == "conversation_companions:talk"
        assert member_link.calls[0][1]["character_id"] == "arseni"
        assert member_link.events[0][0] == "nlp.intent.not_obtained"
        assert member_link.events[0][2] == "android.nlu.rasa"
        assert member_link.events[0][1]["_meta"]["nlu_teacher_only"] is True
        assert member_link.order == ["rpc", "teacher"]
    finally:
        bootstrap.stop()


def test_android_connect_delegates_remote_invitations_to_canonical_hub_skill(
    tmp_path: Path,
) -> None:
    bootstrap = _load_bootstrap()
    bootstrap.start(str(tmp_path), "test", 0)

    class FakeMemberLink:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, float]] = []

        def snapshot(self) -> dict:
            return {
                "configured": True,
                "connected": True,
                "state": "connected",
                "root_url": "https://ru.api.inimatic.com",
                "hub_url": "https://ru.api.inimatic.com/hubs/sn_test",
                "subnet_id": "sn_test",
                "transport_security": "tls",
            }

        def call_hub_tool(
            self, tool: str, arguments: dict, *, timeout: float
        ) -> dict:
            self.calls.append((tool, arguments, timeout))
            time.sleep(0.1)
            return {
                "ok": True,
                "current": {
                    "status": "ready",
                    "summary": "Register a remote browser.",
                    "link": "https://inimatic.com/?intent=connect.register&zone=ru",
                    "qr_text": "https://inimatic.com/?intent=connect.register&zone=ru",
                    "code": "PAIR1234",
                },
            }

    member_link = FakeMemberLink()
    try:
        assert bootstrap._skills is not None
        bootstrap._skills.member_link = member_link

        started = time.monotonic()
        result = bootstrap._skills._prepare_connect(
            "browser", {"webspace_id": "desktop", "refresh": True}
        )

        assert time.monotonic() - started < 0.08
        assert result["current"]["status"] == "pending"
        worker = bootstrap._skills._connect_prepare_thread
        assert worker is not None
        worker.join(timeout=2)
        result = bootstrap._skills._connect_current()
        assert member_link.calls[0][0] == "adaos_connect:prepare"
        assert member_link.calls[0][1]["mode"] == "browser"
        assert result["current"]["status"] == "ready"
        assert result["current"]["source"] == "hub_delegated"
        assert result["current"]["link"].startswith("https://inimatic.com/")
        assert result["current"]["connected"] is True
    finally:
        bootstrap.stop()


def test_android_ystore_structurally_compacts_bloated_history_on_restart(
    tmp_path: Path,
) -> None:
    bootstrap = _load_bootstrap()
    database_path = tmp_path / "compaction.sqlite3"
    store = bootstrap.AndroidYStore(
        database_path,
        b"",
        max_snapshot_bytes=8 * 1024 * 1024,
    )
    try:
        client = Y.YDoc(skip_gc=True)
        runtime = client.get_map("runtime")
        with client.begin_transaction() as transaction:
            for index in range(20_000):
                runtime.set(transaction, f"http_repair_{index}", "discarded history")
        with client.begin_transaction() as transaction:
            for index in range(20_000):
                runtime.pop(transaction, f"http_repair_{index}")
            runtime.set(transaction, "retained_value", "semantic state")
        assert store.apply_update(bytes(Y.encode_state_as_update(client)))
        del transaction, runtime, client
        gc.collect()
        semantic_before = store.snapshot_json()
        source_bytes = store.stats()["snapshot_bytes"]
        generation_before = store.stats()["generation"]
        assert source_bytes > 64 * 1024
    finally:
        store.close()

    compacted = bootstrap.AndroidYStore(
        database_path,
        b"",
        max_snapshot_bytes=64 * 1024,
    )
    try:
        stats = compacted.stats()
        assert compacted.snapshot_json() == semantic_before
        assert stats["compacted_on_startup"] is True
        assert stats["generation"] == generation_before + 1
        assert stats["last_compaction_source_bytes"] == source_bytes
        assert stats["last_compaction_result_bytes"] < source_bytes
        assert stats["snapshot_bytes"] == stats["last_compaction_result_bytes"]
        assert stats["snapshot_pressure"] == "ready"
    finally:
        compacted.close()


def test_android_yws_rejects_oversized_client_history_with_recovery_reason(
    tmp_path: Path,
) -> None:
    bootstrap = _load_bootstrap()
    runtime = json.loads(bootstrap.start(str(tmp_path), "test", 0))
    try:
        uri = f"ws://127.0.0.1:{runtime['port']}/yws/desktop"
        with connect(
            uri,
            origin="https://inimatic.com",
            open_timeout=2,
            close_timeout=2,
            max_size=4 * 1024 * 1024,
        ) as websocket:
            websocket.recv(timeout=2)
            client = Y.YDoc()
            with client.begin_transaction() as transaction:
                client.get_map("runtime").set(
                    transaction,
                    "oversized_history",
                    "x" * (bootstrap._MAX_INBOUND_YJS_UPDATE_BYTES + 4096),
                )
            websocket.send(
                bootstrap._encode_sync_message(
                    2,
                    bytes(Y.encode_state_as_update(client)),
                )
            )
            with pytest.raises(ConnectionClosed) as raised:
                websocket.recv(timeout=2)
            assert raised.value.rcvd is not None
            assert raised.value.rcvd.code == 1009
            assert "inbound_yws_update_payload_blocked" in raised.value.rcvd.reason
            del transaction, client
            gc.collect()
    finally:
        bootstrap.stop()


def test_android_yws_fences_stale_store_generations(tmp_path: Path) -> None:
    bootstrap = _load_bootstrap()
    runtime = json.loads(bootstrap.start(str(tmp_path), "test", 0))
    base = f"http://127.0.0.1:{runtime['port']}"
    try:
        with urllib.request.urlopen(
            f"{base}/api/browser/session/authorize?dev=browser-1&ws=desktop",
            timeout=2,
        ) as response:
            authorization = json.load(response)
        generation = int(authorization["yjs_generation"])
        assert generation >= 1

        with connect(
            f"ws://127.0.0.1:{runtime['port']}/yws/desktop~g{generation + 1}",
            origin="https://inimatic.com",
            open_timeout=2,
            close_timeout=2,
        ) as websocket:
            with pytest.raises(ConnectionClosed) as raised:
                websocket.recv(timeout=2)
            assert raised.value.rcvd is not None
            assert raised.value.rcvd.code == 1012
            assert (
                raised.value.rcvd.reason
                == f"ystore_generation_mismatch:{generation + 1}->{generation}"
            )

        with connect(
            f"ws://127.0.0.1:{runtime['port']}/yws/desktop~g{generation}",
            origin="https://inimatic.com",
            open_timeout=2,
            close_timeout=2,
        ) as websocket:
            assert isinstance(websocket.recv(timeout=2), bytes)
    finally:
        bootstrap.stop()


def test_loopback_sentinel_admits_inimatic_cors_and_private_network(tmp_path: Path) -> None:
    bootstrap = _load_bootstrap()
    runtime = json.loads(bootstrap.start(str(tmp_path), "test", 0))
    request = urllib.request.Request(
        f"http://127.0.0.1:{runtime['port']}/api/node/status",
        method="OPTIONS",
        headers={
            "Origin": "https://inimatic.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
            "Access-Control-Request-Headers": "x-adaos-device-id,x-adaos-trace-id",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 204
            assert response.headers["Access-Control-Allow-Origin"] == "https://inimatic.com"
            assert response.headers["Access-Control-Allow-Private-Network"] == "true"
            allowed_headers = response.headers["Access-Control-Allow-Headers"].lower()
            assert "x-adaos-device-id" in allowed_headers
            assert "x-adaos-trace-id" in allowed_headers
    finally:
        bootstrap.stop()


def test_android_websocket_peer_aborts_after_bounded_send_failure() -> None:
    bootstrap = _load_bootstrap()

    class TimeoutSocket:
        def __init__(self) -> None:
            self.options: list[tuple[int, int, object]] = []
            self.shutdown_called = False
            self.close_called = False

        def setsockopt(self, level: int, option: int, value: object) -> None:
            self.options.append((level, option, value))

        def sendall(self, payload: bytes) -> None:
            raise TimeoutError("stale websocket peer")

        def shutdown(self, how: int) -> None:
            self.shutdown_called = True

        def close(self) -> None:
            self.close_called = True

    connection = TimeoutSocket()
    peer = bootstrap._WebSocketPeer(connection, "yjs")

    peer.send(0x2, b"update")

    assert any(option == socket.SO_SNDTIMEO for _, option, _ in connection.options)
    assert peer.closed is True
    assert connection.shutdown_called is True
    assert connection.close_called is True


def test_android_member_join_reconnect_and_node_owned_yjs_projection(tmp_path: Path) -> None:
    bootstrap = _load_bootstrap()
    fixture = _load_member_fixture()
    evidence = fixture.Evidence(
        code="TEST-JOIN",
        token="test-member-token",
        subnet_id="test-member-subnet",
        hub_url="",
    )
    hub = serve(
        fixture._hub_handler(evidence),
        "127.0.0.1",
        0,
        compression=None,
        max_size=4 * 1024 * 1024,
    )
    hub_port = int(hub.socket.getsockname()[1])
    evidence.hub_url = f"http://127.0.0.1:{hub_port}"
    root = fixture.ThreadingHTTPServer(("127.0.0.1", 0), fixture._root_handler(evidence))
    root_port = int(root.server_address[1])
    hub_thread = threading.Thread(target=hub.serve_forever, daemon=True)
    root_thread = threading.Thread(target=root.serve_forever, daemon=True)
    hub_thread.start()
    root_thread.start()
    runtime = json.loads(bootstrap.start(str(tmp_path), "test", 0))
    base_url = f"http://127.0.0.1:{runtime['port']}"
    try:
        code, joined = _post_json(
            f"{base_url}/api/node/member/join",
            {"root_url": f"http://127.0.0.1:{root_port}", "code": "TEST-JOIN"},
        )
        assert code == 200 and joined["ok"] is True
        assert joined["result"]["current"]["status"] == "pending"
        assert joined["result"]["current"]["join_status"] == "validating"
        assert joined["result"]["current"]["join_code"] == ""
        assert "test-member-token" not in json.dumps(joined)

        def reconnected() -> bool:
            with urllib.request.urlopen(f"{base_url}/api/node/member/status", timeout=2) as response:
                member = json.load(response)
            return bool(
                member.get("connected")
                and int(member.get("reconnect_total") or 0) >= 1
                and evidence.snapshot()["sessions"] >= 2
                and evidence.snapshot()["inbound_probe_sent"]
            )

        _wait_until(reconnected, timeout=15)
        code, renamed = _post_json(
            f"{base_url}/api/tools/call",
            {
                "tool": "subnet_env:set_node_label",
                "arguments": {"node_label": "Linked Android"},
            },
        )
        assert code == 200 and renamed["result"]["node_label"] == "Linked Android"

        def converged() -> bool:
            with urllib.request.urlopen(
                f"{base_url}/api/node/yjs/webspaces/desktop/materialization/snapshot",
                timeout=2,
            ) as response:
                snapshot = json.load(response)["snapshot"]
            return bool(
                evidence.snapshot()["yjs_node_state_total"] >= 2
                and evidence.snapshot()["last_node_label"] == "Linked Android"
                and "member_hub_probe" not in snapshot.get("runtime", {})
            )

        _wait_until(converged)
        with urllib.request.urlopen(f"{base_url}/api/node/status", timeout=2) as response:
            status = json.load(response)
        assert status["subnet_id"] == "test-member-subnet"
        assert status["connected_to_hub"] is True
        assert status["runtime"]["member_link"]["token_present"] is True
        assert status["runtime"]["member_link"]["sent_yjs_total"] == 0
        assert status["runtime"]["member_link"]["ignored_hub_yjs_total"] >= 1
        assert "test-member-token" not in json.dumps(status)

        code, disconnected = _post_json(
            f"{base_url}/api/node/member/disconnect",
            {"forget": True},
        )
        assert code == 200
        assert disconnected["result"]["current"]["configured"] is False
        with urllib.request.urlopen(f"{base_url}/api/node/member/status", timeout=2) as response:
            forgotten = json.load(response)
        assert forgotten["queued_messages"] == 0
        assert forgotten["transport_security"] == "unconfigured"
    finally:
        bootstrap.stop()
        root.shutdown()
        root.server_close()
        hub.shutdown()
        root_thread.join(timeout=2)
        hub_thread.join(timeout=2)


def test_loopback_runtime_serves_no_auth_web_desktop_materialization(tmp_path: Path) -> None:
    bootstrap = _load_bootstrap()
    runtime = json.loads(bootstrap.start(str(tmp_path), "test", 0))
    base = f"http://127.0.0.1:{runtime['port']}"
    try:
        with urllib.request.urlopen(
            f"{base}/api/browser/session/authorize?dev=browser-1&ws=desktop",
            timeout=2,
        ) as response:
            authorization = json.load(response)
        assert authorization["allowed"] is True
        assert authorization["local_auth_required"] is False
        assert authorization["yjs_generation"] >= 1

        with urllib.request.urlopen(
            f"{base}/api/node/yjs/webspaces/desktop/materialization/snapshot"
            "?include_runtime=0&scope=essential",
            timeout=2,
        ) as response:
            materialization = json.load(response)
        assert materialization["state"] == "ready"
        assert materialization["materialization"]["ready"] is True
        assert materialization["materialization"]["current_scenario"] == "web_desktop"
        snapshot = materialization["snapshot"]
        assert snapshot["ui"]["current_scenario"] == "web_desktop"
        assert snapshot["ui"]["application"]["desktop"]["pageSchema"]["id"] == "desktop"
        app_ids = {item["id"] for item in snapshot["data"]["catalog"]["apps"]}
        assert {
            "android_node_settings_app",
            "weather_app",
            "adaos_connect_app",
            "browsers",
            "voice_assistant_app",
            "notebook_skill_app",
            "scenario:taiga_ui_demo_scenario",
        } <= app_ids
        assert snapshot["data"]["installed"]["apps"]
        assert snapshot["data"]["nodes"] == {}
        assert snapshot["data"]["dialog"]["active_channel_id"] == "general"
        assert snapshot["data"]["dialog"]["implementation"]["id"] == (
            "android_local_bounded"
        )
        assert {
            "AdaOS Mobile",
            "Арсений",
            "Ника",
            "Мира",
            "Строитель",
        } == {item["label"] for item in snapshot["data"]["dialog"]["agents"]}
        assert all(
            item["model_backed"] is False
            for item in snapshot["data"]["dialog"]["agents"]
        )
        assert "weather_modal" in snapshot["ui"]["application"]["modals"]
        assert "subnet_env_modal" in snapshot["ui"]["application"]["modals"]
        assert snapshot["registry"]["merged"]["modals"]

        request = urllib.request.Request(
            f"{base}/api/node/projection-demand/client",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "client_id": "browser-1",
                    "session_id": "session-1",
                    "webspace_id": "desktop",
                    "subscriptions": [],
                }
            ).encode("utf-8"),
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            projection = json.load(response)
        assert projection["accepted"] is True
        assert projection["webspace_id"] == "desktop"

        with urllib.request.urlopen(
            f"{base}/api/node/reliability/runtime?webspace_id=desktop",
            timeout=2,
        ) as response:
            reliability = json.load(response)
        assert reliability["available"] is True
        assert reliability["stateSync"]["semanticState"] == "ready"
        assert reliability["stateSync"]["freshnessState"] == "fresh"
    finally:
        bootstrap.stop()


def test_native_yws_ystore_completes_diff_sync_and_persists_yjs_state(tmp_path: Path) -> None:
    import y_py as Y

    bootstrap = _load_bootstrap()
    runtime = json.loads(bootstrap.start(str(tmp_path), "test", 0))
    uri = f"ws://127.0.0.1:{runtime['port']}/yws/desktop"
    document = Y.YDoc()
    try:
        with connect(uri, origin="https://inimatic.com", open_timeout=2, close_timeout=2) as websocket:
            server_step_one = websocket.recv(timeout=2)
            message_type, offset = bootstrap._read_var_uint(server_step_one)
            sync_type, offset = bootstrap._read_var_uint(server_step_one, offset)
            server_vector, _ = bootstrap._read_var_bytes(server_step_one, offset)
            assert (message_type, sync_type) == (0, 0)
            assert server_vector not in {b"", b"\x00"}

            websocket.send(
                bootstrap._encode_sync_message(0, bytes(Y.encode_state_vector(document)))
            )
            seed_message = websocket.recv(timeout=2)
            message_type, offset = bootstrap._read_var_uint(seed_message)
            sync_type, offset = bootstrap._read_var_uint(seed_message, offset)
            seed_update, _ = bootstrap._read_var_bytes(seed_message, offset)
            assert (message_type, sync_type) == (0, 1)
            Y.apply_update(document, seed_update)
            assert json.loads(document.get_map("ui").to_json())["current_scenario"] == "web_desktop"
            assert json.loads(document.get_map("data").to_json())["nodes"] == {}

            before = bytes(Y.encode_state_vector(document))
            with document.begin_transaction() as transaction:
                document.get_map("runtime").set(transaction, "restart_probe", "persisted")
            update = bytes(Y.encode_state_as_update(document, before))
            websocket.send(bootstrap._encode_sync_message(2, update))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                status = json.loads(bootstrap.status())
                if status and status.get("ready"):
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{runtime['port']}/api/node/status",
                        timeout=2,
                    ) as response:
                        status = json.load(response)["runtime"]
                        if status["yjs_update_count"] >= 2 and status["yjs_revision"] >= 3:
                            break
                time.sleep(0.02)
            else:
                raise AssertionError("Yjs update was not persisted")
        del transaction, document
        gc.collect()
    finally:
        bootstrap.stop()

    database_path = tmp_path / "android-yjs.sqlite3"
    assert database_path.is_file()
    with sqlite3.connect(database_path) as connection:
        revision, snapshot_size = connection.execute(
            "SELECT revision, LENGTH(snapshot) FROM y_documents WHERE webspace_id = 'desktop'"
        ).fetchone()
    assert revision >= 2
    assert snapshot_size > len(bootstrap._base_yjs_update)

    restarted = json.loads(bootstrap.start(str(tmp_path), "test", 0))
    try:
        with connect(
            f"ws://127.0.0.1:{restarted['port']}/yws/desktop",
            origin="https://inimatic.com",
            open_timeout=2,
            close_timeout=2,
        ) as websocket:
            websocket.recv(timeout=2)
            restored = Y.YDoc()
            websocket.send(
                bootstrap._encode_sync_message(0, bytes(Y.encode_state_vector(restored)))
            )
            merged_message = websocket.recv(timeout=2)
            _, offset = bootstrap._read_var_uint(merged_message)
            sync_type, offset = bootstrap._read_var_uint(merged_message, offset)
            merged_update, _ = bootstrap._read_var_bytes(merged_message, offset)
            assert sync_type == 1
            Y.apply_update(restored, merged_update)
            assert json.loads(restored.get_map("runtime").to_json())["restart_probe"] == "persisted"
        del restored
        gc.collect()
    finally:
        bootstrap.stop()


def test_fixed_in_process_skills_publish_ws_yjs_and_persist_notebook(tmp_path: Path) -> None:
    bootstrap = _load_bootstrap()
    runtime = json.loads(bootstrap.start(str(tmp_path), "test", 0))
    base_url = f"http://127.0.0.1:{runtime['port']}"
    try:
        with urllib.request.urlopen(f"{base_url}/api/node/status", timeout=2) as response:
            status = json.load(response)
        assert status["runtime"]["skills_ready"] is True
        assert status["runtime"]["skill_execution"] == "in_process"
        assert status["runtime"]["install_profile"] == "android_poc_v1"
        assert {
                "weather_skill",
                "adaos_connect",
                "browsers_skill",
                "voice_assistant",
                "notebook_skill",
            "demo_metrics_skill",
        }.issubset(status["runtime"]["active_skills"])

        code, created = _post_json(
            f"{base_url}/api/tools/call",
            {
                "tool": "notebook_skill:create_note",
                "arguments": {"content": "Android note"},
                "idempotency_key": "create-note-proof",
            },
        )
        assert code == 200 and created["ok"] is True
        note_id = created["result"]["selected_note_id"]
        code, saved = _post_json(
            f"{base_url}/api/tools/call",
            {
                "tool": "notebook_skill:save_note",
                "arguments": {"note_id": note_id, "content": "Persistent Android notebook"},
                "idempotency_key": "save-note-proof",
            },
        )
        assert code == 200
        assert saved["result"]["editor"]["content"] == "Persistent Android notebook"

        code, subnet = _post_json(
            f"{base_url}/api/tools/call",
            {"tool": "subnet_env:get_snapshot", "arguments": {}},
        )
        assert code == 200
        assert subnet["result"]["node_id"] == runtime["node_id"]
        code, subnet = _post_json(
            f"{base_url}/api/tools/call",
            {
                "tool": "subnet_env:set_node_label",
                "arguments": {"node_label": "Pocket AdaOS"},
            },
        )
        assert code == 200
        assert subnet["result"]["node_label"] == "Pocket AdaOS"

        code, disposable = _post_json(
            f"{base_url}/api/tools/call",
            {"tool": "notebook_skill:create_note", "arguments": {"content": "delete me"}},
        )
        disposable_id = disposable["result"]["selected_note_id"]
        code, deleted = _post_json(
            f"{base_url}/api/tools/call",
            {
                "tool": "notebook_skill:delete_note",
                "arguments": {"note_id": disposable_id},
            },
        )
        assert code == 200
        assert all(item["id"] != disposable_id for item in deleted["result"]["items"])

        bootstrap._skills._fetch_weather = lambda _lat, _lon, label, request_id: {
            "current": {
                "city": label,
                "label": label,
                "temp_c": 21.5,
                "condition": "Clear",
                "summary": "Android weather proof",
                "pending": False,
                "source": "test",
                "error": "",
                "request_id": request_id,
                "updated_at": "now",
            },
            "hourly_chart": {"title": "Next hours", "unit": "C", "points": []},
            "daily": [],
        }
        with connect(
            f"ws://127.0.0.1:{runtime['port']}/ws",
            origin="https://inimatic.com",
            open_timeout=2,
            close_timeout=2,
        ) as websocket:
            original_dialog_handler = bootstrap._skills.handle_dialog_message

            def _slow_dialog_handler(payload):
                time.sleep(0.25)
                return original_dialog_handler(payload)

            bootstrap._skills.handle_dialog_message = _slow_dialog_handler
            websocket.send(
                json.dumps(
                    {
                        "ch": "events",
                        "t": "cmd",
                        "id": "dialog-keepalive-proof",
                        "kind": "dialog.user_message",
                        "payload": {"text": "hello", "webspace_id": "desktop"},
                    }
                )
            )
            websocket.send(json.dumps({"type": "ping"}))
            pong_started = time.monotonic()
            pong = json.loads(websocket.recv(timeout=1))
            assert pong == {"type": "pong"}
            assert time.monotonic() - pong_started < 0.2
            dialog_ack = json.loads(websocket.recv(timeout=2))
            assert dialog_ack["id"] == "dialog-keepalive-proof"
            assert dialog_ack["data"]["accepted"] is True
            bootstrap._skills.handle_dialog_message = original_dialog_handler

            ack, _ = _control_command(
                websocket,
                "weather-proof",
                "skill.event.publish",
                {
                    "event_type": "weather.location.requested",
                    "payload": {"city": "Moscow", "request_id": "weather-request"},
                },
            )
            assert ack["data"]["accepted"] is True

            ack, _ = _control_command(
                websocket,
                "subnet-env-proof",
                "skill.event.publish",
                {
                    "event_type": "subnet_env.node_label.changed",
                    "payload": {"node_label": "Android Proof Node"},
                },
            )
            assert ack["data"]["result"]["node_label"] == "Android Proof Node"

            bootstrap._skills._fetch_weather = lambda *_args: (_ for _ in ()).throw(
                OSError("offline proof")
            )
            ack, _ = _control_command(
                websocket,
                "weather-offline-proof",
                "skill.event.publish",
                {
                    "event_type": "weather.location.requested",
                    "payload": {"city": "Berlin", "request_id": "weather-offline-request"},
                },
            )
            assert ack["data"]["result"]["current"]["source"] == "offline"
            assert ack["data"]["result"]["current"]["pending"] is False

            ack, _ = _control_command(
                websocket,
                "connect-proof",
                "adaos_connect.prepare.browser",
                {"mode": "browser", "refresh": True},
            )
            connect_current = ack["data"]["result"]["current"]
            assert connect_current["status"] == "offline"
            assert connect_current["degraded"] is True
            assert connect_current["link"] == ""
            assert connect_current["error"] == "member_link_not_configured"

            ack, _ = _control_command(
                websocket,
                "browser-register-proof",
                "device.register",
                {
                    "device_id": "android-browser-device",
                    "client_id": "android-browser-client",
                    "webspace_id": "desktop",
                    "browser_family": "Chrome",
                    "user_agent": "Android browser proof",
                },
            )
            assert ack["data"]["device_id"] == "android-browser-device"

            ack, _ = _control_command(
                websocket,
                "voice-proof",
                "dialog.user_message",
                {"text": "Привет", "webspace_id": "desktop"},
            )
            assert ack["data"]["accepted"] is True
            assert "локальный ассистент" in ack["data"]["response"]

            ack, _ = _control_command(
                websocket,
                "dialog-agent-nika-proof",
                "dialog.agent.select",
                {
                    "agent_id": "agent:conversation_companions:nika",
                    "webspace_id": "desktop",
                },
            )
            assert ack["data"]["active_agent"]["label"] == "Ника"
            assert ack["data"]["channel_id"] == "conversational"

            ack, _ = _control_command(
                websocket,
                "dialog-nika-turn-proof",
                "dialog.user_message",
                {"text": "статус ноды", "webspace_id": "desktop"},
            )
            assert ack["data"]["active_agent_label"] == "Ника"
            assert ack["data"]["dialog_channel_id"] == "conversational"
            assert "Нода" in ack["data"]["response"]

            ack, _ = _control_command(
                websocket,
                "dialog-builder-channel-proof",
                "dialog.channel.select",
                {"channel_id": "builder", "webspace_id": "desktop"},
            )
            assert ack["data"]["active_agent"]["label"] == "Строитель"

            ack, _ = _control_command(
                websocket,
                "dialog-addressed-arseni-proof",
                "dialog.user_message",
                {"text": "Арсений, привет", "webspace_id": "desktop"},
            )
            assert ack["data"]["active_agent_label"] == "Арсений"
            assert "Я Арсений" in ack["data"]["response"]

            ack, events = _control_command(
                websocket,
                "notebook-stream-proof",
                "webio.stream.snapshot.requested",
                {"webspace_id": "desktop", "receiver": "notebook_skill.notes"},
            )
            assert ack["data"]["snapshot"]["items"]
            assert any(
                event.get("kind") == "webio.stream.desktop.notebook_skill.notes"
                for event in events
            )

            ack, _ = _control_command(
                websocket,
                "taiga-proof",
                "desktop.scenario.set",
                {"webspace_id": "desktop", "scenario_id": "taiga_ui_demo_scenario"},
            )
            assert ack["data"]["scenario_id"] == "taiga_ui_demo_scenario"

            with urllib.request.urlopen(
                f"{base_url}/api/node/yjs/webspaces/desktop/materialization/snapshot",
                timeout=2,
            ) as response:
                taiga_materialization = json.load(response)
            assert taiga_materialization["materialization"]["ready"] is True
            assert taiga_materialization["materialization"]["missing_branches"] == []
            assert (
                taiga_materialization["materialization"]["current_scenario"]
                == "taiga_ui_demo_scenario"
            )
            taiga_application = taiga_materialization["snapshot"]["ui"]["application"]
            assert taiga_application["desktop"]["pageSchema"]["id"] == "taiga_ui_demo"
            assert "apps_catalog" in taiga_application["modals"]
            assert "widgets_catalog" in taiga_application["modals"]
            semantic_views = {
                item["id"]: item["kind"]
                for item in taiga_application["desktop"]["pageSchema"]["semantic"]["views"]
            }
            assert semantic_views["demo_metric_tree"] == "collection_tree"

            ack, events = _control_command(
                websocket,
                "demo-event-proof",
                "demo_metrics.host_action",
                {"action_id": "test", "metric_id": "cpu"},
            )
            assert ack["data"]["result"]["ok"] is True
            assert any(
                event.get("kind") == "webio.stream.desktop.demo_metrics.events"
                for event in events
            )

            ack, _ = _control_command(
                websocket,
                "demo-selection-proof",
                "demo_metrics.selection.changed",
                {"metric_id": "memory"},
            )
            assert ack["data"]["result"]["selection"]["metric_id"] == "memory"

            ack, _ = _control_command(
                websocket,
                "desktop-proof",
                "desktop.webspace.go_home",
                {"webspace_id": "desktop", "wait_for_rebuild": True},
            )
            assert ack["data"]["scenario_id"] == "web_desktop"

            rejected, _ = _control_command(
                websocket,
                "unknown-control-proof",
                "desktop.method.not_implemented",
                {"webspace_id": "desktop"},
            )
            assert rejected["data"]["ok"] is False
            assert rejected["data"]["accepted"] is False
            assert rejected["data"]["error"].startswith(
                "control_command_not_supported_android_poc:"
            )

            ack, _ = _control_command(
                websocket,
                "taiga-http-fallback-proof",
                "desktop.scenario.set",
                {"webspace_id": "desktop", "scenario_id": "taiga_ui_demo_scenario"},
            )
            assert ack["data"]["scenario_id"] == "taiga_ui_demo_scenario"

        request = urllib.request.Request(
            f"{base_url}/api/node/yjs/webspaces/desktop/go-home",
            data=json.dumps({"wait_for_rebuild": True}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Origin": "https://inimatic.com"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            go_home = json.load(response)
        assert go_home["accepted"] is True
        assert go_home["scenario_id"] == "web_desktop"

        with urllib.request.urlopen(
            f"{base_url}/api/node/yjs/webspaces/desktop/materialization/snapshot",
            timeout=2,
        ) as response:
            snapshot = json.load(response)["snapshot"]
        assert snapshot["data"]["weather"]["current"]["source"] == "offline"
        assert snapshot["data"]["weather"]["current"]["request_id"] == "weather-offline-request"
        assert snapshot["data"]["adaos_connect"]["current"]["status"] == "offline"
        assert snapshot["data"]["browsers"]["summary"]["value"] == 0
        assert any(
            item["from"] == "hub" and "локальный ассистент" in item["text"]
            for item in snapshot["data"]["voice_chat"]["messages"]
        )
        assert snapshot["data"]["dialog"]["active_agent"]["label"] == "Арсений"
        assert snapshot["data"]["dialog"]["active_channel_id"] == "conversational"
        assert snapshot["data"]["subnet_env"]["current"]["node_label"] == (
            "Android Proof Node"
        )
        assert snapshot["data"]["desktop"]["notebook"]["editor"]["content"] == (
            "Persistent Android notebook"
        )
        assert snapshot["ui"]["current_scenario"] == "web_desktop"

        code, rejected = _post_json(
            f"{base_url}/api/tools/call",
            {"tool": "arbitrary_skill:run", "arguments": {}},
        )
        assert code == 400
        assert rejected["error"].startswith("skill_not_in_android_descriptor")

        legacy_taiga_application = json.loads(
            json.dumps(bootstrap._skills.taiga_application)
        )
        legacy_taiga_application.pop("modals", None)
        legacy_catalog = json.loads(json.dumps(bootstrap._skills.desktop_catalog))
        legacy_catalog["apps"] = [
            item
            for item in legacy_catalog.get("apps") or []
            if item.get("id") != "android_node_settings_app"
        ]
        legacy_installed = json.loads(json.dumps(bootstrap._skills.desktop_installed))
        legacy_installed["apps"] = [
            item
            for item in legacy_installed.get("apps") or []
            if item != "android_node_settings_app"
        ]
        legacy_registry = json.loads(json.dumps(bootstrap._skills.desktop_registry))
        legacy_registry["merged"]["modals"] = [
            item
            for item in legacy_registry.get("merged", {}).get("modals") or []
            if item != "subnet_env_modal"
        ]
        bootstrap._skills._set_paths(
            {
                "ui/current_scenario": "taiga_ui_demo_scenario",
                "ui/application": legacy_taiga_application,
                "data/catalog": legacy_catalog,
                "data/installed": legacy_installed,
                "registry/merged": legacy_registry["merged"],
                "runtime/environment/materialization/scenario_id": (
                    "taiga_ui_demo_scenario"
                ),
            }
        )
    finally:
        bootstrap.stop()

    restarted = json.loads(bootstrap.start(str(tmp_path), "test", 0))
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{restarted['port']}"
            "/api/node/yjs/webspaces/desktop/materialization/snapshot",
            timeout=2,
        ) as response:
            repaired = json.load(response)
        assert repaired["materialization"]["ready"] is True
        assert repaired["materialization"]["current_scenario"] == (
            "taiga_ui_demo_scenario"
        )
        assert "apps_catalog" in repaired["snapshot"]["ui"]["application"]["modals"]
        assert "widgets_catalog" in repaired["snapshot"]["ui"]["application"]["modals"]
        assert "android_node_settings_app" in {
            item["id"] for item in repaired["snapshot"]["data"]["catalog"]["apps"]
        }
        assert "android_node_settings_app" in repaired["snapshot"]["data"]["installed"][
            "apps"
        ]
        assert "subnet_env_modal" in repaired["snapshot"]["registry"]["merged"]["modals"]
        assert repaired["snapshot"]["data"]["dialog"]["active_agent"]["label"] == (
            "Арсений"
        )

        code, notebook = _post_json(
            f"http://127.0.0.1:{restarted['port']}/api/tools/call",
            {"tool": "notebook_skill:get_notebook_snapshot", "arguments": {}},
        )
        assert code == 200
        assert any(
            item["content"] == "Persistent Android notebook"
            for item in notebook["result"]["items"]
        )
        code, subnet = _post_json(
            f"http://127.0.0.1:{restarted['port']}/api/tools/call",
            {"tool": "subnet_env:get_snapshot", "arguments": {}},
        )
        assert code == 200
        assert subnet["result"]["node_label"] == "Android Proof Node"
    finally:
        bootstrap.stop()
