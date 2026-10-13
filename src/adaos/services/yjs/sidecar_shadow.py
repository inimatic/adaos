from __future__ import annotations

import asyncio
import json
import logging
import os
import queue as queue_module
import threading
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from typing import Any

from adaos.services.yjs.store import add_ystore_write_listener

_log = logging.getLogger("adaos.yjs.sidecar_shadow")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(str(os.getenv(name) or default).strip()))
    except (TypeError, ValueError):
        return max(minimum, default)


def _env_float(name: str, default: float, *, minimum: float = 0.1) -> float:
    try:
        return max(minimum, float(str(os.getenv(name) or default).strip()))
    except (TypeError, ValueError):
        return max(minimum, default)


def _webspaces() -> set[str]:
    raw = str(os.getenv("ADAOS_YJS_SIDECAR_SHADOW_WEBSPACES") or "")
    return {value.strip() for value in raw.replace(";", ",").split(",") if value.strip()}


def _control_url() -> str:
    value = str(
        os.getenv("ADAOS_YJS_SIDECAR_SHADOW_CONTROL_URL")
        or "http://127.0.0.1:7426/v1/yjs-shadow/update"
    ).strip()
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Yjs shadow control URL must use loopback HTTP")
    if parsed.path != "/v1/yjs-shadow/update" or parsed.username or parsed.password:
        raise ValueError("invalid Yjs shadow control URL")
    return value


_ENABLED = _env_flag("ADAOS_YJS_SIDECAR_SHADOW_ENABLE", False)
_WEBSPACES = _webspaces()
_TOKEN = str(os.getenv("ADAOS_TOKEN") or "").strip()
_MAX_UPDATE_BYTES = _env_int("ADAOS_YJS_SIDECAR_SHADOW_MAX_UPDATE_BYTES", 8 << 20)
_QUEUE_LIMIT = _env_int("ADAOS_YJS_SIDECAR_SHADOW_QUEUE_LIMIT", 256)
_TIMEOUT_S = _env_float("ADAOS_YJS_SIDECAR_SHADOW_TIMEOUT_S", 2.0)
_RUNTIME_EPOCH = str(os.getenv("ADAOS_RUNTIME_INSTANCE_ID") or f"python-{uuid.uuid4().hex}").strip()
try:
    _CONTROL_URL = _control_url()
    _CONFIGURATION_ERROR = ""
except Exception as exc:
    _CONTROL_URL = ""
    _CONFIGURATION_ERROR = f"{type(exc).__name__}:{exc}"

_queue: queue_module.Queue[tuple[str, bytes, int]] | None = None
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_stop_event = threading.Event()
_remove_listener = None
_sequence: dict[str, int] = defaultdict(int)
_status_lock = threading.Lock()
_status: dict[str, Any] = {
    "schema": "adaos.yjs.sidecar_shadow_bridge.v1",
    "enabled": _ENABLED,
    "authority": "python",
    "runtime_epoch": _RUNTIME_EPOCH,
    "enqueued_total": 0,
    "accepted_total": 0,
    "dropped_total": 0,
    "failed_total": 0,
    "last_error": _CONFIGURATION_ERROR or None,
}


def _record(**values: Any) -> None:
    with _status_lock:
        _status.update(values)


def sidecar_shadow_mirror_status() -> dict[str, Any]:
    with _status_lock:
        result = dict(_status)
    result["queue_depth"] = _queue.qsize() if _queue is not None else 0
    result["allowed_webspaces"] = sorted(_WEBSPACES)
    return result


def _admitted(webspace_id: str, update: bytes) -> bool:
    if not _ENABLED or not _TOKEN or _CONFIGURATION_ERROR:
        return False
    if webspace_id not in _WEBSPACES and "*" not in _WEBSPACES:
        return False
    return bool(update) and len(update) <= _MAX_UPDATE_BYTES


def _ensure_worker() -> queue_module.Queue[tuple[str, bytes, int]]:
    global _queue, _worker
    with _worker_lock:
        if _queue is None:
            _queue = queue_module.Queue(maxsize=_QUEUE_LIMIT)
        if _worker is None or not _worker.is_alive():
            _stop_event.clear()
            _worker = threading.Thread(
                target=_mirror_worker,
                args=(_queue, _stop_event),
                name="yjs-sidecar-shadow",
                daemon=True,
            )
            _worker.start()
        return _queue


def _mirror_write_listener(
    webspace_id: str,
    update: bytes,
    _metadata: dict[str, Any] | None = None,
) -> None:
    key = str(webspace_id or "").strip() or "default"
    payload = bytes(update or b"")
    if not _admitted(key, payload):
        return
    queue = _ensure_worker()
    with _status_lock:
        _sequence[key] += 1
        sequence = _sequence[key]
    try:
        queue.put_nowait((key, payload, sequence))
        with _status_lock:
            _status["enqueued_total"] = int(_status["enqueued_total"]) + 1
    except queue_module.Full:
        with _status_lock:
            _status["dropped_total"] = int(_status["dropped_total"]) + 1
            _status["last_error"] = "mirror_queue_full"


def _mirror_worker(
    queue: queue_module.Queue[tuple[str, bytes, int]],
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set() or not queue.empty():
        try:
            webspace_id, update, sequence = queue.get(timeout=0.1)
        except queue_module.Empty:
            continue
        try:
            _post_update(webspace_id, update, sequence)
            with _status_lock:
                _status["accepted_total"] = int(_status["accepted_total"]) + 1
                _status["last_error"] = None
                _status["last_accepted_webspace"] = webspace_id
                _status["last_accepted_sequence"] = sequence
        except Exception as exc:
            with _status_lock:
                _status["failed_total"] = int(_status["failed_total"]) + 1
                _status["last_error"] = f"{type(exc).__name__}:{exc}"
            _log.warning(
                "Yjs sidecar shadow mirror failed webspace=%s sequence=%s error=%s",
                webspace_id,
                sequence,
                type(exc).__name__,
            )
        finally:
            queue.task_done()


def _post_update(webspace_id: str, update: bytes, sequence: int) -> None:
    request = urllib.request.Request(
        _CONTROL_URL,
        data=update,
        method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            "X-AdaOS-Token": _TOKEN,
            "X-AdaOS-Webspace-ID": webspace_id,
            "X-AdaOS-Runtime-Epoch": _RUNTIME_EPOCH,
            "X-AdaOS-Yjs-Sequence": str(sequence),
        },
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310
        raw = response.read(1 << 20)
        if int(response.status) != 202:
            raise RuntimeError(f"unexpected_status:{response.status}")
    payload = json.loads(raw or b"{}")
    if payload.get("accepted") is not True:
        raise RuntimeError("shadow_update_not_accepted")


async def wait_for_sidecar_shadow_idle(timeout_s: float = 5.0) -> None:
    queue = _queue
    if queue is None:
        return
    await asyncio.wait_for(
        asyncio.to_thread(queue.join),
        timeout=max(0.1, float(timeout_s)),
    )


def register_sidecar_shadow_mirror() -> bool:
    global _remove_listener
    if _remove_listener is not None:
        return True
    if not _ENABLED:
        return False
    if not _WEBSPACES or not _TOKEN or _CONFIGURATION_ERROR:
        _record(last_error=_CONFIGURATION_ERROR or "missing_shadow_admission_config")
        _log.warning("Yjs sidecar shadow mirror disabled by incomplete admission config")
        return False
    _remove_listener = add_ystore_write_listener(_mirror_write_listener)
    return True


def unregister_sidecar_shadow_mirror() -> None:
    global _queue, _remove_listener, _worker
    remove = _remove_listener
    _remove_listener = None
    if callable(remove):
        remove()
    _stop_event.set()
    worker = _worker
    if worker is not None and worker is not threading.current_thread():
        worker.join(timeout=_TIMEOUT_S + 0.25)
    with _worker_lock:
        if _worker is worker and (worker is None or not worker.is_alive()):
            _worker = None
            _queue = None


register_sidecar_shadow_mirror()
