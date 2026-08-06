"""Localized, semantic presentation for the Builder control surface.

Command identity is deliberately separate from translated labels.  The
surface catalog is small and deterministic; package authors can replace the
presentation later without changing workflow commands or action tokens.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


BUILDER_SURFACE_CATALOG_VERSION = 1
BUILDER_SURFACE_DEFAULT_LOCALE = "en"
BUILDER_SURFACE_LOCALES = ("en", "ru")

_ACTION_LABELS: dict[str, dict[str, str]] = {
    "builder.process.inspect": {"en": "Show process", "ru": "Показать процесс"},
    "builder.change.plan": {"en": "Refine project", "ru": "Доработать проект"},
    "builder.change.extend": {"en": "Add to change", "ru": "Дополнить изменение"},
    "builder.prototype.edit": {"en": "Refine prototype", "ru": "Доработать прототип"},
    "builder.prototype.approve": {"en": "Approve prototype", "ru": "Согласовать прототип"},
    "builder.implementation.start": {"en": "Start implementation", "ru": "Начать автоматизацию"},
    "builder.implementation.iterate": {"en": "Continue implementation", "ru": "Доработать автоматизацию"},
    "builder.prototype.derive": {"en": "Return result to prototype", "ru": "Вернуть результат в прототип"},
    "builder.verification.accept": {"en": "Accept verification", "ru": "Принять проверку"},
    "builder.trial.prepare": {"en": "Start trial", "ru": "Начать апробацию"},
    "builder.trial.accept": {"en": "Accept trial", "ru": "Принять апробацию"},
    "builder.trial.reject": {"en": "Request changes", "ru": "Вернуть на доработку"},
    "builder.publication.publish": {"en": "Begin publication", "ru": "Начать публикацию"},
    "builder.publication.open": {"en": "Open published project", "ru": "Открыть опубликованный проект"},
    "builder.publication.place": {"en": "Place in Webspace", "ru": "Разместить в Webspace"},
    "builder.trial.open": {"en": "Open trial", "ru": "Открыть апробацию"},
    "builder.change.cancel": {"en": "Cancel change", "ru": "Отменить изменение"},
    "builder.preview.prototype": {"en": "Preview prototype", "ru": "Показать прототип"},
    "builder.preview.active": {"en": "Preview implementation", "ru": "Показать автоматизацию"},
    "builder.preview.publication": {"en": "Preview publication", "ru": "Показать публикацию"},
    "builder.project.list": {"en": "Show projects", "ru": "Показать проекты"},
    "builder.preview.link": {"en": "Preview link", "ru": "Ссылка на Preview"},
    "builder.help": {"en": "Help", "ru": "Помощь"},
}

_INPUT_PROMPTS: dict[str, dict[str, str]] = {
    "builder.change.plan": {
        "en": "Describe what should be changed. Builder will turn the request into Issues and a Change.",
        "ru": "Опишите, что нужно изменить. Строитель разложит запрос на Issues и Change.",
    },
    "builder.change.extend": {
        "en": "Describe the additional requirement for the current Change.",
        "ru": "Опишите дополнительное замечание для текущего Change.",
    },
    "builder.prototype.edit": {
        "en": "Describe the required prototype change.",
        "ru": "Опишите требуемое изменение прототипа.",
    },
    "builder.implementation.iterate": {
        "en": "Describe what should be corrected in the current implementation.",
        "ru": "Опишите, что нужно исправить в текущей реализации.",
    },
    "builder.publication.place": {
        "en": "Enter the target Workspace Webspace id for the published project.",
        "ru": "Укажите id целевого Workspace Webspace для опубликованного проекта.",
    },
}


def normalize_builder_locale(value: Any) -> str:
    token = str(value or "").strip().lower().replace("_", "-").split("-", 1)[0]
    return token if token in BUILDER_SURFACE_LOCALES else BUILDER_SURFACE_DEFAULT_LOCALE


def builder_action_label_ref(command: Any) -> str:
    token = str(command or "").strip()
    return f"builder.action.{token.removeprefix('builder.').replace('.', '_')}"


def builder_action_label(command: Any, *, locale: Any = None, fallback: Any = None) -> str:
    token = str(command or "").strip()
    selected = normalize_builder_locale(locale)
    values = _ACTION_LABELS.get(token) or {}
    return str(
        values.get(selected)
        or values.get(BUILDER_SURFACE_DEFAULT_LOCALE)
        or fallback
        or token
    ).strip()


def builder_surface_locale_context(locale: Any = None) -> dict[str, Any]:
    selected = normalize_builder_locale(locale)
    return {
        "locale": selected,
        "default_locale": BUILDER_SURFACE_DEFAULT_LOCALE,
        "fallback_chain": list(dict.fromkeys((selected, BUILDER_SURFACE_DEFAULT_LOCALE))),
        "catalog": "builder.surface",
        "catalog_version": BUILDER_SURFACE_CATALOG_VERSION,
    }


def builder_input_prompt(command: Any, *, locale: Any = None) -> str:
    token = str(command or "").strip()
    selected = normalize_builder_locale(locale)
    values = _INPUT_PROMPTS.get(token) or {}
    return str(
        values.get(selected)
        or values.get(BUILDER_SURFACE_DEFAULT_LOCALE)
        or token
    ).strip()


def localize_builder_explanation(
    explanation: Mapping[str, Any],
    *,
    locale: Any = None,
) -> str:
    selected = normalize_builder_locale(locale)
    state = str(explanation.get("state") or "ready")
    change_ref = str(explanation.get("change_ref") or "").removeprefix("change:")
    blockers = [
        str(item.get("reason_code") or "blocked")
        for item in explanation.get("blockers") or []
        if isinstance(item, Mapping)
    ]
    commands = [
        builder_action_label(f"builder.{str(item)}", locale=selected, fallback=item)
        if not str(item).startswith("builder.")
        else builder_action_label(item, locale=selected)
        for item in explanation.get("next_commands") or []
    ]
    if selected == "ru":
        if state == "published":
            title = str(explanation.get("project_title") or "Проект")
            version = str(explanation.get("published_version") or "текущая")
            placement = explanation.get("placement") if isinstance(explanation.get("placement"), Mapping) else {}
            target = placement.get("target") if isinstance(placement.get("target"), Mapping) else {}
            webspace = str(target.get("webspace_id") or "").strip()
            installed = bool(explanation.get("installed"))
            lines = [f"Версия {version} проекта «{title}» опубликована в stable."]
            lines.append("Установлена в Workspace." if installed else "Установка в Workspace не подтверждена.")
            lines.append(
                f"Размещена в Webspace {webspace}."
                if webspace
                else "Пока не размещена в Webspace."
            )
            return "\n".join(lines)
        summary = (
            f"Изменение {change_ref} находится в состоянии {state}."
            if change_ref
            else "Активного изменения нет. Опишите требуемое изменение."
        )
        reason = "; ".join(blockers[:3]) if blockers else "Активных блокировок нет."
        next_step = ", ".join(commands[:4]) if commands else "ожидать ввода или открыть процесс"
        return f"{summary} Причина: {reason} Далее: {next_step}."
    # Preserve the established English compact projection byte-for-byte.  It
    # is already the canonical diagnostic representation used by API clients.
    return str(explanation.get("text") or "No active Change.")


__all__ = [
    "BUILDER_SURFACE_CATALOG_VERSION",
    "BUILDER_SURFACE_DEFAULT_LOCALE",
    "BUILDER_SURFACE_LOCALES",
    "builder_action_label",
    "builder_action_label_ref",
    "builder_input_prompt",
    "builder_surface_locale_context",
    "localize_builder_explanation",
    "normalize_builder_locale",
]
