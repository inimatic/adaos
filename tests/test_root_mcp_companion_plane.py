from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from adaos.services.root_mcp import companion_plane


class _Bus:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


class _Paths:
    def __init__(self, root: Path) -> None:
        self.root = root

    def root_mcp_state_dir(self) -> Path:
        return self.root


def _surface() -> dict:
    return {
        "surface_id": "adaos.nlu.contextual_action_surface.v1",
        "fingerprint": "surface-1",
        "runtime_state": {
            "current_scenario": "web_desktop",
            "available_modal_ids": ["settings_modal"],
            "catalog_apps": [{"id": "scenario:web_desktop", "scenario_id": "web_desktop"}],
            "catalog_widgets": [{"id": "cpu"}],
            "nodes": [{"id": "node-1", "status": "online"}],
        },
        "voice_capabilities": [{"id": "status.cpu"}],
        "voice_affordances": [
            {
                "id": "settings.open",
                "activation": [
                    {"type": "openModal", "params": {"modalId": "settings_modal"}}
                ],
                "availability": {"status": "reachable"},
            }
        ],
    }


def _ctx(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(paths=_Paths(tmp_path), bus=_Bus())


def test_context_frame_is_stable_and_bounded(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(companion_plane, "get_ctx", lambda: _ctx(tmp_path))
    monkeypatch.setattr(companion_plane, "_action_surface", lambda *args, **kwargs: _surface())

    first = companion_plane.build_context_frame(webspace_id="desktop")
    second = companion_plane.build_context_frame(webspace_id="desktop")

    assert first["schema"] == companion_plane.CONTEXT_SCHEMA
    assert first["current_scenario"] == "web_desktop"
    assert first["context_digest"] == second["context_digest"]
    assert first["boundaries"]["raw_dom_access"] is False
    assert {item["id"] for item in first["affordances"]} >= {
        "ui.modal.open",
        "ui.modal.close",
        "status.codex_tokens.read",
        "status.node_cpu.read",
        "media.catalog.search",
    }


def test_context_frame_deduplicates_and_projects_runtime_inventory(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(companion_plane, "get_ctx", lambda: _ctx(tmp_path))
    surface = _surface()
    surface["runtime_state"]["available_modal_ids"] = ["settings_modal"] * 200
    surface["runtime_state"]["catalog_apps"] = [
        {
            "id": "scenario:web_desktop",
            "scenario_id": "web_desktop",
            "title": "Settings",
            "private_payload": "x" * 20_000,
        },
        {"id": "scenario:web_desktop", "title": "Duplicate"},
    ]
    surface["voice_affordances"] = [
        {
            **surface["voice_affordances"][0],
            "source_path": "must-not-leak",
            "private_payload": "x" * 20_000,
        }
    ]
    monkeypatch.setattr(companion_plane, "_action_surface", lambda *args, **kwargs: surface)

    frame = companion_plane.build_context_frame(webspace_id="desktop")
    encoded = json.dumps(frame, ensure_ascii=False)

    assert frame["available_modal_ids"] == ["settings_modal"]
    assert frame["catalog_apps"] == [
        {"id": "scenario:web_desktop", "scenario_id": "web_desktop", "title": "Settings"}
    ]
    assert frame["inventory"]["available_modal_ids"]["source_count"] == 200
    assert frame["inventory"]["available_modal_ids"]["unique_count"] == 1
    assert frame["inventory"]["available_modal_ids"]["projected_count"] == 1
    assert frame["inventory"]["available_modal_ids"]["truncated"] is False
    assert "must-not-leak" not in encoded
    assert len(encoded.encode("utf-8")) < 16_000


def test_context_digest_ignores_source_order_and_volatile_surface_fingerprint(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(companion_plane, "get_ctx", lambda: _ctx(tmp_path))
    calls = 0

    def surface(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        value = _surface()
        value["fingerprint"] = f"volatile-{calls}"
        value["runtime_state"]["available_modal_ids"] = (
            ["z-modal", "a-modal"] if calls == 1 else ["a-modal", "z-modal"]
        )
        return value

    monkeypatch.setattr(companion_plane, "_action_surface", surface)

    first = companion_plane.build_context_frame(webspace_id="desktop")
    second = companion_plane.build_context_frame(webspace_id="desktop")

    assert first["available_modal_ids"] == ["a-modal", "z-modal"]
    assert first["action_surface_fingerprint"] != second["action_surface_fingerprint"]
    assert first["context_digest"] == second["context_digest"]


def test_stale_context_rejects_ui_mutation(monkeypatch, tmp_path) -> None:
    context = _ctx(tmp_path)
    monkeypatch.setattr(companion_plane, "get_ctx", lambda: context)
    monkeypatch.setattr(companion_plane, "_action_surface", lambda *args, **kwargs: _surface())
    frame = companion_plane.build_context_frame(webspace_id="desktop")

    receipt = companion_plane.execute_action_request(
        {
            "request_id": "request-1",
            "operation": "ui.modal.open",
            "params": {"modal_id": "settings_modal"},
            "webspace_id": "desktop",
            "context_digest": "sha256:" + "0" * 64,
        },
        frame=frame,
    )

    assert receipt["status"] == "rejected"
    assert receipt["error"]["code"] == "context_stale"
    assert context.bus.events == []


def test_unknown_scenario_and_widget_are_rejected_from_current_context(monkeypatch, tmp_path) -> None:
    context = _ctx(tmp_path)
    monkeypatch.setattr(companion_plane, "get_ctx", lambda: context)
    monkeypatch.setattr(companion_plane, "_action_surface", lambda *args, **kwargs: _surface())
    frame = companion_plane.build_context_frame(webspace_id="desktop")

    scenario = companion_plane.execute_action_request(
        {
            "operation": "ui.scenario.open",
            "params": {"scenario_id": "missing"},
            "webspace_id": "desktop",
            "context_digest": frame["context_digest"],
        },
        frame=frame,
    )
    widget = companion_plane.execute_action_request(
        {
            "operation": "ui.widget.focus",
            "params": {"widget_id": "missing"},
            "webspace_id": "desktop",
            "context_digest": frame["context_digest"],
        },
        frame=frame,
    )

    assert scenario["error"]["code"] == "scenario_not_in_current_context"
    assert widget["error"]["code"] == "widget_not_in_current_context"
    assert context.bus.events == []


def test_modal_open_dispatches_semantic_host_event(monkeypatch, tmp_path) -> None:
    context = _ctx(tmp_path)
    monkeypatch.setattr(companion_plane, "get_ctx", lambda: context)
    monkeypatch.setattr(companion_plane, "_action_surface", lambda *args, **kwargs: _surface())
    frame = companion_plane.build_context_frame(webspace_id="desktop")

    receipt = companion_plane.execute_action_request(
        {
            "request_id": "request-2",
            "operation": "ui.modal.open",
            "params": {"modal_id": "settings_modal"},
            "webspace_id": "desktop",
            "context_digest": frame["context_digest"],
        },
        frame=frame,
    )

    assert receipt["status"] == "dispatched"
    assert context.bus.events[0].type == "desktop.modal.open"
    assert context.bus.events[0].payload["modal_id"] == "settings_modal"
    assert companion_plane._recent_receipts(webspace_id="desktop", limit=5)[0]["action_id"] == receipt["action_id"]


def test_affordance_activation_uses_published_plan(monkeypatch, tmp_path) -> None:
    context = _ctx(tmp_path)
    monkeypatch.setattr(companion_plane, "get_ctx", lambda: context)
    monkeypatch.setattr(companion_plane, "_action_surface", lambda *args, **kwargs: _surface())
    frame = companion_plane.build_context_frame(webspace_id="desktop")

    receipt = companion_plane.execute_action_request(
        {
            "operation": "ui.affordance.activate",
            "params": {"affordance_id": "settings.open"},
            "webspace_id": "desktop",
            "context_digest": frame["context_digest"],
        },
        frame=frame,
    )

    assert receipt["status"] == "dispatched"
    event = context.bus.events[0]
    assert event.type == "voice.capability.activate"
    assert "settings_modal" in event.payload["activation_plan"]


def test_read_only_status_does_not_require_context_digest(monkeypatch, tmp_path) -> None:
    context = _ctx(tmp_path)
    monkeypatch.setattr(companion_plane, "get_ctx", lambda: context)
    monkeypatch.setattr(companion_plane, "_action_surface", lambda *args, **kwargs: _surface())
    monkeypatch.setattr(
        companion_plane,
        "_read_codex_tokens",
        lambda: {"available": True, "usage": {"used_24h": 42}},
    )
    frame = companion_plane.build_context_frame(webspace_id="desktop")

    receipt = companion_plane.execute_action_request(
        {
            "operation": "status.codex_tokens.read",
            "params": {},
            "webspace_id": "desktop",
        },
        frame=frame,
    )

    assert receipt["status"] == "completed"
    assert receipt["result"]["usage"]["used_24h"] == 42


def test_codex_token_status_projects_bounded_summary(monkeypatch) -> None:
    from adaos.services import economic_policy

    monkeypatch.setattr(
        economic_policy,
        "current_subnet_economic_status",
        lambda: {
            "source": "test",
            "generated_at": "2026-10-05T00:00:00Z",
            "usage": {
                "codex.api.tokens": {
                    "used_24h": 42,
                    "used_7d": 100,
                    "usage_breakdown": {"huge": "private-detail"},
                }
            },
        },
    )

    result = companion_plane._read_codex_tokens()

    assert result["usage"] == {"used_24h": 42, "used_7d": 100}
    assert "usage_breakdown" not in result["usage"]


def test_plane_publishes_expected_mcp_contracts(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_COMPANION_SAGE_ENABLED", "true")
    contracts = {item.id: item for item in companion_plane.contracts()}

    assert set(contracts) == {
        "companion.context.get",
        "companion.action.preview",
        "companion.action.execute",
        "companion.activity.list",
        "companion.capability_request.capture",
    }
    assert contracts["companion.action.execute"].required_capability == "companion.execute"
    assert contracts["companion.context.get"].required_capability == "companion.read"
