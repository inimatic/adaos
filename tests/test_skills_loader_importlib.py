from __future__ import annotations

import asyncio
import builtins
import sys
import time
import types
from pathlib import Path

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

from adaos.services import skills_loader_importlib as skills_loader_module
from adaos.services.skill.declarations import runtime_stream_receiver_patterns
from adaos.services.skills_loader_importlib import ImportlibSkillsLoader


def test_importlib_loader_keeps_event_loop_responsive_during_discovery_and_import(tmp_path, monkeypatch) -> None:
    handler = tmp_path / "slow_skill" / "handlers" / "main.py"
    handler.parent.mkdir(parents=True)
    handler.write_text("VALUE = 1\n", encoding="utf-8")
    loader = ImportlibSkillsLoader()

    def _slow_runtime_discovery(_root: Path):
        time.sleep(0.08)
        return [(handler, "slow_skill")]

    def _slow_handler_import(_handler: Path, *, reload: bool = False) -> None:
        assert reload is False
        time.sleep(0.08)

    monkeypatch.setattr(loader, "_discover_runtime_handlers", _slow_runtime_discovery)
    monkeypatch.setattr(loader, "_discover_workspace_handlers", lambda _root, _loaded: [])
    monkeypatch.setattr(loader, "_discover_repo_workspace_handlers", lambda _root, _loaded: [])
    monkeypatch.setattr(loader, "_load_skill_declarations", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loader, "_load_handler", _slow_handler_import)

    async def _run() -> int:
        ticks = 0
        done = asyncio.Event()

        async def _ticker() -> None:
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        ticker = asyncio.create_task(_ticker())
        try:
            await loader.import_all_handlers(tmp_path)
        finally:
            done.set()
            await ticker
        return ticks

    assert asyncio.run(_run()) >= 8


def test_importlib_loader_keeps_declaration_loading_off_event_loop(tmp_path, monkeypatch) -> None:
    handler = tmp_path / "slow_declaration_skill" / "handlers" / "main.py"
    handler.parent.mkdir(parents=True)
    handler.write_text("VALUE = 1\n", encoding="utf-8")
    loader = ImportlibSkillsLoader()

    monkeypatch.setattr(loader, "_discover_runtime_handlers", lambda _root: [(handler, "slow_declaration_skill")])
    monkeypatch.setattr(loader, "_discover_workspace_handlers", lambda _root, _loaded: [])
    monkeypatch.setattr(loader, "_discover_repo_workspace_handlers", lambda _root, _loaded: [])
    monkeypatch.setattr(loader, "_load_handler", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loader, "_load_skill_declarations", lambda *_args, **_kwargs: time.sleep(0.08))

    async def _run() -> int:
        ticks = 0
        done = asyncio.Event()

        async def _ticker() -> None:
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        ticker = asyncio.create_task(_ticker())
        try:
            await loader.import_all_handlers(tmp_path)
        finally:
            done.set()
            await ticker
        return ticks

    assert asyncio.run(_run()) >= 6


def test_runtime_handler_discovery_uses_bounded_source_layout(tmp_path, monkeypatch) -> None:
    runtime_skill = tmp_path / ".runtime" / "bounded_skill"
    version_root = runtime_skill / "v1.0"
    slot_root = version_root / "slots" / "A"
    handler = slot_root / "src" / "skills" / "bounded_skill" / "handlers" / "main.py"
    handler.parent.mkdir(parents=True)
    handler.write_text("VALUE = 1\n", encoding="utf-8")
    (runtime_skill / "current_version").write_text("1.0.0\n", encoding="utf-8")
    (version_root / "active").write_text("A\n", encoding="utf-8")
    (slot_root / "resolved.manifest.json").write_text('{"name":"bounded_skill"}\n', encoding="utf-8")

    def _reject_recursive_scan(*_args, **_kwargs):
        raise AssertionError("runtime handler discovery must not recursively scan skill source trees")

    monkeypatch.setattr(Path, "rglob", _reject_recursive_scan)

    assert ImportlibSkillsLoader()._discover_runtime_handlers(tmp_path) == [(handler, "bounded_skill")]


def test_importlib_loader_excludes_deactivated_runtime_and_workspace_fallback(tmp_path, monkeypatch) -> None:
    runtime_skill = tmp_path / ".runtime" / "quarantined_skill"
    version_root = runtime_skill / "v1.0"
    slot_root = version_root / "slots" / "A"
    runtime_handler = slot_root / "src" / "skills" / "quarantined_skill" / "handlers" / "main.py"
    runtime_handler.parent.mkdir(parents=True)
    runtime_handler.write_text("VALUE = 'runtime'\n", encoding="utf-8")
    (runtime_skill / "current_version").write_text("1.0\n", encoding="utf-8")
    (version_root / "active").write_text("A\n", encoding="utf-8")
    (slot_root / "resolved.manifest.json").write_text('{"name":"quarantined_skill"}\n', encoding="utf-8")
    (runtime_skill / "deactivated.json").write_text(
        '{"deactivated":true,"reason":"tests_failed"}\n',
        encoding="utf-8",
    )

    workspace_handler = tmp_path / "quarantined_skill" / "handlers" / "main.py"
    workspace_handler.parent.mkdir(parents=True)
    workspace_handler.write_text("VALUE = 'workspace'\n", encoding="utf-8")

    loaded: list[Path] = []
    loader = ImportlibSkillsLoader()
    monkeypatch.setattr(loader, "_load_handler", lambda handler, **_kwargs: loaded.append(handler))
    asyncio.run(loader.import_all_handlers(tmp_path))

    assert loaded == []
    reload_result = asyncio.run(loader.reload_skill_handlers(tmp_path, "quarantined_skill"))
    assert reload_result["ok"] is False
    assert reload_result["skipped"] is True
    assert reload_result["reason"] == "skill_runtime_deactivated"


def test_importlib_loader_quarantines_evolved_blocking_async_skill_before_import(tmp_path, monkeypatch) -> None:
    skill_dir = tmp_path / "blocking_skill"
    handler = skill_dir / "handlers" / "main.py"
    handler.parent.mkdir(parents=True)
    handler.write_text(
        "\n".join(
            [
                "import time",
                "from adaos.sdk.core.decorators import subscribe",
                "@subscribe('demo.changed')",
                "async def on_demo_changed(payload):",
                "    time.sleep(5)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "skill.yaml").write_text(
        "name: blocking_skill\nversion: '1.0.0'\nevents:\n  subscribe: [demo.changed]\n  publish: []\n",
        encoding="utf-8",
    )
    imported: list[Path] = []
    loader = ImportlibSkillsLoader()
    monkeypatch.setattr(loader, "_load_handler", lambda path, **_kwargs: imported.append(path))

    asyncio.run(loader.import_all_handlers(tmp_path))

    assert imported == []
    marker = tmp_path / ".runtime" / "blocking_skill" / "deactivated.json"
    payload = skills_loader_module.json.loads(marker.read_text(encoding="utf-8"))
    assert payload["reason"] == "runtime_safety_validation_failed"
    assert payload["failure_kind"] == "async_blocking_call"
    assert payload["issues"][0]["code"] == "runtime.async_subscription_blocking_call"


def test_importlib_loader_reload_quarantines_blocking_async_skill_and_removes_subscriptions(
    tmp_path, monkeypatch
) -> None:
    skill_dir = tmp_path / "blocking_reload_skill"
    handler = skill_dir / "handlers" / "main.py"
    handler.parent.mkdir(parents=True)
    handler.write_text(
        "import requests\nasync def refresh():\n    requests.get('https://example.test')\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.yaml").write_text(
        "name: blocking_reload_skill\nversion: '1.0.0'\n",
        encoding="utf-8",
    )
    deactivated: list[set[str]] = []
    emitted: list[tuple[str, dict]] = []
    loader = ImportlibSkillsLoader()
    monkeypatch.setattr(
        "adaos.sdk.core.decorators.deactivate_skill_subscriptions",
        lambda names: deactivated.append(set(names)) or {"skills": sorted(names), "removed_handlers": 1},
    )
    monkeypatch.setattr(
        loader,
        "_emit_runtime_safety_quarantine",
        lambda skill_name, payload: asyncio.sleep(
            0,
            result=emitted.append((skill_name, dict(payload))),
        ),
    )

    result = asyncio.run(loader.reload_skill_handlers(tmp_path, "blocking_reload_skill"))

    assert result["ok"] is False
    assert result["reason"] == "runtime_safety_validation_failed"
    assert deactivated == [{"blocking_reload_skill"}]
    assert emitted[0][0] == "blocking_reload_skill"
    assert result["subscriptions"]["removed_handlers"] == 1


def test_importlib_loader_loads_skill_data_projections(tmp_path, monkeypatch) -> None:
    skill_dir = tmp_path / "infrastate_skill"
    handlers_dir = skill_dir / "handlers"
    handlers_dir.mkdir(parents=True)
    (handlers_dir / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (skill_dir / "skill.yaml").write_text(
        """
name: infrastate_skill
version: "0.1.0"
data_projections:
  - scope: subnet
    slot: infrastate.snapshot
    targets:
      - backend: yjs
        path: data/infrastate
data_routes:
  - surface: modal:infrastate
    route: stream
    receiver: infrastate.events
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "webui.json").write_text(
        '{"webio":{"receivers":{"infrastate.webui":{"mode":"replace"}}}}',
        encoding="utf-8",
    )

    loaded_entries: list[list[dict]] = []

    class _Projections:
        def load_entries(self, entries):
            loaded_entries.append(list(entries))

    class _Ctx:
        projections = _Projections()

    loader = ImportlibSkillsLoader()

    def _assert_declarations_precede_handler(_handler: Path) -> None:
        assert loaded_entries
        assert runtime_stream_receiver_patterns("infrastate_skill") == (
            "infrastate.events",
            "infrastate.webui",
        )

    monkeypatch.setattr(loader, "_load_handler", _assert_declarations_precede_handler)
    monkeypatch.setattr("adaos.services.skills_loader_importlib.get_ctx", lambda: _Ctx())

    asyncio.run(loader.import_all_handlers(tmp_path))

    assert loaded_entries
    assert loaded_entries[0][0]["slot"] == "infrastate.snapshot"


def test_importlib_loader_skips_failed_workspace_handler_and_continues(tmp_path: Path, monkeypatch) -> None:
    bad_skill = tmp_path / "bad_skill"
    (bad_skill / "handlers").mkdir(parents=True)
    (bad_skill / "handlers" / "main.py").write_text(
        "import missing_adaos_test_dependency\n",
        encoding="utf-8",
    )

    good_skill = tmp_path / "good_skill"
    (good_skill / "handlers").mkdir(parents=True)
    (good_skill / "handlers" / "main.py").write_text(
        "\n".join(
            [
                "import builtins",
                "builtins._adaos_good_skill_imported = True",
                "",
            ]
        ),
        encoding="utf-8",
    )

    loader = ImportlibSkillsLoader()
    monkeypatch.setattr(loader, "_sync_runtime_from_repo_workspace_if_missing", lambda root: None)
    warnings: list[str] = []
    projection_loads: list[Path] = []

    def _warning(message: str, *args, **kwargs) -> None:
        warnings.append(message % args)

    monkeypatch.setattr(skills_loader_module._LOG, "warning", _warning)
    monkeypatch.setattr(
        loader,
        "_load_skill_declarations",
        lambda handler, _loaded, *, skill_name: projection_loads.append(handler),
    )
    if hasattr(builtins, "_adaos_good_skill_imported"):
        delattr(builtins, "_adaos_good_skill_imported")

    try:
        asyncio.run(loader.import_all_handlers(tmp_path))

        assert getattr(builtins, "_adaos_good_skill_imported", False) is True
        assert any("skill handler import failed; skipping skill=bad_skill" in item for item in warnings)
        assert any("ModuleNotFoundError" in item for item in warnings)
        assert projection_loads == [
            bad_skill / "handlers" / "main.py",
            good_skill / "handlers" / "main.py",
        ]
    finally:
        for handler in (bad_skill / "handlers" / "main.py", good_skill / "handlers" / "main.py"):
            sys.modules.pop("adaos_skill_" + handler.parent.as_posix().replace("/", "_"), None)
        if hasattr(builtins, "_adaos_good_skill_imported"):
            delattr(builtins, "_adaos_good_skill_imported")


def test_importlib_loader_does_not_reexecute_same_handler_module(tmp_path: Path) -> None:
    skill_dir = tmp_path / "repeat_skill"
    handlers_dir = skill_dir / "handlers"
    handlers_dir.mkdir(parents=True)
    handler = handlers_dir / "main.py"
    handler.write_text(
        "\n".join(
            [
                "import builtins",
                "builtins._adaos_repeat_import_counter = getattr(builtins, '_adaos_repeat_import_counter', 0) + 1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    loader = ImportlibSkillsLoader()
    mod_name = "adaos_skill_" + handler.parent.as_posix().replace("/", "_")
    sys.modules.pop(mod_name, None)
    if hasattr(builtins, "_adaos_repeat_import_counter"):
        delattr(builtins, "_adaos_repeat_import_counter")
    try:
        loader._load_handler(handler)
        loader._load_handler(handler)
        assert getattr(builtins, "_adaos_repeat_import_counter", 0) == 1
    finally:
        sys.modules.pop(mod_name, None)
        if hasattr(builtins, "_adaos_repeat_import_counter"):
            delattr(builtins, "_adaos_repeat_import_counter")


def test_importlib_loader_can_force_reload_handler_module(tmp_path: Path) -> None:
    skill_dir = tmp_path / "reload_skill"
    handlers_dir = skill_dir / "handlers"
    handlers_dir.mkdir(parents=True)
    handler = handlers_dir / "main.py"
    handler.write_text(
        "\n".join(
            [
                "import builtins",
                "builtins._adaos_reload_import_counter = getattr(builtins, '_adaos_reload_import_counter', 0) + 1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    loader = ImportlibSkillsLoader()
    mod_name = "adaos_skill_" + handler.parent.as_posix().replace("/", "_")
    sys.modules.pop(mod_name, None)
    if hasattr(builtins, "_adaos_reload_import_counter"):
        delattr(builtins, "_adaos_reload_import_counter")
    try:
        loader._load_handler(handler)
        loader._load_handler(handler, reload=True)
        assert getattr(builtins, "_adaos_reload_import_counter", 0) == 2
    finally:
        sys.modules.pop(mod_name, None)
        if hasattr(builtins, "_adaos_reload_import_counter"):
            delattr(builtins, "_adaos_reload_import_counter")


def test_handler_source_snapshot_detects_disk_drift_and_reload(tmp_path: Path) -> None:
    skill_dir = tmp_path / "observable_skill"
    handler = skill_dir / "handlers" / "main.py"
    handler.parent.mkdir(parents=True)
    handler.write_text("VALUE = 1\n", encoding="utf-8")

    loader = ImportlibSkillsLoader()
    mod_name = "adaos_skill_" + handler.parent.as_posix().replace("/", "_")
    sys.modules.pop(mod_name, None)
    skills_loader_module._LOADED_HANDLER_SOURCES.pop(mod_name, None)
    try:
        loader._load_handler(handler)
        loaded = skills_loader_module.skill_handler_source_snapshot()
        item = next(item for item in loaded["items"] if item["module"] == mod_name)
        assert item["source_drift"] is False

        handler.write_text("VALUE = 2\n", encoding="utf-8")
        drifted = skills_loader_module.skill_handler_source_snapshot()
        item = next(item for item in drifted["items"] if item["module"] == mod_name)
        assert item["source_drift"] is True
        assert item["drift"] is True
        assert item["current_digest"] != item["loaded_digest"]

        loader._load_handler(handler, reload=True)
        reloaded = skills_loader_module.skill_handler_source_snapshot()
        item = next(item for item in reloaded["items"] if item["module"] == mod_name)
        assert item["source_drift"] is False
        assert item["drift"] is False
    finally:
        sys.modules.pop(mod_name, None)
        skills_loader_module._LOADED_HANDLER_SOURCES.pop(mod_name, None)


def test_handler_source_snapshot_periodically_rehashes_unchanged_stat(monkeypatch, tmp_path: Path) -> None:
    handler = tmp_path / "periodic_skill" / "handlers" / "main.py"
    handler.parent.mkdir(parents=True)
    handler.write_text("VALUE = 1\n", encoding="utf-8")

    loader = ImportlibSkillsLoader()
    mod_name = "adaos_skill_" + handler.parent.as_posix().replace("/", "_")
    sys.modules.pop(mod_name, None)
    skills_loader_module._LOADED_HANDLER_SOURCES.pop(mod_name, None)
    monkeypatch.setenv("ADAOS_SKILL_HANDLER_DIGEST_REVERIFY_S", "1")
    try:
        loader._load_handler(handler)
        handler.write_text("VALUE = 2\n", encoding="utf-8")
        stat = handler.stat()
        record = skills_loader_module._LOADED_HANDLER_SOURCES[mod_name]
        record.update(
            {
                "_observed_size": int(stat.st_size),
                "_observed_mtime_ns": int(stat.st_mtime_ns),
                "_observed_ctime_ns": int(stat.st_ctime_ns),
                "_observed_inode": int(stat.st_ino),
                "_digest_verified_at": 0.0,
            }
        )

        snapshot = skills_loader_module.skill_handler_source_snapshot()
        item = next(item for item in snapshot["items"] if item["module"] == mod_name)

        assert snapshot["digest_reverify_interval_s"] == 1.0
        assert item["source_drift"] is True
        assert item["current_digest"] != item["loaded_digest"]
        assert item["digest_verified_at"] is not None
    finally:
        sys.modules.pop(mod_name, None)
        skills_loader_module._LOADED_HANDLER_SOURCES.pop(mod_name, None)


def test_runtime_source_sync_is_explicit_and_never_runs_in_candidate(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    loader = ImportlibSkillsLoader()
    monkeypatch.setattr(
        loader,
        "_sync_runtime_from_repo_workspace_if_missing",
        lambda _root: calls.append("repo"),
    )
    monkeypatch.setattr(loader, "_sync_runtime_from_workspace", lambda _root: calls.append("workspace"))
    monkeypatch.delenv("ADAOS_SKILL_RUNTIME_SOURCE_SYNC", raising=False)
    monkeypatch.delenv("ADAOS_RUNTIME_TRANSITION_ROLE", raising=False)
    assert ImportlibSkillsLoader._runtime_source_sync_enabled() is False
    asyncio.run(loader.import_all_handlers(tmp_path))
    assert calls == []

    monkeypatch.setenv("ADAOS_SKILL_RUNTIME_SOURCE_SYNC", "1")
    assert ImportlibSkillsLoader._runtime_source_sync_enabled() is True
    asyncio.run(loader.import_all_handlers(tmp_path))
    assert calls == ["repo", "workspace"]

    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    assert ImportlibSkillsLoader._runtime_source_sync_enabled() is False
    asyncio.run(loader.import_all_handlers(tmp_path))
    assert calls == ["repo", "workspace"]
