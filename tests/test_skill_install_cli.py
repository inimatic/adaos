from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from adaos.apps.cli.commands import skill as skill_cmd


def test_skill_reconcile_registers_only_manifest_backed_directories(
    monkeypatch, tmp_path: Path
) -> None:
    skills_dir = tmp_path / "skills"
    for name in ("demo_skill", ".runtime", ".git", "scratch"):
        (skills_dir / name).mkdir(parents=True)
    (skills_dir / "demo_skill" / "skill.yaml").write_text(
        "name: demo_skill\nversion: 1.0.0\n", encoding="utf-8"
    )
    registered: list[str] = []
    unregistered: list[str] = []
    manager = SimpleNamespace(
        reg=SimpleNamespace(
            register=lambda name: registered.append(name),
            unregister=lambda name: unregistered.append(name),
        )
    )
    monkeypatch.setattr(skill_cmd, "_mgr", lambda: manager)
    monkeypatch.setattr(
        skill_cmd,
        "get_ctx",
        lambda: SimpleNamespace(
            paths=SimpleNamespace(skills_dir=lambda: skills_dir)
        ),
    )

    result = CliRunner().invoke(skill_cmd.app, ["reconcile-fs-to-db"])

    assert result.exit_code == 0, result.output
    assert registered == ["demo_skill"]
    assert set(unregistered) == {".runtime", ".git"}
    assert ".runtime" not in result.output


def test_skill_install_can_prepare_the_current_workspace_tree(monkeypatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "workspace" / "skills" / "demo_skill"
    skill_dir.mkdir(parents=True)
    calls: list[tuple[str, object]] = []

    class _Manager:
        def install(self, *_args, **_kwargs):
            raise AssertionError("registry installation must not run for --source workspace")

        def validate_skill(self, name: str, **kwargs):
            calls.append(("validate", (name, kwargs)))
            return SimpleNamespace(ok=True)

        def prepare_runtime(self, name: str, **kwargs):
            calls.append(("prepare", (name, kwargs)))
            return SimpleNamespace(
                name=name,
                version="1.2.3",
                slot="B",
                resolved_manifest=tmp_path / "resolved.manifest.json",
                tests={"pytest": SimpleNamespace(status="passed")},
            )

        def activate_for_space(self, name: str, **kwargs):
            calls.append(("activate", (name, kwargs)))
            return "B"

    monkeypatch.setattr(skill_cmd, "_mgr", lambda: _Manager())
    monkeypatch.setattr(
        skill_cmd,
        "get_ctx",
        lambda: SimpleNamespace(
            paths=SimpleNamespace(skills_workspace_dir=lambda: tmp_path / "workspace" / "skills")
        ),
    )
    monkeypatch.setattr(skill_cmd, "_refresh_runtime_side_effects", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(skill_cmd, "default_webspace_id", lambda: "desktop")

    result = CliRunner().invoke(
        skill_cmd.app,
        [
            "install",
            "demo_skill",
            "--source",
            "workspace",
            "--local",
            "--test",
            "--silent",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "installed demo_skill v1.2.3 into slot B" in result.output
    assert calls[0] == (
        "validate",
        (
            "demo_skill",
            {
                "strict": True,
                "probe_tools": False,
                "source": "workspace",
                "path": skill_dir.resolve(),
            },
        ),
    )
    assert calls[1] == (
        "prepare",
        (
            "demo_skill",
            {
                "path": skill_dir.resolve(),
                "run_tests": True,
                "preferred_slot": None,
            },
        ),
    )


def test_skill_install_rejects_unknown_source_before_contacting_registry(monkeypatch) -> None:
    monkeypatch.setattr(skill_cmd, "_mgr", lambda: (_ for _ in ()).throw(AssertionError("not called")))

    result = CliRunner().invoke(
        skill_cmd.app,
        ["install", "demo_skill", "--source", "temporary"],
    )

    assert result.exit_code != 0
    assert "source must be 'registry' or 'workspace'" in result.output


def test_skill_install_recovery_requires_tests_and_allows_deactivated_prepare(monkeypatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "workspace" / "skills" / "demo_skill"
    skill_dir.mkdir(parents=True)
    prepared: list[dict[str, object]] = []

    class _Manager:
        def validate_skill(self, *_args, **_kwargs):
            return SimpleNamespace(ok=True)

        def prepare_runtime(self, name: str, **kwargs):
            prepared.append(kwargs)
            return SimpleNamespace(
                name=name,
                version="1.2.3",
                slot="B",
                resolved_manifest=tmp_path / "resolved.manifest.json",
                tests={"pytest": SimpleNamespace(status="passed")},
            )

        def activate_for_space(self, *_args, **_kwargs):
            return "B"

    monkeypatch.setattr(skill_cmd, "_mgr", lambda: _Manager())
    monkeypatch.setattr(
        skill_cmd,
        "get_ctx",
        lambda: SimpleNamespace(
            paths=SimpleNamespace(skills_workspace_dir=lambda: tmp_path / "workspace" / "skills")
        ),
    )
    monkeypatch.setattr(skill_cmd, "_refresh_runtime_side_effects", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(skill_cmd, "default_webspace_id", lambda: "desktop")

    rejected = CliRunner().invoke(
        skill_cmd.app,
        ["install", "demo_skill", "--source", "workspace", "--local", "--recover", "--silent"],
    )
    assert rejected.exit_code != 0
    assert "--recover requires --test" in rejected.output

    recovered = CliRunner().invoke(
        skill_cmd.app,
        [
            "install",
            "demo_skill",
            "--source",
            "workspace",
            "--local",
            "--recover",
            "--test",
            "--silent",
        ],
    )
    assert recovered.exit_code == 0, recovered.output
    assert prepared == [
        {
            "path": skill_dir.resolve(),
            "run_tests": True,
            "preferred_slot": None,
            "allow_deactivated": True,
        }
    ]


def test_registry_install_does_not_activate_failed_validation(monkeypatch) -> None:
    calls: list[str] = []

    class _Manager:
        def sync(self):
            calls.append("sync")

        def install(self, *_args, **_kwargs):
            calls.append("install")
            meta = SimpleNamespace(id=SimpleNamespace(value="demo_skill"))
            return meta, SimpleNamespace(ok=False, issues=["blocking async I/O"])

        def prepare_runtime(self, *_args, **_kwargs):
            raise AssertionError("invalid skill must not be prepared")

    monkeypatch.setattr(skill_cmd, "_mgr", lambda: _Manager())

    result = CliRunner().invoke(
        skill_cmd.app,
        ["install", "demo_skill", "--source", "registry", "--local", "--silent"],
    )

    assert result.exit_code == 1
    assert calls == ["sync", "install"]


def test_registry_install_rejects_project_owned_skill_before_sync(monkeypatch) -> None:
    calls: list[str] = []

    class _Manager:
        def ensure_standalone_mutation_allowed(self, name: str, *, operation: str) -> None:
            calls.append(f"preflight:{name}:{operation}")
            raise RuntimeError(
                "project_owned_component: skill:demo_skill is managed by an active project deployment"
            )

        def sync(self):
            calls.append("sync")

    monkeypatch.setattr(skill_cmd, "_mgr", lambda: _Manager())

    result = CliRunner().invoke(
        skill_cmd.app,
        ["install", "demo_skill", "--source", "registry", "--local", "--silent"],
    )

    assert result.exit_code == 1
    assert "project_owned_component" in result.output
    assert calls == ["preflight:demo_skill:skill install"]
