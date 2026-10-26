from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.applications.dev_data_lifecycle import (
    LocalDevSkillDataLifecycle,
)
from adaos.services.applications.native_data_adoption import (
    is_adopted_native_data,
)
from adaos.services.applications.runtime_channel import RuntimeChannelConflict
from adaos.services.skill.runtime_env import SkillRuntimeEnvironment


def _target_manifest() -> dict:
    return {
        "name": "builder_sdk_control_skill",
        "version": "0.2.13",
        "memory_budget": {
            "caches": [{"name": "sessions", "storage": "skill_memory"}]
        },
        "data_lifecycle": {
            "schema": "adaos.skill.data_lifecycle.v1",
            "execution": "native_tools",
            "databases": [],
            "coordination_files": [],
            "legacy_adoption": "core_managed_only",
        },
    }


def _legacy_runtime(tmp_path: Path) -> tuple[Path, SkillRuntimeEnvironment, dict]:
    skills_root = tmp_path / "dev" / "sn_test" / "skills"
    skills_root.mkdir(parents=True)
    env = SkillRuntimeEnvironment(
        skills_root=skills_root,
        skill_name="builder_sdk_control_skill",
    )
    env.prepare_version("0.1.120")
    source_manifest = {
        "name": "builder_sdk_control_skill",
        "version": "0.1.120",
        "memory_budget": {},
    }
    slot = env.read_active_slot("0.1.120")
    resolved = env.build_slot_paths("0.1.120", slot).resolved_manifest
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(source_manifest), encoding="utf-8")
    memory = env.skill_memory_store_path("0.1.120")
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text(
        json.dumps({"builder_skill.sessions": {"session.1": {"state": "automation"}}}),
        encoding="utf-8",
    )
    return skills_root, env, source_manifest


def test_dev_bucket_transition_adopts_core_memory_before_prepare_and_activation(
    tmp_path: Path,
) -> None:
    skills_root, env, _source_manifest = _legacy_runtime(tmp_path)
    target = _target_manifest()
    lifecycle = LocalDevSkillDataLifecycle(
        private_root=tmp_path,
        skills_root=skills_root,
        skill_id="builder_sdk_control_skill",
        target_version="0.2.13",
        target_manifest=target,
        request_id="task.01",
    )
    observed: list[str] = []

    def prepare():
        env.ensure_data_dirs("0.2.13")
        assert is_adopted_native_data(env.data_root("0.2.13"), target)
        observed.append("prepare")
        return SimpleNamespace(
            version="0.2.13",
            slot="B",
            resolved_manifest=tmp_path / "resolved.json",
        )

    prepared, evidence = lifecycle.prepare(prepare)
    assert observed == ["prepare"]
    assert evidence.mode == "fenced_native_dev_transition"
    assert json.loads(
        env.skill_memory_store_path("0.2.13").read_text(encoding="utf-8")
    ) == json.loads(
        env.skill_memory_store_path("0.1.120").read_text(encoding="utf-8")
    )

    completed = lifecycle.complete(
        prepared=prepared,
        active_slot="B",
        runtime_status={"ready": True, "active": True, "version": "0.2.13"},
    )
    assert completed["operation_id"] == evidence.operation_id
    assert completed["target_version"] == "0.2.13"


def test_dev_bucket_transition_reuses_exact_retained_transfer(tmp_path: Path) -> None:
    skills_root, env, _source_manifest = _legacy_runtime(tmp_path)
    target = _target_manifest()

    def run_once():
        lifecycle = LocalDevSkillDataLifecycle(
            private_root=tmp_path,
            skills_root=skills_root,
            skill_id="builder_sdk_control_skill",
            target_version="0.2.13",
            target_manifest=target,
            request_id="task.01",
        )

        def prepare():
            env.ensure_data_dirs("0.2.13")
            assert is_adopted_native_data(env.data_root("0.2.13"), target)
            return SimpleNamespace(
                version="0.2.13",
                slot="B",
                resolved_manifest=tmp_path / "resolved.json",
            )

        return lifecycle.prepare(prepare)

    _first_prepared, first = run_once()
    _second_prepared, second = run_once()
    assert second.operation_id == first.operation_id
    assert second.transfer_receipt == first.transfer_receipt


def test_dev_bucket_transition_rejects_unowned_legacy_state(tmp_path: Path) -> None:
    skills_root, env, _source_manifest = _legacy_runtime(tmp_path)
    foreign = env.data_root("0.1.120") / "db" / "foreign.sqlite"
    foreign.write_bytes(b"not-owned")

    with pytest.raises(ValueError):
        LocalDevSkillDataLifecycle(
            private_root=tmp_path,
            skills_root=skills_root,
            skill_id="builder_sdk_control_skill",
            target_version="0.2.13",
            target_manifest=_target_manifest(),
            request_id="task.01",
        )


def test_dev_bucket_transition_detects_test_mutation_before_activation(
    tmp_path: Path,
) -> None:
    skills_root, env, _source_manifest = _legacy_runtime(tmp_path)
    target = _target_manifest()
    lifecycle = LocalDevSkillDataLifecycle(
        private_root=tmp_path,
        skills_root=skills_root,
        skill_id="builder_sdk_control_skill",
        target_version="0.2.13",
        target_manifest=target,
        request_id="task.01",
    )

    def mutating_prepare():
        env.ensure_data_dirs("0.2.13")
        env.skill_memory_store_path("0.2.13").write_text(
            '{"unexpected":"test mutation"}', encoding="utf-8"
        )
        return SimpleNamespace(
            version="0.2.13",
            slot="B",
            resolved_manifest=tmp_path / "resolved.json",
        )

    with pytest.raises(
        RuntimeChannelConflict,
        match="skill memory changed",
    ):
        lifecycle.prepare(mutating_prepare)
