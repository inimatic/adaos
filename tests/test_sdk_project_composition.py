from __future__ import annotations

import hashlib
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
    monkeypatch.setattr(artifact_context, "_context_view_root", lambda: roots["state"] / "artifact_context" / "views")
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


def _scenario(root: Path, scenario_id: str) -> Path:
    scenario_root = root / scenario_id
    scenario_root.mkdir()
    (scenario_root / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "id": scenario_id,
                "name": scenario_id,
                "title": "Kanban board",
                "description": "Builder prototype",
                "version": "0.1.0",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return scenario_root


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
    assert listed[0]["components"] == created["components"]
    assert listed[0]["entrypoints"] == created["entrypoints"]
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


def test_project_source_can_start_as_empty_builder_draft(project_space) -> None:
    value = _project("draft_project", "one")
    value["components"]["owned"] = []
    value["components"]["dependencies"] = []
    value["entrypoints"] = []
    value["compatibility"] = {"required_entrypoints": []}

    created = compositions.create(value)
    listed = compositions.list_projects()

    assert created["components"]["owned"] == []
    assert listed[0]["primary_ref"] is None


def test_project_can_adopt_an_existing_unowned_builder_component(project_space) -> None:
    _scenario(project_space["scenarios"], "kanban_demo")

    result = compositions.create_for_existing_component(
        "kanban_demo",
        kind="scenario",
        component_id="kanban_demo",
        actor="builder.chat",
    )

    project = result["project"]
    assert result["created_component"] is False
    assert project["ref"] == "project:kanban_demo"
    assert project["components"]["owned"] == [
        {
            "ref": "scenario:kanban_demo",
            "role": "primary",
            "exposure": "application",
            "lifecycle": "bound",
            "relations": ["uses"],
        }
    ]
    assert project["entrypoints"] == [
        {
            "id": "main",
            "presentation": "scenario:kanban_demo",
            "default": True,
            "bindings": {},
        }
    ]
    assert project["publication"]["stage"] == "alpha"
    assert compositions.project_for_component("scenario:kanban_demo")["ref"] == project["ref"]


def test_project_adoption_rejects_a_component_owned_by_another_project(project_space) -> None:
    _scenario(project_space["scenarios"], "kanban_demo")
    compositions.create_for_existing_component(
        "kanban_one",
        kind="scenario",
        component_id="kanban_demo",
    )

    with pytest.raises(compositions.ProjectCompositionError, match="already owned"):
        compositions.create_for_existing_component(
            "kanban_two",
            kind="scenario",
            component_id="kanban_demo",
        )


def test_project_can_idempotently_attach_a_created_companion_skill(project_space) -> None:
    _scenario(project_space["scenarios"], "kanban_demo")
    _skill(project_space["skills"], "kanban_demo_skill")
    compositions.create_for_existing_component(
        "kanban_demo",
        kind="scenario",
        component_id="kanban_demo",
    )

    first = compositions.ensure_owned_component(
        "kanban_demo",
        "skill:kanban_demo_skill",
    )
    second = compositions.ensure_owned_component(
        "kanban_demo",
        "skill:kanban_demo_skill",
    )

    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert [
        item["ref"] for item in second["project"]["components"]["owned"]
    ] == ["scenario:kanban_demo", "skill:kanban_demo_skill"]


def test_project_can_idempotently_declare_a_shared_dependency(project_space) -> None:
    _scenario(project_space["scenarios"], "kanban_demo")
    _skill(project_space["skills"], "shared_search")
    compositions.create_for_existing_component(
        "kanban_demo",
        kind="scenario",
        component_id="kanban_demo",
    )

    first = compositions.ensure_dependency(
        "kanban_demo",
        "skill:shared_search",
        version="^1.0",
    )
    second = compositions.ensure_dependency(
        "kanban_demo",
        "skill:shared_search",
        version="^1.0",
    )

    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["project"]["components"]["dependencies"] == [
        {
            "ref": "skill:shared_search",
            "version": "^1.0",
            "lifecycle": "shared",
            "relations": ["uses"],
        }
    ]


def test_project_composition_expands_release_defaults_without_rewriting_source(project_space) -> None:
    _skill(project_space["skills"], "candidate_skill")
    value = _project("candidate_project", "candidate_skill")
    value["components"]["owned"][0].update(
        {
            "role": "implementation",
            "exposure": "project_only",
            "lifecycle": "bound",
            "relations": ["uses", "realizes"],
        }
    )
    value["components"]["owned"].append(
        {
            "ref": "scenario:research_console",
            "role": "primary",
            "exposure": "application",
            "lifecycle": "bound",
            "relations": ["presents"],
        }
    )
    value["compatibility"] = {
        "required_entrypoints": ["research"],
        "required_contracts": ["adaos.research.manager.v1"],
        "validation_profiles": ["project.conformance"],
    }
    value["publication"] = {
        "stage": "beta",
        "visibility": "listed",
        "channel": "beta",
    }
    value["catalog"]["title_i18n"] = {"en": "Candidate Project", "ru": "\u041a\u0430\u043d\u0434\u0438\u0434\u0430\u0442\u043d\u044b\u0439 \u043f\u0440\u043e\u0435\u043a\u0442"}
    value["catalog"]["description_i18n"] = {"en": "Candidate description", "ru": "\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043a\u0430\u043d\u0434\u0438\u0434\u0430\u0442\u0430"}
    value["install"] = {
        "default": True,
        "features": [
            {
                "id": "research-console",
                "title": "Research console",
                "title_i18n": {"en": "Research console", "ru": "\u0418\u0441\u0441\u043b\u0435\u0434\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u0441\u043a\u0430\u044f \u043a\u043e\u043d\u0441\u043e\u043b\u044c"},
                "default": True,
                "optional": False,
                "components": ["scenario:research_console", "skill:candidate_skill"],
            }
        ],
    }

    created = compositions.create(value)
    normalized = compositions.normalized_definition(created)
    listed = compositions.list_projects()

    assert created["components"]["owned"][0]["exposure"] == "project_only"
    candidate = next(
        item
        for item in normalized["components"]["owned"]
        if item["ref"] == "skill:candidate_skill"
    )
    assert candidate["relations"] == ["realizes", "uses"]
    assert normalized["components"]["dependencies"][0]["lifecycle"] == "shared"
    assert normalized["publication"] == {
        "stage": "beta",
        "visibility": "listed",
        "channel": "beta",
    }
    assert normalized["install"]["default"] is True
    assert normalized["install"]["features"] == [
        {
            "id": "research-console",
            "title": "Research console",
            "title_i18n": {"en": "Research console", "ru": "\u0418\u0441\u0441\u043b\u0435\u0434\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u0441\u043a\u0430\u044f \u043a\u043e\u043d\u0441\u043e\u043b\u044c"},
            "default": True,
            "optional": False,
            "components": ["scenario:research_console", "skill:candidate_skill"],
        }
    ]
    assert normalized["catalog"]["title_i18n"] == {"en": "Candidate Project", "ru": "\u041a\u0430\u043d\u0434\u0438\u0434\u0430\u0442\u043d\u044b\u0439 \u043f\u0440\u043e\u0435\u043a\u0442"}
    assert normalized["catalog"]["description_i18n"] == {"en": "Candidate description", "ru": "\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043a\u0430\u043d\u0434\u0438\u0434\u0430\u0442\u0430"}
    assert normalized["compatibility"]["required_entrypoints"] == ["research"]
    assert listed[0]["stage"] == "beta"
    assert listed[0]["visibility"] == "listed"
    assert listed[0]["default_install"] is True
    assert listed[0]["title_i18n"] == {"en": "Candidate Project", "ru": "\u041a\u0430\u043d\u0434\u0438\u0434\u0430\u0442\u043d\u044b\u0439 \u043f\u0440\u043e\u0435\u043a\u0442"}
    assert listed[0]["description_i18n"] == {"en": "Candidate description", "ru": "\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043a\u0430\u043d\u0434\u0438\u0434\u0430\u0442\u0430"}


def test_project_rejects_install_feature_for_non_owned_component(project_space) -> None:
    value = _project("invalid", "one")
    value["install"] = {
        "features": [
            {
                "id": "external",
                "title": "External component",
                "components": ["skill:two"],
            }
        ]
    }

    with pytest.raises(compositions.ProjectCompositionError, match="owned members"):
        compositions.create(value)


def test_project_rejects_undeclared_required_entrypoint(project_space) -> None:
    value = _project("invalid", "one")
    value["compatibility"] = {"required_entrypoints": ["missing"]}

    with pytest.raises(compositions.ProjectCompositionError, match="not declared"):
        compositions.create(value)


def test_project_replace_is_identity_stable_and_optimistic(project_space) -> None:
    _skill(project_space["skills"], "candidate_skill")
    created = compositions.create(_project("candidate_project", "candidate_skill"))
    replacement = {
        key: value
        for key, value in created.items()
        if key not in {"ref", "manifest_digest", "source_path"}
    }
    replacement["profiles"] = ["adaos.research.implementation.v1"]
    replacement["components"]["owned"][0].update(
        {
            "role": "primary",
            "exposure": "project_only",
            "lifecycle": "bound",
            "relations": ["realizes"],
        }
    )

    updated = compositions.replace(
        "candidate_project",
        replacement,
        expected_manifest_digest=created["manifest_digest"],
    )

    assert updated["id"] == created["id"]
    assert updated["manifest_digest"] != created["manifest_digest"]
    assert updated["components"]["owned"][0]["exposure"] == "project_only"
    with pytest.raises(compositions.ProjectCompositionError, match="changed since"):
        compositions.replace(
            "candidate_project",
            replacement,
            expected_manifest_digest=created["manifest_digest"],
        )

    replacement["id"] = "other"
    with pytest.raises(compositions.ProjectCompositionError, match="cannot change"):
        compositions.replace(
            "candidate_project",
            replacement,
            expected_manifest_digest=updated["manifest_digest"],
        )


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
    assert first["artifact"]["media_type"] == "text/markdown"
    assert Path(resolved["native_path"]).read_text(encoding="utf-8") == "A careful review"
    assert bundle["sources"][0]["artifact_ref"].startswith("artifact://skill/tlp_direction/part0/")
    assert (skill_root / "artifacts" / "part0" / "manifest.yaml").is_file()

    Path(resolved["native_path"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(artifact_context.ArtifactContextError, match="no longer matches"):
        artifact_context.resolve("tlp_direction", "part0", first["artifact"]["artifact_id"])


def test_artifact_context_materializes_digest_bound_audience_views(project_space, tmp_path: Path) -> None:
    _skill(project_space["skills"], "tlp_direction")
    notebook = tmp_path / "experiment.ipynb"
    notebook.write_text('{"cells": []}', encoding="utf-8")
    review = tmp_path / "initial-review.md"
    review.write_text("Evaluator-only oracle", encoding="utf-8")
    artifact_context.add_path(
        "tlp_direction",
        "part0",
        notebook,
        context_policy={"default": "allow", "allow": [], "deny": []},
    )
    hidden = artifact_context.add_path(
        "tlp_direction",
        "part0",
        review,
        role="review",
        context_policy={
            "default": "deny",
            "allow": ["research.evaluation"],
            "deny": [],
            "reason": "hidden evaluator oracle",
        },
    )

    implementation = artifact_context.materialize_context(
        "tlp_direction", "part0", "research.implementation"
    )
    evaluation = artifact_context.materialize_context(
        "tlp_direction", "part0", "research.evaluation"
    )

    assert sorted(path.name for path in Path(implementation["root_path"]).iterdir()) == [
        "experiment.ipynb"
    ]
    assert sorted(path.name for path in Path(evaluation["root_path"]).iterdir()) == [
        "experiment.ipynb",
        "initial-review.md",
    ]
    assert implementation["excluded"] == [
        {"artifact_id": hidden["artifact"]["artifact_id"], "reason": "hidden evaluator oracle"}
    ]
    assert implementation["digest"] != evaluation["digest"]
    assert Path(implementation["manifest_path"]).parent != Path(implementation["root_path"])
    formulation_bundle = artifact_context.source_bundle(
        "tlp_direction", audience="research.formulation"
    )
    evaluation_bundle = artifact_context.source_bundle(
        "tlp_direction", audience="research.evaluation"
    )
    assert [item["name"] for item in formulation_bundle["sources"]] == ["experiment.ipynb"]
    assert sorted(item["name"] for item in evaluation_bundle["sources"]) == [
        "experiment.ipynb",
        "initial-review.md",
    ]
    assert formulation_bundle["excluded"][0]["artifact_id"] == hidden["artifact"]["artifact_id"]
    formulation_digest = formulation_bundle["digest"]
    review.write_text("Changed evaluator-only oracle", encoding="utf-8")
    artifact_context.add_path(
        "tlp_direction",
        "part0",
        review,
        context_policy={
            "default": "deny",
            "allow": ["research.evaluation"],
            "deny": [],
            "reason": "hidden evaluator oracle",
        },
        replace_existing=True,
    )
    assert artifact_context.source_bundle(
        "tlp_direction", audience="research.formulation"
    )["digest"] == formulation_digest


def test_artifact_context_policy_can_be_revised_without_replacing_content(project_space, tmp_path: Path) -> None:
    _skill(project_space["skills"], "tlp_direction")
    source = tmp_path / "review.md"
    source.write_text("A careful review", encoding="utf-8")
    added = artifact_context.add_path("tlp_direction", "part0", source)

    revised = artifact_context.set_context_policy(
        "tlp_direction",
        "part0",
        added["artifact"]["artifact_id"],
        {"default": "deny", "allow": ["evaluator"], "deny": []},
    )

    assert revised["idempotent"] is False
    assert revised["group"]["schema_version"] == "1.1.0"
    assert revised["artifact"]["context_policy"]["allow"] == ["evaluator"]


def test_artifact_context_builds_a_semantic_notebook_digest_and_bounds_untrusted_outputs(project_space, tmp_path: Path) -> None:
    _skill(project_space["skills"], "tlp_direction")
    source = tmp_path / "experiment.ipynb"
    source.write_text(
        json.dumps(
            {
                "cells": [
                    {"cell_type": "markdown", "source": ["# Hypothesis\n", "TLP is unconfirmed."], "metadata": {}},
                    {
                        "cell_type": "code",
                        "source": ["def tropical_pool(x, w):\n", "    return (x + w).amax((-1, -2))\n"],
                        "outputs": [{"output_type": "stream", "text": ["x" * 100_000]}],
                        "metadata": {},
                        "execution_count": 1,
                    },
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )
    added = artifact_context.add_path("tlp_direction", "part0", source)

    extracted = artifact_context.extract_text(
        "tlp_direction", "part0", added["artifact"]["artifact_id"], max_characters=10_000
    )

    assert "TLP is unconfirmed" in extracted["content"]
    assert "def tropical_pool" in extracted["content"]
    assert "x" * 1_000 not in extracted["content"]
    coverage = extracted["coverage"]
    assert coverage["strategy"] == "notebook_semantic_digest_v1"
    assert coverage["raw_bytes"] == source.stat().st_size
    assert coverage["source_characters"] == 91
    assert coverage["total_units"] == 3  # generated inventory plus two source cells
    assert coverage["selected_units"] == 3
    assert coverage["truncated"] is False
    assert coverage["output_items"] == 1
    assert coverage["outputs_classification"] == "exploratory_untrusted_not_confirmatory"
    assert coverage["selection_strategy"] == "source_order"
    assert extracted["provenance"][0]["ref"].endswith("#inventory")
    assert extracted["provenance"][2]["ref"].endswith("#cell=1")


def test_artifact_context_reports_line_level_coverage_for_bounded_text(project_space, tmp_path: Path) -> None:
    _skill(project_space["skills"], "tlp_direction")
    source = tmp_path / "review.md"
    source.write_text("first evidence\n" + "second interpretation\n" * 500, encoding="utf-8")
    added = artifact_context.add_path("tlp_direction", "part0", source)

    extracted = artifact_context.extract_text(
        "tlp_direction", "part0", added["artifact"]["artifact_id"], max_characters=500
    )

    assert extracted["coverage"]["truncated"] is True
    assert extracted["coverage"]["selected_characters"] == 500
    assert extracted["provenance"][0]["ref"].startswith("artifact://skill/tlp_direction/part0/")
    assert "#lines=" in extracted["provenance"][0]["ref"]


def test_notebook_query_selection_reaches_relevant_late_cells_instead_of_prefix_truncation(project_space, tmp_path: Path) -> None:
    _skill(project_space["skills"], "tlp_direction")
    source = tmp_path / "long.ipynb"
    source.write_text(
        json.dumps(
            {
                "cells": [
                    {"cell_type": "markdown", "source": ["unrelated introduction " * 500], "metadata": {}},
                    {"cell_type": "code", "source": ["noise = 'x' * 10000\n"], "outputs": [], "metadata": {}},
                    {"cell_type": "markdown", "source": ["# Paired shift sensitivity\nCompare TropicalMaxPool with MaxPool using paired seeds."], "metadata": {}},
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )
    added = artifact_context.add_path("tlp_direction", "part0", source)

    extracted = artifact_context.extract_text(
        "tlp_direction",
        "part0",
        added["artifact"]["artifact_id"],
        max_characters=1_500,
        query="paired shift sensitivity TLP MaxPool seeds",
    )

    assert "Paired shift sensitivity" in extracted["content"]
    assert extracted["coverage"]["selection_strategy"] == "query_relevance_then_source_order"
    assert "cell-2" in extracted["coverage"]["selected_unit_ids"]
    assert "cell-0" in extracted["coverage"]["omitted_unit_ids"]


def test_local_artifact_group_explicitly_replaces_an_unlocked_intake_path(project_space, tmp_path: Path) -> None:
    _skill(project_space["skills"], "tlp_direction")
    source = tmp_path / "review.md"
    source.write_text("{}", encoding="utf-8")
    first = artifact_context.add_path("tlp_direction", "part0", source, role="review")
    source.write_text("Complete critical review", encoding="utf-8")

    replaced = artifact_context.add_path(
        "tlp_direction",
        "part0",
        source,
        role="review",
        replace_existing=True,
    )
    group = artifact_context.get_group("tlp_direction", "part0")

    assert replaced["replaced"] is True
    assert replaced["previous_artifact"]["digest"] == first["artifact"]["digest"]
    assert len(group["items"]) == 1
    assert group["items"][0]["digest"] == replaced["artifact"]["digest"]
    assert Path(group["root_path"], "review.md").read_text(encoding="utf-8") == "Complete critical review"


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
            {"ref": "skill:research_manager_skill", "relation": "contract-consumer"},
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
            {"ref": "skill:research_manager_skill", "relation": "contract-consumer"},
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

    brief = {"schema": "example.automation_brief.v1", "digest": digest, "objective": "Build it"}
    attached = development_sessions.attach_instruction(
        session["session_id"],
        "automation_brief",
        brief,
        expected_digest=digest,
    )
    repeated_instruction = development_sessions.attach_instruction(
        session["session_id"],
        "automation_brief",
        brief,
        expected_digest=digest,
    )
    restored_instruction = development_sessions.get_instruction(
        session["session_id"], "automation_brief"
    )
    assert attached["instruction"]["access"] == "read-only"
    assert repeated_instruction["idempotent"] is True
    assert restored_instruction["value"] == brief
    assert restored_instruction["instruction"]["content_digest"].startswith("sha256:")

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

    feedback = development_sessions.record_feedback(
        session["session_id"],
        "feasibility_constraint",
        "The accepted paired runner requires a deterministic seed injection point.",
        affected_refs=["skill:tlp_direction"],
        constraints=["Do not replace the accepted paired estimator."],
        evidence=[{"kind": "contract", "ref": "instruction://automation_brief", "digest": digest}],
        proposed_action="revise_engineering_contract",
        protocol_digest=prototype,
    )
    repeated_feedback = development_sessions.record_feedback(
        session["session_id"],
        "feasibility_constraint",
        "The accepted paired runner requires a deterministic seed injection point.",
        affected_refs=["skill:tlp_direction"],
        constraints=["Do not replace the accepted paired estimator."],
        evidence=[{"kind": "contract", "ref": "instruction://automation_brief", "digest": digest}],
        proposed_action="revise_engineering_contract",
        protocol_digest=prototype,
    )
    assert feedback["feedback"]["status"] == "open"
    assert repeated_feedback["idempotent"] is True
    assert development_sessions.list_feedback(session["session_id"], blocking=True) == [feedback["feedback"]]

    with pytest.raises(development_sessions.DevelopmentSessionError, match="outside session context"):
        development_sessions.record_feedback(
            session["session_id"],
            "capability_gap",
            "A missing dependency cannot be addressed inside the accepted target scope.",
            affected_refs=["skill:not_admitted"],
            proposed_action="request_scope",
        )

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


def test_development_session_supports_domain_neutral_contract_handoff(project_space) -> None:
    _skill(project_space["skills"], "candidate_skill")
    compositions.create(_project("candidate_project", "candidate_skill"))
    digest = "sha256:" + "8" * 64

    created = development_sessions.create(
        "candidate_project",
        subject_refs=[
            {
                "kind": "work_item",
                "ref": "work-item:42",
                "revision": 3,
                "digest": digest,
            }
        ],
        contract_inputs=[
            {
                "kind": "implementation_contract",
                "ref": "contract:work-item:42",
                "digest": digest,
                "media_type": "application/json",
            }
        ],
        acceptance_profiles=["project.conformance", "consumer.contracts"],
        agent_profile={
            "provider": "local-agent-provider",
            "model": "qualified-coder",
            "reasoning_effort": "high",
            "tool_profile": "adaos-local-bounded-v1",
        },
    )

    session = created["session"]
    assert session["project_ref"] == "project:candidate_project"
    assert session["artifact_inputs"] == []
    assert session["subject_refs"][0]["ref"] == "work-item:42"
    assert session["contract_inputs"][0]["digest"] == digest
    assert session["acceptance_profiles"] == [
        "project.conformance",
        "consumer.contracts",
    ]
    assert "automation_brief_digest" not in session["handoff"]
    assert session["handoff"]["agent_profile"]["provider"] == "local-agent-provider"


def test_development_session_binds_executable_acceptance_to_admitted_consumer(project_space) -> None:
    _skill(project_space["skills"], "candidate_skill")
    compositions.create(_project("candidate_project", "candidate_skill"))
    requirement = {
        "id": "consumer.contracts",
        "profile": "consumer.contracts",
        "provider_ref": "skill:consumer_skill",
        "operation": "validate_development_candidate",
        "required": True,
        "timeout_seconds": 120,
        "parameters": {"execute_workflow_smoke": True},
    }
    created = development_sessions.create(
        "candidate_project",
        context_members=[
            {
                "ref": "skill:consumer_skill",
                "relation": "contract-consumer",
                "access": "read-only",
                "context": "contract",
            }
        ],
        acceptance_profiles=["project.conformance", "consumer.contracts"],
        acceptance_requirements=[requirement],
    )
    assert created["session"]["acceptance_requirements"] == [requirement]

    with pytest.raises(
        development_sessions.DevelopmentSessionError,
        match="acceptance providers must be admitted",
    ):
        development_sessions.create(
            "candidate_project",
            context_members=[],
            acceptance_requirements=[requirement],
            session_id="candidate_outside_consumer",
        )


def test_development_session_uses_filtered_artifact_view_for_agent_audience(project_space, tmp_path: Path) -> None:
    _skill(project_space["skills"], "tlp_direction")
    compositions.create(_project("tlp_research", "tlp_direction"))
    visible = tmp_path / "notebook.md"
    visible.write_text("Visible source", encoding="utf-8")
    hidden = tmp_path / "oracle.md"
    hidden.write_text("Hidden answer", encoding="utf-8")
    artifact_context.add_path("tlp_direction", "part0", visible)
    artifact_context.add_path(
        "tlp_direction",
        "part0",
        hidden,
        context_policy={"default": "deny", "allow": ["research.evaluation"], "deny": []},
    )

    created = development_sessions.create(
        "tlp_research",
        automation_brief_digest="sha256:" + "1" * 64,
        research_prototype_digest="sha256:" + "2" * 64,
        artifact_groups=["part0"],
        artifact_audience="research.implementation",
        prohibited_actions=["No hidden evaluator access"],
    )

    artifact_input = created["session"]["artifact_inputs"][0]
    assert artifact_input["audience"] == "research.implementation"
    assert artifact_input["context_digest"].startswith("sha256:")
    assert sorted(path.name for path in Path(artifact_input["root_path"]).iterdir()) == ["notebook.md"]


def test_development_session_rejects_instruction_digest_drift(project_space, tmp_path: Path) -> None:
    _skill(project_space["skills"], "tlp_direction")
    compositions.create(_project("tlp_research", "tlp_direction"))
    source = tmp_path / "review.md"
    source.write_text("review", encoding="utf-8")
    artifact_context.add_path("tlp_direction", "part0", source)
    digest = "sha256:" + "1" * 64
    created = development_sessions.create(
        "tlp_research",
        automation_brief_digest=digest,
        research_prototype_digest="sha256:" + "2" * 64,
        artifact_groups=["part0"],
        prohibited_actions=["No execution"],
    )

    with pytest.raises(development_sessions.DevelopmentSessionError, match="declared digest"):
        development_sessions.attach_instruction(
            created["session"]["session_id"],
            "automation_brief",
            {"digest": "sha256:" + "3" * 64},
            expected_digest=digest,
        )


def test_development_session_copies_digest_bound_text_instruction(project_space, tmp_path: Path) -> None:
    _skill(project_space["skills"], "candidate_skill")
    compositions.create(_project("candidate_project", "candidate_skill"))
    source = tmp_path / "source.md"
    source.write_text("# Reviewed evidence\n\nNo confirmatory claim.\n", encoding="utf-8")
    artifact_context.add_path("candidate_skill", "part0", source)
    instruction = tmp_path / "review.md"
    instruction.write_text("# Expert review\n\nTreat notebook output as exploratory.\n", encoding="utf-8")
    expected = "sha256:" + hashlib.sha256(instruction.read_bytes()).hexdigest()
    created = development_sessions.create(
        "candidate_project",
        automation_brief_digest="sha256:" + "1" * 64,
        research_prototype_digest="sha256:" + "2" * 64,
        artifact_groups=["part0"],
        prohibited_actions=["Do not claim confirmation"],
    )

    attached = development_sessions.attach_instruction_file(
        created["session"]["session_id"],
        "reviewed_prose",
        instruction,
        expected_digest=expected,
        media_type="text/markdown",
    )
    restored = development_sessions.get_instruction(
        created["session"]["session_id"], "reviewed_prose"
    )

    assert attached["instruction"]["content_digest"] == expected
    assert restored["content"].startswith("# Expert review")
    assert Path(attached["instruction"]["path"]).parent != instruction.parent


def test_development_session_admits_external_owner_artifact_view(project_space, tmp_path: Path) -> None:
    _skill(project_space["skills"], "candidate_skill")
    _skill(project_space["skills"], "source_direction")
    compositions.create(_project("candidate_project", "candidate_skill"))
    visible = tmp_path / "notebook.ipynb"
    visible.write_text("{}", encoding="utf-8")
    hidden = tmp_path / "oracle.md"
    hidden.write_text("hidden answer", encoding="utf-8")
    artifact_context.add_path("source_direction", "part0", visible)
    artifact_context.add_path(
        "source_direction",
        "part0",
        hidden,
        context_policy={"default": "deny", "allow": ["research.evaluation"], "deny": []},
    )

    created = development_sessions.create(
        "candidate_project",
        automation_brief_digest="sha256:" + "1" * 64,
        research_prototype_digest="sha256:" + "2" * 64,
        artifact_groups=[],
        execution_budget={
            "budget_view": "fixed_downstream",
            "max_wall_seconds": 7200,
            "max_model_tokens": 80000,
            "max_attempts": 1,
            "max_human_interventions": 0,
        },
        agent_profile={
            "provider": "openai-codex-cli",
            "model": "gpt-5.4",
            "reasoning_effort": "high",
            "tool_profile": "adaos-local-bounded-v1",
        },
        artifact_sources=[
            {
                "skill_id": "source_direction",
                "group_id": "part0",
                "audience": "research.calibration.c0_raw",
            }
        ],
        prohibited_actions=["No evaluator access"],
    )

    admitted = created["session"]["artifact_inputs"][0]
    assert admitted["ref"] == "artifact://skill/source_direction/part0"
    assert admitted["audience"] == "research.calibration.c0_raw"
    assert created["session"]["handoff"]["execution_budget"]["max_wall_seconds"] == 7200
    assert created["session"]["handoff"]["validation_budget"] == {
        "schema": "adaos.builder.validation_budget.v1",
        "packaged_pytest_wall_seconds": 120,
        "source": "development_session.execution_budget",
        "execution_max_wall_seconds": 7200,
    }
    assert created["session"]["handoff"]["agent_profile"]["model"] == "gpt-5.4"
    assert [path.name for path in Path(admitted["root_path"]).iterdir()] == ["notebook.ipynb"]


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
