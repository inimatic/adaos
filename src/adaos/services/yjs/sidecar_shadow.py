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
from dataclasses import dataclass
from typing import Any

from adaos.services.yjs.store import add_ystore_write_observer

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
_MAX_SNAPSHOT_BYTES = _env_int("ADAOS_YJS_SIDECAR_SHADOW_MAX_SNAPSHOT_BYTES", 64 << 20)
_QUEUE_LIMIT = _env_int("ADAOS_YJS_SIDECAR_SHADOW_QUEUE_LIMIT", 256)
_TIMEOUT_S = _env_float("ADAOS_YJS_SIDECAR_SHADOW_TIMEOUT_S", 2.0)
_RUNTIME_EPOCH = str(os.getenv("ADAOS_RUNTIME_INSTANCE_ID") or f"python-{uuid.uuid4().hex}").strip()
try:
    _CONTROL_URL = _control_url()
    _CONFIGURATION_ERROR = ""
except Exception as exc:
    _CONTROL_URL = ""
    _CONFIGURATION_ERROR = f"{type(exc).__name__}:{exc}"


@dataclass(frozen=True)
class _MirrorItem:
    webspace_id: str
    update: bytes
    sequence: int
    seed_required: bool = False


_queue: queue_module.Queue[_MirrorItem] | None = None
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_stop_event = threading.Event()
_remove_listener = None
_sequence: dict[str, int] = defaultdict(int)
_seeded_webspaces: set[str] = set()
_seeding_webspaces: set[str] = set()
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
    "seed_enqueued_total": 0,
    "seed_accepted_total": 0,
    "last_error": _CONFIGURATION_ERROR or None,
}


def _refresh_configuration() -> None:
    global _CONFIGURATION_ERROR, _CONTROL_URL, _ENABLED, _MAX_SNAPSHOT_BYTES, _MAX_UPDATE_BYTES
    global _QUEUE_LIMIT, _TIMEOUT_S, _TOKEN, _WEBSPACES

    enabled = _env_flag("ADAOS_YJS_SIDECAR_SHADOW_ENABLE", False)
    webspaces = _webspaces()
    token = str(os.getenv("ADAOS_TOKEN") or "").strip()
    max_update_bytes = _env_int("ADAOS_YJS_SIDECAR_SHADOW_MAX_UPDATE_BYTES", 8 << 20)
    max_snapshot_bytes = _env_int("ADAOS_YJS_SIDECAR_SHADOW_MAX_SNAPSHOT_BYTES", 64 << 20)
    queue_limit = _env_int("ADAOS_YJS_SIDECAR_SHADOW_QUEUE_LIMIT", 256)
    timeout_s = _env_float("ADAOS_YJS_SIDECAR_SHADOW_TIMEOUT_S", 2.0)
    try:
        control_url = _control_url()
        configuration_error = ""
    except Exception as exc:
        control_url = ""
        configuration_error = f"{type(exc).__name__}:{exc}"

    _ENABLED = enabled
    _WEBSPACES = webspaces
    _TOKEN = token
    _MAX_UPDATE_BYTES = max_update_bytes
    _MAX_SNAPSHOT_BYTES = max_snapshot_bytes
    _QUEUE_LIMIT = queue_limit
    _TIMEOUT_S = timeout_s
    _CONTROL_URL = control_url
    _CONFIGURATION_ERROR = configuration_error
    _record(
        enabled=enabled,
        last_error=configuration_error or _status.get("last_error"),
    )


def _record(**values: Any) -> None:
    with _status_lock:
        _status.update(values)


def sidecar_shadow_mirror_status() -> dict[str, Any]:
    with _status_lock:
        result = dict(_status)
    result["queue_depth"] = _queue.qsize() if _queue is not None else 0
    result["allowed_webspaces"] = sorted(_WEBSPACES)
    result["listener_registered"] = _remove_listener is not None
    result["worker_alive"] = bool(_worker is not None and _worker.is_alive())
    result["token_configured"] = bool(_TOKEN)
    result["max_update_bytes"] = _MAX_UPDATE_BYTES
    result["max_snapshot_bytes"] = _MAX_SNAPSHOT_BYTES
    result["queue_limit"] = _QUEUE_LIMIT
    result["timeout_s"] = _TIMEOUT_S
    return result


def _admitted(webspace_id: str, update: bytes) -> bool:
    if not _ENABLED or not _TOKEN or _CONFIGURATION_ERROR:
        return False
    if webspace_id not in _WEBSPACES and "*" not in _WEBSPACES:
        return False
    return bool(update) and len(update) <= _MAX_UPDATE_BYTES


def _ensure_worker() -> queue_module.Queue[_MirrorItem]:
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
        seed_required = key not in _seeded_webspaces and key not in _seeding_webspaces
        try:
            queue.put_nowait(_MirrorItem(key, payload, sequence, seed_required))
            if seed_required:
                _seeding_webspaces.add(key)
            _status["enqueued_total"] = int(_status["enqueued_total"]) + 1
            if seed_required:
                _status["seed_enqueued_total"] = int(_status["seed_enqueued_total"]) + 1
        except queue_module.Full:
            if seed_required:
                _seeding_webspaces.discard(key)
            _status["dropped_total"] = int(_status["dropped_total"]) + 1
            _status["last_error"] = "mirror_queue_full"


def _seed_updates(webspace_id: str) -> tuple[bytes, ...]:
    from adaos.services.yjs.store import ystore_updates_for_shadow  # pylint: disable=import-outside-toplevel

    return ystore_updates_for_shadow(webspace_id)


def _encode_seed(updates: tuple[bytes, ...]) -> bytes:
    from adaos.services.yjs.store import encode_ystore_shadow_seed  # pylint: disable=import-outside-toplevel

    return encode_ystore_shadow_seed(updates)


def _mirror_worker(
    queue: queue_module.Queue[_MirrorItem],
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set() or not queue.empty():
        try:
            item = queue.get(timeout=0.1)
        except queue_module.Empty:
            continue
        try:
            with _status_lock:
                seed_required = item.webspace_id not in _seeded_webspaces
            seed_updates = _seed_updates(item.webspace_id) if seed_required else ()
            mode = "seed" if seed_updates else "update"
            update = _encode_seed(seed_updates) if seed_updates else item.update
            if mode == "seed" and len(update) > _MAX_SNAPSHOT_BYTES:
                raise ValueError("shadow_seed_too_large")
            if mode == "seed":
                _post_update(item.webspace_id, update, item.sequence, mode=mode)
            else:
                _post_update(item.webspace_id, update, item.sequence)
            with _status_lock:
                if seed_required:
                    _seeded_webspaces.add(item.webspace_id)
                    _seeding_webspaces.discard(item.webspace_id)
                _status["accepted_total"] = int(_status["accepted_total"]) + 1
                if mode == "seed":
                    _status["seed_accepted_total"] = int(_status["seed_accepted_total"]) + 1
                _status["last_error"] = None
                _status["last_accepted_webspace"] = item.webspace_id
                _status["last_accepted_sequence"] = item.sequence
        except Exception as exc:
            with _status_lock:
                _seeded_webspaces.discard(item.webspace_id)
                _seeding_webspaces.discard(item.webspace_id)
                _status["failed_total"] = int(_status["failed_total"]) + 1
                _status["last_error"] = f"{type(exc).__name__}:{exc}"
            _log.warning(
                "Yjs sidecar shadow mirror failed webspace=%s sequence=%s error=%s",
                item.webspace_id,
                item.sequence,
                type(exc).__name__,
            )
        finally:
            queue.task_done()


def _post_update(webspace_id: str, update: bytes, sequence: int, *, mode: str = "update") -> None:
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
            "X-AdaOS-Yjs-Mode": mode,
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
    _refresh_configuration()
    if not _ENABLED:
        return False
    if not _WEBSPACES or not _TOKEN or _CONFIGURATION_ERROR:
        _record(last_error=_CONFIGURATION_ERROR or "missing_shadow_admission_config")
        _log.warning("Yjs sidecar shadow mirror disabled by incomplete admission config")
        return False
    _remove_listener = add_ystore_write_observer(_mirror_write_listener)
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
    with _status_lock:
        _seeded_webspaces.clear()
        _seeding_webspaces.clear()
        _sequence.clear()


register_sidecar_shadow_mirror()
