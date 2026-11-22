from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from adaos.services.builder.ui_studio import (
    CONTEXT_LIMIT_BYTES,
    BuilderUIStudioError,
    BuilderUIStudioService,
    compact_webui_inventory,
    validate_ui_studio_intent,
)
from adaos.services.scenario.webspace_components.builder_publication import (
    WebspaceBuilderPublicationService,
)


ABI_ROOT = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi"


def _region(region_id: str, role: str) -> dict:
    return {
        "id": region_id,
        "role": role,
        "presentation": {"wide": "pane", "compact": "stack"},
    }


def _webui() -> dict:
    layout = {
        "version": 2,
        "pattern": "dashboard",
        "density": "compact",
        "contentWidth": "bounded",
        "scroll": "page",
        "regions": [
            _region("heading", "toolbar"),
            _region("navigation", "toolbar"),
            _region("indicator", "utility"),
            _region("period", "utility"),
            _region("main", "main"),
        ],
        "variants": [
            {
                "id": "overview",
                "when": "$state.activeView === 'overview'",
                "pattern": "dashboard",
                "density": "compact",
                "contentWidth": "bounded",
                "regions": [
                    _region("heading", "toolbar"),
                    _region("navigation", "toolbar"),
                    _region("indicator", "utility"),
                    _region("period", "utility"),
                    _region("main", "main"),
                ],
            },
            {
                "id": "passport",
                "when": "$state.activeView === 'passport'",
                "pattern": "dashboard",
                "density": "compact",
                "contentWidth": "bounded",
                "regions": [
                    _region("heading", "toolbar"),
                    _region("navigation", "toolbar"),
                    _region("indicator", "utility"),
                    _region("period", "utility"),
                    _region("main", "main"),
                ],
            },
        ],
    }
    overview_source = {
        "kind": "skill",
        "name": "towns.overview",
        "params": {
            "indicator_id": "$state.indicatorId",
            "period": "$state.period",
            "search": "$state.search",
        },
        "resultPath": "items",
    }
    return {
        "schema": "adaos.webui.v1",
        "ui": {
            "version": "0.1.0",
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "towns",
                        "title": "Towns",
                        "initialState": {
                            "activeView": "overview",
                            "overviewMode": "map",
                            "selectedCityId": "one",
                            "indicatorId": "population",
                            "period": "2024",
                            "search": "",
                        },
                        "layout": layout,
                        "widgets": [
                            {
                                "id": "heading",
                                "type": "static.markdown",
                                "area": "heading",
                                "inputs": {"content": "## Towns"},
                            },
                            {
                                "id": "navigation",
                                "type": "ui.actions",
                                "area": "navigation",
                                "inputs": {
                                    "variant": "toolbar",
                                    "buttons": [
                                        {"id": "overview", "label": "Overview"},
                                        {"id": "passport", "label": "Passport"},
                                    ],
                                    "selectedStateKey": "activeView",
                                },
                                "actions": [
                                    {
                                        "id": "overview",
                                        "on": "click:overview",
                                        "type": "updateState",
                                        "params": {"activeView": "overview"},
                                    },
                                    {
                                        "id": "passport",
                                        "on": "click:passport",
                                        "type": "updateState",
                                        "params": {"activeView": "passport"},
                                    },
                                ],
                            },
                            {
                                "id": "indicator",
                                "type": "input.selector",
                                "area": "indicator",
                                "title": "Indicator",
                                "inputs": {
                                    "options": [
                                        {"value": "population", "label": "Population"}
                                    ],
                                    "value": "$state.indicatorId",
                                },
                                "actions": [
                                    {
                                        "on": "change",
                                        "type": "updateState",
                                        "params": {"indicatorId": "$event.value"},
                                    }
                                ],
                            },
                            {
                                "id": "period",
                                "type": "input.selector",
                                "area": "period",
                                "title": "Period",
                                "visibleIf": "$state.activeView === 'overview'",
                                "inputs": {
                                    "options": [{"value": "2024", "label": "2024"}],
                                    "value": "$state.period",
                                },
                                "actions": [
                                    {
                                        "on": "change",
                                        "type": "updateState",
                                        "params": {"period": "$event.value"},
                                    }
                                ],
                            },
                            {
                                "id": "filters",
                                "type": "ui.queryToolbar",
                                "area": "main",
                                "visibleIf": "$state.activeView === 'overview'",
                                "inputs": {
                                    "controls": [
                                        {
                                            "id": "search",
                                            "label": "Search",
                                            "kind": "search",
                                            "inputType": "search",
                                            "stateKey": "search",
                                        }
                                    ]
                                },
                            },
                            {
                                "id": "overview-mode",
                                "type": "ui.actions",
                                "area": "main",
                                "visibleIf": "$state.activeView === 'overview'",
                                "inputs": {
                                    "buttons": [
                                        {"id": "table", "label": "Table"},
                                        {"id": "map", "label": "Map"},
                                    ],
                                    "selectedStateKey": "overviewMode",
                                },
                            },
                            {
                                "id": "overview-table",
                                "type": "ui.table",
                                "area": "main",
                                "title": "Towns",
                                "visibleIf": "$state.activeView === 'overview' && $state.overviewMode === 'table'",
                                "dataSource": copy.deepcopy(overview_source),
                                "inputs": {
                                    "rowKey": "id",
                                    "selectedStateKey": "selectedCityId",
                                    "columns": [
                                        {"key": "name", "label": "Town"},
                                        {"key": "region", "label": "Region"},
                                        {"key": "value_label", "label": "Value"},
                                        {"key": "period", "label": "Period"},
                                        {"key": "unit", "label": "Unit"},
                                    ],
                                },
                                "actions": [
                                    {
                                        "on": "select",
                                        "type": "updateState",
                                        "params": {
                                            "selectedCityId": "$event.id",
                                            "activeView": "passport",
                                        },
                                    }
                                ],
                            },
                            {
                                "id": "overview-map",
                                "type": "visual.geoMap",
                                "area": "main",
                                "title": "Map",
                                "visibleIf": "$state.activeView === 'overview' && $state.overviewMode === 'map'",
                                "dataSource": {
                                    key: copy.deepcopy(value)
                                    for key, value in overview_source.items()
                                    if key != "resultPath"
                                },
                                "inputs": {
                                    "itemsPath": "items",
                                    "selectionValue": "key",
                                    "rowKey": "id",
                                    "stateKey": "selectedCityId",
                                    "latitudeField": "latitude",
                                    "longitudeField": "longitude",
                                    "labelField": "name",
                                    "publicKeyPath": "map.publicKey",
                                },
                                "actions": [
                                    {
                                        "on": "select",
                                        "type": "updateState",
                                        "params": {
                                            "selectedCityId": "$event.id",
                                            "activeView": "passport",
                                        },
                                    }
                                ],
                            },
                            {
                                "id": "passport-city",
                                "type": "item.details",
                                "area": "main",
                                "title": "Town passport",
                                "visibleIf": "$state.activeView === 'passport'",
                                "dataSource": {
                                    "kind": "skill",
                                    "name": "towns.passport",
                                    "params": {"city_id": "$state.selectedCityId"},
                                    "resultPath": "city",
                                },
                                "inputs": {
                                    "presentation": "section",
                                    "fields": [
                                        {"key": "name", "label": "Town"},
                                        {"key": "region", "label": "Region"},
                                    ],
                                },
                            },
                            {
                                "id": "history-chart",
                                "type": "visual.metricChart",
                                "area": "main",
                                "visibleIf": "$state.activeView === 'passport'",
                                "inputs": {},
                            },
                        ],
                    }
                }
            },
        },
    }


def _intent() -> dict:
    views = [
        ("heading", "header", 100, []),
        ("navigation", "navigation", 95, []),
        ("indicator", "control", 100, []),
        ("period", "control", 90, []),
        ("filters", "filters", 90, []),
        ("overview-map", "primary", 100, []),
        ("overview-table", "collection", 95, ["name", "region", "value_label"]),
        ("passport-city", "detail", 90, []),
    ]
    return {
        "schema": "adaos.builder.ui_studio_intent.v1",
        "intent_id": "ui-intent:towns-test",
        "project_ref": "scenario:towns",
        "profile_ref": "geo_explorer.v1",
        "base_revision": None,
        "goal": {
            "kind": "explore",
            "statement": "Explore towns without losing selection.",
            "primary_ref": "view:overview-map",
        },
        "views": [
            {
                "ref": f"view:{widget_id}",
                "widget_id": widget_id,
                "role": role,
                "priority": priority,
                **({"field_refs": fields} if fields else {}),
            }
            for widget_id, role, priority, fields in views
        ],
        "relations": [
            {
                "id": "map-table",
                "kind": "co_visible",
                "source_refs": ["view:overview-map"],
                "target_refs": ["view:overview-table"],
                "strength": "hard",
            },
            {
                "id": "selection-detail",
                "kind": "selection_drives",
                "source_refs": ["view:overview-map", "view:overview-table"],
                "target_refs": ["view:passport-city"],
                "strength": "hard",
            },
        ],
        "constraints": [
            {
                "id": "preserve",
                "rule": "preserve_data_contracts",
                "strength": "hard",
                "weight": 10,
                "refs": [],
            },
            {
                "id": "context",
                "rule": "bounded_context",
                "strength": "hard",
                "weight": 10,
                "refs": [],
            },
        ],
        "responsive": {
            "wide_min_px": 1180,
            "compact_strategy": "progressive_disclosure",
        },
        "provenance": {
            "kind": "benchmark",
            "source": "test",
            "model": None,
            "request_id": None,
        },
    }


@pytest.fixture
def studio(tmp_path: Path) -> tuple[BuilderUIStudioService, Path]:
    scenarios = tmp_path / "scenarios"
    projects = tmp_path / "projects"
    source = scenarios / "towns"
    source.mkdir(parents=True)
    projects.mkdir()
    (source / "scenario.yaml").write_text("id: towns\nversion: 0.1.0\n", encoding="utf-8")
    (source / "webui.json").write_text(
        json.dumps(_webui(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    revisions = source / "ui_revisions"
    revisions.mkdir()
    (revisions / "001.json").write_text("{}\n", encoding="utf-8")
    (revisions / "current.txt").write_text("001\n", encoding="utf-8")
    return (
        BuilderUIStudioService(
            state_dir=tmp_path / "state",
            dev_scenarios_root=scenarios,
            dev_projects_root=projects,
        ),
        source,
    )


def _widgets(document: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}

    def visit(items):
        for item in items or []:
            result[item["id"]] = item
            visit(item.get("children"))

    visit(document["ui"]["application"]["desktop"]["pageSchema"]["widgets"])
    return result


def test_studio_compiles_scores_and_stages_without_advancing_current(
    studio: tuple[BuilderUIStudioService, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, source = studio
    experiment = service.create_experiment(_intent())

    assert experiment["selected_option_id"] == "atlas"
    scores = {item["option_id"]: item["evaluation"]["score"] for item in experiment["options"]}
    assert scores["atlas"] > scores["registry"] >= 80
    assert all(item["evaluation"]["hard_failures"] == 0 for item in experiment["options"])
    assert experiment["context_budget"]["input_bytes"] < CONTEXT_LIMIT_BYTES

    candidate = service.candidate(experiment["experiment_id"])
    schema = json.loads((ABI_ROOT / "webui.v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(candidate)
    page = candidate["ui"]["application"]["desktop"]["pageSchema"]
    overview = next(item for item in page["layout"]["variants"] if item["id"] == "overview")
    assert overview["pattern"] == "master-detail"
    assert {item["id"] for item in overview["regions"]} >= {
        "studio-primary",
        "studio-secondary",
        "studio-inspector",
    }
    widgets = _widgets(candidate)
    assert widgets["overview-map"]["visibleIf"] == "$state.activeView === 'overview'"
    assert widgets["overview-table"]["visibleIf"] == "$state.activeView === 'overview'"
    assert [item["key"] for item in widgets["overview-table"]["inputs"]["columns"]] == [
        "name",
        "region",
        "value_label",
    ]
    assert all(
        item["sortable"] is True
        for item in widgets["overview-table"]["inputs"]["columns"]
    )
    assert widgets["history-chart"]["inputs"]["showSummary"] is False
    for widget_id in ("overview-map", "overview-table"):
        params = widgets[widget_id]["actions"][0]["params"]
        assert params["selectedCityId"] == "$event.id"
        assert "activeView" not in params
    assert widgets["ui-studio-selection-actions"]["actions"][0]["params"] == {
        "activeView": "passport"
    }

    staged = service.stage(experiment["experiment_id"])
    assert staged["current_pointer_unchanged"] is True
    assert (source / "ui_revisions" / f"{staged['revision']}.json").is_file()
    assert (source / "ui_revisions" / "current.txt").read_text(encoding="utf-8") == "001\n"
    admitted = service.read_staged_revision("towns", staged["revision"])
    assert admitted["after_webui"] == candidate

    monkeypatch.setattr(
        BuilderUIStudioService,
        "from_context",
        classmethod(lambda cls: service),
    )
    rendered, source_space = WebspaceBuilderPublicationService().preview_content_override(
        "towns",
        stage="prototype",
        revision=staged["revision"],
        label="UI Studio test",
        operations=SimpleNamespace(
            clone_json_like=copy.deepcopy,
            scenarios_loader=SimpleNamespace(
                read_content=lambda *args, **kwargs: pytest.fail(
                    "admitted EXP Preview fell back to mutable scenario source"
                )
            ),
        ),
    )
    assert source_space == "dev"
    assert _widgets(rendered)["overview-map"]["sizing"] == "fill"


def test_project_and_primary_scenario_restore_the_same_latest_experiment(
    studio: tuple[BuilderUIStudioService, Path],
) -> None:
    service, _source = studio
    project = Path(service.dev_projects_root or "") / "towns"
    project.mkdir()
    (project / "project.yaml").write_text(
        "id: towns\ncomponents:\n  owned:\n    - ref: scenario:towns\n      role: primary\n",
        encoding="utf-8",
    )
    intent = _intent()
    intent["project_ref"] = "project:towns"

    experiment = service.create_experiment(intent)

    assert experiment["project_ref"] == "scenario:towns"
    assert service.latest("project:towns")["experiment"]["experiment_id"] == experiment["experiment_id"]
    assert service.latest("scenario:towns")["experiment"]["experiment_id"] == experiment["experiment_id"]


def test_staged_revision_rejects_mutated_exp_file(
    studio: tuple[BuilderUIStudioService, Path],
) -> None:
    service, source = studio
    experiment = service.create_experiment(_intent())
    staged = service.stage(experiment["experiment_id"])
    revision_path = source / "ui_revisions" / f"{staged['revision']}.json"
    value = json.loads(revision_path.read_text(encoding="utf-8"))
    value["after_webui"]["ui"]["application"]["desktop"]["pageSchema"][
        "title"
    ] = "Forged"
    revision_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(BuilderUIStudioError, match="authority mismatch"):
        service.read_staged_revision("towns", staged["revision"])


def test_inventory_is_bounded_even_when_widget_content_is_large() -> None:
    webui = _webui()
    webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]["inputs"][
        "content"
    ] = "x" * (2 * 1024 * 1024)

    inventory = compact_webui_inventory(webui)

    assert inventory["input_bytes"] < CONTEXT_LIMIT_BYTES
    assert "x" * 1000 not in json.dumps(inventory)


def test_intent_rejects_unknown_relation_ref() -> None:
    intent = _intent()
    intent["relations"][0]["target_refs"] = ["view:missing"]

    with pytest.raises(BuilderUIStudioError, match="unknown views"):
        validate_ui_studio_intent(intent)


def test_ordinary_llm_proposes_only_intent_then_core_compiles(
    studio: tuple[BuilderUIStudioService, Path],
) -> None:
    service, _source = studio
    proposal = {
        "status": "proposed",
        "message": "Use a map-led workspace.",
        "profile_ref": "geo_explorer.v1",
        "goal_kind": "explore",
        "primary_widget_id": "overview-map",
        "views": [
            {"widget_id": "heading", "role": "header", "priority": 100, "field_refs": []},
            {"widget_id": "navigation", "role": "navigation", "priority": 95, "field_refs": []},
            {"widget_id": "indicator", "role": "control", "priority": 100, "field_refs": []},
            {"widget_id": "period", "role": "control", "priority": 90, "field_refs": []},
            {"widget_id": "filters", "role": "filters", "priority": 90, "field_refs": []},
            {"widget_id": "overview-map", "role": "primary", "priority": 100, "field_refs": []},
            {
                "widget_id": "overview-table",
                "role": "collection",
                "priority": 95,
                "field_refs": ["name", "region", "value_label"],
            },
            {"widget_id": "passport-city", "role": "detail", "priority": 90, "field_refs": []},
        ],
        "relations": [
            {
                "id": "map-table",
                "kind": "co_visible",
                "source_widget_ids": ["overview-map"],
                "target_widget_ids": ["overview-table"],
                "strength": "hard",
            },
            {
                "id": "selection-detail",
                "kind": "selection_drives",
                "source_widget_ids": ["overview-map", "overview-table"],
                "target_widget_ids": ["passport-city"],
                "strength": "hard",
            },
        ],
    }
    calls: list[dict] = []

    def submit(**kwargs):
        calls.append(kwargs)
        return {"job_id": "job-ui-studio"}

    def wait(job_id, **_kwargs):
        assert job_id == "job-ui-studio"
        return {
            "status": "succeeded",
            "output_text": json.dumps(proposal),
        }

    service.submit = submit
    service.wait = wait
    result = service.propose(
        project_ref="scenario:towns",
        prompt="Build a professional map-led analytical dashboard.",
        model="gpt-5",
    )

    assert result["model_calls"] == 1
    assert result["input_bytes"] < CONTEXT_LIMIT_BYTES
    assert result["experiment"]["intent"]["provenance"]["kind"] == "model"
    assert result["experiment"]["selected_option_id"] == "atlas"
    request_text = json.dumps(calls[0], ensure_ascii=False)
    assert "<html" not in request_text.lower()
    assert "webui json" in request_text.lower()
