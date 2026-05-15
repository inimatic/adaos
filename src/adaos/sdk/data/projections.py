"""Shared projection runtime helpers for browser-facing skills.

The module is intentionally SDK-local and import-light. Runtime services are
resolved lazily through ctx_subnet only when a projection write is applied.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import threading
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Callable, Iterable, Mapping


BuildFn = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ProjectionSlot:
    """Declarative browser-facing Yjs projection slot."""

    name: str
    yjs_path: str | None = None
    build: BuildFn | None = None
    events: tuple[str, ...] = ()
    scope: str = "webspace"
    audience: str = "shared"


@dataclass(frozen=True, slots=True)
class StreamReceiver:
    """Declarative volatile stream receiver."""

    name: str
    build: BuildFn | None = None
    min_interval_s: float = 0.0
    audience: str = "shared"


@dataclass(frozen=True, slots=True)
class ProjectionContext:
    """Small context object passed to projection/stream builders."""

    skill_id: str
    webspace_id: str | None = None
    event_topic: str | None = None
    node_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectionWriteResult:
    """Result of one set-if-changed projection attempt."""

    skill_id: str
    slot: str
    webspace_id: str
    fingerprint: str
    written: bool
    skipped: bool
    reason: str
    force: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "slot": self.slot,
            "webspace_id": self.webspace_id,
            "fingerprint": self.fingerprint,
            "written": self.written,
            "skipped": self.skipped,
            "reason": self.reason,
            "force": self.force,
            "error": self.error,
        }


@dataclass(slots=True)
class ProjectionDiagnostics:
    applied_total: int = 0
    skipped_unchanged_total: int = 0
    throttled_total: int = 0
    pressure_blocked_total: int = 0
    errored_total: int = 0
    last_result: ProjectionWriteResult | None = None
    by_slot: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record(self, result: ProjectionWriteResult) -> None:
        self.last_result = result
        slot_state = self.by_slot.setdefault(
            result.slot,
            {
                "applied_total": 0,
                "skipped_unchanged_total": 0,
                "throttled_total": 0,
                "pressure_blocked_total": 0,
                "errored_total": 0,
                "last_webspace_id": None,
                "last_reason": None,
                "last_fingerprint": None,
            },
        )
        slot_state["last_webspace_id"] = result.webspace_id
        slot_state["last_reason"] = result.reason
        slot_state["last_fingerprint"] = result.fingerprint
        if result.error:
            self.errored_total += 1
            slot_state["errored_total"] = int(slot_state["errored_total"]) + 1
        elif result.written:
            self.applied_total += 1
            slot_state["applied_total"] = int(slot_state["applied_total"]) + 1
        elif result.skipped:
            self.skipped_unchanged_total += 1
            slot_state["skipped_unchanged_total"] = int(slot_state["skipped_unchanged_total"]) + 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "applied_total": self.applied_total,
            "skipped_unchanged_total": self.skipped_unchanged_total,
            "throttled_total": self.throttled_total,
            "pressure_blocked_total": self.pressure_blocked_total,
            "errored_total": self.errored_total,
            "last_result": self.last_result.as_dict() if self.last_result else None,
            "by_slot": json.loads(json.dumps(self.by_slot, sort_keys=True)),
        }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))

    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        try:
            raw = to_json()
            if isinstance(raw, str):
                return _json_safe(json.loads(raw))
            return _json_safe(raw)
        except Exception:
            return repr(value)

    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_json_safe(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=repr))

    items = getattr(value, "items", None)
    if callable(items):
        try:
            return {str(key): _json_safe(item) for key, item in items()}
        except Exception:
            return repr(value)

    return repr(value)


def stable_payload_fingerprint(value: Any) -> str:
    """Return a deterministic SHA-256 fingerprint for JSON-like payloads."""

    normalized = _json_safe(value)
    raw = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class DirtyRouter:
    """Maps event topics to dirty projection sections."""

    def __init__(self) -> None:
        self._routes: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def on(self, *patterns: str) -> "_DirtyRouteBuilder":
        return _DirtyRouteBuilder(self, tuple(str(pattern or "").strip() for pattern in patterns if str(pattern or "").strip()))

    def add(self, patterns: Iterable[str], sections: Iterable[str]) -> "DirtyRouter":
        clean_patterns = tuple(str(pattern or "").strip() for pattern in patterns if str(pattern or "").strip())
        clean_sections = tuple(str(section or "").strip() for section in sections if str(section or "").strip())
        if clean_patterns and clean_sections:
            self._routes.append((clean_patterns, clean_sections))
        return self

    def dirty_for(self, topic: str) -> set[str]:
        token = str(topic or "").strip()
        dirty: set[str] = set()
        for patterns, sections in self._routes:
            if any(_topic_matches(pattern, token) for pattern in patterns):
                dirty.update(sections)
        return dirty

    def snapshot(self) -> list[dict[str, list[str]]]:
        return [{"patterns": list(patterns), "sections": list(sections)} for patterns, sections in self._routes]


class _DirtyRouteBuilder:
    def __init__(self, router: DirtyRouter, patterns: tuple[str, ...]) -> None:
        self._router = router
        self._patterns = patterns

    def dirty(self, *sections: str) -> DirtyRouter:
        return self._router.add(self._patterns, sections)


def _topic_matches(pattern: str, topic: str) -> bool:
    if not pattern:
        return False
    if pattern == "*" or pattern == topic:
        return True
    if pattern.endswith("*"):
        return topic.startswith(pattern[:-1])
    return False


class ProjectionRuntime:
    """Minimal per-skill set-if-changed projection runtime."""

    def __init__(self, skill_id: str, *, ctx_subnet: Any | None = None) -> None:
        self.skill_id = str(skill_id or "unknown_skill").strip() or "unknown_skill"
        self._ctx_subnet = ctx_subnet
        self._fingerprints: dict[tuple[str, str], str] = {}
        self._diagnostics = ProjectionDiagnostics()
        self._lock = threading.RLock()

    async def set_if_changed(
        self,
        slot: ProjectionSlot | str,
        value: Any,
        *,
        webspace_id: str | None = None,
        force: bool = False,
        reason: str | None = None,
    ) -> ProjectionWriteResult:
        slot_name = _slot_name(slot)
        ws_id = str(webspace_id or "default").strip() or "default"
        fingerprint = stable_payload_fingerprint(value)
        key = (ws_id, slot_name)

        with self._lock:
            previous = self._fingerprints.get(key)
            if previous == fingerprint:
                result = ProjectionWriteResult(
                    skill_id=self.skill_id,
                    slot=slot_name,
                    webspace_id=ws_id,
                    fingerprint=fingerprint,
                    written=False,
                    skipped=True,
                    reason="unchanged",
                    force=bool(force),
                )
                self._diagnostics.record(result)
                return result

        try:
            setter = self._ctx_subnet or _default_ctx_subnet()
            await setter.set_async(slot_name, value, webspace_id=ws_id)
        except Exception as exc:
            result = ProjectionWriteResult(
                skill_id=self.skill_id,
                slot=slot_name,
                webspace_id=ws_id,
                fingerprint=fingerprint,
                written=False,
                skipped=False,
                reason=str(reason or "write_failed"),
                force=bool(force),
                error=f"{type(exc).__name__}: {exc}",
            )
            with self._lock:
                self._diagnostics.record(result)
            return result

        result = ProjectionWriteResult(
            skill_id=self.skill_id,
            slot=slot_name,
            webspace_id=ws_id,
            fingerprint=fingerprint,
            written=True,
            skipped=False,
            reason=str(reason or ("force_recomputed" if force else "changed")),
            force=bool(force),
        )
        with self._lock:
            self._fingerprints[key] = fingerprint
            self._diagnostics.record(result)
        return result

    def set_if_changed_sync(
        self,
        slot: ProjectionSlot | str,
        value: Any,
        *,
        webspace_id: str | None = None,
        force: bool = False,
        reason: str | None = None,
    ) -> ProjectionWriteResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.set_if_changed(slot, value, webspace_id=webspace_id, force=force, reason=reason)
            )
        raise RuntimeError("ProjectionRuntime.set_if_changed_sync cannot run inside an active event loop")

    def reset(
        self,
        *,
        webspace_id: str | None = None,
        slot: ProjectionSlot | str | None = None,
    ) -> None:
        slot_name = _slot_name(slot) if slot is not None else None
        ws_id = str(webspace_id or "").strip() or None
        with self._lock:
            if ws_id is None and slot_name is None:
                self._fingerprints.clear()
                self._diagnostics = ProjectionDiagnostics()
                return
            for key in list(self._fingerprints):
                key_ws, key_slot = key
                if ws_id is not None and key_ws != ws_id:
                    continue
                if slot_name is not None and key_slot != slot_name:
                    continue
                self._fingerprints.pop(key, None)

    def diagnostics_snapshot(self) -> dict[str, Any]:
        with self._lock:
            payload = self._diagnostics.snapshot()
            payload["skill_id"] = self.skill_id
            payload["fingerprint_entries"] = len(self._fingerprints)
            payload["ts"] = time.time()
            return payload


def _slot_name(slot: ProjectionSlot | str | None) -> str:
    if isinstance(slot, ProjectionSlot):
        name = slot.name
    else:
        name = str(slot or "")
    token = str(name or "").strip()
    if not token:
        raise ValueError("projection slot name is required")
    return token


def _default_ctx_subnet() -> Any:
    from adaos.sdk.data.ctx import subnet

    return subnet


_RUNTIMES: dict[str, ProjectionRuntime] = {}
_RUNTIMES_LOCK = threading.RLock()


def get_projection_runtime(skill_id: str, *, ctx_subnet: Any | None = None) -> ProjectionRuntime:
    token = str(skill_id or "unknown_skill").strip() or "unknown_skill"
    if ctx_subnet is not None:
        return ProjectionRuntime(token, ctx_subnet=ctx_subnet)
    with _RUNTIMES_LOCK:
        runtime = _RUNTIMES.get(token)
        if runtime is None:
            runtime = ProjectionRuntime(token)
            _RUNTIMES[token] = runtime
        return runtime


async def set_projection_if_changed(
    skill_id: str,
    slot: ProjectionSlot | str,
    value: Any,
    *,
    webspace_id: str | None = None,
    force: bool = False,
    reason: str | None = None,
) -> ProjectionWriteResult:
    runtime = get_projection_runtime(skill_id)
    return await runtime.set_if_changed(slot, value, webspace_id=webspace_id, force=force, reason=reason)


def clear_projection_runtime_state(skill_id: str | None = None) -> None:
    with _RUNTIMES_LOCK:
        if skill_id is None:
            _RUNTIMES.clear()
            return
        _RUNTIMES.pop(str(skill_id or "").strip(), None)


__all__ = [
    "DirtyRouter",
    "ProjectionContext",
    "ProjectionDiagnostics",
    "ProjectionRuntime",
    "ProjectionSlot",
    "ProjectionWriteResult",
    "StreamReceiver",
    "clear_projection_runtime_state",
    "get_projection_runtime",
    "set_projection_if_changed",
    "stable_payload_fingerprint",
]
