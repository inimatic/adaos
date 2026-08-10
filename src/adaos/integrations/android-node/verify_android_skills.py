"""Exercise the fixed Android install profile through its browser-facing APIs."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

from websockets.sync.client import connect


def _post(base_url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base_url}/api/tools/call",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Origin": "https://inimatic.com"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.load(response)
    if result.get("ok") is not True:
        raise RuntimeError(result)
    return result["result"]


def _command(websocket, command_id: str, kind: str, payload: dict) -> tuple[dict, list[dict]]:
    websocket.send(
        json.dumps(
            {"ch": "events", "t": "cmd", "id": command_id, "kind": kind, "payload": payload}
        )
    )
    events: list[dict] = []
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        message = json.loads(websocket.recv(timeout=15))
        if message.get("t") == "ack" and message.get("id") == command_id:
            data = message.get("data") or {}
            if data.get("ok") is False:
                raise RuntimeError(data)
            return data, events
        events.append(message)
    raise RuntimeError(f"command was not acknowledged: {kind}")


def _snapshot(base_url: str) -> dict:
    return _materialization(base_url)["snapshot"]


def _materialization(base_url: str) -> dict:
    with urllib.request.urlopen(
        f"{base_url}/api/node/yjs/webspaces/desktop/materialization/snapshot",
        timeout=5,
    ) as response:
        return json.load(response)


def _node_label(marker: str) -> str:
    return f"Android Smoke {marker[-12:]}"[:64]


def _verify_persisted_state(base_url: str, marker: str) -> None:
    notebook = _post(
        base_url,
        {"tool": "notebook_skill:get_notebook_snapshot", "arguments": {}},
    )
    if not any(item.get("content") == marker for item in notebook.get("items") or []):
        raise RuntimeError("notebook marker did not survive restart")
    subnet = _post(
        base_url,
        {"tool": "subnet_env:get_snapshot", "arguments": {}},
    )
    if subnet.get("node_label") != _node_label(marker):
        raise RuntimeError("subnet_env node label did not survive restart")
    dialog = _snapshot(base_url).get("data", {}).get("dialog", {})
    if dialog.get("active_agent", {}).get("label") != "Арсений":
        raise RuntimeError("selected Android dialog agent did not survive restart")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("run", "verify"))
    parser.add_argument("--base-url", default="http://127.0.0.1:18777")
    parser.add_argument("--marker", required=True)
    arguments = parser.parse_args()
    if arguments.mode == "verify":
        _verify_persisted_state(arguments.base_url, arguments.marker)
        print(json.dumps({"ok": True, "mode": "verify", "marker": arguments.marker}))
        return 0

    notebook = _post(
        arguments.base_url,
        {
            "tool": "notebook_skill:create_note",
            "arguments": {"content": ""},
            "idempotency_key": f"android-smoke:{arguments.marker}",
        },
    )
    note_id = str(notebook.get("selected_note_id") or "")
    notebook = _post(
        arguments.base_url,
        {
            "tool": "notebook_skill:save_note",
            "arguments": {"note_id": note_id, "content": arguments.marker},
            "idempotency_key": f"android-smoke-save:{arguments.marker}",
        },
    )
    if notebook.get("editor", {}).get("content") != arguments.marker:
        raise RuntimeError("notebook create result mismatch")
    disposable = _post(
        arguments.base_url,
        {
            "tool": "notebook_skill:create_note",
            "arguments": {"content": "Disposable Android smoke note"},
            "idempotency_key": f"android-smoke-disposable:{arguments.marker}",
        },
    )
    disposable_id = str(disposable.get("selected_note_id") or "")
    deleted = _post(
        arguments.base_url,
        {
            "tool": "notebook_skill:delete_note",
            "arguments": {"note_id": disposable_id},
            "idempotency_key": f"android-smoke-delete:{arguments.marker}",
        },
    )
    if any(item.get("id") == disposable_id for item in deleted.get("items") or []):
        raise RuntimeError("Notebook delete did not remove the disposable note")

    subnet = _post(
        arguments.base_url,
        {"tool": "subnet_env:get_snapshot", "arguments": {}},
    )
    if not subnet.get("node_id") or not subnet.get("subnet_id"):
        raise RuntimeError("subnet_env snapshot is incomplete")

    ws_url = arguments.base_url.replace("http://", "ws://") + "/ws"
    with connect(ws_url, origin="https://inimatic.com", open_timeout=5, close_timeout=2) as websocket:
        subnet_result, _ = _command(
            websocket,
            "subnet-env-smoke",
            "skill.event.publish",
            {
                "event_type": "subnet_env.node_label.changed",
                "payload": {"node_label": _node_label(arguments.marker)},
            },
        )
        if subnet_result.get("result", {}).get("node_label") != _node_label(arguments.marker):
            raise RuntimeError("subnet_env node label was not projected")

        offline_request_id = f"weather-offline-{time.time_ns()}"
        offline_weather, _ = _command(
            websocket,
            "weather-offline-smoke",
            "skill.event.publish",
            {
                "event_type": "weather.location.requested",
                "payload": {
                    "city": "AdaOS-City-That-Does-Not-Exist-948271",
                    "request_id": offline_request_id,
                },
            },
        )
        if offline_weather.get("result", {}).get("current", {}).get("source") != "offline":
            raise RuntimeError("weather did not publish its bounded offline state")

        request_id = f"weather-{time.time_ns()}"
        weather, _ = _command(
            websocket,
            "weather-smoke",
            "skill.event.publish",
            {
                "event_type": "weather.location.requested",
                "payload": {"city": "Moscow", "request_id": request_id},
            },
        )
        if weather.get("result", {}).get("current", {}).get("request_id") != request_id:
            raise RuntimeError("weather request was not projected")
        if weather.get("result", {}).get("current", {}).get("source") != "open-meteo":
            raise RuntimeError("weather did not recover from offline to Open-Meteo")

        connect_result, _ = _command(
            websocket,
            "connect-smoke",
            "adaos_connect.prepare",
            {"mode": "member", "refresh": True},
        )
        connect_current = connect_result.get("result", {}).get("current", {})
        if connect_current.get("status") not in {"offline", "connecting", "connected"}:
            raise RuntimeError("AdaOS Connect did not publish member enrollment state")
        if connect_current.get("link"):
            raise RuntimeError("AdaOS Connect must not present LO as a remote invitation")

        registration, _ = _command(
            websocket,
            "browser-register-smoke",
            "device.register",
            {
                "device_id": "android-smoke-browser",
                "client_id": "android-smoke-client",
                "webspace_id": "desktop",
                "browser_family": "Smoke browser",
                "user_agent": "Android physical smoke",
            },
        )
        if registration.get("device_id") != "android-smoke-browser":
            raise RuntimeError("Browser session registration was rejected")
        browsers, _ = _command(
            websocket,
            "browser-snapshot-smoke",
            "browsers.refresh",
            {"webspace_id": "desktop"},
        )
        if browsers.get("result", {}).get("summary", {}).get("value", 0) < 1:
            raise RuntimeError("Browsers did not project the active control session")

        voice, _ = _command(
            websocket,
            "voice-assistant-smoke",
            "dialog.user_message",
            {"text": "Привет", "webspace_id": "desktop"},
        )
        if voice.get("accepted") is not True or not voice.get("response"):
            raise RuntimeError("Android voice assistant did not complete a local turn")

        selected, _ = _command(
            websocket,
            "dialog-agent-nika-smoke",
            "dialog.agent.select",
            {
                "agent_id": "agent:conversation_companions:nika",
                "webspace_id": "desktop",
            },
        )
        if selected.get("active_agent", {}).get("label") != "Ника":
            raise RuntimeError("Android dialog agent selector did not activate Nika")

        builder, _ = _command(
            websocket,
            "dialog-builder-smoke",
            "dialog.channel.select",
            {"channel_id": "builder", "webspace_id": "desktop"},
        )
        if builder.get("active_agent", {}).get("label") != "Строитель":
            raise RuntimeError("Android dialog channel selector did not activate Builder")

        arseni, _ = _command(
            websocket,
            "dialog-addressed-arseni-smoke",
            "dialog.user_message",
            {"text": "Арсений, привет", "webspace_id": "desktop"},
        )
        if arseni.get("active_agent_label") != "Арсений":
            raise RuntimeError("Android addressed dialog did not activate Arseni")

        stream, stream_events = _command(
            websocket,
            "notebook-stream-smoke",
            "webio.stream.snapshot.requested",
            {"webspace_id": "desktop", "receiver": "notebook_skill.notes"},
        )
        if not stream.get("snapshot", {}).get("items"):
            raise RuntimeError("Notebook stream snapshot is empty")
        if not any(
            event.get("kind") == "webio.stream.desktop.notebook_skill.notes"
            for event in stream_events
        ):
            raise RuntimeError("Notebook stream event was not published")

        skill_demo = _post(
            arguments.base_url,
            {
                "tool": "demo_metrics_skill:emit_demo_event",
                "arguments": {"action_id": "physical_skill_smoke", "metric_id": "memory"},
                "idempotency_key": f"android-smoke-demo:{arguments.marker}",
            },
        )
        if not skill_demo.get("ok"):
            raise RuntimeError("Taiga skill event was rejected")
        skill_event = json.loads(websocket.recv(timeout=5))
        if skill_event.get("kind") != "webio.stream.desktop.demo_metrics.events":
            raise RuntimeError("Taiga skill event was not delivered")

        taiga, _ = _command(
            websocket,
            "taiga-smoke",
            "desktop.scenario.set",
            {"webspace_id": "desktop", "scenario_id": "taiga_ui_demo_scenario"},
        )
        if taiga.get("scenario_id") != "taiga_ui_demo_scenario":
            raise RuntimeError("Taiga UI scenario did not activate")
        taiga_materialization = _materialization(arguments.base_url)
        taiga_application = taiga_materialization.get("snapshot", {}).get("ui", {}).get(
            "application", {}
        )
        if taiga_materialization.get("materialization", {}).get("ready") is not True:
            raise RuntimeError(f"Taiga materialization is not ready: {taiga_materialization}")
        if not {"apps_catalog", "widgets_catalog"}.issubset(
            set((taiga_application.get("modals") or {}).keys())
        ):
            raise RuntimeError("Taiga materialization dropped desktop catalog modals")
        page_widgets = {
            str(item.get("id") or ""): str(item.get("type") or "")
            for item in (
                taiga_application.get("desktop", {})
                .get("pageSchema", {})
                .get("widgets", [])
            )
            if isinstance(item, dict)
        }
        required_widgets = {
            "demo-table": "ui.table",
            "demo-tree": "collection.tree",
            "demo-chart-payload": "visual.metricChart",
        }
        if any(page_widgets.get(key) != value for key, value in required_widgets.items()):
            raise RuntimeError(f"Taiga proof widgets are incomplete: {page_widgets}")
        semantic_views = {
            str(item.get("id") or ""): str(item.get("kind") or "")
            for item in (
                taiga_application.get("desktop", {})
                .get("pageSchema", {})
                .get("semantic", {})
                .get("views")
                or []
            )
            if isinstance(item, dict)
        }
        if semantic_views.get("demo_metric_tree") != "collection_tree":
            raise RuntimeError(f"Taiga semantic tree is missing: {semantic_views}")

        selection, _ = _command(
            websocket,
            "demo-selection-smoke",
            "demo_metrics.selection.changed",
            {"metric_id": "memory"},
        )
        if selection.get("result", {}).get("selection", {}).get("metric_id") != "memory":
            raise RuntimeError("Taiga metric selection was not projected")

        demo, demo_events = _command(
            websocket,
            "demo-event-smoke",
            "demo_metrics.host_action",
            {"action_id": "physical_device_smoke", "metric_id": "cpu"},
        )
        if not demo.get("result", {}).get("ok") or not any(
            event.get("kind") == "webio.stream.desktop.demo_metrics.events"
            for event in demo_events
        ):
            raise RuntimeError("Taiga demo event was not delivered")

        desktop, _ = _command(
            websocket,
            "desktop-smoke",
            "desktop.webspace.go_home",
            {"webspace_id": "desktop", "wait_for_rebuild": True},
        )
        if desktop.get("scenario_id") != "web_desktop":
            raise RuntimeError("desktop.webspace.go_home did not reactivate web_desktop")

    materialization = _materialization(arguments.base_url)
    if materialization.get("materialization", {}).get("ready") is not True:
        raise RuntimeError(f"web_desktop materialization is not ready: {materialization}")
    if materialization.get("materialization", {}).get("current_scenario") != "web_desktop":
        raise RuntimeError("web_desktop did not become the materialized home scenario")
    snapshot = materialization["snapshot"]
    weather_state = snapshot["data"]["weather"]["current"]
    if weather_state.get("request_id") != request_id or weather_state.get("pending") is not False:
        raise RuntimeError("weather Yjs projection is incomplete")
    if snapshot["data"]["subnet_env"]["current"].get("node_label") != _node_label(
        arguments.marker
    ):
        raise RuntimeError("subnet_env Yjs projection is incomplete")
    if snapshot["data"]["demo_metrics"]["selection"].get("metric_id") != "memory":
        raise RuntimeError("Taiga selection Yjs projection is incomplete")
    dialog = snapshot["data"].get("dialog") or {}
    expected_agents = {"AdaOS Mobile", "Арсений", "Ника", "Мира", "Строитель"}
    if {item.get("label") for item in dialog.get("agents") or []} != expected_agents:
        raise RuntimeError("Android dialog roster projection is incomplete")
    if dialog.get("active_agent", {}).get("label") != "Арсений":
        raise RuntimeError("Android active dialog agent projection is incomplete")
    if dialog.get("implementation", {}).get("model_backed") is not False:
        raise RuntimeError("Android dialog capability boundary is not explicit")
    _verify_persisted_state(arguments.base_url, arguments.marker)
    print(
        json.dumps(
            {
                "ok": True,
                "mode": "run",
                "marker": arguments.marker,
                "weather_source": weather_state.get("source"),
                "weather_error": weather_state.get("error") or "",
                "weather_offline_recovered": True,
                "subnet_env_round_trip": True,
                "adaos_connect_member_state": True,
                "browsers_projection": True,
                "voice_assistant_turn": True,
                "dialog_roster": sorted(expected_agents),
                "dialog_agent_switch": True,
                "taiga_widgets": sorted(required_widgets),
                "scenario_round_trip": True,
                "notebook_stream": True,
                "demo_stream": True,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
