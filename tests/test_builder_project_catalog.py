from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import builder as builder_api
from adaos.apps.api.auth import require_token
from adaos.services.builder.project_catalog import BuilderProjectCatalogService


def test_project_catalog_reads_only_manifest_and_prompt_summary(monkeypatch, tmp_path: Path) -> None:
    scenarios = tmp_path / "scenarios"
    skills = tmp_path / "skills"
    scenarios.mkdir()
    skills.mkdir()
    project = scenarios / "shopping"
    project.mkdir()
    (project / "scenario.yaml").write_text(
        "id: shopping\ntitle: Shopping List\ndescription: Groceries\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    (project / "prompt_state.json").write_text(
        '{"archived": false, "updated_at": "2026-07-21T10:00:00Z", "base_tz": "large"}',
        encoding="utf-8",
    )
    tz = project / "tz"
    tz.mkdir()
    (tz / "base_tz.md").write_text("x" * 100_000, encoding="utf-8")
    (skills / "test_skill").mkdir()
    (skills / "test_skill" / "skill.yaml").write_text("name: test_skill\nversion: 1.0.0\n", encoding="utf-8")

    reads: list[Path] = []
    original_read_text = Path.read_text

    def _read_text(path: Path, *args, **kwargs):
        reads.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)
    binding = tmp_path / "state" / "builder" / "workbench" / "bindings"
    binding.mkdir(parents=True)
    (binding / "dev1.json").write_text('{"preview_webspace_id": "preview-one"}', encoding="utf-8")
    service = BuilderProjectCatalogService(
        skills_root=skills,
        scenarios_root=scenarios,
        state_dir=tmp_path / "state",
    )

    items = service.list_projects(
        selected_object_type="scenario",
        selected_object_id="shopping",
        webspace_id="dev1",
    )

    assert [item["id"] for item in items] == ["scenario:shopping", "skill:test_skill"]
    assert items[0]["current"] is True
    assert items[0]["space"] == "preview-one"
    assert items[0]["updated"] == "2026-07-21T10:00:00Z"
    assert sum(path.name == "dev1.json" for path in reads) == 1
    assert sum(path.name == "scenario.yaml" for path in reads) == 1
    assert sum(path.name == "prompt_state.json" for path in reads) == 1
    assert not any(path.name == "base_tz.md" for path in reads)


def test_project_catalog_api_forwards_bounded_query() -> None:
    calls: list[dict] = []

    class _Catalog:
        def list_projects(self, **kwargs):
            calls.append(kwargs)
            return [{"id": "scenario:shopping", "title": "Shopping List"}]

    app = FastAPI()
    app.include_router(builder_api.router, prefix="/api/builder")
    app.dependency_overrides[require_token] = lambda: None
    app.dependency_overrides[builder_api._get_project_catalog_service] = _Catalog
    client = TestClient(app)

    response = client.get(
        "/api/builder/workbench/projects",
        params={
            "kind": "scenario",
            "query": "shop",
            "limit": 20,
            "selected_object_type": "scenario",
            "selected_object_id": "shopping",
            "webspace_id": "dev1",
        },
    )

    assert response.status_code == 200
    assert response.json() == [{"id": "scenario:shopping", "title": "Shopping List"}]
    assert calls == [
        {
            "kind": "scenario",
            "query": "shop",
            "limit": 20,
            "selected_object_type": "scenario",
            "selected_object_id": "shopping",
            "webspace_id": "dev1",
            "include_archived": False,
        }
    ]


def test_project_catalog_hides_archived_projects_unless_requested(tmp_path: Path) -> None:
    scenarios = tmp_path / "scenarios"
    project = scenarios / "archived_scene"
    project.mkdir(parents=True)
    (project / "scenario.yaml").write_text(
        "id: archived_scene\ntitle: Archived scene\ndescription: Old work\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    (project / "prompt_state.json").write_text('{"archived": true}', encoding="utf-8")
    service = BuilderProjectCatalogService(
        skills_root=tmp_path / "skills",
        scenarios_root=scenarios,
        state_dir=tmp_path / "state",
    )

    assert service.list_projects() == []
    assert [item["id"] for item in service.list_projects(include_archived=True)] == [
        "scenario:archived_scene"
    ]


def test_project_catalog_stops_after_yaml_catalog_header(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    project = skills / "large_skill"
    project.mkdir(parents=True)
    nested_tools = "\n".join(
        f"  - name: tool_{index}\n    description: Tool {index}\n    input_schema:\n      type: object"
        for index in range(500)
    )
    (project / "skill.yaml").write_text(
        "name: large_skill\n"
        "version: 1.2.3\n"
        "description: Large development skill.\n"
        "title_i18n:\n"
        "  key: skill.large.title\n"
        "  fallback: Large Skill\n"
        "depends:\n"
        "  - shared_skill\n"
        "tools:\n"
        f"{nested_tools}\n",
        encoding="utf-8",
    )
    service = BuilderProjectCatalogService(
        skills_root=skills,
        scenarios_root=tmp_path / "scenarios",
        state_dir=tmp_path / "state",
    )

    items = service.list_projects(kind="skill")

    assert len(items) == 1
    assert items[0]["object_id"] == "large_skill"
    assert items[0]["depends"] == ["shared_skill"]

    (project / "skill.yaml").write_text(
        "name: large_skill\nversion: 2.0.0\ndescription: Updated development skill.\n",
        encoding="utf-8",
    )

    refreshed = service.list_projects(kind="skill")

    assert refreshed[0]["version"] == "2.0.0"
    assert refreshed[0]["description"] == "Updated development skill."
