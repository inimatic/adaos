"""Minimal console output adapter used by the sandbox scenario runner."""

from __future__ import annotations

import os
import sys


def _write_stdout(text: str) -> None:
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        # Windows service stdout can retain a legacy code page. Escape only
        # unsupported console characters; other notification routes keep text.
        encoding = sys.stdout.encoding or "utf-8"
        sys.stdout.write(text.encode(encoding, errors="backslashreplace").decode(encoding))
    sys.stdout.flush()


def print(text: str | None) -> dict[str, bool]:
    """Write ``text`` to stdout and flush immediately."""

    if text is None:
        return {"ok": True}
    if os.getenv("ADAOS_TESTING"):
        return {"ok": True}
    _write_stdout(f"{text.rstrip()}\n")
    return {"ok": True}


def print_text(text: str, node_id: str | None = None, origin: dict | None = None) -> None:
    prefix = f"[IO/console@{node_id}]" if node_id else "[IO/console]"
    _write_stdout(f"{prefix} {text.rstrip()}\n")


__all__ = ["print", "print_text"]
