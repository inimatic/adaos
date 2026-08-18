from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from adaos.ports.skill_context import CurrentSkill
from adaos.sdk.data import context as sdk_context
from adaos.services.skill.context import SkillContextService
from adaos.services.workspace_registry import write_workspace_registry


class _SkillCtx:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path]] = []
        self.current = None

    def set(self, name: str, path: Path) -> bool:
        self.calls.append((name, path))
        if not path.exists():
            return False
        self.current = CurrentSkill(name=name, path=path)
        return True

    def set_loaded(self, name: str, path: Path, **kwargs) -> bool:
        self.calls.append((name, path))
        self.current = CurrentSkill(name=name, path=path, **kwargs)
        return True

    def clear(self) -> None:
        self.current = None

    def get(self):
        return self.current


class _ExplodingRepo:
    def get(self, name: str):
        raise AssertionError("skills_repo.get should not be used when registry metadata is available")


def test_set_current_skill_prefers_workspace_registry(tmp_path: Path):
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "weather_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text("id: weather_skill\nversion: '1.0.0'\n", encoding="utf-8")

    write_workspace_registry(
        workspace,
        {
            "version": 1,
            "updated_at": "2026-04-18T00:00:00+00:00",
            "skills": [
                {
                    "kind": "skill",
                    "id": "weather_skill",
                    "name": "weather_skill",
                    "path": "skills/weather_skill",
                    "source": {"path": "skills/weather_skill", "manifest": "skills/weather_skill/skill.yaml"},
                }
            ],
            "scenarios": [],
        },
    )

    skill_ctx = _SkillCtx()
    service = SkillContextService(
        ctx=SimpleNamespace(
            paths=SimpleNamespace(workspace_dir=lambda: workspace),
            skill_ctx=skill_ctx,
            skills_repo=_ExplodingRepo(),
        )
    )

    assert service.set_current_skill("weather_skill") is True
    assert skill_ctx.calls == [("weather_skill", skill_dir.resolve())]


def test_use_current_skill_restores_outer_loaded_context(tmp_path: Path, monkeypatch) -> None:
    outer_dir = tmp_path / "outer"
    inner_dir = tmp_path / "inner"
    outer_dir.mkdir()
    inner_dir.mkdir()
    skill_ctx = _SkillCtx()
    outer = CurrentSkill(
        name="outer_skill",
        path=outer_dir,
        runtime_log_path=tmp_path / "outer.runtime.log",
    )
    skill_ctx.set_loaded(
        outer.name,
        outer.path,
        runtime_log_path=outer.runtime_log_path,
    )

    class _Service:
        def get_current_skill(self):
            return skill_ctx.get()

        def set_current_skill(self, name: str) -> bool:
            return skill_ctx.set_loaded(name, inner_dir)

        def restore_current_skill(self, current) -> bool:
            service = SkillContextService(ctx=SimpleNamespace(skill_ctx=skill_ctx))
            return service.restore_current_skill(current)

    monkeypatch.setattr(sdk_context, "_service", lambda: _Service())

    with sdk_context.use_current_skill("inner_skill") as pushed:
        assert pushed is True
        assert skill_ctx.get().name == "inner_skill"

    restored = skill_ctx.get()
    assert restored.name == "outer_skill"
    assert restored.path == outer_dir
    assert restored.runtime_log_path == tmp_path / "outer.runtime.log"


def test_use_current_skill_same_binding_is_noop(tmp_path: Path, monkeypatch) -> None:
    skill_dir = tmp_path / "weather_skill"
    skill_dir.mkdir()
    skill_ctx = _SkillCtx()
    skill_ctx.set_loaded("weather_skill", skill_dir)
    calls_before = list(skill_ctx.calls)

    service = SimpleNamespace(
        get_current_skill=skill_ctx.get,
        set_current_skill=lambda _name: (_ for _ in ()).throw(AssertionError("must not rebind")),
        restore_current_skill=lambda _current: (_ for _ in ()).throw(AssertionError("must not restore")),
    )
    monkeypatch.setattr(sdk_context, "_service", lambda: service)

    with sdk_context.use_current_skill("weather_skill") as pushed:
        assert pushed is True

    assert skill_ctx.calls == calls_before
