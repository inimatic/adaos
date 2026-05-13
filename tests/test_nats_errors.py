from __future__ import annotations

import logging

from adaos.services.nats_errors import (
    SuppressTransientNatsTracebackFilter,
    is_transient_nats_error,
    nats_error_summary,
)


ConnectionClosedError = type("ConnectionClosedError", (Exception,), {})


def test_transient_nats_error_detects_wrapped_connection_close() -> None:
    root = RuntimeError("[hub-io] nats watchdog: task=_reading_task terminated")
    root.__cause__ = ConnectionClosedError("no close frame received or sent")

    assert is_transient_nats_error(root) is True
    assert "ConnectionClosedError: no close frame received or sent" in nats_error_summary(root)


def test_transient_nats_error_detects_windows_semaphore_timeout_text() -> None:
    err = OSError("[WinError 121] The semaphore timeout period has expired")
    localized = OSError(
        "\u041f\u0440\u0435\u0432\u044b\u0448\u0435\u043d \u0442\u0430\u0439\u043c\u0430\u0443\u0442 \u0441\u0435\u043c\u0430\u0444\u043e\u0440\u0430"
    )

    assert is_transient_nats_error(err) is True
    assert is_transient_nats_error(localized) is True


def test_transient_nats_traceback_filter_suppresses_only_known_library_noise() -> None:
    log_filter = SuppressTransientNatsTracebackFilter()
    transient = ConnectionClosedError("no close frame received or sent")
    transient_record = logging.LogRecord(
        "nats.aio.client",
        logging.ERROR,
        __file__,
        1,
        "nats: encountered error",
        (),
        None,
    )
    transient_record.exc_info = (type(transient), transient, transient.__traceback__)

    nontransient = ValueError("bad protocol state")
    nontransient_record = logging.LogRecord(
        "nats.aio.client",
        logging.ERROR,
        __file__,
        1,
        "nats: encountered error",
        (),
        None,
    )
    nontransient_record.exc_info = (type(nontransient), nontransient, nontransient.__traceback__)

    assert log_filter.filter(transient_record) is False
    assert log_filter.filter(nontransient_record) is True
