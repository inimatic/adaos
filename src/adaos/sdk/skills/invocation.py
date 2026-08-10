"""Bounded invocation of a public tool exported by another installed skill."""

from __future__ import annotations

from contextvars import ContextVar
import re
from typing import Any, Mapping

from adaos.sdk.core._ctx import require_ctx
from adaos.services.policy.skill_capabilities import require_skill_capability


_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_INVOCATION_DEPTH: ContextVar[int] = ContextVar("adaos_skill_invocation_depth", default=0)
_MAX_INVOCATION_DEPTH = 4


def _token(value: str, *, field: str) -> str:
    token = str(value or "").strip()
    if not _TOKEN.fullmatch(token):
        raise ValueError(f"{field} must be a valid AdaOS identifier")
    return token


def invoke(
    skill_id: str,
    operation_id: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    timeout: float | None = None,
) -> Any:
    """Invoke one public operation through the active skill runtime.

    The caller must declare ``skills.invoke``. The target still passes the
    normal runtime, activation, tool-schema, capability and timeout checks;
    this helper does not import another skill's Python package or expose its
    private data root.
    """

    ctx = require_ctx("sdk.skills.invoke")
    require_skill_capability(ctx, "skills.invoke")
    target_skill = _token(skill_id, field="skill_id")
    target_operation = _token(operation_id, field="operation_id")
    payload = dict(arguments or {})
    depth = _INVOCATION_DEPTH.get()
    if depth >= _MAX_INVOCATION_DEPTH:
        raise RuntimeError(f"nested skill invocation exceeds {_MAX_INVOCATION_DEPTH} levels")

    from adaos.services.skill.manager import SkillManager

    manager = SkillManager(
        repo=ctx.skills_repo,
        registry=None,
        git=ctx.git,
        paths=ctx.paths,
        bus=ctx.bus,
        caps=ctx.caps,
        settings=ctx.settings,
    )
    marker = _INVOCATION_DEPTH.set(depth + 1)
    try:
        return manager.run_tool(
            target_skill,
            target_operation,
            payload,
            timeout=timeout,
        )
    finally:
        _INVOCATION_DEPTH.reset(marker)


__all__ = ["invoke"]
