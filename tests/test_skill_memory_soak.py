from __future__ import annotations

import gc
import importlib.util
import inspect
import sys
import threading
import types
from pathlib import Path
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


_SKILL_HANDLERS = [
    ("infrastate_skill", "infrastate_runtime_dispose"),
    ("infrascope_skill", "infrascope_runtime_dispose"),
    ("browsers_skill", "browsers_runtime_dispose"),
    ("infra_access_skill", "infra_access_runtime_dispose"),
    ("voice_chat_skill", None),
]
_THREAD_PREFIXES = (
    "infrastate-projection",
    "infrastate-stream",
    "browsers-projection",
    "infra-access-projection",
)


def _handler_path(skill_id: str) -> Path:
    root = Path(__file__).resolve().parents[1]
    return root / ".adaos" / "workspace" / "skills" / skill_id / "handlers" / "main.py"


def _load_handler(skill_id: str):
    module_name = f"test_reload_soak_{skill_id}_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, _handler_path(skill_id))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    except Exception:
        sys.modules.pop(module_name, None)
        raise


def _touch_lazy_executors(module) -> None:
    for attr in ("_projection_executor", "_stream_snapshot_executor"):
        factory = getattr(module, attr, None)
        if not callable(factory):
            continue
        executor = factory()
        future = executor.submit(lambda: None)
        future.result(timeout=2)


def _dispose_handler(module, dispose_name: str | None) -> None:
    cleanup = getattr(module, "_cleanup_runtime_state", None)
    if callable(cleanup):
        kwargs = {"reason": "reload_rss_soak"}
        if "wait" in inspect.signature(cleanup).parameters:
            kwargs["wait"] = True
        cleanup(**kwargs)
        return
    dispose = getattr(module, dispose_name or "", None)
    if callable(dispose):
        dispose(reason="reload_rss_soak")


def _load_dispose_cycle() -> None:
    for skill_id, dispose_name in _SKILL_HANDLERS:
        module = _load_handler(skill_id)
        module_name = module.__name__
        try:
            _touch_lazy_executors(module)
            _dispose_handler(module, dispose_name)
        finally:
            sys.modules.pop(module_name, None)
            del module
    gc.collect()


def _skill_threads_alive() -> list[str]:
    return [
        thread.name
        for thread in threading.enumerate()
        if any(thread.name.startswith(prefix) for prefix in _THREAD_PREFIXES)
    ]


def test_live_skill_reload_rss_soak_keeps_runtime_state_bounded() -> None:
    psutil = pytest.importorskip("psutil")
    process = psutil.Process()

    _load_dispose_cycle()
    gc.collect()
    baseline_rss = int(process.memory_info().rss)

    for _ in range(5):
        _load_dispose_cycle()

    gc.collect()
    final_rss = int(process.memory_info().rss)

    assert _skill_threads_alive() == []
    assert final_rss - baseline_rss <= 64 * 1024 * 1024
