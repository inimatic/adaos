from __future__ import annotations
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Optional

from adaos.services.skill.context import SkillContextService
from adaos.ports.skill_context import CurrentSkill

from adaos.sdk.core._ctx import require_ctx


def _service() -> SkillContextService:
    ctx = require_ctx("sdk.data.context")
    return SkillContextService(ctx)


def set_current_skill(name: str) -> bool:
    return _service().set_current_skill(name)


def clear_current_skill() -> None:
    _service().clear_current_skill()


def get_current_skill() -> Optional[CurrentSkill]:
    return _service().get_current_skill()


@contextmanager
def use_current_skill(name: str) -> Iterator[bool]:
    """Bind a skill for one scope and restore any outer binding on exit."""

    service = _service()
    previous = service.get_current_skill()
    if previous is not None and previous.name == str(name or "").strip():
        yield True
        return

    pushed = service.set_current_skill(name)
    try:
        yield pushed
    finally:
        if pushed:
            service.restore_current_skill(previous)
