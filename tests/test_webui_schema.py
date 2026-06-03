from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError


def _load_schema() -> dict:
    path = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi" / "webui.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


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
