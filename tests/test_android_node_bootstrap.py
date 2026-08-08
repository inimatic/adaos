from __future__ import annotations

import importlib.util
import json
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
        del client
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


def test_android_member_join_reconnect_and_bidirectional_yjs(tmp_path: Path) -> None:
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
                evidence.snapshot()["yjs_update_total"] >= 1
                and snapshot.get("runtime", {}).get("member_hub_probe")
                == "received-from-protocol-hub"
            )

        _wait_until(converged)
        with urllib.request.urlopen(f"{base_url}/api/node/status", timeout=2) as response:
            status = json.load(response)
        assert status["subnet_id"] == "test-member-subnet"
        assert status["connected_to_hub"] is True
        assert status["runtime"]["member_link"]["token_present"] is True
        assert "test-member-token" not in json.dumps(status)

        code, disconnected = _post_json(
            f"{base_url}/api/node/member/disconnect",
            {"forget": True},
        )
        assert code == 200
        assert disconnected["result"]["current"]["configured"] is False
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
            "notebook_skill_app",
            "scenario:taiga_ui_demo_scenario",
        } <= app_ids
        assert snapshot["data"]["installed"]["apps"]
        assert snapshot["data"]["nodes"] == {}
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
            assert ack["data"]["result"]["current"]["status"] == "offline"

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
        bootstrap._skills._set_paths(
            {
                "ui/current_scenario": "taiga_ui_demo_scenario",
                "ui/application": legacy_taiga_application,
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
