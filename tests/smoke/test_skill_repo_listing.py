# tests/smoke/test_skill_repo_listing.py
import logging
from pathlib import Path
from adaos.services.agent_context import get_ctx
from adaos.adapters.skills.git_repo import GitSkillRepository
from adaos.domain import SkillId
from adaos.services.skill.service import SkillService


class _Paths:
    def __init__(self, root: Path) -> None:
        self.root = root

    def base_dir(self) -> Path:
        return self.root

    def workspace_dir(self) -> Path:
        return self.root / "workspace"

    def skills_dir(self) -> Path:
        return self.workspace_dir() / "skills"


def test_list_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path / "base"))
    ctx = get_ctx()
    repo = ctx.skills_repo
    assert repo.list() == []


def test_manifest_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path / "base"))
    ctx = get_ctx()
    skills_root_attr = getattr(ctx.paths, "skills_cache_dir", None)
    skills_root = skills_root_attr() if callable(skills_root_attr) else ctx.paths.skills_dir()
    sd = Path(skills_root) / "skills" / "foo"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "skill.yaml").write_text("id: demo\nversion: '1.2.3'\nname: Demo Skill\n", encoding="utf-8")
    svc = SkillService(repo=ctx.skills_repo, bus=ctx.bus)
    items = svc.list()
    assert any(m.id.value == "demo" and m.version == "1.2.3" for m in items)


def test_listing_quietly_ignores_orphan_runtime_cache_directory(
    tmp_path: Path,
    caplog,
) -> None:
    paths = _Paths(tmp_path)
    (paths.skills_dir() / "retired_skill" / "handlers" / "__pycache__").mkdir(
        parents=True
    )
    repo = GitSkillRepository(paths=paths, git=object())

    with caplog.at_level(logging.ERROR):
        assert repo.list() == []

    assert "required declaration is missing" not in caplog.text
