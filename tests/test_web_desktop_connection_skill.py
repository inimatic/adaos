from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


if "y_py" not in sys.modules:
    sys.modules["y_py"] = types.SimpleNamespace(
        YDoc=type("YDoc", (), {}),
        encode_state_vector=lambda *args, **kwargs: b"",
        encode_state_as_update=lambda *args, **kwargs: b"",
        apply_update=lambda *args, **kwargs: None,
    )
if "ypy_websocket.ystore" not in sys.modules:
    ystore_module = types.ModuleType("ypy_websocket.ystore")
    ystore_module.BaseYStore = type("BaseYStore", (), {})
    ystore_module.YDocNotFound = type("YDocNotFound", (Exception,), {})
    sys.modules["ypy_websocket.ystore"] = ystore_module
if "ypy_websocket" not in sys.modules:
    pkg = types.ModuleType("ypy_websocket")
    pkg.ystore = sys.modules["ypy_websocket.ystore"]
    sys.modules["ypy_websocket"] = pkg


def _load_connection_module():
    root = Path(__file__).resolve().parents[1]
    explicit = str(os.getenv("ADAOS_WEB_DESKTOP_CONNECTION_SKILL") or "").strip()
    candidates = [
        Path(explicit) if explicit else None,
        root
        / ".adaos"
        / "workspace"
        / "skills"
        / "web_desktop_runtime_skill"
        / "handlers"
        / "connect.py",
        *sorted(
            (root / ".adaos" / "dev").glob(
                "*/skills/web_desktop_runtime_skill/handlers/connect.py"
            )
        ),
    ]
    path = next((candidate for candidate in candidates if candidate and candidate.is_file()), None)
    assert path is not None, "web_desktop_runtime_skill connection handler is unavailable"
    module_name = f"test_web_desktop_connection_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _fake_ctx(
    *,
    app_base: str = "https://app.inimatic.com",
    api_base: str = "https://api.inimatic.com",
):
    return SimpleNamespace(
        settings=SimpleNamespace(
            app_base=app_base,
            api_base=api_base,
            subnet_id="sn_test",
            default_hub="sn_test",
        )
    )


def _fake_cfg(
    *, zone_id: str | None = "ru", base_url: str = "https://api.inimatic.com"
):
    return SimpleNamespace(
        zone_id=zone_id,
        subnet_id="sn_test",
        root_state=None,
        root_settings=SimpleNamespace(base_url=base_url, ca_cert="keys/ca.cert"),
        subnet_settings=SimpleNamespace(
            hub=SimpleNamespace(cert="keys/hub_cert.pem", key="keys/hub_private.pem")
        ),
    )


def test_browser_connection_uses_zone_and_public_app_domain(monkeypatch, tmp_path: Path):
    mod = _load_connection_module()
    calls: list[dict[str, object]] = []
    expires_at = 1_800_000_000

    monkeypatch.setattr(mod, "get_ctx", lambda: _fake_ctx())
    monkeypatch.setattr(mod, "load_config", lambda ctx=None: _fake_cfg())
    monkeypatch.setattr(
        mod, "_expand_path", lambda value, fallback: tmp_path / str(value or fallback)
    )

    class FakeRootHttpClient:
        def __init__(self, *, base_url, verify=True, cert=None, **kwargs):
            self.base_url = base_url
            self.cert = cert

        def device_authorize(self, *, payload=None, **kwargs):
            calls.append(
                {
                    "base_url": self.base_url,
                    "cert": self.cert,
                    "payload": payload,
                }
            )
            return {
                "user_code": "PAIR1234",
                "verification_uri_complete": (
                    "https://inimatic.com/?intent=connect.register&zone=ru"
                    "&subnet_id=sn_test&user_code=PAIR1234"
                ),
                "expires_at": expires_at,
            }

    monkeypatch.setattr(mod, "RootHttpClient", FakeRootHttpClient)

    current = mod._browser_current(mod._resolve_context(), request_id="ws-1:1")

    assert calls == [
        {
            "base_url": "https://ru.api.inimatic.com",
            "cert": None,
            "payload": {"owner_id": "sn_test", "zone_id": "ru"},
        }
    ]
    assert current["status"] == "ready"
    assert current["app_base_url"] == "https://inimatic.com"
    assert current["qr_text"] == current["link"]
    assert current["code"] == "PAIR1234"
    assert current["navigation_destination"] == {
        "schema": "adaos.navigation.destination.v1",
        "intent": "connect.register",
        "zone": "ru",
        "subnet_id": "sn_test",
        "user_code": "PAIR1234",
    }


def test_node_connection_falls_back_to_root_token(monkeypatch, tmp_path: Path):
    mod = _load_connection_module()
    calls: list[dict[str, object]] = []
    cert_path = tmp_path / "keys" / "hub_cert.pem"
    key_path = tmp_path / "keys" / "hub_private.pem"
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_text("cert", encoding="utf-8")
    key_path.write_text("key", encoding="utf-8")

    def expand(value, fallback):
        token = str(value or fallback)
        if token.endswith("hub_cert.pem"):
            return cert_path
        if token.endswith("hub_private.pem"):
            return key_path
        return tmp_path / token

    monkeypatch.setattr(mod, "get_ctx", lambda: _fake_ctx())
    monkeypatch.setattr(mod, "load_config", lambda ctx=None: _fake_cfg())
    monkeypatch.setattr(mod, "_expand_path", expand)
    monkeypatch.setenv("ROOT_TOKEN", "root-secret")

    class FakeRootHttpClient:
        def __init__(self, *, base_url, verify=True, cert=None, **kwargs):
            self.base_url = base_url
            self.cert = cert

        def request(self, method, path, *, json=None, headers=None, **kwargs):
            calls.append(
                {
                    "base_url": self.base_url,
                    "cert": self.cert,
                    "path": path,
                    "headers": headers,
                }
            )
            if self.cert is not None:
                raise mod.RootHttpError(
                    "unauthorized",
                    status_code=401,
                    error_code="unauthorized",
                    payload={"ok": False},
                )
            return {"code": "ABCD-EFGH", "expires_at_utc": "2030-01-02T03:04:05Z"}

    monkeypatch.setattr(mod, "RootHttpClient", FakeRootHttpClient)

    current = mod._node_current(mod._resolve_context(), request_id="ws-1:2")

    assert len(calls) == 2
    assert calls[0]["cert"] == (str(cert_path), str(key_path))
    assert calls[1]["headers"] == {"X-Root-Token": "root-secret"}
    assert current["status"] == "ready"
    assert current["code"] == "ABCD-EFGH"
    assert current["qr_text"] == current["link"]
    assert "--zone ru" in current["linux_command"]
    assert "-ZoneId 'ru'" in current["windows_ps_command"]


@pytest.mark.asyncio
async def test_prepare_connection_is_permission_gated_and_returns_result(monkeypatch):
    mod = _load_connection_module()
    required: list[str] = []
    monkeypatch.setattr(mod.sdk_access, "require", required.append)
    monkeypatch.setattr(
        mod,
        "_resolve_context",
        lambda: {
            "cfg": SimpleNamespace(),
            "hub_id": "sn_test",
            "zone_id": "ru",
            "verify": True,
            "cert_tuple": None,
            "root_base_url": "https://ru.api.inimatic.com",
            "app_base_url": "https://inimatic.com",
        },
    )
    monkeypatch.setattr(
        mod,
        "_prepare_current",
        lambda mode, context, *, request_id: {
            "mode": mode,
            "request_id": request_id,
            "status": "ready",
            "code": "DONE",
        },
    )

    result = await mod.prepare_connection(
        mode="node", webspace_id="desktop", refresh=True, force_new=True
    )

    assert required == ["workspace.write"]
    assert result["ok"] is True
    assert result["current"]["status"] == "ready"
    assert result["current"]["code"] == "DONE"


def test_connection_cache_prunes_expired_entries_and_stays_bounded(monkeypatch):
    mod = _load_connection_module()
    now = 1_800_000_000.0
    monkeypatch.setattr(mod.time, "time", lambda: now)
    context = {
        "hub_id": "sn_test",
        "zone_id": "ru",
        "root_base_url": "https://ru.api.inimatic.com",
        "app_base_url": "https://inimatic.com",
    }

    for index in range(mod._PREPARE_CACHE_MAX_ITEMS + 20):
        current = {"expires_at_epoch": now + index + 1, "code": f"CACHE-{index}"}
        mod._cache_current(f"ws-{index}", "browser", context, current)

    assert len(mod._prepare_cache) == mod._PREPARE_CACHE_MAX_ITEMS
    assert ("ws-0", "browser") not in mod._prepare_cache

    monkeypatch.setattr(
        mod.time, "time", lambda: now + mod._PREPARE_CACHE_MAX_ITEMS + 20
    )
    mod._prune_prepare_cache()
    assert mod._prepare_cache == {}
