from __future__ import annotations

from typing import Any, Mapping


class RootMcpToolError(ValueError):
    """A stable, caller-safe failure raised by a Root MCP tool handler."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "invalid_request").strip() or "invalid_request"
        self.details = dict(details or {})
