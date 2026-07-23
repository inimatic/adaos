from __future__ import annotations

import sys
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api.auth import require_token


class _FakeDoc:
    def __init__(self, state: dict[str, dict[str, object]]) -> None:
        self._state = state

    def get_map(self, name: str) -> dict[str, object]:
        return self._state.setdefault(name, {})


class _FakeAsyncDoc:
    def __init__(self, state: dict[str, dict[str, object]]) -> None:
        self._state = state

    async def __aenter__(self) -> _FakeDoc:
        return _FakeDoc(self._state)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _make_client(monkeypatch, state: dict[str, dict[str, object]]) -> TestClient:
    fake_y_py = types.SimpleNamespace(
        YDoc=type("YDoc", (), {}),
        apply_update=lambda *args, **kwargs: None,
    )
    sys.modules.setdefault("y_py", fake_y_py)
    fake_ystore_module = types.ModuleType("ypy_websocket.ystore")
    fake_ystore_module.BaseYStore = object
    fake_ystore_module.YDocNotFound = RuntimeError
    fake_ypy_websocket = types.ModuleType("ypy_websocket")
    fake_ypy_websocket.ystore = fake_ystore_module
    sys.modules.setdefault("ypy_websocket", fake_ypy_websocket)
    sys.modules.setdefault("ypy_websocket.ystore", fake_ystore_module)

    from adaos.apps.api import node_api

    monkeypatch.setattr(node_api, "async_read_ydoc", lambda *args, **kwargs: _FakeAsyncDoc(state))
    app = FastAPI()
    app.include_router(node_api.router, prefix="/api/node")
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def test_node_ui_contract_diagnostics_returns_materialized_issues_and_catalog(monkeypatch) -> None:
    state = {
        "ui": {
            "application": {
                "diagnostics": {
                    "webui_contract": {
                        "status": "invalid",
                        "error_count": 1,
                        "warning_count": 0,
                        "issues": [
                            {
                                "level": "error",
                                "code": "webui.modal.domain.state_route_unknown",
                                "message": "broken route",
                            }
                        ],
                    }
                }
            }
        }
    }
    client = _make_client(monkeypatch, state)

    response = client.get("/api/node/ui/contract-diagnostics", params={"webspace_id": "desktop"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == "adaos.webui.contract_diagnostics.v1"
    assert payload["webspace_id"] == "desktop"
    assert payload["status"] == "invalid"
    assert payload["summary"]["error_count"] == 1
    assert payload["issues"][0]["code"] == "webui.modal.domain.state_route_unknown"
    assert payload["catalog"]["webui.modal.domain.state_route_unknown"]["owner"] == "skill"


def test_node_ui_contract_diagnostics_reports_missing_without_catalog(monkeypatch) -> None:
    client = _make_client(monkeypatch, {"ui": {"application": {}}})

    response = client.get(
        "/api/node/ui/contract-diagnostics",
        params={"webspace_id": "desktop", "include_catalog": "false"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "missing"
    assert payload["materialized"] is False
    assert payload["issues"] == []
    assert "catalog" not in payload
