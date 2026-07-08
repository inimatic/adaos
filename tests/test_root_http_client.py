from __future__ import annotations

from adaos.services.root import client as root_client_module
from adaos.services.root.client import RootHttpClient


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
