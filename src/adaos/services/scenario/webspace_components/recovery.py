from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecoveryDecision:
    fingerprint: str
    duplicate_reason: str | None
    duplicate_age_s: float | None
    command_duplicate: Mapping[str, Any] | None
    previous_status: str
    previous_pending_stale: bool
    pending_stale_after_s: float

    @property
    def deduplicated(self) -> bool:
        return self.duplicate_reason is not None


class WebspaceRecoveryCoordinator:
    """Own semantic reload/reset dedupe and snapshot-restore sequencing."""

    def __init__(
        self,
        *,
        command_cache_limit: int = 256,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._command_cache_limit = max(1, int(command_cache_limit))
        self._clock = clock
        self._command_cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def request_fingerprint(
        *,
        webspace_id: str,
        action: str,
        scenario_id: str | None,
        command_trace: Mapping[str, Any] | None = None,
    ) -> str:
        trace = command_trace if isinstance(command_trace, Mapping) else {}
        trace_fingerprint = str(trace.get("gateway_command_fingerprint") or "").strip()
        if trace_fingerprint:
            return trace_fingerprint
        raw = {
            "webspace_id": str(webspace_id or "").strip() or "default",
            "action": str(action or "").strip() or "reload",
            "scenario_id": str(scenario_id or "").strip() or None,
        }
        encoded = json.dumps(
            raw,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha1(encoded).hexdigest()[:12]

    @staticmethod
    def _command_cache_key(
        *,
        webspace_id: str,
        action: str,
        scenario_id: str | None,
        command_id: str,
    ) -> str:
        raw = {
            "webspace_id": str(webspace_id or "").strip() or "default",
            "action": str(action or "").strip() or "reload",
            "scenario_id": str(scenario_id or "").strip() or None,
            "cmd_id": str(command_id or "").strip(),
        }
        encoded = json.dumps(
            raw,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha1(encoded).hexdigest()[:16]

    def _claim_command_once(
        self,
        *,
        webspace_id: str,
        action: str,
        scenario_id: str | None,
        command_id: str | None,
        fingerprint: str,
        ttl_s: float,
        now: float,
    ) -> Mapping[str, Any] | None:
        command_id = str(command_id or "").strip()
        if not command_id or ttl_s <= 0.0:
            return None

        expired = [
            key
            for key, entry in self._command_cache.items()
            if now - float(entry.get("ts") or 0.0) > ttl_s
        ]
        for key in expired:
            self._command_cache.pop(key, None)
        while len(self._command_cache) >= self._command_cache_limit:
            oldest_key = min(
                self._command_cache,
                key=lambda key: float(self._command_cache[key].get("ts") or 0.0),
            )
            self._command_cache.pop(oldest_key, None)

        key = self._command_cache_key(
            webspace_id=webspace_id,
            action=action,
            scenario_id=scenario_id,
            command_id=command_id,
        )
        existing = self._command_cache.get(key)
        if existing:
            duplicate = dict(existing)
            duplicate.update(
                {
                    "age_s": round(max(0.0, now - float(existing.get("ts") or now)), 3),
                    "ttl_s": ttl_s,
                    "cmd_id": command_id,
                    "cache_key": key,
                }
            )
            return duplicate

        self._command_cache[key] = {
            "ts": now,
            "webspace_id": str(webspace_id or "").strip() or "default",
            "action": str(action or "").strip() or "reload",
            "scenario_id": str(scenario_id or "").strip() or None,
            "fingerprint": str(fingerprint or "").strip(),
            "cmd_id": command_id,
            "cache_key": key,
        }
        return None

    def begin(
        self,
        *,
        webspace_id: str,
        action: str,
        scenario_id: str | None,
        command_trace: Mapping[str, Any] | None,
        previous_state: Mapping[str, Any] | None,
        command_ttl_s: float,
        duplicate_window_s: float,
        pending_stale_after_s: float,
    ) -> RecoveryDecision:
        trace = command_trace if isinstance(command_trace, Mapping) else {}
        previous = previous_state if isinstance(previous_state, Mapping) else {}
        now = self._clock()
        fingerprint = self.request_fingerprint(
            webspace_id=webspace_id,
            action=action,
            scenario_id=scenario_id,
            command_trace=trace,
        )
        command_duplicate = self._claim_command_once(
            webspace_id=webspace_id,
            action=action,
            scenario_id=scenario_id,
            command_id=str(trace.get("cmd_id") or "").strip() or None,
            fingerprint=fingerprint,
            ttl_s=max(0.0, float(command_ttl_s)),
            now=now,
        )

        previous_status = str(previous.get("status") or "").strip().lower()
        previous_age_s: float | None = None
        previous_updated_at = (
            previous.get("updated_at")
            if previous.get("updated_at") is not None
            else previous.get("finished_at")
        )
        if previous_updated_at is None:
            previous_updated_at = previous.get("started_at")
        try:
            if previous_updated_at is not None:
                previous_age_s = round(max(0.0, now - float(previous_updated_at)), 3)
        except (TypeError, ValueError):
            previous_age_s = None

        previous_pending = bool(previous.get("pending"))
        stale_after = max(0.0, float(pending_stale_after_s))
        previous_pending_stale = bool(
            previous_pending
            and stale_after > 0.0
            and previous_age_s is not None
            and previous_age_s >= stale_after
        )

        duplicate_reason: str | None = None
        duplicate_age_s = previous_age_s
        if command_duplicate is not None:
            duplicate_reason = "duplicate_recovery_command"
            duplicate_age_s = command_duplicate.get("age_s")
        elif (
            str(previous.get("action") or "").strip().lower() == action
            and (str(previous.get("scenario_id") or "").strip() or None) == scenario_id
            and str(previous.get("recovery_fingerprint") or "").strip() == fingerprint
        ):
            if previous_pending and not previous_pending_stale:
                duplicate_reason = "already_pending_recovery"
            elif (
                duplicate_window_s > 0.0
                and previous_age_s is not None
                and previous_age_s <= duplicate_window_s
                and previous_status in {"running", "ready", "scheduled"}
            ):
                duplicate_reason = "duplicate_recovery_request"

        return RecoveryDecision(
            fingerprint=fingerprint,
            duplicate_reason=duplicate_reason,
            duplicate_age_s=duplicate_age_s,
            command_duplicate=command_duplicate,
            previous_status=previous_status,
            previous_pending_stale=previous_pending_stale,
            pending_stale_after_s=stale_after,
        )

    async def restore_snapshot(
        self,
        *,
        webspace_id: str,
        restore_store: Callable[[str], Awaitable[dict[str, Any]]],
        reset_room: Callable[[str], Awaitable[dict[str, Any]]],
        read_current_scenario: Callable[[str], Awaitable[str | None]],
        persist_current_scenario: Callable[[str, str], None],
        rebuild: Callable[[str, Mapping[str, Any]], Awaitable[dict[str, Any]]],
        on_error: Callable[[str, BaseException], None] | None = None,
    ) -> dict[str, Any]:
        restore_result = await restore_store(webspace_id)
        if not bool(restore_result.get("accepted")):
            return restore_result

        reset_result: dict[str, Any] = {}
        try:
            reset_result = await reset_room(webspace_id)
        except Exception as exc:
            if on_error is not None:
                on_error("reset_room", exc)

        try:
            restored_scenario = await read_current_scenario(webspace_id)
        except Exception as exc:
            restored_scenario = None
            if on_error is not None:
                on_error("read_current_scenario", exc)
        if restored_scenario:
            try:
                persist_current_scenario(webspace_id, restored_scenario)
            except Exception as exc:
                if on_error is not None:
                    on_error("persist_current_scenario", exc)

        rebuild_result = await rebuild(webspace_id, restore_result)
        return {
            **restore_result,
            **rebuild_result,
            "action": "restore",
            "source_of_truth": "snapshot",
            "reset_room": reset_result,
        }
