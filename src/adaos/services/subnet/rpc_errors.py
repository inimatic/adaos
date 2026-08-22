from __future__ import annotations

from typing import Any, Mapping

from adaos.services.operational_errors import normalized_error_code


MEMBER_RPC_ERROR_SCHEMA = "adaos.subnet.member_rpc_error.v1"

_EXCEPTION_TYPE_CODES: tuple[tuple[type[BaseException], str], ...] = (
    (ModuleNotFoundError, "module_not_found"),
    (ImportError, "import_failed"),
    (FileNotFoundError, "file_not_found"),
    (PermissionError, "permission_denied"),
    (TimeoutError, "operation_timeout"),
    (KeyError, "key_error"),
    (TypeError, "type_error"),
    (ValueError, "value_error"),
    (RuntimeError, "runtime_error"),
)


def _exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(current)
        next_exc = current.__cause__
        if next_exc is None and not current.__suppress_context__:
            next_exc = current.__context__
        current = next_exc
    return tuple(chain)


def _direct_error_code(value: Any) -> str:
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
    return ""


def rpc_error_code(value: Any, *, fallback: str = "rpc_failed") -> str:
    chain = _exception_chain(value) if isinstance(value, BaseException) else ()
    for candidate in chain or (value,):
        code = _direct_error_code(candidate)
        if code:
            return code
    for candidate in reversed(chain):
        for error_type, code in _EXCEPTION_TYPE_CODES:
            if isinstance(candidate, error_type):
                return code
    return normalized_error_code(fallback, fallback="rpc_failed")


def member_rpc_error_payload(exc: BaseException) -> dict[str, str]:
    chain = _exception_chain(exc)
    payload = {
        "schema": MEMBER_RPC_ERROR_SCHEMA,
        "code": rpc_error_code(exc),
        "type": type(exc).__name__,
    }
    if len(chain) > 1:
        payload["cause_type"] = type(chain[-1]).__name__
    return payload


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
