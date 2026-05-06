from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4


def _load_browsers_skill_module():
    if "adaos.sdk.data.ctx" not in sys.modules:
        fake_ctx = types.ModuleType("adaos.sdk.data.ctx")

        class _FakeSubnet:
            def set(self, slot, value, *, webspace_id=None):
                return None

            async def set_async(self, slot, value, *, webspace_id=None):
                return None

        fake_ctx.subnet = _FakeSubnet()
        fake_ctx.current_user = object()
        fake_ctx.selected_user = object()
        sys.modules["adaos.sdk.data.ctx"] = fake_ctx

    if "adaos.services.workspaces.index" not in sys.modules:
        fake_index = types.ModuleType("adaos.services.workspaces.index")
        fake_index.list_workspaces = lambda: []
        sys.modules["adaos.services.workspaces.index"] = fake_index
        if "adaos.services.workspaces" not in sys.modules:
            fake_pkg = types.ModuleType("adaos.services.workspaces")
            fake_pkg.index = fake_index
            sys.modules["adaos.services.workspaces"] = fake_pkg

    root = Path(__file__).resolve().parents[1]
    path = root / ".adaos" / "workspace" / "skills" / "browsers_skill" / "handlers" / "main.py"
    module_name = f"test_browsers_skill_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_browsers_skill_detach_link_refreshes_snapshot_without_nameerror(monkeypatch) -> None:
    mod = _load_browsers_skill_module()
    mod._SELECTED_BROWSER_BY_WS.clear()
    mod._SELECTED_BROWSER_BY_WS["default"] = "missing-browser"

    browser_entry = {
        "id": "browser-1",
        "display_name": "Living room browser",
        "hostname": "tv-browser",
        "access_class": "device",
        "lifetime_mode": "permanent",
        "last_webspace_id": "desktop",
        "last_seen_at": 1715000000.0,
        "online": True,
    }
    published: list[tuple[str, str | None, object]] = []

    async def _fake_set_async(slot, value, *, webspace_id=None):
        published.append((slot, webspace_id, value))

    monkeypatch.setattr(mod.ctx_subnet, "set_async", _fake_set_async)
    monkeypatch.setattr(
        mod.workspace_index,
        "list_workspaces",
        lambda: [
            SimpleNamespace(workspace_id="desktop"),
            SimpleNamespace(workspace_id="default"),
        ],
    )
    monkeypatch.setattr(mod.sdk_access_links, "list_browser_links", lambda: [dict(browser_entry)])
    monkeypatch.setattr(
        mod.sdk_access_links,
        "get_browser_link",
        lambda device_id: dict(browser_entry) if str(device_id or "").strip() == "browser-1" else None,
    )
    monkeypatch.setattr(mod.sdk_access_links, "lifetime_label", lambda _entry: "Permanent")
    monkeypatch.setattr(
        mod.sdk_device_access,
        "detach_device",
        lambda device_ref: {"ok": True, "device_ref": str(device_ref or "").strip(), "entry": {"revoked": True}},
    )

    result = mod.detach_link(node_id="member-1", webspace_id="desktop")

    assert result["ok"] is True
    assert result["device_ref"] == "member:member-1"
    assert mod._SELECTED_BROWSER_BY_WS["default"] == "browser-1"
    assert any(slot == "browsers.current_name" and webspace_id == "default" for slot, webspace_id, _value in published)
    assert any(slot == "browsers.current_name" and webspace_id == "desktop" for slot, webspace_id, _value in published)
