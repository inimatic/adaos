from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import requests


DEFAULT_RECEIVERS = [
    "notebook_skill.notes",
    "ai_event_analysis.results",
    "infrastate.yjs.load_mark",
    "infrastate.logs",
    "infrastate.events",
    "browsers.summary",
    "browsers.devices",
    "conversation_companions.diagnostics",
    "cv_descriptor.events",
]


@dataclass
class Result:
    sent: int = 0
    accepted: int = 0
    failed: int = 0


def publish_event(
    *,
    base_url: str,
    token: str,
    event_type: str,
    receiver: str,
    webspace_id: str,
    test_id: str,
    timeout_s: float,
    node_id: str = "",
    action: str | None = None,
) -> bool:
    payload: dict[str, Any] = {
        "receiver": receiver,
        "webspace_id": webspace_id,
        "_meta": {
            "webspace_id": webspace_id,
            "source": "codex.synthetic_webio_load",
            "test_id": test_id,
        },
    }
    if node_id:
        payload["node_id"] = node_id
        payload["target_node_id"] = node_id
        payload["_meta"]["node_id"] = node_id
        payload["_meta"]["target_node_id"] = node_id
    if action:
        payload["action"] = action

    body = {
        "event_type": event_type,
        "webspace_id": webspace_id,
        "payload": payload,
        "meta": {
            "webspace_id": webspace_id,
            "source": "codex.synthetic_webio_load",
            "test_id": test_id,
        },
    }
    response = requests.post(
        base_url.rstrip("/") + "/api/node/events/publish",
        headers={"X-AdaOS-Token": token, "Accept": "application/json"},
        json=body,
        timeout=timeout_s,
    )
    response.raise_for_status()
    data = response.json()
    return bool(data.get("ok") and data.get("accepted"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish synthetic WebIO demand events to a local AdaOS runtime.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8777")
    parser.add_argument("--token", default="dev-local-token")
    parser.add_argument("--webspace-id", default="desktop")
    parser.add_argument("--node-id", default="")
    parser.add_argument("--duration", type=float, default=180.0)
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between receiver rounds.")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--receiver", action="append", default=[])
    parser.add_argument("--subscription-each-round", action="store_true")
    parser.add_argument("--snapshot", action="store_true", default=True)
    parser.add_argument("--no-snapshot", action="store_false", dest="snapshot")
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    receivers = [item.strip() for item in (args.receiver or DEFAULT_RECEIVERS) if item.strip()]
    test_id = args.label.strip() or f"codex-load-{uuid.uuid4().hex[:8]}"
    result = Result()
    start = time.monotonic()
    deadline = start + max(0.0, float(args.duration))
    first_round = True

    while time.monotonic() < deadline:
        round_start = time.monotonic()
        for receiver in receivers:
            should_send_subscription = first_round or bool(args.subscription_each_round)
            if should_send_subscription:
                result.sent += 1
                try:
                    if publish_event(
                        base_url=args.base_url,
                        token=args.token,
                        event_type="webio.stream.subscription.changed",
                        receiver=receiver,
                        webspace_id=args.webspace_id,
                        node_id=args.node_id,
                        test_id=test_id,
                        timeout_s=float(args.timeout),
                        action="subscribed",
                    ):
                        result.accepted += 1
                    else:
                        result.failed += 1
                except Exception:
                    result.failed += 1
            if args.snapshot:
                result.sent += 1
                try:
                    if publish_event(
                        base_url=args.base_url,
                        token=args.token,
                        event_type="webio.stream.snapshot.requested",
                        receiver=receiver,
                        webspace_id=args.webspace_id,
                        node_id=args.node_id,
                        test_id=test_id,
                        timeout_s=float(args.timeout),
                    ):
                        result.accepted += 1
                    else:
                        result.failed += 1
                except Exception:
                    result.failed += 1
        first_round = False
        elapsed = time.monotonic() - round_start
        sleep_s = max(0.0, float(args.interval) - elapsed)
        if sleep_s > 0:
            time.sleep(sleep_s)

    payload = {
        "test_id": test_id,
        "duration_s": round(time.monotonic() - start, 3),
        "receiver_total": len(receivers),
        "sent": result.sent,
        "accepted": result.accepted,
        "failed": result.failed,
        "webspace_id": args.webspace_id,
        "subscription_each_round": bool(args.subscription_each_round),
        "snapshot": bool(args.snapshot),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
