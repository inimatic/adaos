from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4


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


def _load_weather_module():
    root = Path(__file__).resolve().parents[1]
    path = root / ".adaos" / "workspace" / "skills" / "weather_skill" / "handlers" / "main.py"
    module_name = f"test_weather_skill_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_weather_city_changed_ignores_other_target_node(monkeypatch):
    mod = _load_weather_module()
    projected: list[tuple[str, object, str | None]] = []

    monkeypatch.setattr(mod, "set_current_skill", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mod, "_load_skill_data_projections", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mod, "_load_config", lambda: ("https://example.test", None))
    monkeypatch.setattr(mod, "_fetch_weather_async", lambda *_args, **_kwargs: (True, {"temp": 10, "description": "clear", "wind_ms": 1}))
    monkeypatch.setattr(
        mod,
        "get_ctx",
        lambda: SimpleNamespace(config=SimpleNamespace(node_id="member-local")),
    )
    monkeypatch.setattr(
        mod,
        "ctx_subnet",
        SimpleNamespace(set=lambda slot, payload, webspace_id=None: projected.append((slot, payload, webspace_id))),
    )

    import asyncio

    asyncio.run(
        mod.on_weather_city_changed(
            {
                "city": "Berlin",
                "webspace_id": "desktop",
                "target_node_id": "member-remote",
                "_meta": {"target_node_id": "member-remote"},
            }
        )
    )

    assert projected == []
