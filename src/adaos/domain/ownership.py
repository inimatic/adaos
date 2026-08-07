"""Generic owner references shared by capability bindings and attempts."""

from __future__ import annotations

import re
from typing import Any


_OWNER_REF_RE = re.compile(r"^(?:skill|core|service):[a-z0-9_.-]+$")


class OwnershipContractError(ValueError):
    """Raised when an owner reference violates the runtime ABI."""


class OwnershipIsolationError(PermissionError):
    """Raised when a caller operates on another owner's private resource."""


def validate_owner_ref(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token or not _OWNER_REF_RE.fullmatch(token):
        raise OwnershipContractError(
            "owner_ref must use skill:<id>, service:<id>, or core:<id>"
        )
    return token


__all__ = ["OwnershipContractError", "OwnershipIsolationError", "validate_owner_ref"]
