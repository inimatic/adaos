"""Verified data-profile context for a Development Preview tool call.

The Core binds this context after resolving the active Builder profile.  Skills
may inspect the public contract, while SDK storage helpers consume the private
owner-scoped root.  Application arguments cannot create or replace this
binding.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator, Mapping


_PREVIEW_DATA_BINDING: ContextVar[dict[str, Any] | None] = ContextVar(
    "adaos_verified_preview_data_binding", default=None
)


def preview_data_binding() -> dict[str, Any] | None:
    """Return the active verified Preview profile without physical paths."""

    value = _PREVIEW_DATA_BINDING.get()
    if value is None:
        return None
    return copy.deepcopy(
        {
            key: item
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    )


def _preview_data_root_path() -> Path | None:
    value = _PREVIEW_DATA_BINDING.get()
    root = str((value or {}).get("_data_root") or "").strip()
    return Path(root).expanduser().resolve() if root else None


@contextmanager
def _bind_preview_data_binding(
    value: Mapping[str, Any] | None,
) -> Iterator[None]:
    marker = _PREVIEW_DATA_BINDING.set(
        copy.deepcopy(dict(value)) if isinstance(value, Mapping) else None
    )
    try:
        yield
    finally:
        _PREVIEW_DATA_BINDING.reset(marker)


__all__ = ["preview_data_binding"]
