from __future__ import annotations
import json
import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
import os
from pathlib import Path
import queue
import threading
import time
from typing import Any, Callable, Optional
from datetime import datetime, timezone

from adaos.domain import Event
from adaos.ports.paths import PathProvider
from adaos.ports import EventBus


_ACTIVE_QUEUE_HANDLER: NonBlockingQueueHandler | None = None
_ACTIVE_QUEUE_LOCK = threading.RLock()
_ORIGINAL_LOGGER_ADD_HANDLER = logging.Logger.addHandler
_DIRECT_HANDLER_REDIRECT_TOTAL = 0
_RECENT_DIRECT_HANDLER_REDIRECTS: list[dict[str, object]] = []


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
    captured_exception = getattr(record, "adaos_exception", None)
    if isinstance(captured_exception, dict):
        base["exception"] = captured_exception
    elif record.exc_info:
        base["exception"] = logging.Formatter().formatException(record.exc_info)
    if record.stack_info:
        base["stack"] = str(record.stack_info)
    return json.dumps(base, ensure_ascii=False)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _json_formatter(record)


def _safe_exception_payload(exc_info: tuple[object, object, object] | None) -> dict[str, object] | None:
    if not exc_info or len(exc_info) != 3:
        return None
    exc_type, exc, traceback_obj = exc_info
    frames: list[dict[str, object]] = []
    current = traceback_obj
    while current is not None:
        frame = getattr(current, "tb_frame", None)
        code = getattr(frame, "f_code", None)
        frames.append(
            {
                "filename": str(getattr(code, "co_filename", "") or ""),
                "lineno": int(getattr(current, "tb_lineno", 0) or 0),
                "function": str(getattr(code, "co_name", "") or ""),
            }
        )
        current = getattr(current, "tb_next", None)
    return {
        "type": str(getattr(exc_type, "__name__", "") or type(exc).__name__),
        "module": str(getattr(exc_type, "__module__", "") or ""),
        "message": str(exc),
        "frames": frames[-40:],
    }


def _safe_log_value(value: object, *, depth: int = 0) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth >= 4:
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _safe_log_value(item, depth=depth + 1)
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_log_value(item, depth=depth + 1) for item in list(value)[:100]]
    return str(value)


class SkillContextCaptureFilter(logging.Filter):
    """Capture contextvars before a record crosses into the listener thread."""

    def filter(self, record: logging.LogRecord) -> bool:
        current = _current_skill_context()
        name = str(getattr(current, "name", "") or "").strip() if current is not None else ""
        runtime_log_path = getattr(current, "runtime_log_path", None) if current is not None else None
        record.adaos_skill_name = name
        record.adaos_skill_runtime_log_path = str(runtime_log_path or "")
        return True


class ResilientQueueListener(QueueListener):
    """Keep draining when an individual output handler fails."""

    def __init__(
        self,
        log_queue: queue.Queue[logging.LogRecord],
        *handlers: logging.Handler,
        respect_handler_level: bool,
        on_error: Callable[[logging.Handler, Exception], None],
    ) -> None:
        super().__init__(log_queue, *handlers, respect_handler_level=respect_handler_level)
        self._on_error = on_error

    def handle(self, record: logging.LogRecord) -> None:
        for handler in self.handlers:
            if self.respect_handler_level and record.levelno < handler.level:
                continue
            try:
                handler.handle(record)
            except Exception as exc:
                self._on_error(handler, exc)


class NonBlockingQueueHandler(QueueHandler):
    """Bounded, observable logging handoff that never performs output I/O."""

    def __init__(self, log_queue: queue.Queue[logging.LogRecord], *, level: int) -> None:
        super().__init__(log_queue)
        self.setLevel(level)
        self.addFilter(SkillContextCaptureFilter())
        self._metrics_lock = threading.Lock()
        self._enqueued_total = 0
        self._dropped_total = 0
        self._dropped_by_level: dict[str, int] = {}
        self._high_watermark = 0
        self._last_drop_at: float | None = None
        self._listener: QueueListener | None = None
        self._listener_lock = threading.Lock()
        self._listener_restart_total = 0
        self._listener_failure_total = 0
        self._last_listener_failure: dict[str, object] | None = None
        self._output_handlers: tuple[logging.Handler, ...] = ()
        self._pipeline_closed = False
        self._pipeline_closed_at: float | None = None

    def bind_listener(self, listener: QueueListener, handlers: list[logging.Handler]) -> None:
        self._listener = listener
        self._output_handlers = tuple(handlers)

    def record_listener_failure(self, handler: logging.Handler, exc: Exception) -> None:
        with self._metrics_lock:
            self._listener_failure_total += 1
            self._last_listener_failure = {
                "at": time.time(),
                "handler": type(handler).__name__,
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            }

    def _ensure_listener(self) -> None:
        if self._pipeline_closed:
            return
        listener = self._listener
        if listener is None:
            return
        thread = getattr(listener, "_thread", None)
        if thread is not None and thread.is_alive():
            return
        with self._listener_lock:
            if self._pipeline_closed:
                return
            thread = getattr(listener, "_thread", None)
            if thread is not None and thread.is_alive():
                return
            try:
                listener.start()
            except Exception as exc:
                self.record_listener_failure(self, exc)
                return
            with self._metrics_lock:
                self._listener_restart_total += 1

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        # Never transfer traceback frames, args, or arbitrary custom objects
        # into the listener thread. y_py objects are thread-affine and may be
        # referenced by either record args or traceback locals. Capturing only
        # primitive frame coordinates also avoids linecache disk I/O here.
        message = record.getMessage()
        prepared = logging.LogRecord(
            record.name,
            record.levelno,
            record.pathname,
            record.lineno,
            message,
            (),
            None,
            record.funcName,
            record.stack_info,
        )
        prepared.created = record.created
        prepared.msecs = record.msecs
        prepared.relativeCreated = record.relativeCreated
        prepared.thread = record.thread
        prepared.threadName = record.threadName
        prepared.process = record.process
        prepared.processName = record.processName
        prepared.adaos_skill_name = str(getattr(record, "adaos_skill_name", "") or "")
        prepared.adaos_skill_runtime_log_path = str(
            getattr(record, "adaos_skill_runtime_log_path", "") or ""
        )
        prepared.adaos_exception = _safe_exception_payload(record.exc_info)
        if hasattr(record, "extra"):
            prepared.extra = _safe_log_value(getattr(record, "extra", {}))
        return prepared

    def enqueue(self, record: logging.LogRecord) -> None:
        self._ensure_listener()
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            with self._metrics_lock:
                self._dropped_total += 1
                level = str(record.levelname or "UNKNOWN")
                self._dropped_by_level[level] = self._dropped_by_level.get(level, 0) + 1
                self._last_drop_at = time.time()
            return
        with self._metrics_lock:
            self._enqueued_total += 1
            self._high_watermark = max(self._high_watermark, self.queue.qsize())

    def flush(self) -> None:
        deadline = time.monotonic() + 5.0
        while int(getattr(self.queue, "unfinished_tasks", 0) or 0) > 0 and time.monotonic() < deadline:
            thread = getattr(self._listener, "_thread", None)
            if thread is not None and not thread.is_alive():
                break
            time.sleep(0.005)

    def snapshot(self) -> dict[str, object]:
        with self._metrics_lock:
            enqueued_total = self._enqueued_total
            dropped_total = self._dropped_total
            dropped_by_level = dict(self._dropped_by_level)
            high_watermark = self._high_watermark
            last_drop_at = self._last_drop_at
            listener_restart_total = self._listener_restart_total
            listener_failure_total = self._listener_failure_total
            last_listener_failure = dict(self._last_listener_failure or {}) or None
            pipeline_closed_at = self._pipeline_closed_at
        thread = getattr(self._listener, "_thread", None)
        return {
            "schema": "adaos.logging.queue.v1",
            "configured": True,
            "capacity": int(self.queue.maxsize or 0),
            "queued": self.queue.qsize(),
            "high_watermark": high_watermark,
            "enqueued_total": enqueued_total,
            "dropped_total": dropped_total,
            "dropped_by_level": dropped_by_level,
            "last_drop_at": last_drop_at,
            "listener_alive": bool(thread is not None and thread.is_alive()),
            "listener_restart_total": listener_restart_total,
            "listener_failure_total": listener_failure_total,
            "last_listener_failure": last_listener_failure,
            "pipeline_closed": self._pipeline_closed,
            "pipeline_closed_at": pipeline_closed_at,
        }

    def close(self) -> None:
        if self._pipeline_closed:
            return
        self._pipeline_closed = True
        with self._metrics_lock:
            self._pipeline_closed_at = datetime.now(tz=timezone.utc).timestamp()
        self.flush()
        listener = self._listener
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        for handler in self._output_handlers:
            try:
                handler.close()
            except Exception:
                pass
        super().close()


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
        if str(record.name or "").startswith("adaos.scenario."):
            return False
        captured = str(getattr(record, "adaos_skill_name", "") or "").strip()
        if captured:
            return False
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
        skill_name = str(getattr(record, "adaos_skill_name", "") or "").strip()
        if not skill_name and current is not None:
            skill_name = str(getattr(current, "name", "") or "").strip()
        if not skill_name:
            return
        try:
            path = self._resolve_path(record, current, skill_name=skill_name)
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

    def _resolve_path(self, record: logging.LogRecord, current: object | None, *, skill_name: str) -> Path:
        explicit = str(getattr(record, "adaos_skill_runtime_log_path", "") or "").strip()
        if not explicit and current is not None:
            explicit = str(getattr(current, "runtime_log_path", "") or "").strip()
        if explicit:
            return Path(explicit)
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


class ScenarioLogRouter(logging.Handler):
    """Write dedicated scenario logs from the shared listener thread."""

    def __init__(
        self,
        paths: PathProvider,
        *,
        level: int,
        max_bytes: int = 5_000_000,
        backup_count: int = 3,
    ) -> None:
        super().__init__(level=level)
        self._logs_dir = Path(paths.logs_dir()) / "scenarios"
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._handlers: dict[str, RotatingFileHandler] = {}
        self._configs: dict[str, tuple[int, int]] = {}
        self.setFormatter(JsonFormatter())

    def configure(self, scenario_id: str, *, max_bytes: int, backup_count: int) -> None:
        safe_id = scenario_id.replace("/", "_").replace("\\", "_")
        config = (max(1, int(max_bytes)), max(0, int(backup_count)))
        if self._configs.get(safe_id) == config:
            return
        self._configs[safe_id] = config
        handler = self._handlers.pop(safe_id, None)
        if handler is not None:
            handler.close()

    def emit(self, record: logging.LogRecord) -> None:
        prefix = "adaos.scenario."
        logger_name = str(record.name or "")
        if not logger_name.startswith(prefix):
            return
        scenario_id = logger_name[len(prefix) :].strip()
        if not scenario_id:
            return
        try:
            handler = self._handler_for(scenario_id)
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

    def _handler_for(self, scenario_id: str) -> RotatingFileHandler:
        safe_id = scenario_id.replace("/", "_").replace("\\", "_")
        handler = self._handlers.get(safe_id)
        if handler is not None:
            return handler
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        max_bytes, backup_count = self._configs.get(
            safe_id,
            (self._max_bytes, self._backup_count),
        )
        handler = TolerantRotatingFileHandler(
            self._logs_dir / f"{safe_id}.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setLevel(self.level)
        handler.setFormatter(self.formatter or JsonFormatter())
        self._handlers[safe_id] = handler
        return handler


def _logging_queue_capacity() -> int:
    try:
        value = int(str(os.getenv("ADAOS_LOG_QUEUE_CAPACITY") or "4096").strip())
    except Exception:
        value = 4096
    return max(128, min(value, 65_536))


def _detach_queue_handler(handler: NonBlockingQueueHandler) -> None:
    manager = logging.Logger.manager
    loggers: list[logging.Logger] = [logging.getLogger()]
    for candidate in manager.loggerDict.values():
        if isinstance(candidate, logging.Logger):
            loggers.append(candidate)
    for logger in loggers:
        logger.handlers[:] = [item for item in logger.handlers if item is not handler]


def _all_loggers() -> list[logging.Logger]:
    loggers: list[logging.Logger] = [logging.getLogger()]
    for candidate in logging.Logger.manager.loggerDict.values():
        if isinstance(candidate, logging.Logger):
            loggers.append(candidate)
    return loggers


def _is_nonblocking_logger_handler(
    item: logging.Handler,
    active: NonBlockingQueueHandler | None,
) -> bool:
    return item is active or isinstance(item, (NonBlockingQueueHandler, logging.NullHandler))


def _record_direct_handler_redirect(logger: logging.Logger, handler: logging.Handler) -> None:
    global _DIRECT_HANDLER_REDIRECT_TOTAL
    _DIRECT_HANDLER_REDIRECT_TOTAL += 1
    _RECENT_DIRECT_HANDLER_REDIRECTS.append(
        {
            "at": time.time(),
            "logger": str(logger.name or "root"),
            "handler": type(handler).__name__,
        }
    )
    del _RECENT_DIRECT_HANDLER_REDIRECTS[:-20]


def _protected_logger_add_handler(self: logging.Logger, handler: logging.Handler) -> None:
    """Prevent runtime code from installing output I/O on caller threads."""
    with _ACTIVE_QUEUE_LOCK:
        active = _ACTIVE_QUEUE_HANDLER
        if active is None or _is_nonblocking_logger_handler(handler, active):
            _ORIGINAL_LOGGER_ADD_HANDLER(self, handler)
            return
        retained = [item for item in self.handlers if _is_nonblocking_logger_handler(item, active)]
        if active not in retained:
            retained.append(active)
        removed = [item for item in self.handlers if item not in retained]
        self.handlers[:] = retained
        self.propagate = False
        _record_direct_handler_redirect(self, handler)
    for item in [*removed, handler]:
        try:
            item.close()
        except Exception:
            pass


def _install_nonblocking_handler_guard() -> None:
    if logging.Logger.addHandler is not _protected_logger_add_handler:
        logging.Logger.addHandler = _protected_logger_add_handler


def _route_existing_output_handlers_through_queue(handler: NonBlockingQueueHandler) -> None:
    for logger in _all_loggers():
        direct_handlers = [
            item
            for item in logger.handlers
            if item is not handler and not isinstance(item, logging.NullHandler)
        ]
        if not direct_handlers:
            continue
        logger.handlers[:] = [
            item for item in logger.handlers if _is_nonblocking_logger_handler(item, handler)
        ]
        if handler not in logger.handlers:
            logger.handlers.append(handler)
        logger.propagate = False
        for item in direct_handlers:
            _record_direct_handler_redirect(logger, item)
            try:
                item.close()
            except Exception:
                pass


def _unsafe_direct_logging_handlers(handler: NonBlockingQueueHandler | None) -> list[dict[str, str]]:
    unsafe: list[dict[str, str]] = []
    for logger in _all_loggers():
        for item in logger.handlers:
            if item is handler or isinstance(item, (NonBlockingQueueHandler, logging.NullHandler)):
                continue
            unsafe.append(
                {
                    "logger": str(logger.name or "root"),
                    "handler": type(item).__name__,
                }
            )
    return unsafe[:100]


def logging_queue_snapshot() -> dict[str, object]:
    with _ACTIVE_QUEUE_LOCK:
        handler = _ACTIVE_QUEUE_HANDLER
        if handler is None:
            return {
                "schema": "adaos.logging.queue.v1",
                "configured": False,
                "capacity": 0,
                "queued": 0,
                "high_watermark": 0,
                "enqueued_total": 0,
                "dropped_total": 0,
                "dropped_by_level": {},
                "last_drop_at": None,
                "listener_alive": False,
                "listener_restart_total": 0,
                "listener_failure_total": 0,
                "last_listener_failure": None,
                "pipeline_closed": False,
                "pipeline_closed_at": None,
                "redirected_direct_handler_total": _DIRECT_HANDLER_REDIRECT_TOTAL,
                "recent_direct_handler_redirects": list(_RECENT_DIRECT_HANDLER_REDIRECTS),
                "unsafe_direct_handlers": _unsafe_direct_logging_handlers(None),
            }
        snapshot = handler.snapshot()
        snapshot["redirected_direct_handler_total"] = _DIRECT_HANDLER_REDIRECT_TOTAL
        snapshot["recent_direct_handler_redirects"] = list(_RECENT_DIRECT_HANDLER_REDIRECTS)
        snapshot["unsafe_direct_handlers"] = _unsafe_direct_logging_handlers(handler)
        return snapshot


def configure_nonblocking_logger(logger_name: str, *, level: int | None = None) -> bool:
    """Route a logger through the process-owned nonblocking output queue."""
    name = str(logger_name or "").strip()
    if not name:
        return False
    with _ACTIVE_QUEUE_LOCK:
        handler = _ACTIVE_QUEUE_HANDLER
        if handler is None:
            return False
        logger = logging.getLogger(name)
        logger.handlers[:] = [handler]
        logger.setLevel(handler.level if level is None else level)
        logger.propagate = False
        return True


def configure_skill_module_logging(module_name: str) -> None:
    """Route a synthetic skill module through the protected logging queue."""
    configure_nonblocking_logger(module_name)


def configure_scenario_logging(
    logger_name: str,
    scenario_id: str,
    *,
    level: int,
    max_bytes: int,
    backup_count: int,
) -> bool:
    with _ACTIVE_QUEUE_LOCK:
        handler = _ACTIVE_QUEUE_HANDLER
        if handler is None:
            return False
        router = next(
            (item for item in handler._output_handlers if isinstance(item, ScenarioLogRouter)),
            None,
        )
        if router is None:
            return False
        router.configure(scenario_id, max_bytes=max_bytes, backup_count=backup_count)
        return configure_nonblocking_logger(logger_name, level=level)


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

    global _ACTIVE_QUEUE_HANDLER

    logger = logging.getLogger("adaos")
    resolved_level = (os.getenv("ADAOS_LOG_LEVEL") or level or "INFO").upper()
    logger.setLevel(getattr(logging, resolved_level, logging.INFO))

    with _ACTIVE_QUEUE_LOCK:
        previous_queue_handler = _ACTIVE_QUEUE_HANDLER
        _ACTIVE_QUEUE_HANDLER = None
        if previous_queue_handler is not None:
            _detach_queue_handler(previous_queue_handler)
            previous_queue_handler.close()
        for existing in list(logger.handlers):
            try:
                existing.close()
            except Exception:
                pass
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
    else:
        skill_h = None

    output_handlers: list[logging.Handler] = [stream_h, file_h]
    if skill_h is not None:
        output_handlers.append(skill_h)
    output_handlers.append(ScenarioLogRouter(paths, level=logger.level))

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=_logging_queue_capacity())
    queue_handler = NonBlockingQueueHandler(log_queue, level=logger.level)
    listener = ResilientQueueListener(
        log_queue,
        *output_handlers,
        respect_handler_level=True,
        on_error=queue_handler.record_listener_failure,
    )
    queue_handler.bind_listener(listener, output_handlers)
    listener.start()

    logger.addHandler(queue_handler)
    logger.propagate = False

    _route_existing_output_handlers_through_queue(queue_handler)

    # Normal imports use skills.*, while subscription and runtime loaders use
    # synthetic module names. Parent loggers cover the former; loaders bind the
    # latter explicitly through configure_skill_module_logging().
    for namespace in ("skills", "_adaos_runtime"):
        skill_logger = logging.getLogger(namespace)
        skill_logger.handlers[:] = [queue_handler]
        skill_logger.setLevel(logger.level)
        skill_logger.propagate = False

    root_logger = logging.getLogger()
    root_logger.setLevel(logger.level)
    root_logger.handlers[:] = [queue_handler]
    root_logger.propagate = False

    with _ACTIVE_QUEUE_LOCK:
        _ACTIVE_QUEUE_HANDLER = queue_handler
        _install_nonblocking_handler_guard()

    # Optional noise suppression (apply to handlers so it affects all child loggers).
    try:
        rules = _parse_hide_rules()
        if rules:
            flt = PrefixMinLevelFilter(rules)
            for handler in output_handlers:
                handler.addFilter(flt)
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

    def _diagnostic_fields(ev: Event) -> dict[str, Any]:
        if str(getattr(ev, "type", "") or "") != "skill.service.issue":
            return {}
        payload = getattr(ev, "payload", None)
        if not isinstance(payload, dict):
            return {}
        issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
        message = str(issue.get("message") or "").strip()
        return {
            "skill": str(payload.get("skill") or "").strip() or None,
            "issue_id": str(issue.get("id") or "").strip() or None,
            "issue_type": str(issue.get("type") or "").strip() or None,
            "issue_severity": str(issue.get("severity") or "").strip() or None,
            "issue_message": message[:512] or None,
        }

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
                    **_diagnostic_fields(ev),
                }
            },
        )

    bus.subscribe("", _handler)
