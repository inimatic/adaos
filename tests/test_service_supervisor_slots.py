import asyncio
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest


def _write_service_skill(root: Path, *, port: int) -> None:
    (root / "handlers").mkdir(parents=True, exist_ok=True)
    (root / "skill.yaml").write_text(
        "\n".join(
            [
                "name: slot_service",
                "version: 0.1.0",
                "runtime:",
                "  kind: service",
                "  env:",
                "    mode: venv",
                "    python: '3.11'",
                "service:",
                "  host: 127.0.0.1",
                f"  port: {port}",
                "  command: ['-m', 'handlers.main']",
                "  healthcheck:",
                "    path: /health",
                "    timeout_ms: 1000",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "handlers" / "main.py").write_text("def handle(payload=None):\n    return {'ok': True}\n", encoding="utf-8")


def test_service_startup_readiness_timeout_is_explicit(monkeypatch, tmp_path):
    from adaos.services.skill import service_supervisor as mod

    skill_root = tmp_path / "slow_service"
    skill_root.mkdir()
    spec = mod._resolve_service_spec(
        "slow_service",
        skill_root,
        {
            "runtime": {"kind": "service"},
            "service": {
                "port": 18199,
                "command": ["-m", "handlers.service"],
                "healthcheck": {
                    "path": "/health",
                    "timeout_ms": 50,
                    "startup_timeout_ms": 5000,
                },
            },
        },
    )
    assert spec is not None
    spec.startup_ready_timeout_s = 0.02
    monkeypatch.setattr(
        mod,
        "_http_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError()),
    )
    supervisor = mod.ServiceSkillSupervisor()

    with pytest.raises(TimeoutError, match="did not become ready"):
        asyncio.run(supervisor._wait_ready(spec))

    assert supervisor._health_states["slow_service"]["source"] == (
        "startup_readiness_timeout"
    )


def test_service_supervisor_discovers_active_runtime_slot_instead_of_workspace_source():
    from adaos.services.agent_context import get_ctx
    from adaos.services.skill.service_supervisor import ServiceSkillSupervisor

    ctx = get_ctx()
    skills_root = Path(ctx.paths.skills_dir())
    workspace_skill = skills_root / "slot_service"
    _write_service_skill(workspace_skill, port=1112)

    version_root = skills_root / ".runtime" / "slot_service" / "v0.1"
    (skills_root / ".runtime" / "slot_service").mkdir(parents=True, exist_ok=True)
    (skills_root / ".runtime" / "slot_service" / "current_version").write_text("0.1.0", encoding="utf-8")
    version_root.mkdir(parents=True, exist_ok=True)
    (version_root / "active").write_text("A", encoding="utf-8")

    slot_a = version_root / "slots" / "A" / "src" / "skills" / "slot_service"
    slot_b = version_root / "slots" / "B" / "src" / "skills" / "slot_service"
    _write_service_skill(slot_a, port=1111)
    _write_service_skill(slot_b, port=1113)

    supervisor = ServiceSkillSupervisor()
    supervisor.ensure_discovered(force=True)
    status = supervisor.status("slot_service")

    assert status is not None
    assert status["port"] == 1111
    assert ".runtime" in status["skill_root"]
    assert status["skill_root"].endswith(str(Path("src") / "skills" / "slot_service"))
    assert status["venv_dir"].endswith(str(Path("v0.1") / "venv"))
    process = SimpleNamespace(pid=1234, returncode=None)
    supervisor._procs["slot_service"] = process
    supervisor._proc_specs["slot_service"] = supervisor._spec_key(
        supervisor._specs["slot_service"]
    )
    assert supervisor.status("slot_service")["process_spec_matches"] is True

    (version_root / "active").write_text("B", encoding="utf-8")
    supervisor.ensure_discovered(force=True)
    status = supervisor.status("slot_service")

    assert status is not None
    assert status["port"] == 1113
    assert status["venv_dir"].endswith(str(Path("v0.1") / "venv"))
    assert status["process_spec_matches"] is False


def test_service_supervisor_skips_deactivated_runtime_service():
    from adaos.services.agent_context import get_ctx
    from adaos.services.skill.service_supervisor import ServiceSkillSupervisor

    ctx = get_ctx()
    skills_root = Path(ctx.paths.skills_dir())
    workspace_skill = skills_root / "slot_service"
    _write_service_skill(workspace_skill, port=1112)

    runtime_root = skills_root / ".runtime" / "slot_service"
    version_root = runtime_root / "v0.1"
    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "current_version").write_text("0.1.0", encoding="utf-8")
    version_root.mkdir(parents=True, exist_ok=True)
    (version_root / "active").write_text("A", encoding="utf-8")
    (runtime_root / "deactivated.json").write_text(
        json.dumps(
            {
                "name": "slot_service",
                "version": "0.1.0",
                "slot": "A",
                "deactivated": True,
                "reason": "startup_dependency_install_failed",
            }
        ),
        encoding="utf-8",
    )

    slot_a = version_root / "slots" / "A" / "src" / "skills" / "slot_service"
    _write_service_skill(slot_a, port=1111)

    supervisor = ServiceSkillSupervisor()
    supervisor.ensure_discovered(force=True)

    assert supervisor.status("slot_service") is None
    assert "slot_service" not in supervisor.list()


def test_service_supervisor_reuses_unchanged_non_service_manifest(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.skill import service_supervisor as mod

    skills_root = Path(get_ctx().paths.skills_dir())
    skill_root = skills_root / "module_skill"
    skill_root.mkdir(parents=True, exist_ok=True)
    skill_yaml = skill_root / "skill.yaml"
    skill_yaml.write_text(
        "name: module_skill\nversion: 0.1.0\nruntime:\n  kind: module\n",
        encoding="utf-8",
    )

    original = mod._read_skill_manifest
    target_reads = 0

    def _counted_read(path: Path) -> dict:
        nonlocal target_reads
        if path == skill_root:
            target_reads += 1
        return original(path)

    monkeypatch.setattr(mod, "_read_skill_manifest", _counted_read)
    supervisor = mod.ServiceSkillSupervisor()
    supervisor.ensure_discovered(force=True)
    supervisor._discover_last_at = 0.0
    supervisor.ensure_discovered()

    assert target_reads == 1


def test_service_supervisor_skips_full_scan_when_discovery_sources_are_unchanged(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.skill import service_supervisor as mod

    skills_root = Path(get_ctx().paths.skills_dir())
    skill_root = skills_root / "module_skill"
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "skill.yaml").write_text(
        "name: module_skill\nversion: 0.1.0\nruntime:\n  kind: module\n",
        encoding="utf-8",
    )

    original = mod._runtime_is_deactivated
    discovery_visits = 0

    def _counted_deactivation(root: Path, name: str) -> bool:
        nonlocal discovery_visits
        discovery_visits += 1
        return original(root, name)

    monkeypatch.setattr(mod, "_runtime_is_deactivated", _counted_deactivation)
    supervisor = mod.ServiceSkillSupervisor()
    supervisor.ensure_discovered(force=True)
    first_scan_visits = discovery_visits

    supervisor._discover_last_at = 0.0
    supervisor.ensure_discovered()

    assert first_scan_visits > 0
    assert discovery_visits == first_scan_visits

    registry_path = Path(get_ctx().paths.workspace_dir()) / "registry.json"
    registry_path.write_text('{"version": 2}\n', encoding="utf-8")
    supervisor._discover_last_at = 0.0
    supervisor.ensure_discovered()
    after_registry_change = discovery_visits

    assert after_registry_change > first_scan_visits

    supervisor._discover_last_at = 0.0
    supervisor._discover_last_full_at = time.monotonic() - supervisor._discover_full_interval_s - 1.0
    supervisor.ensure_discovered()

    assert discovery_visits > after_registry_change


def test_service_supervisor_refresh_discovery_does_not_block_event_loop():
    from adaos.services.skill import service_supervisor as mod

    supervisor = mod.ServiceSkillSupervisor()

    def _slow_discovery(*, force: bool = False) -> None:  # noqa: ARG001
        time.sleep(0.15)

    async def _run() -> int:
        ticks = 0

        async def _ticker() -> None:
            nonlocal ticks
            deadline = time.monotonic() + 0.12
            while time.monotonic() < deadline:
                await asyncio.sleep(0.01)
                ticks += 1

        supervisor.ensure_discovered = _slow_discovery  # type: ignore[method-assign]
        ticker = asyncio.create_task(_ticker())
        await supervisor.refresh_discovered(force=True)
        await ticker
        await supervisor.shutdown()
        return ticks

    assert asyncio.run(_run()) >= 3


def test_service_supervisor_watchdog_polls_processes_off_event_loop(monkeypatch):
    from adaos.services.skill import service_supervisor as mod

    supervisor = mod.ServiceSkillSupervisor()
    poll_started = threading.Event()
    original_sleep = asyncio.sleep

    class _SlowProcess:
        pid = 9199
        returncode = None

        @staticmethod
        def poll():
            poll_started.set()
            time.sleep(0.15)
            return None

    async def _refresh_discovered(*, force=False):  # noqa: ARG001
        return None

    async def _fast_sleep(delay):
        await original_sleep(0 if delay == 2.0 else delay)

    supervisor._procs["slow_service"] = _SlowProcess()
    supervisor.refresh_discovered = _refresh_discovered  # type: ignore[method-assign]
    monkeypatch.setattr(mod.asyncio, "sleep", _fast_sleep)

    async def _exercise() -> int:
        watchdog = asyncio.create_task(supervisor._watchdog_loop())
        await asyncio.to_thread(poll_started.wait, 1.0)
        ticks = 0
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            ticks += 1
            await original_sleep(0.01)
        supervisor._shutdown_requested = True
        await asyncio.wait_for(watchdog, timeout=1.0)
        return ticks

    assert asyncio.run(_exercise()) >= 5


def test_service_supervisor_shutdown_prevents_late_service_restart():
    from adaos.services.skill import service_supervisor as mod

    supervisor = mod.ServiceSkillSupervisor()
    ensure_calls: list[tuple[str, bool]] = []

    async def _refresh_discovered(*, force: bool = False) -> None:  # noqa: ARG001
        return None

    async def _ensure_started(name, spec, *, force: bool) -> None:  # noqa: ANN001, ARG001
        ensure_calls.append((name, force))

    supervisor._specs["slot_service"] = object()  # type: ignore[assignment]
    supervisor.refresh_discovered = _refresh_discovered  # type: ignore[method-assign]
    supervisor.ensure_started = _ensure_started  # type: ignore[method-assign]

    async def _run() -> None:
        await supervisor.shutdown()
        await supervisor.start_all()

    asyncio.run(_run())

    assert ensure_calls == []
    assert supervisor._task is None
    assert supervisor._health_task is None


def test_service_supervisor_start_all_attributes_each_service_result(monkeypatch):
    from adaos.services.skill import service_supervisor as mod

    supervisor = mod.ServiceSkillSupervisor()
    calls: list[str] = []
    recorded: list[str] = []
    logged: list[str] = []

    def _capture(message, *args, **_kwargs) -> None:  # noqa: ANN001
        logged.append(message % args if args else str(message))

    monkeypatch.setattr(mod._log, "info", _capture)
    monkeypatch.setattr(mod._log, "warning", _capture)
    monkeypatch.setattr(mod._log, "log", lambda _level, message, *args, **kwargs: _capture(message, *args, **kwargs))

    async def _refresh_discovered(*, force: bool = False) -> None:  # noqa: ARG001
        return None

    async def _ensure_started(name, spec, *, force: bool) -> None:  # noqa: ANN001, ARG001
        calls.append(name)
        if name == "broken_service":
            raise RuntimeError("health timeout")

    async def _record_failure(name, spec, exc) -> None:  # noqa: ANN001, ARG001
        recorded.append(name)

    supervisor._specs = {"broken_service": object(), "ready_service": object()}  # type: ignore[assignment]
    supervisor.refresh_discovered = _refresh_discovered  # type: ignore[method-assign]
    supervisor.ensure_started = _ensure_started  # type: ignore[method-assign]
    supervisor._record_ensure_failure = _record_failure  # type: ignore[method-assign]
    supervisor._ensure_background_tasks = lambda: None  # type: ignore[method-assign]

    asyncio.run(supervisor.start_all())

    assert calls == ["broken_service", "ready_service"]
    assert recorded == ["broken_service"]
    assert any("service skill startup result skill=broken_service status=failed" in line for line in logged)
    assert any("service skill startup result skill=ready_service status=ready" in line for line in logged)
    assert any("service skill startup summary attempted=2 failed=1" in line for line in logged)


def test_get_service_supervisor_replaces_shutdown_singleton(monkeypatch):
    from adaos.services.skill import service_supervisor as mod

    supervisor = mod.ServiceSkillSupervisor()
    monkeypatch.setattr(mod, "_SUPERVISOR", supervisor)

    asyncio.run(supervisor.shutdown())

    replacement = mod.get_service_supervisor()

    assert replacement is not supervisor
    assert replacement._shutdown_requested is False


def test_service_supervisor_defaults_dependency_service_to_bucket_venv(tmp_path):
    from adaos.services.skill import service_supervisor as mod

    runtime_root = tmp_path / ".runtime" / "slideshow_skill" / "v0.1"
    skill_root = runtime_root / "slots" / "A" / "src" / "skills" / "slideshow_skill"
    skill_root.mkdir(parents=True)

    spec = mod._resolve_service_spec(
        "slideshow_skill",
        skill_root,
        {
            "name": "slideshow_skill",
            "runtime": {"kind": "service"},
            "service": {
                "host": "127.0.0.1",
                "port": 18104,
                "command": ["-m", "handlers.service"],
            },
            "dependencies": ["pillow>=10.0.0"],
        },
    )

    assert spec is not None
    assert spec.env_mode == "venv"
    assert spec.venv_dir == runtime_root / "venv"


def test_service_supervisor_prefers_service_dependencies_for_venv(tmp_path):
    from adaos.services.skill import service_supervisor as mod

    runtime_root = tmp_path / ".runtime" / "svc" / "v1.0"
    skill_root = runtime_root / "slots" / "A" / "src" / "skills" / "svc"
    skill_root.mkdir(parents=True)

    spec = mod._resolve_service_spec(
        "svc",
        skill_root,
        {
            "name": "svc",
            "runtime": {"kind": "service", "env": {"mode": "venv"}},
            "dependencies": ["requests==2.31.0"],
            "service": {
                "host": "127.0.0.1",
                "port": 18105,
                "command": ["-m", "handlers.service"],
                "dependencies": ["torch==2.10.0", "faiss-cpu==1.13.2"],
            },
        },
    )

    assert spec is not None
    assert spec.env_mode == "venv"
    assert spec.venv_dir == runtime_root / "venv"
    assert spec.dependencies == ["torch==2.10.0", "faiss-cpu==1.13.2"]


def test_service_supervisor_registers_governed_ui_surface(tmp_path):
    from adaos.services.skill import service_supervisor as mod

    skill_root = tmp_path / "skills" / "tracker_service"
    skill_root.mkdir(parents=True)
    spec = mod._resolve_service_spec(
        "tracker_service",
        skill_root,
        {
            "name": "tracker_service",
            "runtime": {"kind": "service"},
            "service": {
                "host": "127.0.0.1",
                "port": 18121,
                "command": ["-m", "handlers.service"],
                "ui": {
                    "enabled": True,
                    "path": "/adaos/service-ui",
                    "access": "authenticated",
                    "origin_policy": "same-origin",
                    "embedding": "same-origin",
                    "max_request_bytes": 4096,
                },
            },
        },
    )

    assert spec is not None
    assert spec.ui_enabled is True
    assert spec.ui_path == "/adaos/service-ui"
    assert spec.ui_embedding == "same-origin"
    assert spec.ui_max_request_bytes == 4096


def test_service_supervisor_rejects_ungoverned_ui_policy(tmp_path):
    from adaos.services.skill import service_supervisor as mod

    skill_root = tmp_path / "skills" / "unsafe_service"
    skill_root.mkdir(parents=True)
    with pytest.raises(ValueError, match="origin policy"):
        mod._resolve_service_spec(
            "unsafe_service",
            skill_root,
            {
                "runtime": {"kind": "service"},
                "service": {
                    "port": 18122,
                    "command": ["-m", "handlers.service"],
                    "ui": {"enabled": True, "origin_policy": "any"},
                },
            },
        )


def test_service_supervisor_injects_owner_scoped_storage_locations_only_into_process_env(tmp_path):
    from adaos.services.agent_context import get_ctx
    from adaos.services.skill import service_supervisor as mod
    from adaos.services.storage.blob import BlobStorageBroker, LocalBlobStorageProvider
    from adaos.services.storage.relational import RelationalStorageBroker
    from adaos.adapters.db.relational import SQLiteRelationalStorageProvider

    runtime_root = tmp_path / ".runtime" / "storage_service" / "v1.0"
    skill_root = runtime_root / "slots" / "A" / "src" / "skills" / "storage_service"
    skill_root.mkdir(parents=True)
    spec = mod._resolve_service_spec(
        "storage_service",
        skill_root,
        {
            "runtime": {"kind": "service"},
            "capabilities": ["storage.relational", "storage.blob"],
            "service": {
                "port": 18123,
                "command": ["-m", "handlers.service"],
                "storage": {
                    "relational": {
                        "logical_name": "backend",
                        "environment": "ADAOS_SERVICE_BACKEND_URI",
                        "requirements": {"locality": "any"},
                    },
                    "blob": {
                        "logical_name": "artifacts",
                        "environment": "ADAOS_SERVICE_ARTIFACT_URI",
                        "requirements": {"locality": "any"},
                    },
                },
            },
        },
    )
    assert spec is not None
    ctx = get_ctx()
    object.__setattr__(ctx, "relational_storage", RelationalStorageBroker((SQLiteRelationalStorageProvider(),)))
    object.__setattr__(ctx, "blob_storage", BlobStorageBroker((LocalBlobStorageProvider(),)))
    supervisor = mod.ServiceSkillSupervisor()

    environment = supervisor._service_storage_environment(spec, runtime_root)

    assert environment["ADAOS_SERVICE_BACKEND_URI"].startswith("sqlite:///")
    assert environment["ADAOS_SERVICE_ARTIFACT_URI"].startswith("file:///")
    assert "sqlite:///" not in environment["ADAOS_SERVICE_RELATIONAL_BINDING"]
    assert "file:///" not in environment["ADAOS_SERVICE_BLOB_BINDING"]
    assert '"owner_ref":"skill:storage_service"' in environment["ADAOS_SERVICE_RELATIONAL_BINDING"]


def test_service_supervisor_pythonpath_includes_package_root(tmp_path):
    from adaos.services.agent_context import get_ctx
    from adaos.services.skill import service_supervisor as mod

    ctx = get_ctx()
    skill_root = tmp_path / "skills" / "demo_service"
    skill_root.mkdir(parents=True)

    entries = mod._service_pythonpath(ctx.paths, skill_root).split(os.pathsep)
    package_dir = ctx.paths.package_dir() if callable(ctx.paths.package_dir) else ctx.paths.package_dir

    assert str(skill_root) in entries
    assert str(Path(package_dir).resolve().parent) in entries


def test_service_supervisor_overrides_foreign_skill_scope(monkeypatch, tmp_path):
    from adaos.services.agent_context import get_ctx
    from adaos.services.skill import service_supervisor as mod

    name = "owned_service"
    skill_root = (
        tmp_path / ".runtime" / name / "v0.1" / "slots" / "A" / "src" / "skills" / name
    )
    skill_root.mkdir(parents=True)
    spec = mod.ServiceSpec(
        skill=name,
        skill_root=skill_root,
        host="127.0.0.1",
        port=18129,
        command=["-m", "handlers.service"],
        workdir=skill_root,
        env_mode="global",
        python_selector=None,
        venv_dir=None,
        dependencies=[],
        requirements_file=None,
        health_path="/health",
        health_timeout_ms=1000,
        self_managed_enabled=False,
        crash_max_in_window=3,
        crash_window_s=60,
        crash_cooloff_s=60,
        health_interval_s=10,
        health_failures_before_issue=3,
        hook_on_issue=None,
        hook_on_self_heal=None,
        hook_timeout_s=10.0,
        doctor_enabled=False,
        doctor_cooldown_s=300,
        doctor_issue_types=[],
        doctor_include_log_tail_lines=0,
        publish_topics=("owned_service.changed",),
    )
    captured = {}
    bridge_scope = {}

    class _Proc:
        pid = 9129

        def poll(self):
            return None

    def _popen(_cmd, **kwargs):
        captured.update(kwargs["env"])
        return _Proc()

    monkeypatch.setenv("ADAOS_SKILL_NAME", "foreign_skill")
    monkeypatch.setenv("ADAOS_SKILL_ENV_PATH", str(tmp_path / "foreign" / "skill_env.json"))
    monkeypatch.setattr(mod, "_service_health_ok", lambda _spec: False)
    monkeypatch.setattr(mod, "_service_listener_snapshot", lambda _spec: {"pid": 0})
    monkeypatch.setattr(
        mod,
        "service_event_bridge_environment",
        lambda skill, *, publish_topics=(): (
            bridge_scope.update(skill=skill, publish_topics=tuple(publish_topics))
            or {
                "ADAOS_SERVICE_EVENT_BRIDGE_URL": "http://127.0.0.1:8777/internal",
                "ADAOS_SERVICE_EVENT_BRIDGE_TOKEN": "scoped-token",
            }
        ),
    )
    monkeypatch.setattr(mod.subprocess, "Popen", _popen)

    ctx = get_ctx()
    object.__setattr__(ctx, "config", SimpleNamespace(node_id="node-service-owner"))
    supervisor = mod.ServiceSkillSupervisor()
    supervisor._specs[name] = spec
    supervisor.ensure_discovered = lambda *args, **kwargs: None  # type: ignore[method-assign]
    supervisor._wait_ready = lambda _spec: asyncio.sleep(0)  # type: ignore[method-assign]
    asyncio.run(supervisor.ensure_started(name, spec, force=True))

    bucket = skill_root.parents[4]
    assert captured["ADAOS_SKILL_NAME"] == name
    assert captured["ADAOS_SKILL_ROOT"] == str(skill_root)
    assert captured["ADAOS_SKILL_ENV_PATH"] == str(bucket / "data" / "db" / "skill_env.json")
    assert captured["ADAOS_RUNTIME_INSTANCE_ID"] == mod.runtime_instance_id()
    assert captured["ADAOS_NODE_ID"] == "node-service-owner"
    assert captured["ADAOS_SERVICE_OWNER_PID"] == str(os.getpid())
    assert bridge_scope == {
        "skill": name,
        "publish_topics": ("owned_service.changed",),
    }


def test_service_supervisor_adopts_healthy_untracked_endpoint(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.skill import service_supervisor as mod

    ctx = get_ctx()
    root = Path(ctx.paths.skills_dir()) / "rasa_nlu_service_skill"
    root.mkdir(parents=True, exist_ok=True)
    spec = mod.ServiceSpec(
        skill="rasa_nlu_service_skill",
        skill_root=root,
        host="127.0.0.1",
        port=18092,
        command=["-m", "handlers.main"],
        workdir=root,
        env_mode="venv",
        python_selector="3.11",
        venv_dir=None,
        dependencies=[],
        requirements_file=None,
        health_path="/health",
        health_timeout_ms=1000,
        self_managed_enabled=False,
        crash_max_in_window=3,
        crash_window_s=60,
        crash_cooloff_s=60,
        health_interval_s=10,
        health_failures_before_issue=3,
        hook_on_issue=None,
        hook_on_self_heal=None,
        hook_timeout_s=10.0,
        doctor_enabled=False,
        doctor_cooldown_s=300,
        doctor_issue_types=[],
        doctor_include_log_tail_lines=0,
    )

    popen_called = False

    def _popen_should_not_run(*args, **kwargs):
        nonlocal popen_called
        popen_called = True
        raise AssertionError("healthy untracked service endpoint should not be spawned again")

    monkeypatch.setattr(mod, "_http_get", lambda url, *, timeout_ms: (200, '{"ok": true}'))
    monkeypatch.setattr(
        mod,
        "_service_listener_snapshot",
        lambda service_spec: {
            "pid": 4242,
            "cwd": str(service_spec.workdir),
            "workdir_matches": True,
            "ownership_verified": True,
            "runtime_instance_matches": True,
            "ownership_basis": "runtime_instance",
        },
    )
    monkeypatch.setattr(mod.subprocess, "Popen", _popen_should_not_run)

    supervisor = mod.ServiceSkillSupervisor()
    supervisor._specs[spec.skill] = spec
    supervisor.ensure_discovered = lambda *args, **kwargs: None  # type: ignore[method-assign]

    asyncio.run(supervisor.ensure_started(spec.skill, spec, force=True))

    assert popen_called is False
    status = supervisor.status(spec.skill, check_health=True)
    assert status is not None
    assert status["running"] is False
    assert status["pid"] is None
    assert status["health_ok"] is True
    assert status["external_ready"] is True

    monkeypatch.setattr(
        mod,
        "_service_listener_snapshot",
        lambda service_spec: {
            "pid": 4343,
            "cwd": str(service_spec.workdir),
            "workdir_matches": True,
            "owner_runtime_instance_id": "rt-a-previous",
            "expected_runtime_instance_id": "rt-a-current",
            "service_skill_matches": True,
            "ownership_verified": False,
            "ownership_basis": "runtime_instance_mismatch",
        },
    )
    replaced_status = supervisor.status(spec.skill, check_health=True)
    assert replaced_status is not None
    assert replaced_status["external_ready"] is True
    assert replaced_status["health_observation_source"] == "external_adoption"
    assert "external_listener" not in replaced_status


def test_service_supervisor_stop_terminates_owned_process_tree(monkeypatch):
    from adaos.services.skill import service_supervisor as mod

    alive = True
    terminated: list[tuple[int, float]] = []

    class _Proc:
        pid = 9129

        def poll(self):
            return None if alive else 0

        def terminate(self):
            raise AssertionError("tree termination should handle the tracked process")

        def kill(self):
            raise AssertionError("tree termination should handle the tracked process")

    def _terminate(pid: int, *, timeout_s: float) -> bool:
        nonlocal alive
        terminated.append((pid, timeout_s))
        alive = False
        return True

    monkeypatch.setattr(mod, "_terminate_process_tree", _terminate)
    supervisor = mod.ServiceSkillSupervisor()
    supervisor._procs["nested_service"] = _Proc()
    supervisor._proc_specs["nested_service"] = ("old",)

    asyncio.run(supervisor.stop("nested_service", timeout_s=1.25))

    assert terminated == [(9129, 1.25)]
    assert "nested_service" not in supervisor._procs
    assert "nested_service" not in supervisor._proc_specs


def test_service_supervisor_stop_terminates_verified_external_listener(monkeypatch):
    from adaos.services.skill import service_supervisor as mod

    terminated: list[tuple[int, float]] = []
    monkeypatch.setattr(
        mod,
        "_service_listener_snapshot",
        lambda _spec: {
            "pid": 4242,
            "ownership_verified": True,
            "owner_runtime_instance_id": "rt-a-current",
            "service_skill_matches": True,
        },
    )
    monkeypatch.setattr(
        mod,
        "_terminate_process_tree",
        lambda pid, *, timeout_s: terminated.append((pid, timeout_s)) or True,
    )
    supervisor = mod.ServiceSkillSupervisor()

    class _Spec:
        self_managed_enabled = False
        doctor_enabled = False

    spec = _Spec()
    supervisor._specs["external_service"] = spec
    supervisor._spec_key = lambda _spec: ("external",)  # type: ignore[method-assign]
    supervisor._external_ready_specs["external_service"] = ("external",)
    supervisor._external_ready_at["external_service"] = 10.0

    asyncio.run(supervisor.stop("external_service", timeout_s=1.5))

    assert terminated == [(4242, 1.5)]
    assert "external_service" not in supervisor._external_ready_specs
    assert "external_service" not in supervisor._external_ready_at


def test_service_supervisor_stop_refuses_unverified_external_listener(monkeypatch):
    from adaos.services.skill import service_supervisor as mod

    monkeypatch.setattr(
        mod,
        "_service_listener_snapshot",
        lambda _spec: {
            "pid": 4343,
            "ownership_verified": False,
            "ownership_basis": "runtime_instance_mismatch",
            "service_skill_matches": True,
        },
    )
    monkeypatch.setattr(
        mod,
        "_terminate_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unverified listener must not be terminated")),
    )
    supervisor = mod.ServiceSkillSupervisor()

    class _Spec:
        self_managed_enabled = False
        doctor_enabled = False

    spec = _Spec()
    supervisor._specs["external_service"] = spec
    supervisor._spec_key = lambda _spec: ("external",)  # type: ignore[method-assign]
    supervisor._external_ready_specs["external_service"] = ("external",)

    asyncio.run(supervisor.stop("external_service"))

    assert supervisor._external_ready_specs["external_service"] == ("external",)
    assert supervisor._issues_cache["external_service"][-1]["type"] == "service_stop_owner_unverified"


def test_service_supervisor_resource_pressure_quarantines_verified_external_listener(monkeypatch):
    from adaos.services.skill import service_supervisor as mod

    terminated: list[int] = []
    monkeypatch.setattr(
        mod,
        "_service_listener_snapshot",
        lambda _spec: {
            "pid": 4545,
            "ownership_verified": True,
            "owner_runtime_instance_id": "rt-a-current",
            "service_skill_matches": True,
        },
    )
    monkeypatch.setattr(
        mod,
        "_terminate_process_tree",
        lambda pid, *, timeout_s: terminated.append(pid) or True,
    )
    monkeypatch.setattr(mod, "_process_tree_pids", lambda pid: {int(pid)})
    supervisor = mod.ServiceSkillSupervisor()

    class _Spec:
        self_managed_enabled = False

    spec = _Spec()
    supervisor._specs["external_service"] = spec
    supervisor._spec_key = lambda _spec: ("external",)  # type: ignore[method-assign]
    supervisor._external_ready_specs["external_service"] = ("external",)
    supervisor.ensure_discovered = lambda *args, **kwargs: None  # type: ignore[method-assign]

    async def _refresh_discovered(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    supervisor.refresh_discovered = _refresh_discovered  # type: ignore[method-assign]
    supervisor.status = lambda name, check_health=False: {  # type: ignore[method-assign]
        "name": name,
        "running": False,
        "external_ready": name in supervisor._external_ready_specs,
    }

    result = asyncio.run(
        supervisor.quarantine_resource_pressure(
            "external_service",
            reason="supervisor.memory.skill_pressure",
            pressure={"skill_rss_bytes": 3 * 1024 * 1024 * 1024, "observed_pids": [4545]},
            cooloff_s=90.0,
        )
    )

    assert terminated == [4545]
    assert result["ok"] is True
    assert result["stopped"] is True
    assert result["cooloff_until"] > time.time() + 80.0
    assert result["matched_pids"] == [4545]
    assert supervisor.issues("external_service")[-1]["type"] == "memory_resource_pressure"


def test_service_supervisor_resource_pressure_refuses_stale_same_name_process(monkeypatch):
    from adaos.services.skill import service_supervisor as mod

    monkeypatch.setattr(
        mod,
        "_service_listener_snapshot",
        lambda _spec: {
            "pid": 4545,
            "ownership_verified": True,
            "owner_runtime_instance_id": "rt-a-current",
            "service_skill_matches": True,
        },
    )
    monkeypatch.setattr(mod, "_process_tree_pids", lambda pid: {4545, 4546})
    monkeypatch.setattr(
        mod,
        "_terminate_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("mismatched process must not stop service")),
    )
    supervisor = mod.ServiceSkillSupervisor()

    class _Spec:
        self_managed_enabled = False
        doctor_enabled = False

    spec = _Spec()
    supervisor._specs["external_service"] = spec
    supervisor._spec_key = lambda _spec: ("external",)  # type: ignore[method-assign]
    supervisor._external_ready_specs["external_service"] = ("external",)
    supervisor.ensure_discovered = lambda *args, **kwargs: None  # type: ignore[method-assign]

    async def _refresh_discovered(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    supervisor.refresh_discovered = _refresh_discovered  # type: ignore[method-assign]
    supervisor.status = lambda name, check_health=False: {  # type: ignore[method-assign]
        "name": name,
        "running": False,
        "external_ready": True,
    }

    result = asyncio.run(
        supervisor.quarantine_resource_pressure(
            "external_service",
            reason="supervisor.memory.skill_pressure",
            pressure={"skill_rss_bytes": 3 * 1024 * 1024 * 1024, "observed_pids": [9999]},
        )
    )

    assert result["ok"] is False
    assert result["error"] == "observed_process_owner_mismatch"
    assert supervisor._issues_cache["external_service"][-1]["type"] == "memory_resource_pressure_owner_mismatch"


def test_service_resource_activity_reports_sustained_declared_io_pressure(tmp_path):
    from adaos.services.skill import service_supervisor as mod

    skill_root = tmp_path / "skills" / "observed_service"
    skill_root.mkdir(parents=True)
    manifest = {
        "runtime": {"kind": "service"},
        "service": {"port": 18140, "command": ["-m", "handlers.service"]},
        "memory_budget": {
            "expected_rss_mb": 64,
            "process": {
                "sustained_samples": 2,
                "max_write_bytes_per_second": 100,
                "max_file_descriptors": 8,
            },
        },
    }
    spec = mod._resolve_service_spec("observed_service", skill_root, manifest)
    assert spec is not None
    assert spec.resource_budget == {
        "sustained_samples": 2,
        "max_write_bytes_per_second": 100,
        "max_file_descriptors": 8,
        "expected_rss_mb": 64,
    }
    supervisor = mod.ServiceSkillSupervisor()

    base = {
        "available": True,
        "reason": "sampled",
        "owner_pid": 123,
        "generation": "same",
        "process_total": 1,
        "pids": [123],
        "rss_bytes": 1024,
        "read_bytes": 0,
        "thread_total": 2,
        "open_handle_total": 4,
        "open_handle_kind": "file_descriptors",
    }
    supervisor._record_resource_sample("observed_service", spec, {**base, "observed_at": 10.0, "write_bytes": 0})
    first = supervisor._record_resource_sample(
        "observed_service", spec, {**base, "observed_at": 12.0, "write_bytes": 400}
    )
    second = supervisor._record_resource_sample(
        "observed_service", spec, {**base, "observed_at": 14.0, "write_bytes": 800}
    )

    assert first == []
    assert [item["metric"] for item in second] == ["write_bytes_per_second"]
    activity = supervisor._resource_activity["observed_service"]
    assert activity["write_bytes_per_second"] == 200.0
    assert activity["pressure"] == "sustained"
    assert activity["violations"][0]["samples"] == 2


def test_service_status_reads_cached_resource_activity(monkeypatch, tmp_path):
    from adaos.services.skill import service_supervisor as mod

    skill_root = tmp_path / "skills" / "cached_service"
    skill_root.mkdir(parents=True)
    spec = mod._resolve_service_spec(
        "cached_service",
        skill_root,
        {
            "runtime": {"kind": "service"},
            "service": {"port": 18141, "command": ["-m", "handlers.service"]},
        },
    )
    assert spec is not None

    class _Proc:
        pid = 141
        returncode = None

        @staticmethod
        def poll():
            raise AssertionError("status must not poll processes")

    supervisor = mod.ServiceSkillSupervisor()
    supervisor._specs["cached_service"] = spec
    proc = _Proc()
    supervisor._procs["cached_service"] = proc
    supervisor._process_states["cached_service"] = {
        "process_identity": id(proc),
        "pid": proc.pid,
        "running": True,
        "exit_code": None,
        "observed_at": time.time(),
        "source": "test",
    }
    supervisor._health_states["cached_service"] = {
        "ok": True,
        "observed_at": time.time(),
        "source": "test",
    }
    supervisor._resource_activity["cached_service"] = {
        "schema": "adaos.skill_service_resource_activity.v1",
        "available": True,
        "write_bytes_per_second": 42.0,
    }
    monkeypatch.setattr(supervisor, "ensure_discovered", lambda: None)
    monkeypatch.setattr(
        mod,
        "_process_tree_resource_counters",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("status must not sample processes")),
    )
    monkeypatch.setattr(
        mod,
        "_service_health_ok",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("status must not probe health")),
    )

    status = supervisor.status("cached_service", check_health=True)

    assert status is not None
    assert status["resource_activity"]["write_bytes_per_second"] == 42.0
    assert status["health_ok"] is True
    assert status["health_observation_source"] == "test"


def test_service_supervisor_serializes_concurrent_starts(monkeypatch):
    from adaos.services.skill import service_supervisor as mod

    supervisor = mod.ServiceSkillSupervisor()
    active = 0
    max_active = 0

    async def _ensure_started_owned(name, spec, *, force):  # noqa: ANN001, ARG001
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1

    monkeypatch.setattr(supervisor, "_ensure_started_owned", _ensure_started_owned)

    async def _run() -> None:
        await asyncio.gather(
            supervisor.ensure_started("nested_service", object(), force=False),
            supervisor.ensure_started("nested_service", object(), force=True),
        )

    asyncio.run(_run())

    assert max_active == 1


def test_service_supervisor_dependency_setup_does_not_block_event_loop(monkeypatch, tmp_path):
    from adaos.services.skill import service_supervisor as mod

    skill_root = tmp_path / "skills" / "slow_service"
    skill_root.mkdir(parents=True)
    spec = mod.ServiceSpec(
        skill="slow_service",
        skill_root=skill_root,
        host="127.0.0.1",
        port=18131,
        command=["-m", "handlers.main"],
        workdir=skill_root,
        env_mode="venv",
        python_selector="3.11",
        venv_dir=tmp_path / "venv",
        dependencies=["slow-dependency==1"],
        requirements_file=None,
        health_path="/health",
        health_timeout_ms=1000,
        self_managed_enabled=False,
        crash_max_in_window=3,
        crash_window_s=60,
        crash_cooloff_s=60,
        health_interval_s=10,
        health_failures_before_issue=3,
        hook_on_issue=None,
        hook_on_self_heal=None,
        hook_timeout_s=10.0,
        doctor_enabled=False,
        doctor_cooldown_s=300,
        doctor_issue_types=[],
        doctor_include_log_tail_lines=0,
    )
    setup_started = threading.Event()
    release_setup = threading.Event()
    setup_finished = threading.Event()

    class _Proc:
        pid = 9131

        def poll(self):
            return None

    def _slow_select_python(_spec):
        setup_started.set()
        release_setup.wait(timeout=1.0)
        setup_finished.set()
        return Path(os.sys.executable)

    monkeypatch.setattr(mod, "_service_health_ok", lambda _spec: False)
    monkeypatch.setattr(mod, "_service_listener_snapshot", lambda _spec: {"pid": 0})
    monkeypatch.setattr(mod, "_spawn_service_process", lambda *args, **kwargs: _Proc())

    supervisor = mod.ServiceSkillSupervisor()
    monkeypatch.setattr(supervisor, "_select_python", _slow_select_python)
    monkeypatch.setattr(supervisor, "_wait_ready", lambda _spec: asyncio.sleep(0))

    async def _run() -> bool:
        start_task = asyncio.create_task(supervisor.ensure_started(spec.skill, spec, force=True))
        while not setup_started.is_set():
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.01)
        responsive_during_setup = not setup_finished.is_set()
        release_setup.set()
        await start_task
        return responsive_during_setup

    assert asyncio.run(_run()) is True


def test_service_supervisor_prepares_launch_plan_off_event_loop(monkeypatch, tmp_path):
    from adaos.services.skill import service_supervisor as mod

    name = "launch_plan_service"
    skill_root = tmp_path / ".runtime" / name / "v0.1" / "slots" / "A" / "src" / "skills" / name
    skill_root.mkdir(parents=True)
    spec = mod._resolve_service_spec(
        name,
        skill_root,
        {
            "runtime": {"kind": "service"},
            "service": {"port": 18124, "command": ["-m", "handlers.service"]},
        },
    )
    assert spec is not None
    setup_started = threading.Event()
    setup_finished = threading.Event()

    class _Proc:
        pid = 9132

        def poll(self):
            return None

    def _slow_launch_plan(_name, _spec, _python):  # noqa: ANN001
        setup_started.set()
        time.sleep(0.2)
        setup_finished.set()
        return ([str(os.sys.executable)], {}, tmp_path / "service.log")

    monkeypatch.setattr(mod, "_service_health_ok", lambda _spec: False)
    monkeypatch.setattr(mod, "_service_listener_snapshot", lambda _spec: {"pid": 0})
    monkeypatch.setattr(mod, "_spawn_service_process", lambda *args, **kwargs: _Proc())

    supervisor = mod.ServiceSkillSupervisor()
    monkeypatch.setattr(supervisor, "_select_python", lambda _spec: Path(os.sys.executable))
    monkeypatch.setattr(supervisor, "_service_launch_plan", _slow_launch_plan)
    monkeypatch.setattr(supervisor, "_wait_ready", lambda _spec: asyncio.sleep(0))

    async def _run() -> bool:
        task = asyncio.create_task(supervisor.ensure_started(name, spec, force=True))
        while not setup_started.is_set():
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.02)
        responsive_during_setup = not setup_finished.is_set()
        await task
        return responsive_during_setup

    assert asyncio.run(_run()) is True


def test_service_supervisor_restarts_stale_endpoint_from_old_runtime_instance(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.skill import service_supervisor as mod

    ctx = get_ctx()
    root = Path(ctx.paths.skills_dir()) / ".runtime" / "rasa_nlu_service_skill" / "v0.2" / "slots" / "B" / "src" / "skills" / "rasa_nlu_service_skill"
    root.mkdir(parents=True, exist_ok=True)
    spec = mod.ServiceSpec(
        skill="rasa_nlu_service_skill",
        skill_root=root,
        host="127.0.0.1",
        port=18092,
        command=["-m", "handlers.main"],
        workdir=root,
        env_mode="global",
        python_selector=None,
        venv_dir=None,
        dependencies=[],
        requirements_file=None,
        health_path="/health",
        health_timeout_ms=1000,
        self_managed_enabled=False,
        crash_max_in_window=3,
        crash_window_s=60,
        crash_cooloff_s=60,
        health_interval_s=10,
        health_failures_before_issue=3,
        hook_on_issue=None,
        hook_on_self_heal=None,
        hook_timeout_s=10.0,
        doctor_enabled=False,
        doctor_cooldown_s=300,
        doctor_issue_types=[],
        doctor_include_log_tail_lines=0,
    )

    stale_alive = True
    terminated: list[int] = []
    spawned: list[list[str]] = []

    class _Proc:
        pid = 5252

        def poll(self):
            return None

    def _health_ok(_spec):
        return stale_alive

    def _terminate(pid, *, timeout_s):
        nonlocal stale_alive
        terminated.append(pid)
        stale_alive = False
        return True

    def _popen(cmd, **kwargs):
        spawned.append(list(cmd))
        return _Proc()

    monkeypatch.setattr(mod, "_service_health_ok", _health_ok)
    monkeypatch.setattr(
        mod,
        "_service_listener_snapshot",
        lambda _spec: {
            "pid": 4242,
            "cwd": str(root),
            "workdir_matches": True,
            "owner_runtime_instance_id": "rt-a-old",
            "expected_runtime_instance_id": "rt-a-current",
            "service_skill_matches": True,
            "ownership_verified": False,
            "ownership_basis": "runtime_instance_mismatch",
        },
    )
    monkeypatch.setattr(mod, "_terminate_process_tree", _terminate)
    monkeypatch.setattr(mod.subprocess, "Popen", _popen)

    supervisor = mod.ServiceSkillSupervisor()
    supervisor._specs[spec.skill] = spec
    supervisor.ensure_discovered = lambda *args, **kwargs: None  # type: ignore[method-assign]
    supervisor._wait_ready = lambda _spec: asyncio.sleep(0)  # type: ignore[method-assign]

    asyncio.run(supervisor.ensure_started(spec.skill, spec, force=True))

    assert terminated == [4242]
    assert spawned
    assert supervisor.status(spec.skill)["running"] is True
    issues = supervisor.issues(spec.skill)
    assert issues[-1]["type"] == "stale_service_endpoint"
    assert issues[-1]["details"]["owner_runtime_instance_id"] == "rt-a-old"
    assert issues[-1]["details"]["expected_runtime_instance_id"] == "rt-a-current"
    assert issues[-1]["details"]["ownership_basis"] == "runtime_instance_mismatch"


def test_service_supervisor_refuses_duplicate_start_when_unhealthy_listener_exists(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.skill import service_supervisor as mod

    ctx = get_ctx()
    root = (
        Path(ctx.paths.skills_dir())
        / ".runtime"
        / "rasa_nlu_service_skill"
        / "v0.2"
        / "slots"
        / "A"
        / "src"
        / "skills"
        / "rasa_nlu_service_skill"
    )
    root.mkdir(parents=True, exist_ok=True)
    spec = mod.ServiceSpec(
        skill="rasa_nlu_service_skill",
        skill_root=root,
        host="127.0.0.1",
        port=18092,
        command=["-m", "handlers.main"],
        workdir=root,
        env_mode="global",
        python_selector=None,
        venv_dir=None,
        dependencies=[],
        requirements_file=None,
        health_path="/health",
        health_timeout_ms=1000,
        self_managed_enabled=False,
        crash_max_in_window=3,
        crash_window_s=60,
        crash_cooloff_s=60,
        health_interval_s=10,
        health_failures_before_issue=3,
        hook_on_issue=None,
        hook_on_self_heal=None,
        hook_timeout_s=10.0,
        doctor_enabled=False,
        doctor_cooldown_s=300,
        doctor_issue_types=[],
        doctor_include_log_tail_lines=0,
    )

    spawned: list[list[str]] = []

    def _popen_should_not_run(cmd, **kwargs):
        spawned.append(list(cmd))
        raise AssertionError("unhealthy occupied port should not spawn duplicate service")

    monkeypatch.setattr(mod, "_service_health_ok", lambda _spec: False)
    monkeypatch.setattr(
        mod,
        "_service_listener_snapshot",
        lambda _spec: {
            "pid": 4242,
            "cwd": str(root),
            "cmdline": ["python", "-m", "handlers.main"],
            "workdir_matches": True,
        },
    )
    monkeypatch.setattr(mod.subprocess, "Popen", _popen_should_not_run)

    supervisor = mod.ServiceSkillSupervisor()
    supervisor._specs[spec.skill] = spec
    supervisor.ensure_discovered = lambda *args, **kwargs: None  # type: ignore[method-assign]

    asyncio.run(supervisor.ensure_started(spec.skill, spec, force=False))

    assert spawned == []
    assert supervisor._cooloff_until[spec.skill] > time.time()
    issues = supervisor.issues(spec.skill)
    assert issues[-1]["type"] == "service_endpoint_unhealthy_listener_present"
    assert issues[-1]["details"]["pid"] == 4242


def test_service_supervisor_installs_changed_dependencies_for_existing_venv(tmp_path, monkeypatch):
    from adaos.services.skill import service_supervisor as mod

    skill_root = tmp_path / "skills" / "dep_service"
    skill_root.mkdir(parents=True)
    venv_dir = tmp_path / "venv"
    python = venv_dir / ("Scripts/python.exe" if mod.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")

    def _spec(dependencies: list[str]) -> mod.ServiceSpec:
        return mod.ServiceSpec(
            skill="dep_service",
            skill_root=skill_root,
            host="127.0.0.1",
            port=18111,
            command=["-m", "handlers.main"],
            workdir=skill_root,
            env_mode="venv",
            python_selector="3.11",
            venv_dir=venv_dir,
            dependencies=dependencies,
            requirements_file=None,
            health_path="/health",
            health_timeout_ms=1000,
            self_managed_enabled=False,
            crash_max_in_window=3,
            crash_window_s=60,
            crash_cooloff_s=60,
            health_interval_s=10,
            health_failures_before_issue=3,
            hook_on_issue=None,
            hook_on_self_heal=None,
            hook_timeout_s=10.0,
            doctor_enabled=False,
            doctor_cooldown_s=300,
            doctor_issue_types=[],
            doctor_include_log_tail_lines=0,
        )

    installs: list[list[str]] = []
    supervisor = mod.ServiceSkillSupervisor()
    monkeypatch.setattr(supervisor, "_install_deps", lambda _python, spec: installs.append(list(spec.dependencies)))

    assert supervisor._select_python(_spec(["demo-dep==1"])) == python
    assert installs == [["demo-dep==1"]]

    assert supervisor._select_python(_spec(["demo-dep==1"])) == python
    assert installs == [["demo-dep==1"]]

    assert supervisor._select_python(_spec(["demo-dep==2"])) == python
    assert installs == [["demo-dep==1"], ["demo-dep==2"]]


def test_service_supervisor_refreshes_host_site_overlay_for_existing_venv_with_current_marker(tmp_path, monkeypatch):
    from adaos.services.skill import service_supervisor as mod

    skill_root = tmp_path / "skills" / "dep_service"
    skill_root.mkdir(parents=True)
    venv_dir = tmp_path / "venv"
    python = venv_dir / ("Scripts/python.exe" if mod.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    host_site = tmp_path / "host" / "Lib" / "site-packages"
    host_site.mkdir(parents=True)

    spec = mod.ServiceSpec(
        skill="dep_service",
        skill_root=skill_root,
        host="127.0.0.1",
        port=18111,
        command=["-m", "handlers.main"],
        workdir=skill_root,
        env_mode="venv",
        python_selector="3.11",
        venv_dir=venv_dir,
        dependencies=["demo-dep==1"],
        requirements_file=None,
        health_path="/health",
        health_timeout_ms=1000,
        self_managed_enabled=False,
        crash_max_in_window=3,
        crash_window_s=60,
        crash_cooloff_s=60,
        health_interval_s=10,
        health_failures_before_issue=3,
        hook_on_issue=None,
        hook_on_self_heal=None,
        hook_timeout_s=10.0,
        doctor_enabled=False,
        doctor_cooldown_s=300,
        doctor_issue_types=[],
        doctor_include_log_tail_lines=0,
    )

    supervisor = mod.ServiceSkillSupervisor()
    marker_path = venv_dir / ".adaos-service-deps.json"
    marker_path.write_text(supervisor._dependency_marker(spec), encoding="utf-8")
    monkeypatch.setattr(mod, "_current_interpreter_site_packages", lambda: [host_site])
    monkeypatch.setattr(supervisor, "_install_deps", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected install")))

    assert supervisor._select_python(spec) == python
    overlay = mod._venv_site_packages(venv_dir) / "_adaos_host_site.pth"
    assert overlay.read_text(encoding="utf-8") == f"{host_site.resolve()}\n"
