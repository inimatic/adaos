from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from adaos.sdk.builder import artifacts as builder_artifacts, development_sessions
from adaos.sdk.developer import artifact_context, compositions, projects


@pytest.fixture
def project_space(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    roots = {
        "projects": (tmp_path / "projects").resolve(),
        "skills": (tmp_path / "skills").resolve(),
        "scenarios": (tmp_path / "scenarios").resolve(),
        "state": (tmp_path / "state").resolve(),
    }
    for root in roots.values():
        root.mkdir(parents=True)
    monkeypatch.setattr(compositions, "_root_parent", lambda: roots["projects"])
    monkeypatch.setattr(projects, "_roots", lambda: (roots["skills"], roots["scenarios"]))
    monkeypatch.setattr(development_sessions, "_state_root", lambda: roots["state"] / "builder" / "development_sessions")
    return roots


def _skill(root: Path, skill_id: str, *, presentation: bool = True) -> Path:
    skill_root = root / skill_id
    skill_root.mkdir()
    manifest = {
        "name": skill_id,
        "version": "0.1.0",
        "description": "Research direction",
        "research_direction": {"schema": "adaos.research.direction.v1"},
    }
    if presentation:
        manifest["presentations"] = [
            {
                "id": "research-workbench",
                "scenario": "research_workbench",
                "contract": "adaos.research.direction.v1",
                "default": True,
                "bindings": {"direction_ref": "skill:self"},
            }
        ]
    (skill_root / "skill.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return skill_root


def _project(project_id: str, skill_id: str) -> dict:
    return {
        "schema": "adaos.project.v1",
        "kind": "project",
        "id": project_id,
        "version": "0.1.0",
        "profiles": ["adaos.research.direction.v1"],
        "components": {
            "owned": [{"ref": f"skill:{skill_id}", "role": "primary"}],
            "dependencies": [{"ref": "project:adaos_research_platform", "version": "^0.1"}],
        },
        "entrypoints": [
            {
                "id": "research",
                "presentation": "scenario:research_workbench",
                "default": True,
                "bindings": {"direction_ref": f"skill:{skill_id}"},
            }
        ],
        "catalog": {
            "title": "TLP Research",
            "description": "A governed direction",
            "categories": ["research"],
            "tags": ["tlp"],
        },
        "lifecycle": {
            "uninstall": {
                "components": "remove_if_unreferenced",
                "runtime_data": "retain",
                "source_artifacts": "retain",
            }
        },
    }


def test_project_manifest_lists_by_profile_and_resolves_entrypoint(project_space) -> None:
    _skill(project_space["skills"], "tlp_direction")
    created = compositions.create(_project("tlp_research", "tlp_direction"))

    listed = compositions.list_projects(profile="adaos.research.direction.v1")
    presentation = compositions.resolve_presentation("skill:tlp_direction")

    assert created["ref"] == "project:tlp_research"
    assert created["manifest_digest"].startswith("sha256:")
    assert listed[0]["manifest_digest"] == created["manifest_digest"]
    assert listed[0]["primary_ref"] == "skill:tlp_direction"
    assert presentation == {
        "source": "project",
        "project_ref": "project:tlp_research",
        "id": "research",
        "presentation": "scenario:research_workbench",
        "default": True,
        "bindings": {"direction_ref": "skill:tlp_direction"},
    }


def test_project_requires_one_primary_component(project_space) -> None:
    value = _project("invalid", "one")
    value["components"]["owned"].append({"ref": "skill:two", "role": "primary"})

    with pytest.raises(compositions.ProjectCompositionError, match="exactly one primary"):
        compositions.create(value)


def test_local_artifact_group_copies_files_and_detects_tampering(project_space, tmp_path: Path) -> None:
    skill_root = _skill(project_space["skills"], "tlp_direction")
    source = tmp_path / "review.md"
    source.write_text("A careful review", encoding="utf-8")

    first = artifact_context.add_path("tlp_direction", "part0", source, role="review")
    second = artifact_context.add_path("tlp_direction", "part0", source, role="review")
    resolved = artifact_context.resolve("tlp_direction", "part0", first["artifact"]["artifact_id"])
    bundle = artifact_context.source_bundle("tlp_direction")

    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert Path(resolved["native_path"]).read_text(encoding="utf-8") == "A careful review"
    assert bundle["sources"][0]["artifact_ref"].startswith("artifact://skill/tlp_direction/part0/")
    assert (skill_root / "artifacts" / "part0" / "manifest.yaml").is_file()

    Path(resolved["native_path"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(artifact_context.ArtifactContextError, match="no longer matches"):
        artifact_context.resolve("tlp_direction", "part0", first["artifact"]["artifact_id"])


def test_private_local_checkpoint_binds_code_and_keeps_artifacts_separate(project_space, tmp_path: Path, monkeypatch) -> None:
    skill_root = _skill(project_space["skills"], "tlp_direction")
    (skill_root / "handlers").mkdir()
    (skill_root / "handlers" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    source = tmp_path / "review.md"
    source.write_text("private source", encoding="utf-8")
    added = artifact_context.add_path("tlp_direction", "part0", source)
    monkeypatch.setattr(
        builder_artifacts,
        "require_ctx",
        lambda _feature=None: SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: project_space["state"])),
    )

    first = builder_artifacts.local_checkpoint(kind="skill", artifact_id="tlp_direction")
    Path(added["group"]["root_path"], "review.md").write_text("changed private source", encoding="utf-8")
    second = builder_artifacts.local_checkpoint(kind="skill", artifact_id="tlp_direction")
    (skill_root / "handlers" / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    third = builder_artifacts.local_checkpoint(kind="skill", artifact_id="tlp_direction")

    assert first["scope"] == "local"
    assert first["bytes_uploaded"] == 0
    assert first["source_tree"] == second["source_tree"]
    assert third["source_tree"] != second["source_tree"]
    checkpoint = json.loads(Path(first["stored_path"]).read_text(encoding="utf-8"))
    assert all(not item["path"].startswith("artifacts/") for item in checkpoint["files"])


def test_development_session_separates_write_targets_and_readonly_context(project_space, tmp_path: Path) -> None:
    _skill(project_space["skills"], "tlp_direction")
    compositions.create(_project("tlp_research", "tlp_direction"))
    source = tmp_path / "review.md"
    source.write_text("A careful review", encoding="utf-8")
    artifact_context.add_path("tlp_direction", "part0", source)
    digest = "sha256:" + "1" * 64
    prototype = "sha256:" + "2" * 64

    created = development_sessions.create(
        "tlp_research",
        automation_brief_digest=digest,
        research_prototype_digest=prototype,
        artifact_groups=["part0"],
        context_members=[
            {"ref": "scenario:research_workbench", "relation": "presentation"},
            {"ref": "skill:research_orchestrator_skill", "relation": "dependency"},
        ],
        prohibited_actions=["Do not run experiments."],
        base_release={"scope": "local", "source_tree": "sha256:" + "3" * 64},
    )
    repeated = development_sessions.create(
        "tlp_research",
        automation_brief_digest=digest,
        research_prototype_digest=prototype,
        artifact_groups=["part0"],
        context_members=[
            {"ref": "scenario:research_workbench", "relation": "presentation"},
            {"ref": "skill:research_orchestrator_skill", "relation": "dependency"},
        ],
        prohibited_actions=["Do not run experiments."],
        base_release={"scope": "local", "source_tree": "sha256:" + "3" * 64},
    )

    session = created["session"]
    assert session["targets"]["primary"][0]["ref"] == "skill:tlp_direction"
    assert session["context_members"][0]["access"] == "read-only"
    assert session["artifact_inputs"][0]["access"] == "read-only"
    assert session["artifact_inputs"][0]["manifest_digest"].startswith("sha256:")
    assert session["base_release"]["scope"] == "local"
    assert repeated["idempotent"] is True

    bound = development_sessions.bind(session["session_id"], "desktop")
    restored = development_sessions.binding_for("desktop")
    assert bound["binding"]["focus_ref"] == "skill:tlp_direction"
    assert restored == bound["binding"]

    target_file = project_space["skills"] / "tlp_direction" / "handlers" / "main.py"
    target_file.parent.mkdir()
    artifact_file = Path(session["artifact_inputs"][0]["root_path"]) / "review.md"
    review = development_sessions.review_changes(
        session["session_id"],
        [str(target_file), str(artifact_file), str(tmp_path / "outside.py"), "relative.py"],
    )
    assert review["ok"] is False
    assert review["admitted"] == [str(target_file.resolve())]
    assert [item["reason"] for item in review["violations"]] == [
        "read_only_artifact_input",
        "outside_development_session_scope",
        "path_must_be_absolute",
    ]

    _skill(project_space["skills"], "shared_metrics")
    expansion = development_sessions.request_scope_expansion(
        session["session_id"],
        "skill:shared_metrics",
        "The accepted metric is not available through the current contract.",
    )
    assert expansion["approved"] is False
    assert expansion["request"]["status"] == "requested"
    repeated_expansion = development_sessions.request_scope_expansion(
        session["session_id"],
        "skill:shared_metrics",
        "The accepted metric is not available through the current contract.",
    )
    assert repeated_expansion["request"]["request_id"] == expansion["request"]["request_id"]

    later = development_sessions.create(
        "tlp_research",
        automation_brief_digest="sha256:" + "4" * 64,
        research_prototype_digest="sha256:" + "5" * 64,
        artifact_groups=["part0"],
        context_members=[{"ref": "scenario:research_workbench", "relation": "presentation"}],
        prohibited_actions=["Do not run experiments."],
        session_id="dev_0000_lexically_earlier",
    )
    assert development_sessions.list_sessions(project_id="tlp_research")[-1]["session_id"] == later["session"]["session_id"]


def test_development_session_rejects_non_owned_write_target(project_space, tmp_path: Path) -> None:
    _skill(project_space["skills"], "tlp_direction")
    _skill(project_space["skills"], "shared_dependency")
    compositions.create(_project("tlp_research", "tlp_direction"))
    source = tmp_path / "review.md"
    source.write_text("review", encoding="utf-8")
    artifact_context.add_path("tlp_direction", "part0", source)

    with pytest.raises(development_sessions.DevelopmentSessionError, match="not owned"):
        development_sessions.create(
            "tlp_research",
            automation_brief_digest="sha256:" + "1" * 64,
            research_prototype_digest="sha256:" + "2" * 64,
            artifact_groups=["part0"],
            primary_targets=["skill:shared_dependency"],
            prohibited_actions=["No execution"],
        )
