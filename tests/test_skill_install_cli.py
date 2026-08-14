from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from adaos.apps.cli.commands import skill as skill_cmd


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
