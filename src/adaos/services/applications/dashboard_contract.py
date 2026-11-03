"""Public read contract for the Core-owned Application instrument panel."""


def dashboard_schema():
    nullable_text = {"type": ["string", "null"]}
    action = {"type": "object", "properties": {
        "eligible": {"type": "boolean"}, "reason": nullable_text,
        "required_capability": nullable_text, "requires_authorization": {"type": "boolean"},
        "requires_review": {"type": "boolean"}, "mode": {"enum": ["open", "switch_and_open", "navigate"]},
        "tool_id": nullable_text, "arguments": {"type": "object"}},
        "required": ["eligible"], "additionalProperties": True}
    position = {"type": "object", "properties": {
        "id": {"enum": ["private_alpha", "private_beta", "private_stable", "public_beta", "public_stable"]},
        "scope": {"enum": ["private", "public"]}, "stage": {"enum": ["alpha", "beta", "stable"]},
        "version": {"type": "string"}, "release_digest": nullable_text, "candidate_id": nullable_text,
        "source": {"type": "string"}, "active": {"type": "boolean"},
        "availability": {"enum": ["development", "ready", "expired", "unavailable"]},
        "hidden_on_desktop": {"type": "boolean"}, "available_at": nullable_text,
        "updated_at": nullable_text, "expires_at": nullable_text, "admission_kind": nullable_text,
        "actions": {"type": "object", "additionalProperties": action}},
        "required": ["id", "scope", "stage", "version", "active", "availability", "hidden_on_desktop", "actions"],
        "additionalProperties": True}
    return {"type": "object", "properties": {
        "schema": {"const": "adaos.application.dashboard.v1"}, "application_id": {"type": "string"},
        "revision": {"type": "string"}, "lifecycle_revision": nullable_text, "observed_at": nullable_text,
        "freshness": {"enum": ["observed", "unavailable"]}, "publisher_local": {"type": "boolean"},
        "active_position_id": nullable_text, "positions": {"type": "array", "maxItems": 5, "items": position},
        "groups": {"type": "array", "maxItems": 2, "items": {"type": "object", "properties": {
            "id": {"enum": ["private", "public"]}, "position_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "position_ids"], "additionalProperties": False}},
        "operation": {"type": ["object", "null"]}, "read_only": {"const": True}},
        "required": ["schema", "application_id", "revision", "publisher_local", "positions", "groups", "read_only"],
        "additionalProperties": True}
