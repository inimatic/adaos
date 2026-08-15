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
