from __future__ import annotations

import asyncio
import importlib
import sys
import types
from types import SimpleNamespace

y_py_module = sys.modules.get("y_py")
if y_py_module is None:
    y_py_module = types.SimpleNamespace()
    sys.modules["y_py"] = y_py_module
if not hasattr(y_py_module, "YDoc"):
    y_py_module.YDoc = type("YDoc", (), {})
if not hasattr(y_py_module, "YMap"):
    y_py_module.YMap = type("YMap", (), {})
if not hasattr(y_py_module, "YArray"):
    y_py_module.YArray = type("YArray", (), {})
if not hasattr(y_py_module, "encode_state_vector"):
    y_py_module.encode_state_vector = lambda *args, **kwargs: b""
if not hasattr(y_py_module, "encode_state_as_update"):
    y_py_module.encode_state_as_update = lambda *args, **kwargs: b""
if not hasattr(y_py_module, "apply_update"):
    y_py_module.apply_update = lambda *args, **kwargs: None
if "ypy_websocket.ystore" not in sys.modules:
    ystore_module = types.ModuleType("ypy_websocket.ystore")
    ystore_module.BaseYStore = type("BaseYStore", (), {})
    ystore_module.YDocNotFound = type("YDocNotFound", (Exception,), {})
    sys.modules["ypy_websocket.ystore"] = ystore_module
if "ypy_websocket" not in sys.modules:
    pkg = types.ModuleType("ypy_websocket")
    pkg.ystore = sys.modules["ypy_websocket.ystore"]
    sys.modules["ypy_websocket"] = pkg

mod = importlib.import_module("adaos.services.subnet.link_client")


def test_member_link_client_skips_hub_follow_when_node_config_disables_updates(monkeypatch) -> None:
    client = mod.MemberLinkClient()
    monkeypatch.delenv("ADAOS_MEMBER_FOLLOW_HUB_UPDATE", raising=False)
    monkeypatch.setattr(mod, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(core_update_enabled=False)))

    def _fail_post_local_admin(*_args, **_kwargs):
        raise AssertionError("local admin must not be called")

    monkeypatch.setattr(mod.MemberLinkClient, "_post_local_admin", staticmethod(_fail_post_local_admin))

    asyncio.run(
        client._follow_hub_core_update(
            {
                "state": "countdown",
                "action": "update",
                "target_rev": "rev2026",
                "target_version": "abc123",
            }
        )
    )

    assert client._last_follow_key == ""
    assert client._last_follow_result == {}
