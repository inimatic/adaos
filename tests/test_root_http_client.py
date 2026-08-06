from __future__ import annotations

import pytest

from adaos.services.root import client as root_client_module
from adaos.services.root.client import RootHttpClient, RootHttpError


class _FakeRootHttpLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def debug(self, *args) -> None:
        self.calls.append(("debug", args))

    def info(self, *args) -> None:
        self.calls.append(("info", args))

    def warning(self, *args, **kwargs) -> None:  # noqa: ARG002
        self.calls.append(("warning", args))


def test_routine_control_report_success_logs_debug(monkeypatch) -> None:
    class _Response:
        status_code = 202
        content = b'{"ok": true}'
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, *args, **kwargs):  # noqa: ARG002
            return _Response()

    monkeypatch.setattr(root_client_module.httpx, "Client", _Client)
    fake_logger = _FakeRootHttpLogger()
    monkeypatch.setattr(root_client_module, "_ROOT_HTTP_LOG", fake_logger)
    client = RootHttpClient(base_url="https://api.example.test")

    assert client.request("POST", "/v1/hub/control/report") == {"ok": True}

    assert fake_logger.calls
    assert fake_logger.calls[-1][0] == "debug"


def test_non_report_control_success_keeps_info(monkeypatch) -> None:
    class _Response:
        status_code = 202
        content = b'{"ok": true}'
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, *args, **kwargs):  # noqa: ARG002
            return _Response()

    monkeypatch.setattr(root_client_module.httpx, "Client", _Client)
    fake_logger = _FakeRootHttpLogger()
    monkeypatch.setattr(root_client_module, "_ROOT_HTTP_LOG", fake_logger)
    client = RootHttpClient(base_url="https://api.example.test")

    assert client.request("POST", "/v1/hub/control/other") == {"ok": True}

    assert fake_logger.calls
    assert fake_logger.calls[-1][0] == "info"


def test_binary_response_preserves_bytes_without_text_decoding(monkeypatch) -> None:
    archive = b"PK\x03\x04\xff\x00artifact"

    class _Response:
        status_code = 200
        content = archive
        text = "must not be used"

        def json(self):
            raise AssertionError("binary success must not be decoded as JSON")

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, *args, **kwargs):  # noqa: ARG002
            return _Response()

    monkeypatch.setattr(root_client_module.httpx, "Client", _Client)

    assert RootHttpClient(base_url="https://api.example.test").request(
        "GET",
        "/v1/artifacts/packages/digest/content",
        response_bytes=True,
    ) == archive


def test_binary_response_keeps_structured_error_contract(monkeypatch) -> None:
    class _Response:
        status_code = 404
        content = b'{"error":"package_not_found"}'
        text = '{"error":"package_not_found"}'

        def json(self):
            return {"error": "package_not_found"}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, *args, **kwargs):  # noqa: ARG002
            return _Response()

    monkeypatch.setattr(root_client_module.httpx, "Client", _Client)

    with pytest.raises(RootHttpError) as raised:
        RootHttpClient(base_url="https://api.example.test").request(
            "GET",
            "/v1/artifacts/packages/digest/content",
            response_bytes=True,
        )

    assert raised.value.status_code == 404
    assert raised.value.error_code == "package_not_found"


def test_hub_control_report_uses_short_configurable_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        status_code = 202
        content = b'{"ok": true}'
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    class _Client:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, *args, **kwargs):  # noqa: ARG002
            return _Response()

    monkeypatch.setenv("ADAOS_HUB_CONTROL_REPORT_TIMEOUT_S", "1.25")
    monkeypatch.setattr(root_client_module.httpx, "Client", _Client)
    client = RootHttpClient(base_url="https://api.example.test")

    assert client.hub_control_report(payload={"ok": True}) == {"ok": True}
    assert captured["timeout"] == 1.25


def test_hub_lifecycle_report_uses_dedicated_endpoint_and_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _request(_self, method, path, **kwargs):
        captured.update(method=method, path=path, **kwargs)
        return {"ok": True}

    monkeypatch.setenv("ADAOS_HUB_LIFECYCLE_REPORT_TIMEOUT_S", "1.5")
    monkeypatch.setattr(RootHttpClient, "_request", _request)
    client = RootHttpClient(base_url="https://api.example.test")

    assert client.hub_lifecycle_report(payload={"revision": 7}) == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/hub/lifecycle/report"
    assert captured["json"] == {"revision": 7}
    assert captured["timeout"] == 1.5


def test_draft_push_sends_vcs_commit_message(monkeypatch) -> None:
    captured: list[tuple[str, str, dict]] = []
    client = RootHttpClient(base_url="https://api.example.test")

    def _request(_self, method, path, **kwargs):
        captured.append((method, path, dict(kwargs.get("json") or {})))
        return {"ok": True, "stored_path": "draft/path", "commit": "abc123"}

    monkeypatch.setattr(RootHttpClient, "_request", _request)

    client.push_skill_draft(
        name="demo_skill",
        archive_b64="eA==",
        node_id="node-1",
        sha256="sha",
        message="Builder generated a form",
    )
    client.push_scenario_draft(
        name="demo_scenario",
        archive_b64="eA==",
        node_id="node-1",
        sha256="sha",
        message="Builder generated a dashboard",
    )

    assert captured[0][1:] == (
        "/v1/skills/draft",
        {
            "name": "demo_skill",
            "archive_b64": "eA==",
            "node_id": "node-1",
            "sha256": "sha",
            "message": "Builder generated a form",
        },
    )
    assert captured[1][1:] == (
        "/v1/scenarios/draft",
        {
            "name": "demo_scenario",
            "archive_b64": "eA==",
            "node_id": "node-1",
            "sha256": "sha",
            "message": "Builder generated a dashboard",
        },
    )
