from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
import types
import urllib.request
from pathlib import Path

from websockets.sync.client import connect


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
            "weather_app",
            "adaos_connect_app",
            "notebook_skill_app",
            "scenario:taiga_ui_demo_scenario",
        } <= app_ids
        assert snapshot["data"]["installed"]["apps"]
        assert snapshot["data"]["nodes"] == {}
        assert "weather_modal" in snapshot["ui"]["application"]["modals"]
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
                        if status["yjs_update_count"] == 1 and status["yjs_revision"] >= 2:
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
