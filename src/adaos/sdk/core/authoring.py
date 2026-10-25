"""Capability-gated platform authoring from an isolated Application runtime.

Only explicit SDK authoring operations enter this context. Application data,
credentials, execution ports and the immutable Trial paths stay isolated.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import inspect
from typing import Any


@contextmanager
def development_context(feature: str):
    from adaos.services.agent_context import get_ctx, use_ctx
    from adaos.services.policy.caller import CallerAccessDenied

    try:
        ctx = get_ctx()
    except RuntimeError:
        # Pure metadata operations and pre-bootstrap contract tests do not
        # acquire authority merely by importing or inspecting SDK functions.
        yield None
        return
    owner = getattr(ctx, "authority_context", None)
    if owner is None:
        yield ctx
        return
    skill = ctx.skill_ctx.get()
    if skill is None:
        raise CallerAccessDenied("authoring_skill_scope_missing")
    if owner is ctx or owner.skill_ctx.get() is not skill:
        raise CallerAccessDenied("authoring_skill_scope_mismatch")
    from adaos.services.policy.skill_capabilities import require_skill_capability
    from adaos.sdk import access

    # The sealed consumer must declare the capability, and the authenticated
    # caller must hold it. Neither actor/tool arguments nor an owner filesystem
    # path are an authorization receipt. Keep the same bound SkillContext and
    # caller/Application ContextVars while entering the platform service.
    require_skill_capability(ctx, "applications.develop")
    with use_ctx(owner):
        access.require("applications.develop")
        yield owner


def _govern(function):
    if getattr(function, "__adaos_authoring_operation__", False):
        return function
    feature = f"{function.__module__}.{function.__name__}"
    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def operation(*args, **kwargs):
            with development_context(feature):
                return await function(*args, **kwargs)
    else:
        @wraps(function)
        def operation(*args, **kwargs):
            with development_context(feature):
                return function(*args, **kwargs)
    operation.__adaos_authoring_operation__ = True
    return operation


def bind_authoring_operations(module: Any, names=None, *, exclude=()):
    """Bind an explicit facade set; preserve signatures, contracts and docs."""
    for name in names if names is not None else tuple(vars(module)):
        function = getattr(module, name, None)
        if (name.startswith("_") or name in exclude or not inspect.isfunction(function)
                or function.__module__ != module.__name__):
            continue
        setattr(module, name, _govern(function))


__all__: list[str] = []
