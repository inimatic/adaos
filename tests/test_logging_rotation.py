from __future__ import annotations

import logging
import logging.handlers
from types import MethodType

from adaos.services.logging import TolerantRotatingFileHandler


def test_tolerant_rotating_file_handler_writes_when_rollover_is_locked(tmp_path):
    logfile = tmp_path / "adaos.log"
    logfile.write_text("already over limit\n", encoding="utf-8")

    handler = TolerantRotatingFileHandler(logfile, maxBytes=1, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))

    def locked_rotate(self, source, dest):  # noqa: ANN001
        raise PermissionError(32, "locked by another process", source, dest)

    handler.rotate = MethodType(locked_rotate, handler)
    try:
        record = logging.LogRecord("adaos.test", logging.INFO, __file__, 1, "survived", (), None)
        handler.handle(record)
    finally:
        handler.close()

    assert "survived" in logfile.read_text(encoding="utf-8")


def test_tolerant_rotating_file_handler_does_not_stat_path_during_rollover_check(
    tmp_path,
    monkeypatch,
):
    logfile = tmp_path / "adaos.log"
    logfile.write_text("already over limit\n", encoding="utf-8")

    handler = TolerantRotatingFileHandler(logfile, maxBytes=1, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))

    def fail_isfile(path):  # noqa: ANN001
        raise AssertionError(f"unexpected rollover stat for {path}")

    monkeypatch.setattr(logging.handlers.os.path, "isfile", fail_isfile)
    try:
        record = logging.LogRecord("adaos.test", logging.INFO, __file__, 1, "no stat", (), None)
        handler.handle(record)
    finally:
        handler.close()

    assert "no stat" in logfile.read_text(encoding="utf-8")
