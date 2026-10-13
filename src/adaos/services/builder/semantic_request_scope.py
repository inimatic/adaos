"""Caller-selected project fence propagated into native Builder execution."""
from contextlib import contextmanager
from contextvars import ContextVar

_TARGET = ContextVar('builder_semantic_request_target', default=None)


@contextmanager
def request_target(project_ref):
    token = _TARGET.set(project_ref)
    try:
        yield
    finally:
        _TARGET.reset(token)


def current_target():
    return _TARGET.get()
