"""SDK-only linked decision and exact response/effect closure.

The example's command completes a local governed aggregate. An Application with
external effects additionally registers its declared PA/GWR executor and durable
effect assertion; a notification never becomes that executor.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adaos.sdk import access, chat, notifications, workflow


DEFINITION = Path(__file__).with_name("workflow.json")


def _identity():
    user = access.current_user()
    application = access.application()
    if not user or not application or not application.get("component_ref"):
        raise PermissionError("verified initiating Application/user required")
    return "user:" + user["id"], application["component_ref"]


def ask(*, task_id: str, title: str, conversation_id: str, occurred_at: float):
    actor, owner = _identity()
    instance_id = "decision-example:" + task_id
    workflow.ensure_instance(DEFINITION, instance_id)
    interaction = workflow.create_interaction(DEFINITION, instance_id, actor_id=actor,
        conversation_id=conversation_id, owner=owner, command_context_id="decision-context:" + task_id,
        interaction_id="decision-example:" + task_id, prompt=title,
        expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
    # Canonical PA presentation owns tokens, navigation, input and action origin.
    presentation = chat.request(interaction, conversation_id=conversation_id, owner=owner)
    # Capture the event occurrence once in the source/outbox, as in the companion
    # transactional example, when wiring this recipe to an asynchronous source.
    receipt = notifications.emit(category_id="decision.required", event_id="decision-created:" + task_id,
        source_id=instance_id, source_revision=interaction["generation"] + 1, occurred_at=occurred_at,
        payload={"title": title, "interaction_id": interaction["interaction_id"]})
    return {"interaction": interaction, "presentation": presentation, "notification": receipt}


def answer(*, task_id: str, interaction_id: str, generation: int, action_token: str,
           presentation_id: str, operation_key: str):
    actor, _ = _identity()
    admitted = chat.respond(interaction_id, actor_id=actor, expected_generation=generation,
        idempotency_key=operation_key, action_token=action_token, presentation_id=presentation_id)
    result = workflow.invoke_interaction_response(DEFINITION, "decision-example:"+task_id,
        admitted["response"], actor_id=actor)
    outcome = workflow.get_interaction_outcome(admitted["response"]["response_id"], actor_id=actor)
    return {"execution": result, "outcome": outcome}
