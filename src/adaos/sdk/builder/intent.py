"""Public Builder SDK operations for intent and Prototype Brief inspection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def capture(
    statement: str,
    *,
    locale: str | None = None,
    source: Mapping[str, Any] | None = None,
    project_ref: str | None = None,
    change_ref: str | None = None,
    authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from adaos.services.builder_intent import capture_intent

    return capture_intent(
        statement,
        locale=locale,
        source=source,
        project_ref=project_ref,
        change_ref=change_ref,
        authority=authority,
    )


def compile_brief(intent: Mapping[str, Any] | str) -> dict[str, Any]:
    from adaos.services.builder_intent import compile_prototype_brief

    return compile_prototype_brief(intent)


__all__ = ["capture", "compile_brief"]
