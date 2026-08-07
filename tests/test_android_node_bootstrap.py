from __future__ import annotations

import json
import types
import urllib.request
from pathlib import Path


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
    module = types.ModuleType("adaos_android_bootstrap_test")
    module.__file__ = str(BOOTSTRAP_PATH)
    source = BOOTSTRAP_PATH.read_text(encoding="utf-8")
    exec(compile(source, str(BOOTSTRAP_PATH), "exec"), module.__dict__)
    return module


def test_loopback_sentinel_persists_identity_and_reports_member_status(tmp_path: Path) -> None:
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
        assert status["runtime"]["yjs_ready"] is False
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
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 204
            assert response.headers["Access-Control-Allow-Origin"] == "https://inimatic.com"
            assert response.headers["Access-Control-Allow-Private-Network"] == "true"
    finally:
        bootstrap.stop()
