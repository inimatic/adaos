from pathlib import Path


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
    assert status["venv_dir"].endswith(str(Path("slots") / "A" / "venv"))

    (version_root / "active").write_text("B", encoding="utf-8")
    supervisor.ensure_discovered(force=True)
    status = supervisor.status("slot_service")

    assert status is not None
    assert status["port"] == 1113
    assert status["venv_dir"].endswith(str(Path("slots") / "B" / "venv"))
