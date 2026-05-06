from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
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


def _load_voice_chat_module():
    root = Path(__file__).resolve().parents[1]
    path = root / ".adaos" / "workspace" / "skills" / "voice_chat_skill" / "handlers" / "main.py"
    module_name = f"test_voice_chat_skill_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_voice_chat_get_snapshot_reads_node_scoped_messages(monkeypatch):
    mod = _load_voice_chat_module()

    class _Map:
        def __init__(self, payload):
            self._payload = payload

        def to_json(self):
            return self._payload

    class _Doc:
        def get_map(self, name: str):
            assert name == "data"
            return _Map({
                "nodes": {
                    "member-01": {
                        "voice_chat": {
                            "messages": [
                                {"id": "m-1", "from": "user", "text": "weather in Berlin"},
                            ],
                            "last_refresh_ts": 123.0,
                        }
                    }
                }
            })

    class _Ctx:
        def __enter__(self):
            return _Doc()

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(mod, "get_ydoc", lambda *_args, **_kwargs: _Ctx())

    snapshot = mod.get_snapshot(webspace_id="desktop", target_node_id="member-01")

    assert snapshot["voice_chat"]["messages"][0]["text"] == "weather in Berlin"
    assert snapshot["messages"][0]["text"] == "weather in Berlin"
    assert snapshot["last_refresh_ts"] == 123.0


def test_voice_chat_skill_yaml_exports_get_snapshot():
    root = Path(__file__).resolve().parents[1]
    raw = (
        root
        / ".adaos"
        / "workspace"
        / "skills"
        / "voice_chat_skill"
        / "skill.yaml"
    ).read_text(encoding="utf-8")

    assert "name: \"get_snapshot\"" in raw
