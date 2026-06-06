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


def _load_infrastate_module():
    root = Path(__file__).resolve().parents[1]
    path = root / ".adaos" / "workspace" / "skills" / "infrastate_skill" / "handlers" / "main.py"
    module_name = f"test_infrastate_core_widget_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_infrastate_core_slot_summary_includes_version_and_commit() -> None:
    mod = _load_infrastate_module()

    subtitle = mod._core_slot_summary_subtitle(
        {
            "active_slot": "B",
            "active_manifest": {
                "slot": "B",
                "build_version": "0.1.15+44.9e172ff",
                "git_short_commit": "9e172ff",
            },
        },
        {"version": "0.1.64"},
    )

    assert subtitle == "slot B | 0.1.15 | 9e172ff"


def test_infrastate_core_slot_summary_prefers_running_version_for_legacy_manifest() -> None:
    mod = _load_infrastate_module()

    subtitle = mod._core_slot_summary_subtitle(
        {
            "active_slot": "A",
            "active_manifest": {
                "slot": "A",
                "target_version": "0.1.0",
                "git_short_commit": "6b63485",
            },
        },
        {"version": "0.1.64", "runtime_build_version": "0.1.64+141.6b63485"},
    )

    assert subtitle == "slot A | 0.1.64 | 6b63485"


def test_infrastate_core_slot_summary_uses_running_version_for_stale_default_manifest() -> None:
    mod = _load_infrastate_module()

    subtitle = mod._core_slot_summary_subtitle(
        {
            "active_slot": "B",
            "active_manifest": {
                "slot": "B",
                "build_version": "0.1.0+1.b10da50",
                "git_short_commit": "b10da50",
            },
        },
        {"version": "0.1.217", "runtime_build_version": "0.1.217+1.b10da50"},
    )

    assert subtitle == "slot B | 0.1.217 | b10da50"


def test_infrastate_core_slot_summary_infers_bumped_version_from_stale_manifest_subject() -> None:
    mod = _load_infrastate_module()

    subtitle = mod._core_slot_summary_subtitle(
        {
            "active_slot": "B",
            "active_manifest": {
                "slot": "B",
                "build_version": "0.1.0+1.b10da50",
                "git_short_commit": "b10da50",
                "git_subject": "chore: bump adaos version to 0.1.217",
            },
        },
        {"version": "0.1.0"},
    )

    assert subtitle == "slot B | 0.1.217 | b10da50"
