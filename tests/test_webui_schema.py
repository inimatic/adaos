from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError


def _load_schema() -> dict:
    path = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi" / "webui.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_webui_schema_accepts_grouped_filterable_image_cards() -> None:
    schema = _load_schema()
    payload = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "catalog",
                        "layout": {"type": "stack", "areas": [{"id": "main", "role": "main"}]},
                        "widgets": [
                            {
                                "id": "cards",
                                "type": "ui.list",
                                "area": "main",
                                "inputs": {
                                    "variant": "cards",
                                    "titleKey": "title",
                                    "imageKey": "media.src",
                                    "imageAltKey": "media.alt",
                                    "groupBy": "category",
                                    "groupDisplay": "accordion",
                                    "cardMinWidth": 220,
                                    "cardImageRatio": "4 / 3",
                                    "meta": [
                                        {"key": "duration", "label": "Time", "kind": "badge"},
                                        {"key": "favorite", "kind": "boolean", "trueLabel": "Favorite"},
                                    ],
                                    "filters": [
                                        {"key": "category", "stateKey": "categoryFilter", "operator": "equals"},
                                        {"key": "duration", "stateKey": "maxDuration", "operator": "lte"},
                                    ],
                                    "buttons": [{"id": "favorite", "label": "Favorite", "icon": "heart-outline"}],
                                },
                            }
                        ],
                    }
                }
            }
        },
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_safe_state_mutations_and_membership_filter() -> None:
    schema = _load_schema()
    payload = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "interactive-catalog",
                        "layout": {"type": "single", "areas": [{"id": "main"}]},
                        "widgets": [
                            {
                                "id": "catalog",
                                "type": "ui.list",
                                "area": "main",
                                "inputs": {
                                    "filters": [{"key": "id", "stateKey": "favorites", "operator": "in"}],
                                },
                                "actions": [
                                    {
                                        "on": "click:favorite",
                                        "type": "mutateState",
                                        "params": {
                                            "operations": [
                                                {"op": "toggleArrayItem", "path": "favorites", "value": "$event.id"},
                                                {"op": "increment", "path": "cart.$event.id", "amount": 1, "min": 0},
                                            ]
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                }
            }
        },
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_requires_interval_for_auto_actions() -> None:
    schema = _load_schema()
    payload = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "bad-auto-action",
                        "layout": {"type": "single", "areas": [{"id": "main"}]},
                        "widgets": [{"id": "content", "type": "item.details", "area": "main"}],
                        "autoActions": [{"id": "tick", "action": {"type": "updateState", "params": {"tick": True}}}],
                    }
                }
            }
        },
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_responsive_form_layout() -> None:
    schema = _load_schema()
    payload = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "responsive-form",
                        "layout": {"type": "single", "areas": [{"id": "main", "role": "main"}]},
                        "widgets": [
                            {
                                "id": "filters",
                                "type": "ui.form",
                                "area": "main",
                                "inputs": {
                                    "layout": "responsiveGrid",
                                    "minFieldWidth": 180,
                                    "fields": [
                                        {"id": "search", "type": "shortText", "label": "Search"},
                                        {"id": "notes", "type": "longText", "label": "Notes", "span": "full"},
                                    ],
                                },
                            }
                        ],
                    }
                }
            }
        },
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_deterministic_tabs_modal_page_and_multistep_behaviors() -> None:
    schema = _load_schema()
    payload = {
        "schema": "adaos.webui.v1",
        "behaviors": [
            {
                "id": "recipe-editor",
                "kind": "multistep",
                "statePath": "recipeEditor.step",
                "initial": "details",
                "states": [
                    {"id": "details", "label_i18n": {"key": "recipes.step.details"}, "view": "recipe-details"},
                    {"id": "ingredients", "label": "Ingredients", "view": "recipe-ingredients"},
                    {"id": "review", "label": "Review", "view": "recipe-review", "terminal": True},
                ],
                "transitions": [
                    {"on": "next", "from": "details", "to": "ingredients", "effect": "local_state"},
                    {"on": "next", "from": "ingredients", "to": "review", "guard": "recipe.valid", "effect": "local_state"},
                    {
                        "on": "submit",
                        "from": "review",
                        "to": "review",
                        "effect": "runtime_action",
                        "action": {"type": "callHost", "target": "recipes.save"},
                    },
                ],
            },
            {
                "id": "recipe-tabs",
                "kind": "tabs",
                "initial": "all",
                "states": [{"id": "all"}, {"id": "favorites"}],
                "transitions": [{"on": "favorite", "from": "all", "to": "favorites", "effect": "none"}],
            },
            {
                "id": "recipe-modal",
                "kind": "modal",
                "initial": "closed",
                "states": [{"id": "closed"}, {"id": "open"}],
                "transitions": [{"on": "open", "from": "closed", "to": "open", "effect": "local_state"}],
            },
        ],
    }
    Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_responsive_split_widths_and_multiline_chat() -> None:
    schema = _load_schema()
    payload = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "builder",
                        "layout": {
                            "type": "split",
                            "sidebarWidth": 320,
                            "auxWidth": 360,
                            "areas": [
                                {"id": "left", "role": "nav", "width": 320},
                                {"id": "main", "role": "main"},
                                {"id": "right", "role": "aux"},
                            ],
                        },
                        "widgets": [
                            {
                                "id": "builder-chat",
                                "type": "ui.chat",
                                "area": "main",
                                "inputs": {
                                    "multiline": True,
                                    "composerRows": 4,
                                    "composerAutoGrow": True,
                                    "sendOnEnter": True,
                                    "sendOnCtrlEnter": True,
                                    "sendCommand": "voice.chat.user",
                                },
                            }
                        ],
                    }
                }
            }
        },
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_semantic_danger_action_button() -> None:
    schema = _load_schema()
    payload = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "project-overview",
                        "layout": {"type": "single", "areas": [{"id": "main", "role": "main"}]},
                        "widgets": [
                            {
                                "id": "project-actions",
                                "type": "ui.actions",
                                "area": "main",
                                "inputs": {
                                    "variant": "stack",
                                    "buttons": [
                                        {
                                            "id": "archive",
                                            "label": "Archive",
                                            "icon": "archive-outline",
                                            "kind": "danger",
                                            "fill": "solid",
                                        }
                                    ],
                                },
                            }
                        ],
                    }
                }
            }
        },
    }

    Draft202012Validator(schema).validate(payload)

    payload["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]["inputs"]["buttons"][0][
        "kind"
    ] = "red"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)

    payload["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]["inputs"]["buttons"][0][
        "kind"
    ] = "danger"
    payload["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]["inputs"]["buttons"][0][
        "appearance"
    ] = "danger"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


def test_webui_schema_rejects_unbounded_split_widths() -> None:
    schema = _load_schema()
    payload = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "builder",
                        "layout": {
                            "type": "split",
                            "sidebarWidth": 900,
                            "areas": [{"id": "main", "role": "main"}],
                        },
                        "widgets": [],
                    }
                }
            }
        },
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


def test_webui_schema_rejects_dotted_widget_properties() -> None:
    schema = _load_schema()
    payload = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "invalid-form",
                        "layout": {"type": "single", "areas": [{"id": "main", "role": "main"}]},
                        "widgets": [
                            {
                                "id": "form",
                                "type": "ui.form",
                                "area": "main",
                                "inputs": {"fields": [{"id": "title", "type": "shortText"}]},
                                "inputs.secondaryActions": [{"id": "cancel", "label": "Cancel"}],
                            }
                        ],
                    }
                }
            }
        },
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


def test_webui_schema_validates_widgets_inside_application_modals() -> None:
    schema = _load_schema()
    payload = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "catalog",
                        "layout": {"type": "single", "areas": [{"id": "main"}]},
                        "widgets": [{"id": "catalog", "type": "ui.list", "area": "main"}],
                    }
                },
                "modals": {
                    "edit_modal": {
                        "schema": {
                            "id": "edit_modal",
                            "layout": {"type": "stack", "areas": [{"id": "modal"}]},
                            "widgets": [
                                {
                                    "id": "edit_form",
                                    "type": "ui.form",
                                    "area": "modal",
                                    "inputs": {"fields": [{"id": "title", "type": "shortText"}]},
                                    "inputs.secondaryActions": [{"id": "cancel", "label": "Cancel"}],
                                }
                            ],
                        }
                    }
                },
            }
        },
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_details_image_mapping() -> None:
    schema = _load_schema()
    payload = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "catalog-detail",
                        "layout": {
                            "type": "split",
                            "pattern": "focus-detail",
                            "areas": [
                                {"id": "main", "role": "main"},
                                {"id": "details", "role": "aux"},
                            ],
                        },
                        "widgets": [
                            {
                                "id": "detail",
                                "type": "item.details",
                                "area": "details",
                                "inputs": {
                                    "selectedStateKey": "selectedId",
                                    "imageKey": "media.url",
                                    "imageAltKey": "title",
                                    "imageRatio": "4 / 3",
                                },
                            }
                        ],
                    }
                }
            }
        },
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_staged_load_hints() -> None:
    schema = _load_schema()
    payload = {
        "apps": [
            {
                "id": "prompt_ide",
                "title": "Prompt IDE",
                "load": {"structure": "visible", "data": "interaction", "focus": "primary"},
            }
        ],
        "widgets": [
            {
                "id": "chat_widget",
                "type": "ui.chat",
                "load": {
                    "structure": "visible",
                    "data": "deferred",
                    "focus": "off_focus",
                    "offFocusReadyState": "hydrating",
                },
            }
        ],
        "registry": {
            "modals": {
                "prompt_modal": {
                    "title": "Prompt",
                    "load": {
                        "structure": "interaction",
                        "data": "deferred",
                        "focus": "off_focus",
                        "offFocusReadyState": "hydrating",
                    },
                    "schema": {
                        "id": "prompt_modal",
                        "load": {"structure": "interaction", "data": "deferred", "focus": "off_focus"},
                        "layout": {"type": "single", "pattern": "stack", "areas": [{"id": "main"}]},
                        "widgets": [
                            {
                                "id": "prompt_widget",
                                "type": "ui.chat",
                                "area": "main",
                                "load": {"structure": "visible", "data": "deferred", "focus": "off_focus"},
                            }
                        ],
                    },
                }
            }
        },
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_stream_receivers_and_stream_data_sources() -> None:
    schema = _load_schema()
    payload = {
        "webio": {
            "receivers": {
                "telemetry_feed": {
                    "mode": "append",
                    "collectionKey": "items",
                    "dedupeBy": "id",
                    "maxItems": 120,
                    "initialState": {"items": []},
                    "transport": "hub",
                    "snapshotPolicy": "on_subscribe",
                    "sequenceField": "seq",
                    "updatedAtField": "updated_at",
                    "budget": {
                        "maxPayloadBytes": 8192,
                        "maxPublishHz": 2,
                        "coalesceMs": 250,
                        "maxFanout": 8,
                        "maxSnapshotHz": 0.2,
                    },
                    "guardVisibility": {
                        "degradedState": "Telemetry stream paused",
                        "log": "service.telemetry_skill.runtime.log",
                        "quarantine": True,
                        "metric": "webio.stream.telemetry_feed.suppressed",
                    },
                    "route": {
                        "kind": "stream",
                        "surface": "widget:telemetry",
                        "owner": "telemetry_skill",
                        "firstPaint": "empty telemetry list",
                        "recovery": "request bounded snapshot on subscribe",
                        "updateSource": ["telemetry.sampled"],
                    },
                }
            }
        },
        "widgets": [
            {
                "id": "telemetry_widget",
                "type": "ui.jsonViewer",
                "area": "main",
                "dataSource": {
                    "kind": "stream",
                    "receiver": "telemetry_feed",
                    "scope": "shared",
                },
            }
        ],
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_runtime_data_sources_and_auto_actions() -> None:
    schema = _load_schema()
    payload = {
        "apps": [
            {
                "id": "runtime_app",
                "title": "Runtime",
                "subtitle": "Runtime-backed UI",
                "icon": "pulse-outline",
                "launchModal": "runtime_modal",
                "action": {"openModal": "runtime_modal"},
            }
        ],
        "registry": {
            "modals": {
                "runtime_modal": {
                    "schema": {
                        "id": "runtime_modal",
                        "initialState": {"poll": "on"},
                        "autoActions": [
                            {
                                "id": "runtime_tick",
                                "intervalMs": 2500,
                                "enabledIf": "$state.poll === 'on'",
                                "action": {
                                    "on": "interval",
                                    "type": "callSkill",
                                    "target": "runtime_skill.refresh",
                                    "params": {"reason": "auto"},
                                },
                            }
                        ],
                        "layout": {
                            "type": "single",
                            "pattern": "stack",
                            "areas": [{"id": "main", "label": "Main"}],
                        },
                        "widgets": [
                            {
                                "id": "skill_data",
                                "type": "ui.jsonViewer",
                                "area": "main",
                                "dataSource": {"kind": "skill", "name": "runtime_skill.snapshot"},
                            },
                            {
                                "id": "api_data",
                                "type": "ui.jsonViewer",
                                "area": "main",
                                "dataSource": {"kind": "api", "url": "/api/node/status", "method": "GET"},
                            },
                            {
                                "id": "static_data",
                                "type": "ui.jsonViewer",
                                "area": "main",
                                "dataSource": {"kind": "static", "value": {"ok": True}},
                            },
                        ],
                    }
                }
            }
        },
        "contributions": [
            {
                "extensionPoint": "desktop.apps",
                "type": "app",
                "id": "runtime_app",
                "title": "Runtime",
                "subtitle": "Runtime-backed UI",
                "icon": "pulse-outline",
                "launchModal": "runtime_modal",
                "action": {"openModal": "runtime_modal"},
                "autoInstall": True,
            }
        ],
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_modal_domain_and_ownership_contract() -> None:
    schema = _load_schema()
    payload = {
        "interface": {
            "schema": "adaos.ui.skill_interface.v1",
            "defaultView": "demo.notes.list",
            "views": {
                "demo.notes.list": {"surfaces": ["modal"], "params": {}},
                "demo.note.edit": {
                    "surfaces": ["modal"],
                    "params": {"note_id": {"type": "string", "required": True}},
                },
            },
        },
        "registry": {
            "modals": {
                "demo_modal": {
                    "implements": ["demo.notes.list", "demo.note.edit"],
                    "schema": {
                        "id": "demo_modal",
                        "layout": {"type": "single", "areas": [{"id": "main", "role": "main"}]},
                        "interface": {
                            "schema": "adaos.ui.modal.interface.v1",
                            "defaultRoute": "notes.list",
                            "history": {"url": True, "mode": "push"},
                            "domain": {
                                "schema": "adaos.ui.modal_domain.v1",
                                "defaultState": "notes.list",
                                "stateKey": "demoRoute",
                                "states": {
                                    "notes.list": {
                                        "kind": "collection",
                                        "route": "notes.list",
                                        "view": "demo.notes.list",
                                    },
                                    "note.edit": {
                                        "kind": "entity",
                                        "route": "note.edit",
                                        "view": "demo.note.edit",
                                        "entity": {"type": "note", "idParam": "note_id"},
                                    },
                                },
                            },
                            "ownership": {
                                "schema": "adaos.ui.state_ownership.v1",
                                "domainState": {"owner": "skill:demo_skill", "store": "skill_memory"},
                                "routeState": {"owner": "browser", "scope": "modal", "keys": ["selectedId"]},
                                "viewState": {"owner": "browser", "scope": "modal"},
                                "persistence": {"owner": "skill:demo_skill", "ack": "tool:demo_skill.save_note"},
                            },
                            "routes": {
                                "notes.list": {"view": "demo.notes.list", "params": {}},
                                "note.edit": {
                                    "view": "demo.note.edit",
                                    "params": {"note_id": {"type": "string", "required": True}},
                                },
                            },
                        },
                        "widgets": [],
                    },
                }
            }
        },
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_interaction_resources_and_action_feedback() -> None:
    schema = _load_schema()
    payload = {
        "resources": {
            "weather.current": {
                "kind": "svg",
                "path": "assets/icons/current.svg",
                "mime": "image/svg+xml",
                "cacheKey": "sha256:abc123",
                "delivery": "core",
            },
            "weather.preview": {
                "kind": "image",
                "path": "assets/preview.webp",
                "mime": "image/webp",
                "alt": "Weather preview",
            },
        },
        "apps": [
            {
                "id": "weather_app",
                "title": "Weather",
                "icon": "resource:weather.current",
                "launchModal": "weather_modal",
            }
        ],
        "registry": {
            "modals": {
                "weather_modal": {
                    "title": "Weather",
                    "loading": {
                        "statePath": "data/weather/current",
                        "loadingText": "Loading weather...",
                        "skeleton": "card",
                        "timeoutMs": 9000,
                    },
                    "schema": {
                        "id": "weather_modal",
                        "layout": {"type": "single", "pattern": "stack", "areas": [{"id": "main"}]},
                        "interaction": {
                            "initialFocus": {"ref": "widget:weather-city-input", "strategy": "restore_or_first"},
                            "submit": {
                                "defaultAction": "weather.search",
                                "enterKey": "submit",
                                "scope": "focused_form",
                            },
                        },
                        "widgets": [
                            {
                                "id": "weather-preview",
                                "type": "visual.image",
                                "area": "main",
                                "dataSource": {"kind": "resource", "resource": "weather.preview"},
                                "loading": {"skeleton": "card", "emptyText": "No preview yet"},
                            },
                            {
                                "id": "weather-city-input",
                                "type": "input.text",
                                "area": "main",
                                "inputs": {
                                    "bindField": "city",
                                    "commitMode": "manual",
                                    "saveLabel": "Search",
                                },
                                "interaction": {"defaultAction": "weather.search"},
                                "actions": [
                                    {
                                        "id": "weather.search",
                                        "on": "change",
                                        "type": "callHost",
                                        "target": "skill.event.publish",
                                        "params": {
                                            "event_type": "weather.location.requested",
                                            "payload": {
                                                "city": "$event.value",
                                                "request_id": "$client.requestId",
                                            },
                                        },
                                        "feedback": {
                                            "pending": {
                                                "disable": True,
                                                "label": "Searching...",
                                                "icon": "sync-outline",
                                            },
                                            "observe": {
                                                "kind": "y",
                                                "path": "data/weather/current",
                                                "scope": "node",
                                                "timeoutMs": 9000,
                                                "match": {
                                                    "request_id": "$client.requestId",
                                                    "pending": False,
                                                },
                                                "advanceFields": ["request_id", "updated_at", "pending"],
                                            },
                                            "timeout": {"state": "degraded", "message": "Weather update timed out"},
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                }
            }
        },
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_validates_open_url_actions() -> None:
    schema = _load_schema()
    payload = {
        "widgets": [
            {
                "id": "attachments",
                "type": "ui.list",
                "actions": [
                    {
                        "on": "click:open",
                        "type": "openUrl",
                        "params": {
                            "url": "$event.url",
                            "target": "_blank",
                            "download": True,
                        },
                    }
                ],
            }
        ],
    }

    Draft202012Validator(schema).validate(payload)

    broken = {
        "widgets": [
            {
                "id": "attachments",
                "type": "ui.list",
                "actions": [
                    {
                        "on": "click:open",
                        "type": "openUrl",
                        "params": {"target": "_blank"},
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(broken)


def test_webui_schema_accepts_google_forms_like_form_fields() -> None:
    schema = _load_schema()
    payload = {
        "registry": {
            "modals": {
                "survey_modal": {
                    "schema": {
                        "id": "survey_modal",
                        "layout": {"type": "single", "areas": [{"id": "main"}]},
                        "widgets": [
                            {
                                "id": "survey",
                                "type": "ui.form",
                                "area": "main",
                                "inputs": {
                                    "submitLabel": "Send",
                                    "fields": [
                                        {"id": "intro", "type": "section", "title": "Feedback"},
                                        {
                                            "id": "name",
                                            "type": "shortText",
                                            "label": "Name",
                                            "required": True,
                                            "validation": {"minLength": 2},
                                        },
                                        {"id": "comment", "type": "paragraph", "label": "Comment"},
                                        {
                                            "id": "segment",
                                            "type": "multipleChoice",
                                            "label": "Segment",
                                            "options": [
                                                {"label": "Builder", "value": "builder", "gotoSection": "builder_details"},
                                                {"label": "Operator", "value": "operator"},
                                            ],
                                        },
                                        {
                                            "id": "features",
                                            "type": "checkboxes",
                                            "label": "Features",
                                            "options": ["Forms", "Charts", "Tables"],
                                        },
                                        {"id": "priority", "type": "dropdown", "options": ["Low", "Medium", "High"]},
                                        {"id": "score", "type": "linearScale", "min": 1, "max": 5},
                                        {"id": "rating", "type": "rating", "ratingMax": 5},
                                        {
                                            "id": "matrix_single",
                                            "type": "singleChoiceGrid",
                                            "rows": ["UX", "Runtime"],
                                            "columns": ["Bad", "OK", "Good"],
                                        },
                                        {
                                            "id": "matrix_multi",
                                            "type": "checkboxGrid",
                                            "rows": ["Prompt IDE", "Builder"],
                                            "columns": ["Fast", "Useful"],
                                        },
                                        {"id": "day", "type": "date"},
                                        {"id": "time", "type": "time"},
                                        {"id": "window", "type": "dateRange"},
                                        {"id": "time_window", "type": "time_range"},
                                        {"id": "attachment", "type": "fileUpload", "accept": ".json", "maxFiles": 2},
                                        {"id": "note", "type": "staticContent", "markdown": "Thanks."},
                                    ],
                                },
                                "actions": [
                                    {"on": "submit", "type": "callHost", "target": "survey.submit"},
                                    {"on": "save_draft", "type": "callHost", "target": "survey.save_draft"},
                                ],
                            }
                        ],
                    }
                }
            }
        }
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_rejects_unknown_form_field_type() -> None:
    schema = _load_schema()
    payload = {
        "widgets": [
            {
                "id": "broken_form",
                "type": "ui.form",
                "inputs": {
                    "fields": [
                        {"id": "unknown", "type": "planetScale"},
                    ]
                },
            }
        ]
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_frame_viewer_media_surface_contract() -> None:
    schema = _load_schema()
    payload = {
        "widgets": [
            {
                "id": "slideshow_widget",
                "title": "ReDevice slideshow",
                "type": "visual.frameViewer",
                "dataSource": {
                    "kind": "stream",
                    "receiver": "slideshow.session",
                    "nodeId": "$state.nodeId",
                },
                "inputs": {
                    "imageField": "image.src",
                    "fullscreenMediaField": "image.fullscreen_media",
                    "prefetchMediaField": "image.next_media",
                    "aspectRatio": "16 / 9",
                    "fullscreenOnClick": True,
                    "nativeFullscreen": True,
                    "retainLastImageOnEmpty": True,
                    "emptyText": "Start slideshow to show the current photo.",
                    "headerActions": [
                        {"id": "play", "label": "Play", "icon": "play-outline"},
                        {
                            "id": "fav",
                            "label": "Favorite",
                            "icon": "star-outline",
                            "labelField": "favorite_label",
                            "iconField": "favorite_icon",
                            "idField": "favorite_action",
                        },
                    ],
                    "fullscreenActions": [
                        {
                            "id": "close",
                            "label": "Close",
                            "icon": "close-outline",
                            "local": "closeFullscreen",
                        }
                    ],
                    "keyboardActions": {"ArrowLeft": "next", "ArrowRight": "prev", "ArrowUp": "fav"},
                    "swipeActions": {"left": "next", "right": "prev", "up": "fav", "down": "hide"},
                    "metrics": [{"label": "Frame", "path": "frame.label"}],
                },
                "actions": [
                    {
                        "on": "click:play",
                        "type": "callSkill",
                        "target": "slideshow.control",
                        "params": {"action": "start"},
                    }
                ],
            }
        ],
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_rejects_invalid_stream_route_metadata() -> None:
    schema = _load_schema()
    payload = {
        "webio": {
            "receivers": {
                "telemetry_feed": {
                    "mode": "replace",
                    "route": {
                        "kind": "yjs",
                        "surface": "widget:telemetry",
                    },
                }
            }
        }
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


def test_webui_schema_rejects_invalid_stream_budget() -> None:
    schema = _load_schema()
    payload = {
        "webio": {
            "receivers": {
                "telemetry_feed": {
                    "mode": "replace",
                    "budget": {
                        "maxPayloadBytes": 0,
                    },
                }
            }
        }
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


def test_webui_schema_rejects_scheduler_specific_load_details() -> None:
    schema = _load_schema()
    payload = {
        "widgets": [
            {
                "id": "chat_widget",
                "type": "ui.chat",
                "load": {"structure": "visible", "scheduler": "critical_path"},
            }
        ]
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


def test_webui_schema_rejects_stream_receiver_without_mode() -> None:
    schema = _load_schema()
    payload = {
        "webio": {
            "receivers": {
                "telemetry_feed": {
                    "collectionKey": "items",
                }
            }
        }
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_literal_collection_filter() -> None:
    schema = _load_schema()
    payload = {
        "widgets": [
            {
                "id": "cart",
                "type": "ui.list",
                "inputs": {
                    "filters": [
                        {"key": "quantity", "operator": "gt", "value": 0},
                    ]
                },
            }
        ]
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_state_selected_list_sort_and_conditional_action() -> None:
    schema = _load_schema()
    payload = {
        "widgets": [
            {
                "id": "catalog",
                "type": "ui.list",
                "inputs": {
                    "sort": {
                        "stateKey": "sort_by",
                        "options": {
                            "price_asc": {"key": "price", "direction": "asc", "numeric": True},
                            "price_desc": {"key": "price", "direction": "desc", "numeric": True},
                        },
                    }
                },
                "actions": [
                    {
                        "on": "select",
                        "enabledIf": "$state.detailMode === 'modal'",
                        "type": "openModal",
                        "params": {"modalId": "product_details"},
                    }
                ],
            }
        ]
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_accepts_state_selected_full_surface_layout_variants() -> None:
    schema = _load_schema()
    payload = {
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "research_workbench",
                        "initialState": {"viewMode": "portfolio"},
                        "layout": {
                            "type": "single",
                            "areas": [{"id": "portfolio", "role": "main"}],
                            "variants": [
                                {
                                    "id": "direction",
                                    "when": "$state.viewMode === 'direction'",
                                    "type": "split",
                                    "pattern": "focus-detail",
                                    "auxWidth": 460,
                                    "areas": [
                                        {"id": "workspace", "role": "main"},
                                        {"id": "context", "role": "aux"},
                                    ],
                                },
                                {
                                    "id": "portfolio",
                                    "default": True,
                                    "type": "single",
                                    "pattern": "stack",
                                    "areas": [{"id": "portfolio", "role": "main"}],
                                },
                            ],
                        },
                        "widgets": [
                            {"id": "directions", "type": "ui.list", "area": "portfolio"},
                            {"id": "discussion", "type": "ui.chat", "area": "workspace"},
                            {"id": "consensus", "type": "static.markdown", "area": "context"},
                        ],
                    }
                }
            }
        }
    }

    Draft202012Validator(schema).validate(payload)


def test_webui_schema_rejects_ambiguous_layout_variant_without_when_or_default() -> None:
    schema = _load_schema()
    payload = {
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "invalid",
                        "layout": {
                            "type": "single",
                            "areas": [{"id": "main"}],
                            "variants": [
                                {"id": "unknown", "type": "single", "areas": [{"id": "main"}]}
                            ],
                        },
                        "widgets": [],
                    }
                }
            }
        }
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)
