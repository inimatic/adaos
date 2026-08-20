from __future__ import annotations

import importlib
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from adaos.skills import runtime_runner as runtime_runner_module
from adaos.sdk.core import decorators as sdk_decorators
from adaos.services import skills_loader_importlib as skills_loader_module


def _write_skill(root: Path, name: str, marker: str) -> Path:
    skill_dir = root / name
    (skill_dir / "handlers").mkdir(parents=True, exist_ok=True)
    (skill_dir / "handlers" / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "handlers" / "main.py").write_text(
        "def get_snapshot(**kwargs):\n"
        f"    return {{'skill': '{name}', 'marker': '{marker}', 'kwargs': dict(kwargs)}}\n",
        encoding="utf-8",
    )
    return skill_dir


def _write_bare_tool_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    (skill_dir / "handlers").mkdir(parents=True, exist_ok=True)
    (skill_dir / "handlers" / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "handlers" / "main.py").write_text(
        "from adaos.sdk.core.decorators import tool\n\n"
        "@tool\n"
        "def detach_link(node_id=None, target_node_id=None, webspace_id=None):\n"
        "    return {\n"
        "        'node_id': node_id,\n"
        "        'target_node_id': target_node_id,\n"
        "        'webspace_id': webspace_id,\n"
        "    }\n\n"
        "@tool\n"
        "def refresh_snapshot(webspace_id=None):\n"
        "    return {'webspace_id': webspace_id}\n\n"
        "@tool\n"
        "def ping():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    return skill_dir


def _write_skill_with_service_helper(root: Path, name: str, marker: str) -> Path:
    skill_dir = root / name
    (skill_dir / "handlers").mkdir(parents=True, exist_ok=True)
    (skill_dir / "service").mkdir(parents=True, exist_ok=True)
    (skill_dir / "handlers" / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "service" / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "service" / "helper.py").write_text(f"MARKER = '{marker}'\n", encoding="utf-8")
    (skill_dir / "handlers" / "main.py").write_text(
        "from service.helper import MARKER\n\n"
        "def get_snapshot(**kwargs):\n"
        "    return {'marker': MARKER}\n",
        encoding="utf-8",
    )
    return skill_dir


def _write_skill_with_shared_package_name(root: Path, name: str, marker: str) -> Path:
    skill_dir = root / name
    (skill_dir / "handlers").mkdir(parents=True, exist_ok=True)
    (skill_dir / "research").mkdir(parents=True, exist_ok=True)
    (skill_dir / "handlers" / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "research" / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "research" / "local.py").write_text(f"MARKER = '{marker}'\n", encoding="utf-8")
    (skill_dir / "handlers" / "main.py").write_text(
        "from research.local import MARKER\n\n"
        "def get_snapshot():\n"
        "    return {'marker': MARKER}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_execute_tool_isolates_generic_handlers_main_between_skills(tmp_path: Path) -> None:
    alpha = _write_skill(tmp_path, "alpha_skill", "alpha")
    beta = _write_skill(tmp_path, "beta_skill", "beta")

    before = {key: sys.modules[key] for key in list(sys.modules.keys()) if key == "handlers" or key.startswith("handlers.")}
    try:
        first = runtime_runner_module.execute_tool(alpha, module="handlers.main", attr="get_snapshot", payload={"city": "Berlin"})
        second = runtime_runner_module.execute_tool(beta, module="handlers.main", attr="get_snapshot", payload={"city": "Moscow"})
    finally:
        for key in list(sys.modules.keys()):
            if key == "handlers" or key.startswith("handlers."):
                sys.modules.pop(key, None)
        sys.modules.update(before)

    assert first["skill"] == "alpha_skill"
    assert second["skill"] == "beta_skill"
    assert second["marker"] == "beta"


def test_execute_tool_reuses_active_subscription_module_for_lifecycle_state(tmp_path: Path) -> None:
    skill_dir = tmp_path / "stateful_skill"
    handler = skill_dir / "handlers" / "main.py"
    handler.parent.mkdir(parents=True)
    handler.write_text(
        "STATE = {'active': 0}\n"
        "def start_worker():\n"
        "    STATE['active'] += 1\n"
        "def drain(**_kwargs):\n"
        "    previous = STATE['active']\n"
        "    STATE['active'] = 0\n"
        "    return {'previous': previous, 'active': STATE['active']}\n",
        encoding="utf-8",
    )
    before_sources = dict(skills_loader_module._LOADED_HANDLER_SOURCES)
    before_modules = dict(sys.modules)
    before_snapshots = dict(runtime_runner_module._SKILL_SOURCE_SNAPSHOTS)
    try:
        loader = object.__new__(skills_loader_module.ImportlibSkillsLoader)
        loader._load_handler(handler)
        loaded = skills_loader_module.loaded_handler_module_for_path(handler)
        assert loaded is not None
        loaded.start_worker()

        result = runtime_runner_module.execute_tool(
            skill_dir,
            module="handlers.main",
            attr="drain",
            payload={"reason": "test"},
        )

        assert result == {"previous": 1, "active": 0}
        assert loaded.STATE == {"active": 0}
    finally:
        runtime_runner_module._SKILL_SOURCE_SNAPSHOTS.clear()
        runtime_runner_module._SKILL_SOURCE_SNAPSHOTS.update(before_snapshots)
        skills_loader_module._LOADED_HANDLER_SOURCES.clear()
        skills_loader_module._LOADED_HANDLER_SOURCES.update(before_sources)
        for key in list(sys.modules):
            if key not in before_modules:
                sys.modules.pop(key, None)
        sys.modules.update(before_modules)


def test_execute_tool_does_not_reuse_active_module_after_source_drift(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "drifting_skill", "loaded")
    handler = skill_dir / "handlers" / "main.py"
    before_sources = dict(skills_loader_module._LOADED_HANDLER_SOURCES)
    before_modules = dict(sys.modules)
    before_snapshots = dict(runtime_runner_module._SKILL_SOURCE_SNAPSHOTS)
    try:
        loader = object.__new__(skills_loader_module.ImportlibSkillsLoader)
        loader._load_handler(handler)
        loaded = skills_loader_module.loaded_handler_module_for_path(handler)
        assert loaded is not None
        assert loaded.get_snapshot()["marker"] == "loaded"

        handler.write_text(
            "def get_snapshot(**kwargs):\n"
            "    return {'skill': 'drifting_skill', 'marker': 'changed', 'kwargs': dict(kwargs)}\n",
            encoding="utf-8",
        )
        future = time.time() + 2
        os.utime(handler, (future, future))

        result = runtime_runner_module.execute_tool(
            skill_dir,
            module="handlers.main",
            attr="get_snapshot",
            payload={},
        )

        assert result["marker"] == "changed"
        assert skills_loader_module.loaded_handler_module_for_path(handler) is None
    finally:
        runtime_runner_module._SKILL_SOURCE_SNAPSHOTS.clear()
        runtime_runner_module._SKILL_SOURCE_SNAPSHOTS.update(before_snapshots)
        skills_loader_module._LOADED_HANDLER_SOURCES.clear()
        skills_loader_module._LOADED_HANDLER_SOURCES.update(before_sources)
        for key in list(sys.modules):
            if key not in before_modules:
                sys.modules.pop(key, None)
        sys.modules.update(before_modules)


def test_execute_tool_isolates_same_named_local_packages_between_skills(tmp_path: Path) -> None:
    alpha = _write_skill_with_shared_package_name(tmp_path, "alpha_skill", "alpha")
    beta = _write_skill_with_shared_package_name(tmp_path, "beta_skill", "beta")
    tracked_prefixes = ("handlers", "research", "_adaos_runtime.alpha_skill", "_adaos_runtime.beta_skill")
    before_modules = {
        key: sys.modules[key]
        for key in list(sys.modules.keys())
        if key.startswith(tracked_prefixes)
    }
    before_path = list(sys.path)
    try:
        first = runtime_runner_module.execute_tool(
            alpha, module="handlers.main", attr="get_snapshot", payload={}
        )
        second = runtime_runner_module.execute_tool(
            beta, module="handlers.main", attr="get_snapshot", payload={}
        )
        first_again = runtime_runner_module.execute_tool(
            alpha, module="handlers.main", attr="get_snapshot", payload={}
        )
    finally:
        sys.path[:] = before_path
        for key in list(sys.modules.keys()):
            if key.startswith(tracked_prefixes):
                sys.modules.pop(key, None)
        sys.modules.update(before_modules)

    assert first["marker"] == "alpha"
    assert second["marker"] == "beta"
    assert first_again["marker"] == "alpha"


def test_execute_tool_reloads_skill_modules_when_source_changes(tmp_path: Path) -> None:
    skill_dir = _write_skill_with_service_helper(tmp_path, "delta_skill", "one")

    first = runtime_runner_module.execute_tool(skill_dir, module="handlers.main", attr="get_snapshot", payload={})

    helper = skill_dir / "service" / "helper.py"
    helper.write_text("MARKER = 'two'\n", encoding="utf-8")
    future = time.time() + 2
    os.utime(helper, (future, future))

    second = runtime_runner_module.execute_tool(skill_dir, module="handlers.main", attr="get_snapshot", payload={})

    assert first["marker"] == "one"
    assert second["marker"] == "two"


def test_execute_tool_does_not_reuse_stale_skill_module_across_slot_paths(tmp_path: Path) -> None:
    slot_a = tmp_path / "slots" / "A" / "src" / "skills"
    slot_b = tmp_path / "slots" / "B" / "src" / "skills"
    first_slot = _write_skill(slot_a, "redevice_settings", "old-slot")
    second_slot = _write_skill(slot_b, "redevice_settings", "new-slot")

    tracked_prefixes = ("skills.redevice_settings", "redevice_settings", "handlers")
    before_modules = {
        key: sys.modules[key]
        for key in list(sys.modules.keys())
        if key == "skills" or key.startswith(tracked_prefixes)
    }
    before_snapshots = dict(runtime_runner_module._SKILL_SOURCE_SNAPSHOTS)
    try:
        runtime_runner_module._SKILL_SOURCE_SNAPSHOTS.clear()
        first = runtime_runner_module.execute_tool(
            first_slot,
            module="handlers.main",
            attr="get_snapshot",
            payload={},
        )
        second = runtime_runner_module.execute_tool(
            second_slot,
            module="handlers.main",
            attr="get_snapshot",
            payload={},
        )
    finally:
        runtime_runner_module._SKILL_SOURCE_SNAPSHOTS.clear()
        runtime_runner_module._SKILL_SOURCE_SNAPSHOTS.update(before_snapshots)
        for key in list(sys.modules.keys()):
            if key == "skills" or key.startswith(tracked_prefixes):
                sys.modules.pop(key, None)
        sys.modules.update(before_modules)

    assert first["marker"] == "old-slot"
    assert second["marker"] == "new-slot"


def test_execute_tool_uses_exact_active_slot_when_old_skills_package_is_loaded(tmp_path: Path) -> None:
    slot_a = tmp_path / "slots" / "A" / "src"
    slot_b = tmp_path / "slots" / "B" / "src"
    (slot_a / "skills").mkdir(parents=True, exist_ok=True)
    (slot_a / "skills" / "__init__.py").write_text("", encoding="utf-8")
    old_skill = _write_skill(slot_a / "skills", "builder_skill", "old-slot")
    new_skill = _write_skill(slot_b / "skills", "builder_skill", "new-slot")

    tracked_prefixes = ("skills", "builder_skill", "handlers", "_adaos_runtime.builder_skill")
    before_modules = {
        key: sys.modules[key]
        for key in list(sys.modules.keys())
        if key == "skills" or key.startswith(tracked_prefixes)
    }
    before_path = list(sys.path)
    before_snapshots = dict(runtime_runner_module._SKILL_SOURCE_SNAPSHOTS)
    try:
        runtime_runner_module._SKILL_SOURCE_SNAPSHOTS.clear()
        for key in list(sys.modules.keys()):
            if key == "skills" or key.startswith(tracked_prefixes):
                sys.modules.pop(key, None)
        sys.path.insert(0, str(slot_a))
        old_module = importlib.import_module("skills.builder_skill.handlers.main")
        assert old_module.get_snapshot()["marker"] == "old-slot"

        current = runtime_runner_module.execute_tool(
            new_skill,
            module="handlers.main",
            attr="get_snapshot",
            payload={},
        )
    finally:
        runtime_runner_module._SKILL_SOURCE_SNAPSHOTS.clear()
        runtime_runner_module._SKILL_SOURCE_SNAPSHOTS.update(before_snapshots)
        sys.path[:] = before_path
        for key in list(sys.modules.keys()):
            if key == "skills" or key.startswith(tracked_prefixes):
                sys.modules.pop(key, None)
        sys.modules.update(before_modules)

    assert current["marker"] == "new-slot"


def test_execute_tool_supports_bare_tool_decorator(tmp_path: Path) -> None:
    skill_dir = _write_bare_tool_skill(tmp_path, "gamma_skill")

    before = {key: sys.modules[key] for key in list(sys.modules.keys()) if key == "handlers" or key.startswith("handlers.")}
    try:
        detach_result = runtime_runner_module.execute_tool(
            skill_dir,
            module="handlers.main",
            attr="detach_link",
            payload={
                "node_id": "node-a",
                "target_node_id": "node-b",
                "webspace_id": "ws-1",
                "_meta": {
                    "webspace_id": "ws-1",
                    "target_node_id": "node-b",
                },
            },
        )
        refresh_result = runtime_runner_module.execute_tool(
            skill_dir,
            module="handlers.main",
            attr="refresh_snapshot",
            payload={
                "webspace_id": "ws-2",
                "_meta": {
                    "webspace_id": "ws-2",
                },
            },
        )
        ping_result = runtime_runner_module.execute_tool(
            skill_dir,
            module="handlers.main",
            attr="ping",
            payload={
                "_meta": {
                    "webspace_id": "ws-3",
                },
            },
        )
    finally:
        for key in list(sys.modules.keys()):
            if key == "handlers" or key.startswith("handlers."):
                sys.modules.pop(key, None)
        sys.modules.update(before)

    assert detach_result == {
        "node_id": "node-a",
        "target_node_id": "node-b",
        "webspace_id": "ws-1",
    }
    assert refresh_result == {
        "webspace_id": "ws-2",
    }
    assert ping_result == {
        "ok": True,
    }


def test_execute_tool_discards_a_partially_imported_skill_module(tmp_path: Path) -> None:
    skill_dir = tmp_path / "recoverable_skill"
    (skill_dir / "handlers").mkdir(parents=True)
    (skill_dir / "handlers" / "main.py").write_text(
        "import builtins\n"
        "from adaos.sdk.core.decorators import subscribe\n"
        "@subscribe('test.partial_import')\n"
        "def on_event(_event): return None\n"
        "if not getattr(builtins, '_adaos_runtime_allow_test_import', False):\n"
        "    raise RuntimeError('first import fails')\n"
        "def get_snapshot():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    marker = "_adaos_runtime_allow_test_import"
    before = getattr(__import__("builtins"), marker, None)
    had_before = hasattr(__import__("builtins"), marker)
    try:
        with pytest.raises(RuntimeError, match="first import fails"):
            runtime_runner_module.execute_tool(
                skill_dir,
                module="handlers.main",
                attr="get_snapshot",
                payload={},
            )
        synthetic_prefix = f"_adaos_runtime.{skill_dir.name}."
        assert not any(
            str(getattr(fn, "__module__", "")).startswith(synthetic_prefix)
            for _topic, fn in sdk_decorators.subscriptions
        )
        setattr(__import__("builtins"), marker, True)

        assert runtime_runner_module.execute_tool(
            skill_dir,
            module="handlers.main",
            attr="get_snapshot",
            payload={},
        ) == {"ok": True}
    finally:
        if had_before:
            setattr(__import__("builtins"), marker, before)
        else:
            delattr(__import__("builtins"), marker)


def test_execute_tool_does_not_leak_handler_declarations(tmp_path: Path) -> None:
    skill_dir = tmp_path / "declaration_skill"
    handler = skill_dir / "handlers" / "main.py"
    handler.parent.mkdir(parents=True)
    handler.write_text(
        "from adaos.sdk.core.decorators import subscribe, tool\n"
        "@subscribe('test.execution_only')\n"
        "def on_event(_event): return None\n"
        "@tool('read_state', side_effects='none')\n"
        "def read_state(_payload=None): return {'ok': True}\n",
        encoding="utf-8",
    )
    snapshot = sdk_decorators._registry_snapshot()
    try:
        assert runtime_runner_module.execute_tool(
            skill_dir,
            module="handlers.main",
            attr="read_state",
            payload={},
        ) == {"ok": True}

        synthetic_prefix = f"_adaos_runtime.{skill_dir.name}."
        assert not any(
            str(getattr(fn, "__module__", "")).startswith(synthetic_prefix)
            for _topic, fn in sdk_decorators.subscriptions
        )
        assert not any(name.startswith(synthetic_prefix) for name in sdk_decorators.tools_registry)
    finally:
        sdk_decorators._restore_registry_snapshot(snapshot)


def test_execute_tool_serializes_concurrent_first_imports(tmp_path: Path) -> None:
    skill_dir = tmp_path / "concurrent_skill"
    (skill_dir / "handlers").mkdir(parents=True)
    (skill_dir / "handlers" / "main.py").write_text(
        "import time\n"
        "time.sleep(0.15)\n"
        "def get_snapshot(value=None):\n"
        "    return {'value': value}\n",
        encoding="utf-8",
    )
    start = threading.Barrier(2)

    def invoke(value: int) -> dict[str, int]:
        start.wait(timeout=2)
        return runtime_runner_module.execute_tool(
            skill_dir,
            module="handlers.main",
            attr="get_snapshot",
            payload={"value": value},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, (1, 2)))

    assert sorted(item["value"] for item in results) == [1, 2]
