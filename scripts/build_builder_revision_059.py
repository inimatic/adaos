"""Add the experimental LLM UI Studio surface to the active Builder DEV package."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / ".adaos" / "dev" / "sn_6acf0c01"
SCENARIO = DEV / "scenarios" / "builder"
SKILL = DEV / "skills" / "builder_sdk_control_skill"
WEBUI = SCENARIO / "webui.json"
SCENARIO_JSON = SCENARIO / "scenario.json"
SCENARIO_YAML = SCENARIO / "scenario.yaml"
CURRENT = SCENARIO / "ui_revisions" / "current.txt"
HANDLER = SKILL / "handlers" / "main.py"
SKILL_YAML = SKILL / "skill.yaml"
REVISION = "006"
VERSION = "0.2.125"
SKILL_VERSION = "0.2.37"
MARKER = "builder-ui-studio-explicit-variant-preview-062"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected an object in {path}")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


HANDLER_BLOCK = r'''


def _ui_studio_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    experiment = value.get("experiment") if isinstance(value.get("experiment"), Mapping) else {}
    options = []
    selected = str(experiment.get("selected_option_id") or "").strip()
    for item in experiment.get("options") or []:
        if not isinstance(item, Mapping):
            continue
        evaluation = item.get("evaluation") if isinstance(item.get("evaluation"), Mapping) else {}
        option_id = str(item.get("option_id") or "").strip()
        options.append({
            "option_id": option_id,
            "label": item.get("label"),
            "score": evaluation.get("score"),
            "passed": bool(evaluation.get("passed")),
            "hard_failures": int(evaluation.get("hard_failures") or 0),
            "primary_ref": item.get("primary_ref"),
            "layout_pattern": item.get("layout_pattern"),
            "selected": option_id == selected,
            "status_label": "Выбран" if option_id == selected else "Допущен" if evaluation.get("passed") else "Отклонён",
        })
    selected_option = next((item for item in options if item["selected"]), {})
    budget = experiment.get("context_budget") if isinstance(experiment.get("context_budget"), Mapping) else {}
    intent = experiment.get("intent") if isinstance(experiment.get("intent"), Mapping) else {}
    provenance = intent.get("provenance") if isinstance(intent.get("provenance"), Mapping) else {}
    provenance_kind = str(provenance.get("kind") or "").strip()
    provenance_source = str(provenance.get("source") or "").strip()
    provenance_model = str(provenance.get("model") or "").strip()
    if provenance_kind == "benchmark":
        intent_source_label = f"Эталон · {provenance_source or 'benchmark'}"
    elif provenance_kind == "model":
        intent_source_label = f"Обычная LLM · {provenance_model or provenance_source or 'model'}"
    else:
        intent_source_label = provenance_source or provenance_kind or None
    return {
        "ok": True,
        "status": value.get("status") or experiment.get("status") or "not_started",
        "status_label": "Эксперимент не создан" if not experiment else str(experiment.get("status") or "evaluated"),
        "project_ref": value.get("project_ref"),
        "experiment_id": experiment.get("experiment_id"),
        "profile_ref": intent.get("profile_ref"),
        "intent_id": intent.get("intent_id"),
        "goal": (intent.get("goal") or {}).get("statement") if isinstance(intent.get("goal"), Mapping) else None,
        "intent_source_label": intent_source_label,
        "base_revision": experiment.get("base_revision"),
        "selected_option_id": selected or None,
        "selected_option_label": selected_option.get("label"),
        "selected_score": selected_option.get("score"),
        "staged_revision": experiment.get("staged_revision"),
        "context_usage": (
            f"{budget.get('input_bytes', 0)} / {budget.get('limit_bytes', 0)} bytes · "
            f"{budget.get('included_widgets', 0)} of {budget.get('total_widgets', 0)} widgets"
            if budget else None
        ),
        "context_truncated": bool(budget.get("truncated")),
        "can_preview": bool(experiment and selected_option.get("passed")),
        "options": options,
        "experiment": dict(experiment) if experiment else None,
    }


@tool("get_ui_studio", summary="Read the latest isolated typed UI experiment for the selected project.", side_effects="none")
def get_ui_studio(
    object_type: str = DEFAULT_PROJECT_KIND,
    object_id: str = DEFAULT_PROJECT_ID,
) -> dict[str, Any]:
    kind, project_id = _identity(object_type, object_id)
    return _ui_studio_projection(ui_studio.state(kind, project_id))


@tool("create_ui_studio_benchmark", summary="Compile and evaluate the Small Cities UI benchmark without changing its accepted revision.", side_effects="local_write")
def create_ui_studio_benchmark(
    object_type: str = DEFAULT_PROJECT_KIND,
    object_id: str = DEFAULT_PROJECT_ID,
) -> dict[str, Any]:
    kind, project_id = _identity(object_type, object_id)
    if project_id != "small_cities_analytics":
        raise ValueError("small-cities.v1 is available only for the Small Cities benchmark project")
    ui_studio.benchmark(kind, project_id, benchmark_id="small-cities.v1")
    return _ui_studio_projection(ui_studio.state(kind, project_id))


@tool("propose_ui_studio", summary="Use an ordinary configured LLM to propose compact UI intent; Codex is not involved.", side_effects="external_write")
def propose_ui_studio(
    prompt: str,
    model: str,
    reasoning_effort: str = "medium",
    object_type: str = DEFAULT_PROJECT_KIND,
    object_id: str = DEFAULT_PROJECT_ID,
) -> dict[str, Any]:
    kind, project_id = _identity(object_type, object_id)
    return ui_studio.propose(
        kind,
        project_id,
        prompt=prompt,
        model=model,
        reasoning_effort=reasoning_effort,
    )


@tool("select_ui_studio_option", summary="Select one passed UI Studio alternative without changing project source.", side_effects="local_write")
def select_ui_studio_option(experiment_id: str, option_id: str) -> dict[str, Any]:
    ui_studio.select(experiment_id, option_id)
    result = ui_studio.inspect(experiment_id)
    return _ui_studio_projection({
        "status": "ready",
        "project_ref": result.get("project_ref"),
        "experiment": result,
    })


@tool("preview_ui_studio", summary="Stage one passed UI experiment and show its exact EXP revision in paired Preview.", side_effects="ui_navigation")
def preview_ui_studio(
    experiment_id: str,
    option_id: str | None = None,
    webspace_id: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return ui_studio.preview(
        experiment_id,
        option_id,
        source_webspace_id=_preview_source_webspace_id(webspace_id, _meta),
    )
'''


TOOLS = [
    {
        "name": "get_ui_studio",
        "description": "Read the latest isolated typed UI experiment for the selected project.",
        "entry": "handlers.main:get_ui_studio",
        "side_effects": "none",
        "input_schema": {
            "type": "object",
            "properties": {
                "object_type": {"type": "string", "enum": ["project", "scenario"]},
                "object_id": {"type": "string", "minLength": 1},
            },
        },
        "output_schema": {"type": "object"},
    },
    {
        "name": "create_ui_studio_benchmark",
        "description": "Compile and evaluate the Small Cities UI benchmark without changing its accepted revision.",
        "entry": "handlers.main:create_ui_studio_benchmark",
        "side_effects": "local_write",
        "input_schema": {
            "type": "object",
            "properties": {
                "object_type": {"type": "string", "enum": ["project", "scenario"]},
                "object_id": {"type": "string", "minLength": 1},
            },
        },
        "output_schema": {"type": "object"},
    },
    {
        "name": "propose_ui_studio",
        "description": "Use an ordinary configured LLM to propose compact UI intent; Codex is not involved.",
        "entry": "handlers.main:propose_ui_studio",
        "side_effects": "external_write",
        "input_schema": {
            "type": "object",
            "properties": {
                "object_type": {"type": "string", "enum": ["project", "scenario"]},
                "object_id": {"type": "string", "minLength": 1},
                "prompt": {"type": "string", "minLength": 1, "maxLength": 8000},
                "model": {"type": "string", "minLength": 1},
                "reasoning_effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh"], "default": "medium"},
            },
            "required": ["prompt", "model"],
        },
        "output_schema": {"type": "object"},
    },
    {
        "name": "select_ui_studio_option",
        "description": "Select one passed UI Studio alternative without changing project source.",
        "entry": "handlers.main:select_ui_studio_option",
        "side_effects": "local_write",
        "input_schema": {
            "type": "object",
            "properties": {
                "experiment_id": {"type": "string", "pattern": "^UIX-[A-F0-9]{12}$"},
                "option_id": {"type": "string", "minLength": 1},
            },
            "required": ["experiment_id", "option_id"],
        },
        "output_schema": {"type": "object"},
    },
    {
        "name": "preview_ui_studio",
        "description": "Stage one passed UI experiment and show its exact EXP revision in paired Preview.",
        "entry": "handlers.main:preview_ui_studio",
        "side_effects": "ui_navigation",
        "input_schema": {
            "type": "object",
            "properties": {
                "experiment_id": {"type": "string", "pattern": "^UIX-[A-F0-9]{12}$"},
                "option_id": {"type": "string"},
                "webspace_id": {"type": "string"},
            },
            "required": ["experiment_id"],
        },
        "output_schema": {"type": "object"},
    },
]


def _patch_handler() -> None:
    source = HANDLER.read_text(encoding="utf-8")
    if "    ui_studio,\n" not in source:
        needle = "    semantic_ui,\n    workflow,"
        if needle not in source:
            raise RuntimeError("Builder SDK handler import anchor changed")
        source = source.replace(
            needle,
            "    semantic_ui,\n    ui_studio,\n    workflow,",
            1,
        )
    anchor = "\n\n__all__ = ["
    if anchor not in source:
        raise RuntimeError("Builder SDK handler export anchor changed")
    block_start = source.find("\ndef _ui_studio_projection(")
    if block_start >= 0:
        block_end = source.find(anchor, block_start)
        if block_end < 0:
            raise RuntimeError("Builder UI Studio handler block boundary changed")
        source = source[:block_start] + HANDLER_BLOCK.rstrip() + source[block_end:]
    else:
        source = source.replace(anchor, HANDLER_BLOCK + anchor, 1)
    exports = [
        "get_ui_studio",
        "create_ui_studio_benchmark",
        "propose_ui_studio",
        "select_ui_studio_option",
        "preview_ui_studio",
    ]
    for name in reversed(exports):
        token = f'    "{name}",'
        if token not in source:
            source = source.replace("__all__ = [\n", f'__all__ = [\n{token}\n', 1)
    compile(source, str(HANDLER), "exec")
    HANDLER.write_text(source, encoding="utf-8")


def _patch_skill_manifest() -> None:
    manifest = yaml.safe_load(SKILL_YAML.read_text(encoding="utf-8-sig")) or {}
    tools = manifest.setdefault("tools", [])
    existing = {str(item.get("name") or "") for item in tools if isinstance(item, dict)}
    for tool in TOOLS:
        if tool["name"] not in existing:
            tools.append(copy.deepcopy(tool))
    manifest["version"] = SKILL_VERSION
    assessment = (
        manifest.setdefault("runtime", {})
        .setdefault("activation", {})
        .setdefault("assessment", {})
    )
    assessment["tool_count"] = len(tools)
    assessment["handler_digest"] = "sha256:" + hashlib.sha256(HANDLER.read_bytes()).hexdigest()
    SKILL_YAML.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _studio_widgets() -> list[dict[str, Any]]:
    visible = "$state.workbenchView === 'ui-studio'"
    source = {
        "kind": "skill",
        "name": "builder_sdk_control_skill.get_ui_studio",
        "params": {
            "object_type": "$state.selectedProjectKind",
            "object_id": "$state.selectedProjectId",
            "_meta": {"current_scenario": "builder"},
        },
        "cacheTtlMs": 0,
        "invalidationTags": ["builder.ui-studio", "builder.project.preview"],
        "preserveLastValue": True,
    }
    return [
        {
            "id": "ui-studio-intro",
            "type": "static.markdown",
            "area": "main",
            "visibleIf": visible,
            "inputs": {
                "content": (
                    "## LLM UI Studio · экспериментальный режим\n\n"
                    "Обычная LLM формирует только компактный **intent**: роли представлений, "
                    "отношения и приоритеты. Core собирает несколько декларативных вариантов, "
                    "проверяет контракты данных, адаптивность и непрерывность выбора. HTML, CSS "
                    "и Angular-код модель не генерирует. Кандидаты остаются EXP-ревизиями до "
                    "отдельного решения о принятии."
                )
            },
        },
        {
            "id": "ui-studio-state",
            "type": "item.details",
            "area": "main",
            "title": "Эксперимент",
            "visibleIf": visible,
            "dataSource": copy.deepcopy(source),
            "inputs": {
                "presentation": "section",
                "fields": [
                    {"key": "status_label", "label": "Состояние"},
                    {"key": "experiment_id", "label": "Experiment"},
                    {"key": "profile_ref", "label": "Профиль"},
                    {"key": "goal", "label": "Задание / цель интерфейса"},
                    {"key": "intent_source_label", "label": "Источник задания"},
                    {"key": "base_revision", "label": "Базовая ревизия"},
                    {"key": "selected_option_label", "label": "Выбранный вариант"},
                    {"key": "selected_score", "label": "Оценка / 100"},
                    {"key": "context_usage", "label": "LLM context"},
                    {"key": "staged_revision", "label": "Preview EXP"},
                ],
                "stateBindings": {
                    "uiStudioExperimentId": "experiment_id",
                    "uiStudioSelectedOptionId": "selected_option_id",
                    "uiStudioCanPreview": "can_preview",
                },
                "emptyText": "Эксперимент ещё не создан.",
            },
        },
        {
            "id": "ui-studio-actions",
            "type": "ui.actions",
            "area": "main",
            "visibleIf": visible,
            "inputs": {
                "variant": "toolbar",
                "buttons": [
                    {
                        "id": "benchmark",
                        "label": "Создать новый эталонный эксперимент",
                        "visibleIf": "$state.selectedProjectId === 'small_cities_analytics'",
                    },
                ],
            },
            "actions": [
                {
                    "id": "benchmark",
                    "on": "click:benchmark",
                    "type": "callSkill",
                    "target": "builder_sdk_control_skill.create_ui_studio_benchmark",
                    "params": {
                        "object_type": "$state.selectedProjectKind",
                        "object_id": "$state.selectedProjectId",
                        "_meta": {"current_scenario": "builder"},
                    },
                    "invalidates": ["builder.ui-studio"],
                },
            ],
        },
        {
            "id": "ui-studio-options",
            "type": "ui.table",
            "area": "main",
            "title": "Варианты",
            "visibleIf": visible,
            "dataSource": {**copy.deepcopy(source), "resultPath": "options"},
            "inputs": {
                "rowKey": "option_id",
                "selectedStateKey": "uiStudioSelectedOptionId",
                "columns": [
                    {"key": "label", "label": "Вариант"},
                    {"key": "score", "label": "Оценка"},
                    {"key": "status_label", "label": "Статус"},
                    {"key": "primary_ref", "label": "Главная область"},
                    {"key": "hard_failures", "label": "Hard failures"},
                ],
                "buttons": [
                    {
                        "id": "select-option",
                        "label": "Выбрать",
                        "whenKey": "selected",
                        "whenEquals": False,
                    },
                    {
                        "id": "preview-option",
                        "label": "Preview ↗",
                        "tooltip": "Открыть выбранный Preview в новой вкладке",
                        "icon": "open-outline",
                        "whenKey": "selected",
                        "whenEquals": True,
                    },
                ],
                "emptyText": "Создайте эксперимент, чтобы сравнить варианты.",
            },
            "actions": [
                {
                    "id": "select-option",
                    "on": "click:select-option",
                    "type": "callSkill",
                    "target": "builder_sdk_control_skill.select_ui_studio_option",
                    "params": {
                        "experiment_id": "$state.uiStudioExperimentId",
                        "option_id": "$event.option_id",
                    },
                    "invalidates": ["builder.ui-studio"],
                },
                {
                    "id": "preview-option",
                    "on": "click:preview-option",
                    "type": "callSkill",
                    "target": "builder_sdk_control_skill.preview_ui_studio",
                    "params": {
                        "experiment_id": "$state.uiStudioExperimentId",
                        "option_id": "$event.option_id",
                        "_meta": {"current_scenario": "builder"},
                    },
                    "resultStateKey": "uiStudioPreviewResult",
                    "pendingStateKey": "uiStudioPreviewPending",
                    "errorStateKey": "uiStudioPreviewError",
                    "invalidates": ["builder.ui-studio", "builder.project.preview"],
                    "followups": [
                        {
                            "type": "openWorkspace",
                            "params": {
                                "webspace_id": "$state.uiStudioPreviewResult.preview.preview_webspace_id",
                                "expected_scenario_id": "$state.uiStudioPreviewResult.preview.target.scenario_id",
                                "newWindow": True,
                            },
                        }
                    ],
                },
            ],
        },
        {
            "id": "ui-studio-prompt",
            "type": "ui.form",
            "area": "main",
            "title": "Новый intent через обычную LLM",
            "visibleIf": visible,
            "inputs": {
                "layout": "responsiveGrid",
                "minFieldWidth": 280,
                "showSubmit": True,
                "submitLabel": "Предложить и оценить варианты",
                "fields": [
                    {
                        "id": "prompt",
                        "type": "longText",
                        "label": "Цель интерфейса",
                        "required": True,
                        "defaultValue": "Собери профессиональный аналитический интерфейс: карта и реестр видны одновременно, выбор города не теряется, детали раскрываются постепенно.",
                    },
                    {
                        "id": "model",
                        "type": "shortText",
                        "label": "Обычная LLM-модель (не Codex)",
                        "required": True,
                        "defaultValue": "gpt-5",
                    },
                ],
            },
            "actions": [
                {
                    "on": "submit",
                    "type": "callSkill",
                    "target": "builder_sdk_control_skill.propose_ui_studio",
                    "params": {
                        "object_type": "$state.selectedProjectKind",
                        "object_id": "$state.selectedProjectId",
                        "prompt": "$event.values.prompt",
                        "model": "$event.values.model",
                        "reasoning_effort": "medium",
                        "_meta": {"current_scenario": "builder"},
                    },
                    "invalidates": ["builder.ui-studio"],
                }
            ],
        },
    ]


def _patch_webui(before: dict[str, Any]) -> dict[str, Any]:
    after = copy.deepcopy(before)
    page = after["ui"]["application"]["desktop"]["pageSchema"]
    views = next(
        (item for item in page["widgets"] if item.get("id") == "design-workbench-views"),
        None,
    )
    if not isinstance(views, dict):
        raise RuntimeError("Builder workbench view selector is missing")
    buttons = views.setdefault("inputs", {}).setdefault("buttons", [])
    if not any(item.get("id") == "ui-studio" for item in buttons if isinstance(item, dict)):
        buttons.insert(
            1,
            {
                "id": "ui-studio",
                "icon": "color-wand-outline",
                "label": "LLM UI Studio",
            },
        )
    generated = {item["id"]: item for item in _studio_widgets()}
    refreshed = []
    seen = set()
    for item in page["widgets"]:
        widget_id = str(item.get("id") or "") if isinstance(item, dict) else ""
        if widget_id in generated:
            refreshed.append(copy.deepcopy(generated[widget_id]))
            seen.add(widget_id)
        else:
            refreshed.append(item)
    refreshed.extend(copy.deepcopy(item) for key, item in generated.items() if key not in seen)
    page["widgets"] = refreshed
    page.setdefault("initialState", {}).update(
        {
            "uiStudioExperimentId": "",
            "uiStudioSelectedOptionId": "",
            "uiStudioCanPreview": False,
            "uiStudioPreviewResult": {},
            "uiStudioPreviewPending": False,
            "uiStudioPreviewError": {},
        }
    )
    builder_meta = page.setdefault("meta", {}).setdefault("builder", {})
    builder_meta.update(
        {
            "ui_revision": REVISION,
            "previous_revision": str(builder_meta.get("ui_revision") or "002"),
            "ui_studio": {
                "status": "experimental",
                "profile_ref": "geo_explorer.v1",
                "benchmark": "small-cities.v1",
                "model_boundary": "typed_intent_only",
            },
        }
    )
    after["ui"]["version"] = VERSION
    summary = after["ui"].setdefault("user_summary", {})
    behavior = summary.setdefault("expected_behavior", [])
    statement = (
        "LLM UI Studio asks an ordinary LLM for bounded typed intent; Core owns "
        "variant compilation, evaluation, EXP staging, and Preview."
    )
    if statement not in behavior:
        behavior.append(statement)
    # The long-lived Builder package still carries historical header-button
    # extensions that predate the current strict button union.  Validate every
    # newly introduced Studio widget against today's contract without claiming
    # that this bounded revision migrated unrelated legacy widgets.
    schema = _read(ROOT / "src" / "adaos" / "abi" / "webui.v1.schema.json")
    widget_validator = Draft202012Validator(
        {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": "#/$defs/widgetConfig",
        }
    )
    for widget in _studio_widgets():
        errors = sorted(
            widget_validator.iter_errors(widget),
            key=lambda item: list(item.absolute_path),
        )
        if errors:
            error = errors[0]
            location = ".".join(str(item) for item in error.absolute_path) or "$"
            raise RuntimeError(
                f"Generated UI Studio widget {widget['id']} invalid at {location}: {error.message}"
            )
    return after


def build() -> None:
    required = [WEBUI, SCENARIO_JSON, SCENARIO_YAML, HANDLER, SKILL_YAML, CURRENT]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("Builder DEV package is incomplete: " + ", ".join(missing))
    before = _read(WEBUI)
    prior_revision = CURRENT.read_text(encoding="utf-8-sig").strip()
    if prior_revision not in {"002", "003", "004", "005", REVISION}:
        raise RuntimeError(f"Unexpected active Builder revision: {prior_revision}")
    _patch_handler()
    _patch_skill_manifest()
    after = _patch_webui(before)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    scenario_manifest = yaml.safe_load(SCENARIO_YAML.read_text(encoding="utf-8-sig")) or {}
    scenario_manifest["version"] = VERSION
    scenario_manifest["updated_at"] = now
    SCENARIO_YAML.write_text(
        yaml.safe_dump(scenario_manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    scenario = _read(SCENARIO_JSON)
    scenario["ui"] = copy.deepcopy(after["ui"])
    scenario["version"] = VERSION
    scenario["updated_at"] = now

    _write(WEBUI, after)
    _write(SCENARIO_JSON, scenario)
    revision = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": REVISION,
        "created_at": now,
        "scenario_id": "builder",
        "request": {
            "text": "Add an experimental LLM UI Studio surface with typed intent, variants, evaluation and exact Preview."
        },
        "patch": {
            "id": MARKER,
            "target": "builder.workbench",
            "operation": "add_typed_llm_ui_studio",
            "profile_ref": "geo_explorer.v1",
            "benchmark": "small-cities.v1",
            "status": "applied",
        },
        "before_webui": before,
        "after_webui": after,
        "preview_state": {},
    }
    _write(SCENARIO / "ui_revisions" / f"{REVISION}.json", revision)
    CURRENT.write_text(REVISION + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "revision": REVISION,
                "version": VERSION,
                "skill_version": SKILL_VERSION,
                "widgets": len(after["ui"]["application"]["desktop"]["pageSchema"]["widgets"]),
                "tools": len((yaml.safe_load(SKILL_YAML.read_text(encoding="utf-8")) or {}).get("tools") or []),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    build()
