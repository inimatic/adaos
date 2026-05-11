from __future__ import annotations

import importlib.util
import json
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


def test_weather_city_changed_projects_without_blocking_sync_ctx_set(monkeypatch):
    mod = _load_weather_module()
    projected: list[tuple[str, dict, str | None]] = []

    class _CtxSubnet:
        def set(self, *_args, **_kwargs):
            raise AssertionError("async weather handler must not call sync ctx_subnet.set")

        async def set_async(self, slot, payload, webspace_id=None):
            projected.append((slot, payload, webspace_id))

    async def _fetch_weather_async(*_args, **_kwargs):
        return True, {"temp": 10, "description": "clear", "wind_ms": 1}

    monkeypatch.setattr(mod, "set_current_skill", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mod, "_load_skill_data_projections", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mod, "_load_config", lambda: ("https://example.test", None))
    monkeypatch.setattr(mod, "_fetch_weather_async", _fetch_weather_async)
    monkeypatch.setattr(
        mod,
        "get_ctx",
        lambda: SimpleNamespace(config=SimpleNamespace(node_id="member-local")),
    )
    monkeypatch.setattr(mod, "ctx_subnet", _CtxSubnet())

    import asyncio

    async def _run():
        await mod.on_weather_city_changed(
            {
                "city": "Berlin",
                "webspace_id": "desktop",
                "target_node_id": "member-local",
                "_meta": {"target_node_id": "member-local"},
            }
        )
        tasks = list(mod._WEATHER_UPDATE_TASKS.values())
        if tasks:
            await asyncio.gather(*tasks)

    asyncio.run(_run())

    assert [entry[0] for entry in projected] == ["weather.snapshot", "weather.snapshot"]
    assert [entry[1].get("status") for entry in projected] == ["refreshing", "ok"]
    assert {entry[2] for entry in projected} == {"desktop"}
    assert projected[-1][1]["current"]["source"] == "api"


def test_weather_legacy_openweathermap_endpoint_uses_open_meteo(monkeypatch):
    mod = _load_weather_module()
    request: dict[str, object] = {}

    class _Response:
        status_code = 200

        def json(self):
            return {"current": {"temperature_2m": 11.25, "wind_speed_10m": 2.5}}

    def _get(url, *, params=None, timeout=None):
        request["url"] = url
        request["params"] = params
        request["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(mod.requests, "get", _get)

    ok, data = mod._fetch_weather("https://api.openweathermap.org/data/2.5/weather", "Moscow")

    assert ok is True
    assert request["url"] == mod.DEFAULT_API_ENDPOINT
    assert request["params"]["latitude"] == 55.75
    assert request["params"]["longitude"] == 37.62
    assert request["params"]["current"] == "temperature_2m,wind_speed_10m"
    assert data["temp_c"] == 11.25
    assert data["wind_ms"] == 2.5


def test_weather_config_migrates_legacy_openweathermap_endpoint(monkeypatch):
    mod = _load_weather_module()
    memory = {
        "api_entry_point": "https://api.openweathermap.org/data/2.5/weather",
        "default_city": "Berlin",
    }

    monkeypatch.setattr(mod, "memory_get", lambda key: memory.get(key))
    monkeypatch.setattr(mod, "memory_set", lambda key, value: memory.__setitem__(key, value))
    monkeypatch.setattr(mod, "get_current_skill", lambda: None)

    api_entry_point, default_city = mod._load_config()

    assert api_entry_point == mod.DEFAULT_API_ENDPOINT
    assert memory["api_entry_point"] == mod.DEFAULT_API_ENDPOINT
    assert default_city == "Berlin"


def test_weather_async_fetch_preserves_skill_i18n_in_worker_thread(monkeypatch):
    mod = _load_weather_module()
    from adaos.services.agent_context import get_ctx

    ctx = get_ctx()
    skill_dir = ctx.paths.skills_workspace_dir() / "weather_skill"
    (skill_dir / "i18n").mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(
        "name: weather_skill\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    (skill_dir / "i18n" / "en.json").write_text(
        json.dumps(
            {
                "runtime.weather.errors.status": "Weather API returned status {status}",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class _Response:
        status_code = 503

        def json(self):
            return {}

    monkeypatch.setattr(mod.requests, "get", lambda *_args, **_kwargs: _Response())

    import asyncio

    ok, data = asyncio.run(mod._fetch_weather_async("https://example.test", "Berlin"))

    assert ok is False
    assert data["error"] == "Weather API returned status 503"
