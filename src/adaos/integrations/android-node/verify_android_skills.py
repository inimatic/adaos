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
    with urllib.request.urlopen(
        f"{base_url}/api/node/yjs/webspaces/desktop/materialization/snapshot",
        timeout=5,
    ) as response:
        return json.load(response)["snapshot"]


def _verify_note(base_url: str, marker: str) -> None:
    notebook = _post(
        base_url,
        {"tool": "notebook_skill:get_notebook_snapshot", "arguments": {}},
    )
    if not any(item.get("content") == marker for item in notebook.get("items") or []):
        raise RuntimeError("notebook marker did not survive restart")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("run", "verify"))
    parser.add_argument("--base-url", default="http://127.0.0.1:18777")
    parser.add_argument("--marker", required=True)
    arguments = parser.parse_args()
    if arguments.mode == "verify":
        _verify_note(arguments.base_url, arguments.marker)
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

    ws_url = arguments.base_url.replace("http://", "ws://") + "/ws"
    with connect(ws_url, origin="https://inimatic.com", open_timeout=5, close_timeout=2) as websocket:
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

        connect_result, _ = _command(
            websocket,
            "connect-smoke",
            "adaos_connect.prepare.browser",
            {"mode": "browser", "refresh": True},
        )
        if connect_result.get("result", {}).get("current", {}).get("status") != "offline":
            raise RuntimeError("AdaOS Connect did not publish its bounded offline state")

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
            "desktop.scenario.set",
            {"webspace_id": "desktop", "scenario_id": "web_desktop"},
        )
        if desktop.get("scenario_id") != "web_desktop":
            raise RuntimeError("web_desktop did not reactivate")

    snapshot = _snapshot(arguments.base_url)
    weather_state = snapshot["data"]["weather"]["current"]
    if weather_state.get("request_id") != request_id or weather_state.get("pending") is not False:
        raise RuntimeError("weather Yjs projection is incomplete")
    _verify_note(arguments.base_url, arguments.marker)
    print(
        json.dumps(
            {
                "ok": True,
                "mode": "run",
                "marker": arguments.marker,
                "weather_source": weather_state.get("source"),
                "weather_error": weather_state.get("error") or "",
                "scenario_round_trip": True,
                "notebook_stream": True,
                "demo_stream": True,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
