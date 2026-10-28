"""Phase timings for management reads, independent of result caching."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import logging
import time


_log = logging.getLogger("adaos.applications.reads")
_phases: ContextVar[dict[str, float] | None] = ContextVar("application_read_phases", default=None)


@contextmanager
def read_phase(name: str):
    phases = _phases.get()
    started = time.perf_counter()
    try:
        yield
    finally:
        if phases is not None:
            phases[name] = round(phases.get(name, 0.0) + (time.perf_counter() - started) * 1000, 3)


def observe_read(operation: str):
    """Log cold work as well as warm work; never record inputs or payloads."""
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            phases: dict[str, float] = {}
            token = _phases.set(phases)
            started = time.perf_counter()
            failed = True
            try:
                result = function(*args, **kwargs)
                failed = False
                return result
            finally:
                elapsed = (time.perf_counter() - started) * 1000
                _phases.reset(token)
                emit = _log.info if elapsed >= 100 else _log.debug
                emit("application management read operation=%s total_ms=%.3f failed=%s phases_ms=%s",
                     operation, elapsed, failed, phases)
        return measured
    return decorate
