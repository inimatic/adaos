from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from adaos.services.agent_context import get_ctx
import adaos.services.skill.manager as manager_module
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


def test_core_snapshot_prefers_active_slot_manifest_identity(monkeypatch) -> None:
    mgr = _manager()
    monkeypatch.setattr(
        manager_module,
        "BUILD_INFO",
        SimpleNamespace(
            version="0.1.937+1.6ec1f11",
            build_date="2026-08-24T00:00:00+00:00",
            git_commit="6ec1f110ffc010624f0f59326d30a08661b84160",
        ),
    )
    monkeypatch.setattr(
        mgr,
        "_git_text",
        lambda *_args: "e7055633f24ee87dfd927c8fed6fb54c0b1cd80e",
    )

    snapshot = mgr._current_core_compatibility_snapshot()

    assert snapshot["commit"] == "6ec1f110ffc010624f0f59326d30a08661b84160"
    assert snapshot["short_commit"] == "6ec1f110"


def test_core_repo_root_prefers_active_slot_checkout(monkeypatch, tmp_path: Path) -> None:
    mgr = _manager()
    slot_repo = tmp_path / "state" / "core_slots" / "slots" / "B" / "repo"
    (slot_repo / ".git").mkdir(parents=True)
    monkeypatch.delenv("ADAOS_SLOT_REPO_ROOT", raising=False)
    monkeypatch.delenv("ADAOS_REPO_ROOT", raising=False)
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_ACTIVE_CORE_SLOT", "B")

    assert mgr._current_core_repo_root() == slot_repo.resolve()


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


def test_core_requirement_falls_back_to_dev_build_version_when_commit_diverged(monkeypatch) -> None:
    mgr = _manager()
    monkeypatch.setattr(mgr, "_current_core_contains_commit", lambda *_args, **_kwargs: False)

    assert mgr._core_requirement_satisfied(
        required_version="0.1.0+2749.4b79d00f",
        required_commit="4b79d00fed6e0c352cb3076661657cbb50ac1e64",
        current_version="0.1.0+2756.5eb6ebda",
        current_commit="5eb6ebda0f63a56ecb1c8b407d3df0ad76c42fd6",
    )


def test_core_requirement_blocks_commit_only_requirement_when_commit_diverged(monkeypatch) -> None:
    mgr = _manager()
    monkeypatch.setattr(mgr, "_current_core_contains_commit", lambda *_args, **_kwargs: False)

    assert not mgr._core_requirement_satisfied(
        required_version="",
        required_commit="4b79d00fed6e0c352cb3076661657cbb50ac1e64",
        current_version="0.1.0+2756.5eb6ebda",
        current_commit="5eb6ebda0f63a56ecb1c8b407d3df0ad76c42fd6",
    )
