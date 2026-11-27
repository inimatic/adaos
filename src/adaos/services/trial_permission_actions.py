"""Personal presentation of the exact outstanding Builder Trial decision."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adaos.services.development_tickets import (
    PUBLICATION_PERMISSION_PENDING_ACTION_KIND,
    DevelopmentTicketService,
)
from adaos.services.personalization_runtime import current_user_id


def owner_ref(ctx: Any) -> str:
    return f"user:{current_user_id(ctx)}"


def active_request(ticket: Mapping[str, Any]) -> dict[str, Any] | None:
    request = DevelopmentTicketService.publication_permission_request(ticket)
    if not request:
        return None
    for ref in reversed(ticket.get("pending_action_refs") or []):
        if (
            ref.get("kind") == PUBLICATION_PERMISSION_PENDING_ACTION_KIND
            and ref.get("status") in {"pending", "postponed"}
            and ref.get("task_id") == request["task_id"]
            and ref.get("package_digest") == request["package_digest"]
            and ref.get("id")
        ):
            return {**request, "action_id": ref["id"], "ticket_id": ticket["ticket_id"]}
    return None


def matches_action(request: Mapping[str, Any], action: Mapping[str, Any]) -> bool:
    domain = action.get("domain_ref") or {}
    return (
        action.get("id") == request["action_id"]
        and action.get("webspace_id") == request["webspace_id"]
        and action.get("kind") == PUBLICATION_PERMISSION_PENDING_ACTION_KIND
        and all(domain.get(key) == request[key] for key in (
            "ticket_id", "task_id", "package_digest", "object_type", "object_id",
        ))
    )


def require_response_authority(ctx: Any, action: Mapping[str, Any], responder: Mapping[str, Any] | None,
                               *, require_current: bool) -> None:
    if not responder or responder.get("verified") is not True or responder.get("actor") != owner_ref(ctx):
        raise PermissionError("Trial permission response requires the verified owner")
    if require_current:
        ticket = DevelopmentTicketService().get_ticket(str((action.get("domain_ref") or {}).get("ticket_id") or ""))
        request = active_request(ticket) if ticket else None
        if not request or not matches_action(request, action):
            raise ValueError("Trial permission request is no longer current")


_PERMISSIONS = {
    "applications.read": ("read the application catalog", "читать каталог приложений"),
    "external_provider.use": ("call external providers", "обращаться к внешним провайдерам"),
    "storage.relational": ("use relational storage", "использовать реляционное хранилище"),
    "workspace.read": ("read workspace data", "читать данные рабочего пространства"),
    "workspace.write": ("write workspace data", "записывать данные рабочего пространства"),
}


def decision_copy(ticket: Mapping[str, Any], request: Mapping[str, Any], *, locale: str) -> dict[str, Any]:
    meta = ticket.get("metadata") or {}
    title = str(meta.get("application_title") or _application_title(request["object_id"]))[:160]
    version = str(meta.get("package_version") or "")[:64]
    subject = f"{title} {version}".strip()
    ru = str(locale).lower().startswith("ru")
    permissions = sorted(set(request["permissions"]))
    bullets = [
        f"• {_PERMISSIONS.get(permission, (permission, permission))[int(ru)]}"
        for permission in permissions
    ]
    if ru:
        heading = f"{subject} · Разрешения для Trial"
        question = "Builder завершил проверки и ждёт разрешения на пробный запуск перед Beta."
        effect = "Разрешение действует для этой сборки. Builder продолжит проверку в Trial. При отказе переход в Beta остановится."
        labels = {"approve": "Разрешить для Trial", "refuse": "Отказать", "postpone": "Решить позже"}
    else:
        heading = f"{subject} · Trial permissions"
        question = "Builder has validated the build and needs permission to run its Trial before Beta."
        effect = "Permission applies to this build. Builder will continue the Trial. Refusing stops the transition to Beta."
        labels = {"approve": "Allow for this Trial", "refuse": "Refuse", "postpone": "Decide later"}
    return {"title": heading, "request_text": "\n".join([question, *bullets, "", effect]), "labels": labels}


def _application_title(object_id: str) -> str:
    from adaos.services.applications.store import ApplicationStore, ApplicationStoreError
    from adaos.services.runtime_paths import current_authority_state_dir

    try:
        display = ApplicationStore(current_authority_state_dir()).get_application(object_id).display
    except (ApplicationStoreError, FileNotFoundError, ValueError):
        return object_id
    return str(display.get("title") or display.get("name") or object_id)


def present(action: Mapping[str, Any], ticket: Mapping[str, Any], request: Mapping[str, Any], *, locale: str) -> dict[str, Any]:
    copy = decision_copy(ticket, request, locale=locale)
    choices = [
        {**{key: value for key, value in choice.items() if key != "label_i18n"},
         "label": copy["labels"].get(choice["id"], choice.get("label"))}
        for choice in action.get("allowed_actions") or []
    ]
    return {**action, "title": copy["title"], "summary": "", "request_text": copy["request_text"], "allowed_actions": choices}


async def list_personal_actions(ctx: Any, *, principal: Mapping[str, Any], locale: str = "en") -> list[dict[str, Any]]:
    """Render the same legacy action in the owner's authenticated sessions."""
    if principal.get("kind") != "user" or principal.get("actor_id") != owner_ref(ctx):
        return []
    from adaos.services.pending_actions import list_pending_actions_async
    from adaos.services.runtime_executor import run_runtime_interactive

    tickets = await run_runtime_interactive(
        DevelopmentTicketService().list_tickets,
        status="waiting_for_user", source="builder_publication_gate", limit=50,
    )
    projections: dict[str, dict[str, Any]] = {}
    result = []
    for ticket in tickets:
        request = active_request(ticket)
        if not request:
            continue
        webspace_id = request["webspace_id"]
        if webspace_id not in projections:
            snapshot = await list_pending_actions_async(webspace_id=webspace_id, include_terminal=False)
            projections[webspace_id] = snapshot["by_id"]
        action = projections[webspace_id].get(request["action_id"])
        if action and matches_action(request, action):
            result.append(present(action, ticket, request, locale=locale))
    return result


def invalidate_personal_actions() -> None:
    from adaos.services.conversation_store import _publish_interaction_projection_invalidation

    _publish_interaction_projection_invalidation("conversation.interaction.changed")
