"""Typed local settings for the active Application's owned skill.

Requires manifest configuration.schema/defaults and configuration.read/write.
DEV uses a separate synthetic store. Credential resolution is a separate contract.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from adaos.sdk.core._ctx import require_ctx


def _service():
    from adaos.services.applications.runtime_configuration import ApplicationRuntimeConfiguration
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return ApplicationRuntimeConfiguration(require_ctx("sdk.data.configuration"))
    raise RuntimeError("Configuration performs local I/O; use a_read/a_write from async handlers")


def read() -> dict[str, Any]:
    """Return {revision, values} for declared settings of the selected production
    Application skill, or isolated DEV defaults/overrides. Requires
    configuration.read. Unprepared Beta is rejected; no production values or
    credential refs are copied into DEV.
    """
    return _service().read()


def write(values: Mapping[str, Any], *, expected_revision: int) -> dict[str, Any]:
    """Replace typed non-secret values at the revision returned by read().
    Requires configuration.write; stale revisions and schemas fail, never reset.
    Credential bindings are preserved and cannot be supplied by this call.
    """
    return _service().write(values, expected_revision=expected_revision)


async def a_read() -> dict[str, Any]:
    """Async read() retaining active skill/caller context outside the event loop."""
    return await asyncio.to_thread(read)


async def a_write(values: Mapping[str, Any], *, expected_revision: int) -> dict[str, Any]:
    """Async write() retaining active skill/caller context outside the event loop."""
    return await asyncio.to_thread(write, values, expected_revision=expected_revision)


__all__ = ["read", "write", "a_read", "a_write"]
