from __future__ import annotations

from typing import Any, Mapping

from adaos.services.operational_errors import normalized_error_code


MEMBER_RPC_ERROR_SCHEMA = "adaos.subnet.member_rpc_error.v1"


def rpc_error_code(value: Any, *, fallback: str = "rpc_failed") -> str:
    explicit = getattr(value, "code", None)
    if explicit:
        code = normalized_error_code(explicit, fallback="")
        if code:
            return code

    text = str(value or "").strip()
    if text:
        segments = [candidate.strip() for candidate in text.split(":")]
        if not isinstance(value, BaseException) and segments:
            legacy_type = segments[0].lower()
            if legacy_type.endswith(("error", "exception")):
                segments = segments[1:] or segments
        for candidate in segments:
            code = normalized_error_code(candidate.strip(), fallback="")
            if code:
                return code
        code = normalized_error_code(text, fallback="")
        if code:
            return code
    return normalized_error_code(fallback, fallback="rpc_failed")


def member_rpc_error_payload(exc: BaseException) -> dict[str, str]:
    return {
        "schema": MEMBER_RPC_ERROR_SCHEMA,
        "code": rpc_error_code(exc),
        "type": type(exc).__name__,
    }


class RemoteMemberRpcError(RuntimeError):
    def __init__(self, code: str, *, remote_type: str = "") -> None:
        self.code = rpc_error_code(code)
        self.remote_type = normalized_error_code(remote_type, fallback="")
        super().__init__(self.code)


def remote_member_rpc_error(value: Any) -> RemoteMemberRpcError:
    if isinstance(value, Mapping):
        return RemoteMemberRpcError(
            rpc_error_code(value.get("code")),
            remote_type=str(value.get("type") or ""),
        )
    return RemoteMemberRpcError(rpc_error_code(value))
