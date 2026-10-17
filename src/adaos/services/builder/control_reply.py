"""Bounded control-plane replies; retained sessions remain with their owner."""
from __future__ import annotations

from collections.abc import Mapping
import json
from urllib.parse import urlencode

from adaos.services.log_redaction import redact_log_text

SCHEMA = "adaos.builder.control_reply.v1"
MAX_BYTES = 48 * 1024  # Reserve room for native dialog/topic and tool envelopes.


def control_reply(result: Mapping, *, object_type: str, object_id: str, locale: str = "ru") -> dict:
    """Project facts without changing execution, history, budgets or unknowns."""
    shortened = []

    def scalar(value, path, limit=1200):
        if value is None or type(value) in (bool, int, float):
            return value
        if not isinstance(value, str):
            return None
        value = redact_log_text(value)
        if len(value) > limit:
            shortened.append(path)
            return value[:limit] + "…"
        return value

    def fields(value, names, path):
        if not isinstance(value, Mapping):
            return {}
        return {key: scalar(value[key], path + "." + key) for key in names if key in value}

    session = result.get("session") or {}
    session = session if isinstance(session, Mapping) else {}
    automation = result.get("automation") or {}
    automation = automation if isinstance(automation, Mapping) else {}
    worker = result.get("worker") or {}
    worker = worker if isinstance(worker, Mapping) else {}
    failure = session.get("last_failure") or {}
    failure = failure if isinstance(failure, Mapping) else {}
    task_id = (result.get("task_id") or worker.get("task_id")
               or session.get("current_task_id") or automation.get("task_id"))
    status = str(automation.get("status") or session.get("status") or result.get("status") or "unknown")
    reply = {"schema": SCHEMA, **fields(result, (
        "ok", "status", "error", "message", "model_started", "recovered",
        "reused_validated_result", "resumed_environment_interruption",
        "resumed_exact_checkpoint", "retried_unchanged_request", "checkpoint_digest",
    ), "result"), "project": {"type": object_type, "id": object_id},
        "task_id": scalar(task_id, "task_id"),
        "session": fields(session, ("session_id", "status", "current_task_id", "iteration", "updated_at"), "session"),
        "automation": fields(automation, (
            "status", "phase", "task_id", "iteration", "run_terminal",
            "waiting_for_input", "can_submit", "error", "failure_stage", "retryable",
        ), "automation"),
        "last_failure": fields(failure, ("failure_class", "stage", "message", "retryable", "failure_id"), "failure"),
        "model_started": result.get("model_started") if type(result.get("model_started")) is bool else None,
    }
    progress = automation.get("progress") or session.get("progress") or {}
    reply["automation"]["progress"] = fields(progress, ("status", "stage", "message", "updated_at"), "progress")
    budget = automation.get("budget_usage") or {}
    reply["budget"] = fields(budget, ("status", "usage_state", "billable_status", "overrun_tokens", "billable_overrun_tokens"), "budget")
    if isinstance(budget, Mapping):
        reply["budget"]["observed"] = fields(budget.get("observed"), (
            "attempts", "wall_seconds", "model_tokens", "input_tokens", "cached_input_tokens", "output_tokens",
            "reasoning_tokens", "budget_tokens", "billable_tokens", "cost_usd", "usage_complete",
        ), "budget.observed")
    reply["worker"] = fields(worker, ("ok", "task_id", "reused_validated_result", "recovery_stage"), "worker")
    details = failure.get("details") or {}
    details = details if isinstance(details, Mapping) else {}
    if automation.get("waiting_for_input") or status == "awaiting_input":
        next_action = "answer_questions"
    elif result.get("model_started") is False and "recovery_queued" in str(result.get("status")):
        next_action = "wait"
    elif status == "completed":
        next_action = "review_result"
    elif status in {"failed", "blocked", "cancelled"}:
        next_action = "resume_validated" if details.get("model_rerun_required") is False else "inspect_failure"
    else:
        next_action = "wait" if status != "unknown" else "inspect_state"
    reply["next_action"] = next_action
    # Queue acknowledgements used to repeat the entire instruction. The
    # instruction is already retained in the canonical topic and task packet;
    # the control message should explain the current stage in the user's locale.
    stages = {
        'queued': ('Задача принята и ожидает выполнения.', 'Task accepted and queued.'),
        'workspace_preparing': ('Готовлю отдельное рабочее окружение.', 'Preparing an isolated workspace.'),
        'in_progress': ('Builder выполняет доработку. Следующий этап — проверка результата.',
                        'Builder is implementing the change. Result validation follows.'),
        'tests_running': ('Проверяю код, контракты и интерфейс. Результат ещё не принят.',
                          'Checking code, contracts and interface. The result is not accepted yet.'),
        'commit_ready': ('Код прошёл локальную проверку; проверяю доставку и браузерные сценарии.',
                         'Local code checks passed; checking delivery and browser journeys.'),
    }
    if status in stages and next_action == 'wait' and not reply.get('error'):
        reply['message'] = stages[status][1 if locale == 'en' else 0]
        reply['message_source'] = 'localized_stage_contract; instruction_retained_in_canonical_topic'
    if not reply.get("message"):
        messages = {
            "wait": ("Запрос принят. Работа продолжается; о следующем этапе сообщу в этом чате.",
                     "Request accepted. Work continues; the next stage will be reported in this chat."),
            "review_result": ("Разработка и проверки завершены. Результат доступен в Preview проекта.",
                              "Development and checks completed. Open the result in the project Preview."),
            "answer_questions": ("Нужно уточнение. Ответьте на вопросы текущего задания в этом чате.",
                                 "Clarification is needed. Answer the current task questions in this chat."),
            "resume_validated": ("Результат сохранён. Для повторной проверки без новой реализации отправьте «Продолжи».",
                                 "The result is retained. Send Continue to resume validation without a new implementation."),
            "inspect_failure": ("Работа остановилась. Причина сохранена в состоянии задания; результат не объявлен готовым.",
                                "Work stopped. The reason is retained in task state; the result is not accepted."),
            "inspect_state": ("Состояние пока неизвестно. Обновите состояние текущего задания.",
                              "The state is unknown. Refresh the current task state."),
        }
        reply["message"] = messages[next_action][1 if locale == "en" else 0]
    query = urlencode({"object_type": object_type, "object_id": object_id, "details": "true"})
    reply["evidence"] = {
        "full_state_url": "/api/builder/automation/status?" + query,
        "diagnostics": {"operation": "adaos.sdk.builder.automation.get_diagnostics",
                        "arguments": {"object_type": object_type, "object_id": object_id, "page_size": 50}},
        "access": "existing authenticated Builder access; no new public artifact",
        "retained_by": "Builder session and immutable task evidence owners",
    }
    reply["shortened_fields"] = shortened
    # The schema is bounded by field count and scalar lengths. Fail explicitly
    # if future additions break that contract instead of returning a huge blob.
    if len(json.dumps(reply, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_BYTES:
        raise ValueError("builder_control_reply_exceeds_wire_budget")
    return reply
