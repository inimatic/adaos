from __future__ import annotations
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from adaos.domain import Event
from adaos.ports.paths import PathProvider
from adaos.ports import EventBus


def _json_formatter(record: logging.LogRecord) -> str:
    # `record.asctime` is only populated when a base Formatter runs `formatTime()`.
    # Since we generate JSON directly, compute timestamps ourselves.
    try:
        ts = float(getattr(record, "created", 0.0) or 0.0)
    except Exception:
        ts = 0.0
    iso = None
    try:
        if ts:
            iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        iso = None
    base = {
        "level": record.levelname,
        "logger": record.name,
        "msg": record.getMessage(),
        "time": iso,
        "ts": ts or None,
    }
    if hasattr(record, "extra"):
        try:
            base.update(record.extra)  # type: ignore[attr-defined]
        except Exception:
            pass
    return json.dumps(base, ensure_ascii=False)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _json_formatter(record)


class TolerantRotatingFileHandler(RotatingFileHandler):
    """Keep logging alive when Windows briefly locks a log during rollover."""

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self.stream is None:
            self.stream = self._open()
        if self.maxBytes <= 0:
            return False
        try:
            pos = self.stream.tell()
            if not pos:
                return False
            msg = f"{self.format(record)}\n"
            return pos + len(msg) >= self.maxBytes
        except OSError as exc:
            if _is_rollover_lock_error(exc):
                return False
            raise

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError as exc:
            if not _is_rollover_lock_error(exc):
                raise
            # On Windows another process can hold adaos.log/adaos.log.1 open
            # while RotatingFileHandler tries to rename it. Reopen the current
            # file so the pending record is still written; the next rollover
            # attempt can retry once the lock is gone.
            if self.stream is None:
                self.stream = self._open()


def _is_rollover_lock_error(exc: OSError) -> bool:
    if isinstance(exc, PermissionError):
        return True
    try:
        return int(getattr(exc, "winerror", 0) or 0) == 32
    except Exception:
        return False


def _parse_log_level(name: str | None, *, default: int) -> int:
    if not name:
        return default
    try:
        raw = str(name).strip().upper()
    except Exception:
        return default
    if not raw:
        return default
    if raw == "WARN":
        raw = "WARNING"
    if raw.isdigit():
        try:
            return int(raw)
        except Exception:
            return default
    try:
        v = getattr(logging, raw)
    except Exception:
        return default
    if isinstance(v, int):
        return v
    return default


def _parse_hide_rules() -> list[tuple[str, int]]:
    """
    Hide chatty loggers without changing global log level.

    Env:
    - ADAOS_LOG_HIDE: comma-separated rules:
        * `prefix` -> hide below ADAOS_LOG_HIDE_LEVEL
        * `prefix=LEVEL` / `prefix:LEVEL` -> hide below LEVEL for that prefix
    - ADAOS_LOG_HIDE_LEVEL: default level for rules without explicit LEVEL (default: WARNING)
    """
    raw = os.getenv("ADAOS_LOG_HIDE", "") or ""
    try:
        s = str(raw).strip()
    except Exception:
        s = ""
    if not s:
        return []
    default_level = _parse_log_level(os.getenv("ADAOS_LOG_HIDE_LEVEL", "WARNING"), default=logging.WARNING)
    rules: list[tuple[str, int]] = []
    for token in s.split(","):
        try:
            item = str(token).strip()
        except Exception:
            continue
        if not item:
            continue
        sep = "=" if "=" in item else (":" if ":" in item else None)
        if sep:
            prefix, lvl = item.split(sep, 1)
            prefix = prefix.strip()
            min_level = _parse_log_level(lvl, default=default_level)
        else:
            prefix = item.strip()
            min_level = default_level
        if not prefix:
            continue
        rules.append((prefix, int(min_level)))
    return rules


class PrefixMinLevelFilter(logging.Filter):
    def __init__(self, rules: list[tuple[str, int]]):
        super().__init__()
        self._rules = [(p, int(lvl)) for (p, lvl) in rules if p]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            name = record.name
            level = record.levelno
        except Exception:
            return True
        for prefix, min_level in self._rules:
            if name.startswith(prefix):
                return level >= min_level
        return True


def _current_skill_context():
    try:
        from adaos.services.agent_context import get_ctx  # pylint: disable=import-outside-toplevel

        skill_ctx = getattr(get_ctx(), "skill_ctx", None)
        if skill_ctx is None:
            return None
        return skill_ctx.get()
    except Exception:
        return None


class SuppressSkillContextFilter(logging.Filter):
    """Keep skill-scoped records out of the platform-wide adaos.log handlers."""

    def filter(self, record: logging.LogRecord) -> bool:
        return _current_skill_context() is None


class SkillContextLogRouter(logging.Handler):
    """Mirror adaos.* records emitted inside a skill context into that skill log."""

    def __init__(
        self,
        paths: PathProvider,
        *,
        level: int,
        max_bytes: int = 5_000_000,
        backup_count: int = 3,
    ) -> None:
        super().__init__(level=level)
        self._paths = paths
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._handlers: dict[Path, RotatingFileHandler] = {}
        self.setFormatter(JsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        current = _current_skill_context()
        skill_name = str(getattr(current, "name", "") or "").strip() if current is not None else ""
        if not skill_name:
            return
        try:
            path = self._resolve_path(current)
            handler = self._handler_for(path)
            handler.handle(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        for handler in list(self._handlers.values()):
            try:
                handler.close()
            except Exception:
                pass
        self._handlers.clear()
        super().close()

    def _resolve_path(self, current: object) -> Path:
        explicit = getattr(current, "runtime_log_path", None)
        if explicit:
            return Path(explicit)
        skill_name = str(getattr(current, "name", "") or "").strip()
        fn = getattr(self._paths, "skill_runtime_log_path", None)
        if callable(fn):
            return Path(fn(skill_name))
        return Path(self._paths.logs_dir()) / f"service.{skill_name}.runtime.log"

    def _handler_for(self, path: Path) -> RotatingFileHandler:
        resolved = path.resolve()
        handler = self._handlers.get(resolved)
        if handler is not None:
            return handler
        resolved.parent.mkdir(parents=True, exist_ok=True)
        handler = TolerantRotatingFileHandler(
            resolved,
            maxBytes=self._max_bytes,
            backupCount=self._backup_count,
            encoding="utf-8",
        )
        handler.setLevel(self.level)
        handler.setFormatter(self.formatter or JsonFormatter())
        self._handlers[resolved] = handler
        return handler


def setup_logging(paths: PathProvider, level: str = "INFO") -> logging.Logger:
    """
    Настройка логов:
      - консоль (stderr)
      - файл {logs_dir}/adaos.log (ротация)
    JSON формат, чтобы легко парсить.
    """
    logs_dir = Path(paths.logs_dir())
    logs_dir.mkdir(parents=True, exist_ok=True)
    logfile = logs_dir / "adaos.log"

    logger = logging.getLogger("adaos")
    resolved_level = (os.getenv("ADAOS_LOG_LEVEL") or level or "INFO").upper()
    logger.setLevel(getattr(logging, resolved_level, logging.INFO))
    logger.handlers.clear()

    stream_h = logging.StreamHandler()
    stream_h.setFormatter(JsonFormatter())
    stream_h.setLevel(logger.level)

    file_h = TolerantRotatingFileHandler(logfile, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_h.setFormatter(JsonFormatter())
    file_h.setLevel(logger.level)

    if str(os.getenv("ADAOS_LOG_ROUTE_SKILL_CONTEXT", "1") or "1").strip() != "0":
        skill_filter = SuppressSkillContextFilter()
        stream_h.addFilter(skill_filter)
        file_h.addFilter(skill_filter)
        skill_h = SkillContextLogRouter(paths, level=logger.level)
        logger.addHandler(skill_h)

    logger.addHandler(stream_h)
    logger.addHandler(file_h)
    logger.propagate = False

    # Optional noise suppression (apply to handlers so it affects all child loggers).
    try:
        rules = _parse_hide_rules()
        if rules:
            flt = PrefixMinLevelFilter(rules)
            stream_h.addFilter(flt)
            file_h.addFilter(flt)
    except Exception:
        pass
    # logger.info("logging.initialized", extra={"extra": {"logfile": str(logfile)}})
    return logger


def attach_event_logger(bus: EventBus, logger: Optional[logging.Logger] = None) -> None:
    """
    Подписывает логгер на все события шины.
    """
    try:
        if str(os.getenv("ADAOS_LOG_EVENTS", "1") or "1").strip() == "0":
            return
    except Exception:
        pass
    base_logger = logger or logging.getLogger("adaos.events")
    try:
        include_payload = str(os.getenv("ADAOS_LOG_EVENTS_PAYLOAD", "0") or "0").strip() != "0"
    except Exception:
        include_payload = False

    def _handler(ev: Event) -> None:
        iso_time = datetime.fromtimestamp(getattr(ev, "ts", 0), tz=timezone.utc).isoformat() if getattr(ev, "ts", None) else None
        payload = ev.payload if include_payload else None
        base_logger.info(
            "event",
            extra={
                "extra": {
                    "time": iso_time,
                    "type": ev.type,
                    "source": ev.source,
                    "ts": ev.ts,
                    "payload": payload,
                }
            },
        )

    bus.subscribe("", _handler)
