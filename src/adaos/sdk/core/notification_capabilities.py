"""Notification authoring pack derived from the executable public facade."""
from __future__ import annotations

import inspect
from typing import Any

from adaos.domain.notifications import digest


def notification_capability_pack() -> dict[str, Any]:
    from adaos.sdk import notifications
    members = []
    for name in notifications.__all__:
        function = getattr(notifications, name)
        if inspect.isfunction(function):
            members.append({"api": "adaos.sdk.notifications." + name,
                            "permissions": list(getattr(function, "__adaos_public_contract__", {}).get("permissions", []))})
    members.append({"api": "adaos.sdk.testing.notification_story", "permissions": []})
    contract = {
        "capabilities": ["notifications.authoring", "notifications.send", "notifications.manage"],
        "permissions": ["notifications.send", "notifications.manage"],
        "effects": ["notification_intent", "recipient_state", "attention_preferences", "endpoint_policy"],
        "boundedness": {"kind": "exact_capability_pack", "max_categories": 100, "max_page_items": 100},
        "stability": "beta", "runtime_support": {"owner": "notifications", "min_contract": 1},
        "action_closure": {"members": [m["api"] for m in members],
            "requires": ["verified_application", "release_category", "source_event_idempotency",
                         "recipient_scope", "current_user_policy", "independent_delivery",
                         "no_production_preview", "no_implicit_consent"]},
    }
    contract["digest"] = "sha256:" + digest(contract)
    return {
        "kind": "sdk_capability_pack", "name": "adaos.sdk.capability.notifications.v1",
        "module": "adaos.sdk.notifications",
        "summary": "Notifications, scheduled reminders, background completion updates, digest, quiet hours and mute. "
                   "Напоминания, дай знать когда готово, сводка, тихие часы, слишком много сообщений, доставка в MAX и Telegram.",
        "description": "Declare release-owned categories, emit an owned committed source fact, capture background publisher, "
                       "inspect private attention/delivery state and manage the verified user's rules. "
                       "Read/archive/snooze do not approve or execute a linked Interaction.",
        "contract": contract, "members": members, "meta": {"stability": "beta", "pack_version": 1},
        "examples": [
            {"id": "notifications.committed_source.v1",
             "source": "examples/notification-skill/handlers/main.py",
             "declaration": "examples/notification-skill/notifications/manifest.json",
             "stages": ["validate_declaration", "admitted_application_and_category", "source_commit_and_outbox",
                        "emit_with_stable_event_and_occurrence", "inspect_acceptance", "independent_delivery_receipts"],
             "expected": {"retry_model_calls": 0, "producer_credentials": 0}},
            {"id": "notifications.background_completion.v1",
             "source": "examples/notification-skill/handlers/main.py",
             "stages": ["capture_publisher_at_ingress", "persist_ref_with_task", "commit_source_outcome",
                        "emit_captured", "reconcile_receipt"],
             "expected": {"follow_current_focus": False, "revoked_or_retired_publisher": "denied"}},
            {"id": "notifications.human_decision.v1",
             "source": "examples/notification-decision/handlers/main.py",
             "dependency": "adaos.sdk.capability.human_decision.v1",
             "stages": ["create_canonical_interaction_and_executor", "emit_notice_linking_declared_interaction",
                        "present_and_admit_verified_response", "invoke_exact_response", "observe_effect_outcome"],
             "expected": {"notification_read_executes_effect": False, "notification_snooze_renews_decision_ttl": False}},
            {"id": "notifications.recipient_management.v1",
             "source": "examples/notification-manager/handlers/main.py",
             "stages": ["verified_management_grant", "read_owned_state", "cas_read_snooze_or_category_mute",
                        "inspect_endpoint", "explicit_one_shot_test"],
             "expected": {"producer_can_manage_preferences": False, "failed_test_enables_fallback": False}},
        ],
        "must_keep": ["source/recipient authority", "current mute/quiet/expiry", "preview isolation",
                      "no sender/credentials/history queue", "Interaction/effect authority stays in PA/GWR"],
    }


def notification_contract_summary() -> dict[str, Any]:
    """Bounded worker hint; exact metadata is retrieved from SDK discovery."""
    return {
        "pack": "adaos.sdk.capability.notifications.v1", "owner": "Core Notifications and Attention",
        "producer": "adaos.sdk.notifications.emit / a_emit; exact committed source event and occurred_at",
        "background": "capture_publisher at admitted ingress; persist publisher_ref with task; emit_captured after outcome",
        "audience": "emit_to_source / emit_captured_to_source resolve canonical Interaction explicit assignees/participants; 25 per page, 100 per event; no caller recipient list",
        "declaration": "skill.yaml notifications.manifest points into owned package; compile and release disclosure must agree",
        "management": "notifications.manage plus verified user/Application grant; send alone cannot read or change global preferences",
        "preview": "synthetic user/namespace; no real recipient, endpoint grant or production policy mutation",
        "delivery": "current policy/source/endpoint checks and independent existing outbox receipts; endpoint capabilities gate each transport",
        "interaction": "notice links canonical PA/GWR Interaction; read/archive/snooze never approve or execute it",
        "missing": "return explicit Core/SDK capability gap; do not generate sender, credential store or history delivery scan",
    }
