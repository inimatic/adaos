import asyncio
import os
from pathlib import Path
import time


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

    (version_root / "active").write_text("B", encoding="utf-8")
    supervisor.ensure_discovered(force=True)
    status = supervisor.status("slot_service")

    assert status is not None
    assert status["port"] == 1113
    assert status["venv_dir"].endswith(str(Path("v0.1") / "venv"))


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


def test_service_supervisor_restarts_stale_endpoint_from_old_runtime_location(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.skill import service_supervisor as mod

    ctx = get_ctx()
    root = Path(ctx.paths.skills_dir()) / ".runtime" / "rasa_nlu_service_skill" / "v0.2" / "slots" / "B" / "src" / "skills" / "rasa_nlu_service_skill"
    root.mkdir(parents=True, exist_ok=True)
    old_root = Path(ctx.paths.skills_dir()) / ".runtime" / "rasa_nlu_service_skill" / "v0.1" / "slots" / "A" / "src" / "skills" / "rasa_nlu_service_skill"
    old_root.mkdir(parents=True, exist_ok=True)
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
            "cwd": str(old_root),
            "workdir_matches": False,
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
