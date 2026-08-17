from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.agent_context import get_ctx
from adaos.services.skill import manager as skill_manager_module
from adaos.services.skill.dependency_requirements import (
    MissingLocalDependencyError,
    resolve_skill_dependency_args,
)
from adaos.services.skill.manager import SkillDependencyIsolationError, SkillManager
from adaos.services.skill.runtime_env import SkillRuntimeEnvironment


def test_local_non_python_module_dependency_is_checked_without_pip_install(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    skill_dir = repo_root / ".adaos" / "workspace" / "skills" / "demo_skill"
    backend = repo_root / "src" / "adaos" / "integrations" / "adaos-backend"
    skill_dir.mkdir(parents=True)
    backend.mkdir(parents=True)
    (backend / "package.json").write_text('{"name":"adaos-backend"}\n', encoding="utf-8")

    args = resolve_skill_dependency_args(
        ["requests", "src/adaos/integrations/adaos-backend"],
        skill_dir=skill_dir,
        repo_root=repo_root,
    )

    assert args == ["requests"]


def test_editable_marker_is_dropped_for_non_python_module_dependency(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    skill_dir = repo_root / ".adaos" / "workspace" / "skills" / "demo_skill"
    backend = repo_root / "src" / "adaos" / "integrations" / "adaos-backend"
    skill_dir.mkdir(parents=True)
    backend.mkdir(parents=True)
    (backend / "package.json").write_text('{"name":"adaos-backend"}\n', encoding="utf-8")

    args = resolve_skill_dependency_args(
        ["-e", "src/adaos/integrations/adaos-backend"],
        skill_dir=skill_dir,
        repo_root=repo_root,
    )

    assert args == []


def test_local_python_dependency_is_resolved_from_repo_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    skill_dir = repo_root / ".adaos" / "workspace" / "skills" / "demo_skill"
    package = repo_root / "src" / "adaos" / "integrations" / "python-module"
    skill_dir.mkdir(parents=True)
    package.mkdir(parents=True)
    (package / "pyproject.toml").write_text("[project]\nname = 'python-module'\nversion = '0.1.0'\n", encoding="utf-8")

    args = resolve_skill_dependency_args(
        ["src/adaos/integrations/python-module"],
        skill_dir=skill_dir,
        repo_root=repo_root,
    )

    assert args == [str(package.resolve())]


def test_missing_submodule_dependency_reports_init_command(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    skill_dir = repo_root / ".adaos" / "workspace" / "skills" / "demo_skill"
    skill_dir.mkdir(parents=True)
    (repo_root / ".gitmodules").write_text(
        "\n".join(
            [
                '[submodule "src/adaos/integrations/adaos-backend"]',
                "\tpath = src/adaos/integrations/adaos-backend",
                "\turl = git@example.invalid:adaos-backend.git",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(MissingLocalDependencyError) as excinfo:
        resolve_skill_dependency_args(
            ["src/adaos/integrations/adaos-backend"],
            skill_dir=skill_dir,
            repo_root=repo_root,
        )

    message = str(excinfo.value)
    assert "src/adaos/integrations/adaos-backend" in message
    assert "git submodule update --init --recursive src/adaos/integrations/adaos-backend" in message


def test_light_non_service_skill_dependencies_install_into_runtime_vendor(monkeypatch, tmp_path: Path) -> None:
    ctx = get_ctx()
    mgr = SkillManager(git=ctx.git, paths=ctx.paths, caps=SimpleNamespace(require=lambda *_args, **_kwargs: None))
    env = SkillRuntimeEnvironment(skills_root=tmp_path / "skills", skill_name="demo_skill")
    env.prepare_version("1.0.0")
    slot = env.build_slot_paths("1.0.0", "A")
    skill_dir = tmp_path / "demo_skill"
    skill_dir.mkdir()

    commands: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs):
        commands.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(skill_manager_module.subprocess, "run", _run)
    monkeypatch.setattr(skill_manager_module, "ensure_dependency_disk_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mgr, "_constraints_file", lambda: None)
    monkeypatch.setattr(mgr, "_repo_root_for_dependency_resolution", lambda: tmp_path)

    paths = mgr._install_python_dependencies(
        manifest={"dependencies": ["requests==2.31.0"]},
        slot=slot,
        skill_dir=skill_dir,
    )

    assert paths == [str(slot.vendor_dir)]
    assert len(commands) == 1
    assert "--target" in commands[0]
    assert str(slot.vendor_dir) in commands[0]
    assert commands[0][-1] == "requests==2.31.0"


def test_satisfied_runtime_vendor_is_reused_without_disk_guard_or_install(monkeypatch, tmp_path: Path) -> None:
    ctx = get_ctx()
    mgr = SkillManager(git=ctx.git, paths=ctx.paths, caps=SimpleNamespace(require=lambda *_args, **_kwargs: None))
    env = SkillRuntimeEnvironment(skills_root=tmp_path / "skills", skill_name="weather_skill")
    env.prepare_version("2.6.18")
    slot = env.build_slot_paths("2.6.18", "A")
    skill_dir = tmp_path / "weather_skill"
    skill_dir.mkdir()
    dist_info = slot.vendor_dir / "requests-2.34.2.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: requests\nVersion: 2.34.2\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        skill_manager_module.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pip must not run")),
    )
    monkeypatch.setattr(
        skill_manager_module,
        "ensure_dependency_disk_budget",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("disk guard must not run")),
    )
    monkeypatch.setattr(mgr, "_constraints_file", lambda: None)
    monkeypatch.setattr(mgr, "_repo_root_for_dependency_resolution", lambda: tmp_path)

    paths = mgr._install_python_dependencies(
        manifest={"dependencies": ["requests>=2.31"]},
        slot=slot,
        skill_dir=skill_dir,
    )

    assert paths == [str(slot.vendor_dir)]


def test_runtime_vendor_reuse_rejects_missing_transitive_dependency(tmp_path: Path) -> None:
    vendor_dir = tmp_path / "vendor"
    dist_info = vendor_dir / "requests-2.34.2.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        "Name: requests\n"
        "Version: 2.34.2\n"
        "Requires-Dist: urllib3>=2\n",
        encoding="utf-8",
    )

    assert skill_manager_module._vendor_satisfies_requirements(vendor_dir, ["requests>=2.31"]) is False


def test_exact_runtime_restore_selects_requested_version_and_slot(monkeypatch, tmp_path: Path) -> None:
    ctx = get_ctx()
    mgr = SkillManager(git=ctx.git, paths=ctx.paths, caps=SimpleNamespace(require=lambda *_args, **_kwargs: None))
    env = SkillRuntimeEnvironment(skills_root=tmp_path / "skills", skill_name="weather_skill")
    env.prepare_version("2.6.18")
    target = env.build_slot_paths("2.6.18", "B")
    target.resolved_manifest.write_text(
        '{"name":"weather_skill","version":"2.6.18"}\n',
        encoding="utf-8",
    )
    target_source = target.src_dir / "skills" / "weather_skill"
    target_source.mkdir(parents=True)
    (target_source / "skill.yaml").write_text("name: weather_skill\nversion: 2.6.18\n", encoding="utf-8")
    env.prepare_version("2.6.19")
    env.set_active_slot("2.6.19", "A")
    env.active_version_marker().write_text("2.6.19", encoding="utf-8")

    monkeypatch.setattr(mgr, "_runtime_env", lambda _name: env)
    monkeypatch.setattr(skill_manager_module, "install_skill_in_capacity", lambda *_args, **_kwargs: None)

    result = mgr.restore_runtime_selection_exact("weather_skill", version="2.6.18", slot="B")

    assert result == {
        "ok": True,
        "restored_active_version": "2.6.18",
        "restored_active_slot": "B",
    }
    assert env.resolve_active_version() == "2.6.18"
    assert env.read_active_slot("2.6.18") == "B"
    assert env.read_deactivation() == {}


def test_exact_runtime_restore_rejects_manifest_without_skill_source(monkeypatch, tmp_path: Path) -> None:
    ctx = get_ctx()
    mgr = SkillManager(git=ctx.git, paths=ctx.paths, caps=SimpleNamespace(require=lambda *_args, **_kwargs: None))
    env = SkillRuntimeEnvironment(skills_root=tmp_path / "skills", skill_name="weather_skill")
    env.prepare_version("2.6.18")
    target = env.build_slot_paths("2.6.18", "B")
    target.resolved_manifest.write_text(
        '{"name":"weather_skill","version":"2.6.18"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(mgr, "_runtime_env", lambda _name: env)

    with pytest.raises(RuntimeError, match="reason=skill_source_missing"):
        mgr.restore_runtime_selection_exact("weather_skill", version="2.6.18", slot="B")


def test_light_service_skill_dependencies_install_into_runtime_vendor_for_inprocess_tools(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ctx = get_ctx()
    mgr = SkillManager(git=ctx.git, paths=ctx.paths, caps=SimpleNamespace(require=lambda *_args, **_kwargs: None))
    env = SkillRuntimeEnvironment(skills_root=tmp_path / "skills", skill_name="slideshow_skill")
    env.prepare_version("1.0.0")
    slot = env.build_slot_paths("1.0.0", "A")
    skill_dir = tmp_path / "slideshow_skill"
    skill_dir.mkdir()

    commands: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs):
        commands.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(skill_manager_module.subprocess, "run", _run)
    monkeypatch.setattr(skill_manager_module, "ensure_dependency_disk_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mgr, "_constraints_file", lambda: None)
    monkeypatch.setattr(mgr, "_repo_root_for_dependency_resolution", lambda: tmp_path)

    paths = mgr._install_python_dependencies(
        manifest={"runtime": {"kind": "service"}, "dependencies": ["pillow>=10.0.0"]},
        slot=slot,
        skill_dir=skill_dir,
    )

    assert paths == [str(slot.vendor_dir)]
    assert len(commands) == 1
    assert "--target" in commands[0]
    assert str(slot.vendor_dir) in commands[0]
    assert commands[0][-1] == "pillow>=10.0.0"


def test_heavy_non_service_skill_dependencies_require_explicit_isolation(monkeypatch, tmp_path: Path) -> None:
    ctx = get_ctx()
    mgr = SkillManager(git=ctx.git, paths=ctx.paths, caps=SimpleNamespace(require=lambda *_args, **_kwargs: None))
    env = SkillRuntimeEnvironment(skills_root=tmp_path / "skills", skill_name="media_indexer_skill")
    env.prepare_version("1.0.0")
    slot = env.build_slot_paths("1.0.0", "A")
    skill_dir = tmp_path / "media_indexer_skill"
    skill_dir.mkdir()

    commands: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs):
        commands.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(skill_manager_module.subprocess, "run", _run)
    monkeypatch.setattr(mgr, "_constraints_file", lambda: None)
    monkeypatch.setattr(mgr, "_repo_root_for_dependency_resolution", lambda: tmp_path)

    with pytest.raises(SkillDependencyIsolationError, match="runtime.kind: service"):
        mgr._install_python_dependencies(
            manifest={"dependencies": ["torch==2.10.0", "opencv-python-headless==4.13.0.92"]},
            slot=slot,
            skill_dir=skill_dir,
        )

    assert commands == []


def test_heavy_service_skill_dependencies_are_not_vendor_installed_by_default(monkeypatch, tmp_path: Path) -> None:
    ctx = get_ctx()
    mgr = SkillManager(git=ctx.git, paths=ctx.paths, caps=SimpleNamespace(require=lambda *_args, **_kwargs: None))
    env = SkillRuntimeEnvironment(skills_root=tmp_path / "skills", skill_name="neural_nlu_service_skill")
    env.prepare_version("1.0.0")
    slot = env.build_slot_paths("1.0.0", "A")
    skill_dir = tmp_path / "neural_nlu_service_skill"
    skill_dir.mkdir()

    commands: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs):
        commands.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(skill_manager_module.subprocess, "run", _run)
    monkeypatch.setattr(mgr, "_constraints_file", lambda: None)
    monkeypatch.setattr(mgr, "_repo_root_for_dependency_resolution", lambda: tmp_path)

    with pytest.raises(SkillDependencyIsolationError, match="service boundary"):
        mgr._install_python_dependencies(
            manifest={"runtime": {"kind": "service", "env": {"mode": "venv"}}, "dependencies": ["torch==2.10.0"]},
            slot=slot,
            skill_dir=skill_dir,
        )

    assert commands == []


def test_explicit_shared_dependency_mode_installs_into_current_interpreter(monkeypatch, tmp_path: Path) -> None:
    ctx = get_ctx()
    mgr = SkillManager(git=ctx.git, paths=ctx.paths, caps=SimpleNamespace(require=lambda *_args, **_kwargs: None))
    env = SkillRuntimeEnvironment(skills_root=tmp_path / "skills", skill_name="legacy_skill")
    env.prepare_version("1.0.0")
    slot = env.build_slot_paths("1.0.0", "A")
    skill_dir = tmp_path / "legacy_skill"
    skill_dir.mkdir()

    commands: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs):
        commands.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(skill_manager_module.subprocess, "run", _run)
    monkeypatch.setattr(skill_manager_module, "ensure_dependency_disk_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mgr, "_constraints_file", lambda: None)
    monkeypatch.setattr(mgr, "_repo_root_for_dependency_resolution", lambda: tmp_path)
    monkeypatch.setattr(skill_manager_module, "_environment_satisfies_requirements", lambda _args: False)

    paths = mgr._install_python_dependencies(
        manifest={
            "runtime": {"env": {"mode": "shared"}},
            "dependencies": ["requests==2.31.0"],
        },
        slot=slot,
        skill_dir=skill_dir,
    )

    assert paths == []
    assert len(commands) == 1
    assert "--target" not in commands[0]
    assert commands[0][-1] == "requests==2.31.0"


def test_explicit_shared_dependency_mode_reuses_satisfied_interpreter(monkeypatch, tmp_path: Path) -> None:
    ctx = get_ctx()
    mgr = SkillManager(git=ctx.git, paths=ctx.paths, caps=SimpleNamespace(require=lambda *_args, **_kwargs: None))
    env = SkillRuntimeEnvironment(skills_root=tmp_path / "skills", skill_name="shared_ml_skill")
    env.prepare_version("1.0.0")
    slot = env.build_slot_paths("1.0.0", "A")
    skill_dir = tmp_path / "shared_ml_skill"
    skill_dir.mkdir()

    monkeypatch.setattr(
        skill_manager_module.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pip must not run")),
    )
    monkeypatch.setattr(
        skill_manager_module,
        "ensure_dependency_disk_budget",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("disk guard must not run")),
    )
    monkeypatch.setattr(skill_manager_module, "_environment_satisfies_requirements", lambda args: list(args) == ["torch"])
    monkeypatch.setattr(mgr, "_constraints_file", lambda: None)
    monkeypatch.setattr(mgr, "_repo_root_for_dependency_resolution", lambda: tmp_path)

    paths = mgr._install_python_dependencies(
        manifest={
            "runtime": {"env": {"mode": "shared", "allow_heavy_dependencies": True}},
            "dependencies": ["torch"],
        },
        slot=slot,
        skill_dir=skill_dir,
    )

    assert paths == []


def test_heavy_in_process_dependencies_can_be_explicitly_allowed(monkeypatch, tmp_path: Path) -> None:
    ctx = get_ctx()
    mgr = SkillManager(git=ctx.git, paths=ctx.paths, caps=SimpleNamespace(require=lambda *_args, **_kwargs: None))
    env = SkillRuntimeEnvironment(skills_root=tmp_path / "skills", skill_name="transitional_skill")
    env.prepare_version("1.0.0")
    slot = env.build_slot_paths("1.0.0", "A")
    skill_dir = tmp_path / "transitional_skill"
    skill_dir.mkdir()

    commands: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs):
        commands.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(skill_manager_module.subprocess, "run", _run)
    monkeypatch.setattr(skill_manager_module, "ensure_dependency_disk_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mgr, "_constraints_file", lambda: None)
    monkeypatch.setattr(mgr, "_repo_root_for_dependency_resolution", lambda: tmp_path)

    paths = mgr._install_python_dependencies(
        manifest={
            "runtime": {"env": {"allow_heavy_dependencies": True}},
            "dependencies": ["torch==2.10.0"],
        },
        slot=slot,
        skill_dir=skill_dir,
    )

    assert paths == [str(slot.vendor_dir)]
    assert "--target" in commands[0]
