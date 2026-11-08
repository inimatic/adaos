"""Notification links retain canonical Interaction/navigation authority."""
from __future__ import annotations

import json

from adaos.domain.notifications import token
from adaos.services.conversation_interactions import principal_can_read_interaction


def resolve_actions(con, binding, category, payload, principal_ref):
    actions = []
    for declaration in category["actions"]:
        target = declaration["target"].format_map(dict(payload))
        token(target, "notification action target")
        if declaration["kind"] == "navigate":
            if not target.startswith("application:" + binding.application_id + "/"):
                raise PermissionError("notification navigation must remain in the admitted Application")
        else:
            row = con.execute("SELECT payload_json FROM conversation_interactions WHERE interaction_id=?", (target,)).fetchone()
            interaction = json.loads(row[0]) if row else None
            if (not interaction or interaction["owner"] not in {binding.author_ref, "application:" + binding.application_id}
                or not principal_can_read_interaction(interaction, {"kind": "user", "id": principal_ref.removeprefix("user:")})):
                raise PermissionError("notification cannot link an unavailable or foreign Interaction")
        actions.append({"kind": declaration["kind"], "target": target})
    return actions
