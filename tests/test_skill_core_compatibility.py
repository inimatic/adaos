from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from adaos.services.agent_context import get_ctx
from adaos.services.skill.manager import SkillCoreCompatibilityError, SkillManager


def _manager() -> SkillManager:
    ctx = get_ctx()
    return SkillManager(git=ctx.git, paths=ctx.paths, caps=SimpleNamespace(require=lambda *_args, **_kwargs: None))


def test_skill_push_stamps_current_core_requirement(monkeypatch, tmp_path: Path) -> None:
    mgr = _manager()
    skill_dir = tmp_path / "demo_skill"
    skill_dir.mkdir()
    (skill_dir / "skill.yaml").write_text("name: demo_skill\nversion: 1.2.3\n", encoding="utf-8")

    monkeypatch.setattr(
        mgr,
        "_current_core_compatibility_snapshot",
        lambda: {
            "version": "0.1.204+301.d17a960c",
            "build_date": "2026-06-05T00:00:00+00:00",
            "commit": "d17a960cd15567a2840e04a88564ecbc89f109ff",
            "short_commit": "d17a960c",
        },
    )

    assert mgr._bump_skill_manifest_for_push(skill_dir) == "1.2.4"

    manifest = yaml.safe_load((skill_dir / "skill.yaml").read_text(encoding="utf-8"))
    core = manifest["compatibility"]["adaos_core"]
    assert core["min_version"] == "0.1.204+301.d17a960c"
    assert core["min_commit"] == "d17a960cd15567a2840e04a88564ecbc89f109ff"
    assert core["min_short_commit"] == "d17a960c"
    assert core["source"] == "skill_push"


def test_prepare_runtime_rejects_skill_requiring_newer_core(monkeypatch, tmp_path: Path) -> None:
    mgr = _manager()
    skill_dir = tmp_path / "new_core_skill"
    handlers = skill_dir / "handlers"
    handlers.mkdir(parents=True)
    (handlers / "main.py").write_text("def handle(topic, payload):\n    return {}\n", encoding="utf-8")
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "name: new_core_skill",
                "version: 1.0.0",
                "compatibility:",
                "  adaos_core:",
                "    min_version: 9999.0.0",
                "    min_short_commit: future",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        mgr,
        "_current_core_compatibility_snapshot",
        lambda: {"version": "1.0.0", "build_date": "", "commit": "", "short_commit": "current"},
    )

    with pytest.raises(SkillCoreCompatibilityError, match="requires AdaOS core >= 9999.0.0"):
        mgr.prepare_runtime("new_core_skill", path=skill_dir, run_tests=False)


def test_core_version_compare_uses_local_metadata_when_available() -> None:
    mgr = _manager()

    assert not mgr._version_at_least("0.1.204+1.aaa", "0.1.204+2.bbb")
    assert mgr._version_at_least("0.1.204", "0.1.204+999.future")
