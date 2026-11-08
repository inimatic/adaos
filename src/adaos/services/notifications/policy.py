"""Pure explainable attention policy; grants are intersected at the Core boundary."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from adaos.services.attention_schedule import instant, quiet_period
from adaos.domain.notifications import endpoint_probe_authority

_WEIGHTS = {"subnet_ref": 1, "application_id": 2, "category_id": 4, "source_id": 8}
_TEXT = {
    "immediate": ("Ready for selected destinations", "Готово к отправке выбранным получателям"),
    "center_only": ("Only in Notification Center", "Только в центре уведомлений"),
    "muted": ("Disabled by your notification rule", "Отключено вашим правилом уведомлений"),
    "quiet_hours": ("Deferred until quiet hours end", "Отложено до окончания тихих часов"),
    "snoozed": ("Snoozed by you", "Отложено вами"),
    "archived": ("Archived by you", "Убрано вами в архив"),
    "expired": ("The notification is no longer current", "Срок актуальности уведомления истёк"),
    "withdrawn": ("The source is no longer available", "Источник больше недоступен"),
    "digest": ("Included in a scheduled summary", "Включено в запланированную сводку"),
    "preview": ("Isolated preview: external delivery disabled", "Изолированный предпросмотр: внешняя отправка отключена"),
    "endpoint_unavailable": ("Selected destination is unavailable", "Выбранный канал доставки недоступен"),
    "already_read": ("This revision has already been read", "Эта версия уже прочитана"),
    "budget": ("Deferred by the interruption budget", "Отложено по лимиту уведомлений"),
    "permission_denied": ("Current permission no longer allows delivery", "Текущее разрешение больше не допускает отправку"),
    "probe_policy_changed": ("The settings changed after this one-time test", "Настройки изменились после этого разового теста"),
    "shared_destination_not_admitted": ("Shared destination has no notification admission", "Для общего чата не разрешена доставка уведомлений"),
}


def effective_choice(record: Mapping[str, Any], preferences: Mapping[str, Any]) -> dict[str, Any]:
    probe = endpoint_probe_authority(record)
    if probe and probe.get("policy_revision") == preferences["revision"]:
        return {"mode": "immediate", "endpoint_ids": [probe["endpoint_id"]], "quiet_exception": probe.get("allow_quiet", False),
                "matched_scopes": [], "hard_mutes": []}
    choice = copy.deepcopy(preferences["defaults"])
    rules = []
    for item in preferences["rules"]:
        scope = item["scope"]
        if all(record.get(key) == value for key, value in scope.items()):
            rules.append((sum(_WEIGHTS[key] for key in scope), item))
    hard_mutes = []
    for _, item in sorted(rules, key=lambda pair: pair[0]):
        choice = copy.deepcopy(item["choice"])
        if choice["mode"] == "off":
            hard_mutes.append(item["scope"])
    if hard_mutes:
        choice["mode"] = "off"
    return {**choice, "matched_scopes": [item["scope"] for _, item in rules], "hard_mutes": hard_mutes}


def explain(reason: str) -> dict[str, str]:
    en, ru = _TEXT.get(reason, (reason, reason))
    return {"en": en, "ru": ru}


def evaluate(
    record: Mapping[str, Any], preferences: Mapping[str, Any], endpoints: list[dict],
    *, now=None, admission: bool = False,
) -> dict[str, Any]:
    current = instant(now).timestamp()
    choice = effective_choice(record, preferences)
    mode = choice["mode"]
    reason, due = mode, None
    selected = set(choice["endpoint_ids"])
    if not admission:
        selected.intersection_update(record.get("admitted_endpoint_ids", []))
        selected.difference_update(record.get("cancelled_endpoint_ids", []))
    active = [endpoint for endpoint in endpoints
              if endpoint["endpoint_id"] in selected and endpoint["status"] == "active"
              and endpoint["principal_ref"] == record["principal_ref"]
              and endpoint["subnet_ref"] == record["subnet_ref"]
              and (admission or record.get("admitted_endpoint_revisions", {}).get(endpoint["endpoint_id"]) == endpoint.get("revision"))
              and (endpoint.get("expires_at") is None or endpoint["expires_at"] > current)]
    visible = mode != "off" if admission else not record.get("admission_muted", False)
    alert = mode in {"digest", "immediate"}
    probe = endpoint_probe_authority(record)
    if record["source_state"] != "active":
        reason, alert = "withdrawn", False
    elif record["expires_at"] <= current:
        reason, alert = "expired", False
    elif probe and probe.get("policy_revision") != preferences["revision"]:
        reason, alert = "probe_policy_changed", False
    elif mode == "off" or record.get("admission_muted", False):
        reason, alert = "muted", False
        if admission or record.get("admission_muted", False):
            visible = False
    elif record.get("test_only"):
        reason, alert = "preview", False
    elif record.get("archived"):
        reason, alert = "archived", False
    elif record.get("snoozed_until") and record["snoozed_until"] > current:
        reason, due, alert = "snoozed", record["snoozed_until"], False
    elif record.get("suppress_if_read") and record.get("read_revision", 0) >= record["content_revision"]:
        reason, alert = "already_read", False
    elif alert:
        quiet, until = quiet_period(preferences["quiet_hours"], now=current)
        if quiet and not choice.get("quiet_exception", False):
            reason, due, alert = "quiet_hours", until.timestamp() if until else None, False
        elif not active:
            reason, alert = "endpoint_unavailable", False
    if due is not None and due >= record["expires_at"]:
        # Keep the card queryable, but do not wake it after its source lifetime.
        reason, due, alert = "expired", None, False
    return {
        "schema": "adaos.notifications.attention_decision.v1",
        "policy_revision": preferences["revision"], "mode": mode, "reason": reason,
        "visible": visible, "alert": alert, "next_eligible_at": due,
        "endpoint_ids": [endpoint["endpoint_id"] for endpoint in active],
        "matched_scopes": choice["matched_scopes"], "hard_mutes": choice["hard_mutes"],
        "quiet_exception": bool(choice.get("quiet_exception", False)),
        "escalate_after_seconds": choice.get("escalate_after_seconds"),
        "explanation": explain(reason),
    }
