"""Safe, localized refusal text; model/provider exception prose is not UI policy."""

from collections.abc import Mapping
import re


def interpretation_refusal_kind(findings):
    rows = [
        row
        for row in (findings if isinstance(findings, (list, tuple)) else ())
        if isinstance(row, Mapping)
    ]
    if any(
        row.get("gate") == "G0" and row.get("owner") == "source_admission"
        for row in rows
    ):
        return "source"
    if any(
        row.get("gate") in ("G5", "G6")
        and row.get("owner") in ("context_compilation", "context_delivery")
        for row in rows
    ):
        return "context"
    if any(
        row.get("gate") == "G4" and row.get("owner") == "capability_admission"
        for row in rows
    ):
        return "capability"
    if any(row.get("code") == "task.clarification_required" for row in rows):
        return "clarification"
    if any(row.get('code') in {'task.source_record_polarity_unresolved',
        'task.source_operation_polarity_unresolved'} for row in rows):
        return 'source_semantics'
    if any(
        row.get("gate") == "G3" and row.get("owner") == "task_interpretation"
        for row in rows
    ):
        return "interpretation"
    return "unknown"


def execution_evidence_failure_text(*, locale="en"):
    return (
        "Core не смог надёжно привязать свидетельства к исходному Change. Прототип не изменён; "
        "сохранённый вызов LLM нельзя повторять или относить к другому Change."
        if str(locale).lower().startswith("ru")
        else "Core could not safely bind execution evidence to the originating Change. The Prototype is unchanged; "
        "retained inference must not be repeated or attributed to another Change."
    )


def interpretation_provider_failure_text(rejection, *, locale="en", status=None):
    """Explain only typed provider evidence, never raw exception prose."""
    from adaos.services.ai.provider_rejection import safe_rejection

    evidence = safe_rejection(rejection)
    kind = (evidence or {}).get("kind")
    russian = str(locale).lower().startswith("ru")
    explanations = {
        "schema": (
            "Провайдер отклонил схему структурированного ответа. Требуется исправление контракта в Core; менять требования приложения не нужно.",
            "The provider rejected the structured-output schema. Core contract correction is required; do not change application requirements.",
        ),
        "model": (
            "Выбранная модель недоступна у провайдера. Выберите доступную модель ChatGPT.",
            "The selected model is unavailable from the provider. Select an available ChatGPT model.",
        ),
        "authentication": (
            "Требуется восстановить подключение аккаунта.",
            "The account connection must be restored.",
        ),
        "account_scope": (
            "Требуется проверить разрешения подключения аккаунта.",
            "The account connection permissions must be checked.",
        ),
        "quota": (
            "Провайдер сообщил об ограничении доступного ресурса аккаунта.",
            "The provider reported an account allowance limit.",
        ),
    }
    if status == "cancelled":
        explanation = (
            "Интерпретация отменена." if russian else "Interpretation was cancelled."
        )
    elif status == "interrupted":
        explanation = (
            "Выполнение интерпретации прервано. Проверьте сохранённое задание."
            if russian
            else "Interpretation was interrupted. Inspect the retained attempt."
        )
    else:
        explanation = explanations.get(
            kind,
            (
                "Провайдер не завершил стадию интерпретации; причина требует проверки сохранённого задания.",
                "The provider did not complete interpretation; inspect the retained attempt to establish the cause.",
            ),
        )[0 if russian else 1]
    suffix = (
        " Прототип не изменён. Задание сохранено; автоматического повтора и переключения на API нет."
        if russian
        else " The Prototype is unchanged. The attempt is retained; there is no automatic retry or API fallback."
    )
    return explanation + suffix


def interpretation_refusal_text(result, *, locale="en"):
    # The rolling native async handler retains the full typed refusal under
    # llm_job and copies only its error/detail onto the submit wrapper. Unwrap
    # this one known envelope, never arbitrary nested prose or another error.
    if isinstance(result, Mapping) and result.get("error") in {
        "prototype_task_provider_failed",
        "prototype_task_interpretation_rejected",
        "prototype_execution_evidence_unavailable",
    }:
        child = result.get("llm_job")
        if isinstance(child, Mapping) and child.get("error") == result["error"]:
            result = child
    if (
        isinstance(result, Mapping)
        and result.get("error") == "prototype_execution_evidence_unavailable"
    ):
        return execution_evidence_failure_text(locale=locale)
    if (
        isinstance(result, Mapping)
        and result.get("error") == "prototype_task_provider_failed"
    ):
        return interpretation_provider_failure_text(
            result.get("provider_rejection"),
            locale=locale,
            status=result.get("provider_status"),
        )
    if (
        not isinstance(result, Mapping)
        or result.get("error") != "prototype_task_interpretation_rejected"
    ):
        return None
    findings = result.get("findings")
    findings = findings if isinstance(findings, (list, tuple)) else ()
    codes = sorted(
        {
            row["code"]
            for row in findings
            if isinstance(row, Mapping)
            and isinstance(row.get("code"), str)
            and re.fullmatch(r"task\.[a-z_]{1,64}", row["code"])
        }
    )[:12]
    russian = str(locale).lower().startswith("ru")
    kind = interpretation_refusal_kind(result.get("findings", []))
    if kind in {"source", "context", "capability"}:
        explanation = {
            "source": (
                "Исходные данные и выбранный Project/Change не согласованы.",
                "The source and selected Project/Change disagree.",
            ),
            "context": (
                "Core остановил сборку или допуск контекста.",
                "Core stopped context composition or admission.",
            ),
            "capability": (
                "Для задачи не подтверждена требуемая возможность платформы.",
                "A required platform capability is not admitted for this task.",
            ),
        }[kind][0 if russian else 1]
        text = explanation + (
            " Прототип не изменён; это не основание исправлять ответ модели. Автоматического повторного запроса нет."
            if russian
            else " The Prototype is unchanged; this does not authorize model-response repair. No automatic resubmission was made."
        )
    elif kind == 'source_semantics':
        text = (
            'Core распознал отрицательный контекст, но текущий разбор не может надёжно определить '
            'назначение данных или направление операции. Превращать их в положительные факты или действия нельзя. Это ограничение '
            'допуска смысла, а не отсутствие указанных вами значений. Прототип не изменён; '
            'автоматического повторного запроса нет.'
            if russian else
            'Core recognized a negative context, but the current source grammar cannot safely resolve '
            'the data meaning or operation direction. It cannot become positive facts or actions. This is a source-meaning '
            'admission limit, not missing supplied values. The Prototype is unchanged; no automatic retry was made.'
        )
    elif kind == "clarification":
        text = (
            "До генерации нужно уточнить существенные требования. Прототип не изменён."
            if russian
            else "Material requirements need clarification before generation. The Prototype is unchanged."
        )
    else:
        text = (
            "Проверка интерпретации остановила подготовку. Прототип не изменён; "
            "проверьте коды диагностики. Автоматического повторного запроса нет."
            if russian
            else "Interpretation admission stopped preparation. The Prototype is unchanged; "
            "inspect the diagnostic codes. No automatic resubmission was made."
        )
    if codes:
        text += (
            "\n" + ("Диагностика: " if russian else "Diagnostics: ") + ", ".join(codes)
        )
    return text
