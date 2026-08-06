from __future__ import annotations

from pathlib import Path
from typing import Any

from adaos.services.bounded_io import bounded_jsonl_tail


class WatchdogStatusCompactor:
    """Own bounded watchdog persistence and public status shaping."""

    @classmethod
    def compact_json_value(
        cls,
        value: Any,
        *,
        depth: int = 0,
        max_depth: int = 3,
        max_items: int = 20,
        max_text: int = 512,
    ) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) <= max_text:
                return value
            return f"{value[:max_text]}...<truncated:{len(value) - max_text}>"
        if depth >= max_depth:
            if isinstance(value, dict):
                return {"_truncated": True, "type": "dict", "size": len(value)}
            if isinstance(value, (list, tuple)):
                return {"_truncated": True, "type": "list", "size": len(value)}
            return repr(value)
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= max_items:
                    result["_truncated_items"] = max(0, len(value) - max_items)
                    break
                result[str(key)] = cls.compact_json_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_text=max_text,
                )
            return result
        if isinstance(value, (list, tuple)):
            items = [
                cls.compact_json_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_text=max_text,
                )
                for item in list(value)[:max_items]
            ]
            if len(value) > max_items:
                items.append({"_truncated_items": len(value) - max_items})
            return items
        return repr(value)

    @classmethod
    def compact_channel_state(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        keys = {
            "root_control_status",
            "route_status",
            "hub_root_status",
            "hub_root_state",
            "hub_root_browser_status",
            "hub_root_browser_state",
            "hub_member_status",
            "member_state",
            "assessment_state",
            "assessment_reason",
            "transition_state",
            "transition_reason",
            "connected",
            "last_error",
            "last_close_reason",
            "last_summary",
            "hub_url",
        }
        return {
            key: cls.compact_json_value(item, max_depth=2, max_text=256)
            for key, item in value.items()
            if key in keys and item is not None
        }

    @classmethod
    def compact_required_link(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        keys = {
            "kind",
            "role",
            "owner",
            "state",
            "reason",
            "ready",
            "visible",
            "desired_state",
            "current_owner",
            "planned_owner",
            "future_owner",
            "continuity_mode",
            "sidecar_enabled",
            "reconnect_total",
            "cooldown_sec",
            "verify_timeout_sec",
            "served_by",
            "blockers",
            "transport_state",
            "transition_state",
            "handoff_state",
            "handoff_ready",
            "recovery_policy",
        }
        return {
            key: cls.compact_json_value(item, max_depth=2, max_text=256)
            for key, item in value.items()
            if key in keys and item is not None
        }

    @classmethod
    def compact_decision(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        keys = {
            "reason",
            "message",
            "action",
            "transport_owner",
            "root_control_status",
            "route_status",
            "hub_root_status",
            "hub_root_state",
            "hub_root_browser_status",
            "hub_root_browser_state",
            "continuity_mode",
            "handoff_state",
            "handoff_ready",
            "recovery_policy",
            "hub_member_status",
            "member_state",
            "assessment_state",
            "assessment_reason",
            "transition_state",
            "transition_reason",
            "last_error",
            "last_close_reason",
            "last_event",
            "last_summary",
        }
        result = {
            key: cls.compact_json_value(item, max_depth=2, max_text=256)
            for key, item in value.items()
            if key in keys and item is not None
        }
        channel = cls.compact_channel_state(value.get("channel_before"))
        if channel:
            result["channel"] = channel
        required_link = cls.compact_required_link(value.get("required_upstream_link"))
        if required_link:
            result["required_upstream_link"] = required_link
        return result

    @classmethod
    def compact_verification(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        result = {
            key: cls.compact_json_value(value.get(key), max_depth=2, max_text=256)
            for key in ("ok", "state", "source", "attempts", "timeout_sec", "error")
            if value.get(key) is not None
        }
        channel = cls.compact_channel_state(value.get("channel"))
        if channel:
            result["channel"] = channel
        return result

    @classmethod
    def compact_result(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        keys = {
            "ok",
            "accepted",
            "error",
            "message",
            "state",
            "restart",
            "reconnect",
            "action",
        }
        return {
            key: cls.compact_json_value(item, max_depth=2, max_text=256)
            for key, item in value.items()
            if key in keys and item is not None
        }

    @classmethod
    def compact_last_result(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        result = {
            key: cls.compact_json_value(value.get(key), max_depth=1, max_text=256)
            for key in ("requested_at", "action")
            if value.get(key) is not None
        }
        decision = cls.compact_decision(value.get("decision"))
        if decision:
            result["decision"] = decision
        action_result = cls.compact_result(value.get("result"))
        if action_result:
            result["result"] = action_result
        verification = cls.compact_verification(value.get("verification"))
        if verification:
            result["verification"] = verification
        return result

    @classmethod
    def compact_event(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        result = {
            key: cls.compact_json_value(value.get(key), max_depth=2, max_text=256)
            for key in ("ts", "runtime_url", "event", "action", "transport_owner")
            if value.get(key) is not None
        }
        decision = cls.compact_decision(value.get("decision"))
        if decision:
            result["decision"] = decision
        action_result = cls.compact_result(value.get("result"))
        if action_result:
            result["result"] = action_result
        verification = cls.compact_verification(value.get("verification"))
        if verification:
            result["verification"] = verification
        return result

    @staticmethod
    def read_tail(
        path: Path,
        *,
        limit: int = 20,
        max_bytes: int = 256 * 1024,
    ) -> list[dict[str, Any]]:
        return bounded_jsonl_tail(
            path,
            limit=limit,
            max_bytes=max(4096, int(max_bytes or 0)),
        )
