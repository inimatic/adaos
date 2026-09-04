"""Public typed access to the current skill's Resource Workbench records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from adaos.sdk.core import SdkRuntimeNotInitialized
from adaos.sdk.data.context import get_current_skill
from adaos.services.resources.workbench import ResourceWorkbenchService


def _identity() -> tuple[str, dict[str, str]]:
    current = get_current_skill()
    skill_name = str(getattr(current, "name", "") or "").strip()
    if not skill_name:
        raise SdkRuntimeNotInitialized("sdk.resources", "current skill is not set")
    return skill_name, {"id": f"skill:{skill_name}", "role": "member"}


def _owned_type(resource_type: str) -> tuple[str, dict[str, str]]:
    skill_name, actor = _identity()
    token = str(resource_type or "").strip()
    if not token.startswith(f"skill.{skill_name}."):
        raise PermissionError(f"resource_type is not owned by current skill: {token}")
    return token, actor


def definition(resource_type: str) -> dict[str, Any] | None:
    """Return one resource declaration owned by the current skill."""

    token, _actor = _owned_type(resource_type)
    return ResourceWorkbenchService().definition(token)


def query(
    resource_type: str,
    *,
    filters: Mapping[str, Any] | None = None,
    search: str = "",
    sort: Sequence[Any] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Query one current-skill resource without exposing actor escalation."""

    token, actor = _owned_type(resource_type)
    request: dict[str, Any] = {
        "schema": "adaos.resource.query.v1",
        "resource_type": token,
        "filters": dict(filters or {}),
        "search": str(search or ""),
        "sort": list(sort or []),
        "actor": actor,
    }
    if limit is not None:
        request["limit"] = int(limit)
    return ResourceWorkbenchService().query(request)


def operate(
    resource_type: str,
    operation_id: str,
    *,
    record_id: str = "",
    payload: Mapping[str, Any] | None = None,
    expected_revision: int | str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Invoke one declared operation for a current-skill resource."""

    token, actor = _owned_type(resource_type)
    request: dict[str, Any] = {
        "schema": "adaos.resource.operation.v1",
        "resource_type": token,
        "operation_id": str(operation_id or "").strip(),
        "record_id": str(record_id or "").strip(),
        "payload": dict(payload or {}),
        "actor": actor,
    }
    if expected_revision is not None:
        request["expected_revision"] = expected_revision
    if idempotency_key:
        request["idempotency_key"] = str(idempotency_key)
    return ResourceWorkbenchService().operate(request)


__all__ = ["definition", "operate", "query"]
