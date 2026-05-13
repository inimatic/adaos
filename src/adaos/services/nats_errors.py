from __future__ import annotations

import logging
from collections.abc import Iterator


_TRANSIENT_TYPE_NAMES = {
    "ClientConnectionResetError",
    "ConnectionAbortedError",
    "ConnectionClosedError",
    "ConnectionClosedOK",
    "ConnectionResetError",
    "ErrStaleConnection",
    "IncompleteReadError",
    "TimeoutError",
    "UnexpectedEOF",
}

_TRANSIENT_TEXT_MARKERS = (
    "cannot write to closing transport",
    "clientconnectionreseterror",
    "connection reset",
    "connection aborted",
    "connectionclosed",
    "forcibly closed",
    "keepalive ping timeout",
    "no close frame received or sent",
    "semaphore timeout",
    "unexpected eof",
    "winerror 64",
    "winerror 121",
    "\u043f\u0440\u0435\u0432\u044b\u0448\u0435\u043d \u0442\u0430\u0439\u043c\u0430\u0443\u0442 \u0441\u0435\u043c\u0430\u0444\u043e\u0440\u0430",
)

_TRANSIENT_WINERRORS = {64, 121, 995, 10053, 10054, 10060}
_TRANSIENT_ERRNOS = {104, 110, 111, 113}


def iter_exception_chain(exc: BaseException | None) -> Iterator[BaseException]:
    """Yield an exception with its causal/context chain without looping forever."""
    current = exc
    seen: set[int] = set()
    while current is not None:
        marker = id(current)
        if marker in seen:
            return
        seen.add(marker)
        yield current
        next_exc = current.__cause__
        if next_exc is None and not getattr(current, "__suppress_context__", False):
            next_exc = current.__context__
        current = next_exc


def is_transient_nats_error(exc: BaseException | None) -> bool:
    """Return true for transport-level NATS drops that should reconnect quietly."""
    for item in iter_exception_chain(exc):
        type_name = type(item).__name__
        if type_name in _TRANSIENT_TYPE_NAMES:
            return True
        if isinstance(item, (TimeoutError, ConnectionError)):
            return True
        if isinstance(item, OSError):
            winerror = getattr(item, "winerror", None)
            errno = getattr(item, "errno", None)
            if winerror in _TRANSIENT_WINERRORS or errno in _TRANSIENT_ERRNOS:
                return True
        text = str(item).strip().lower()
        if text and any(marker in text for marker in _TRANSIENT_TEXT_MARKERS):
            return True
    return False


def nats_error_summary(exc: BaseException | None, *, max_parts: int = 3) -> str:
    parts: list[str] = []
    for item in iter_exception_chain(exc):
        text = str(item).strip()
        if text:
            parts.append(f"{type(item).__name__}: {text}")
        else:
            parts.append(type(item).__name__)
        if len(parts) >= max_parts:
            break
    return " <- ".join(parts) if parts else "unknown"


class SuppressTransientNatsTracebackFilter(logging.Filter):
    """Hide nats-py tracebacks for known transient transport drops.

    The AdaOS supervisor still logs a concise reconnect event. This filter only
    suppresses the raw library traceback for the specific `nats: encountered
    error` record, so unexpected protocol/auth/runtime errors remain visible.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(getattr(record, "msg", "") or "")
        if "nats: encountered error" not in message:
            return True
        exc_info = getattr(record, "exc_info", None)
        exc = exc_info[1] if isinstance(exc_info, tuple) and len(exc_info) >= 2 else None
        if isinstance(exc, BaseException) and is_transient_nats_error(exc):
            return False
        return True


def install_transient_nats_log_filter(logger: logging.Logger | str = "nats.aio.client") -> None:
    target = logging.getLogger(logger) if isinstance(logger, str) else logger
    for existing in target.filters:
        if isinstance(existing, SuppressTransientNatsTracebackFilter):
            return
    target.addFilter(SuppressTransientNatsTracebackFilter())


__all__ = [
    "SuppressTransientNatsTracebackFilter",
    "install_transient_nats_log_filter",
    "is_transient_nats_error",
    "iter_exception_chain",
    "nats_error_summary",
]
