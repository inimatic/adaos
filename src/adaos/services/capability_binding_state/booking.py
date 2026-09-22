"""Deterministic ``booking.reserve`` runtime used by the CBS7 domain proof."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from adaos.domain.artifact_release import canonical_payload_digest


class BookingError(RuntimeError):
    code = "booking_error"

    def __init__(self, message: str, *, effect_receipt: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.effect_receipt = copy.deepcopy(dict(effect_receipt)) if effect_receipt else None


class InvalidBookingInterval(BookingError):
    code = "invalid_interval"


class BookingUnavailable(BookingError):
    code = "unavailable"


class BookingConflict(BookingError):
    code = "conflict"


class StaleAvailability(BookingError):
    code = "stale_read"


class ProviderTimeout(BookingError):
    code = "provider_timeout"


class ReconciliationRequired(BookingError):
    code = "reconciliation_required"


class IdempotencyConflict(BookingError):
    code = "idempotency_conflict"


class BookingInProgress(BookingError):
    code = "in_progress"


def _instant(value: Any, *, field: str) -> datetime:
    token = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidBookingInterval(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise InvalidBookingInterval(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _overlaps(left_start: str, left_end: str, right_start: str, right_end: str) -> bool:
    return _instant(left_start, field="starts_at") < _instant(right_end, field="ends_at") and _instant(
        right_start, field="starts_at"
    ) < _instant(left_end, field="ends_at")


@dataclass(frozen=True, slots=True)
class BookingCommand:
    idempotency_key: str
    resource_ref: str
    customer_ref: str
    starts_at: str
    ends_at: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BookingCommand":
        result = cls(
            idempotency_key=str(value.get("idempotency_key") or "").strip(),
            resource_ref=str(value.get("resource_ref") or "").strip(),
            customer_ref=str(value.get("customer_ref") or "").strip(),
            starts_at=str(value.get("starts_at") or "").strip(),
            ends_at=str(value.get("ends_at") or "").strip(),
        )
        if not result.idempotency_key or not result.resource_ref or not result.customer_ref:
            raise InvalidBookingInterval(
                "idempotency_key, resource_ref, and customer_ref are required"
            )
        starts = _instant(result.starts_at, field="starts_at")
        ends = _instant(result.ends_at, field="ends_at")
        if starts >= ends:
            raise InvalidBookingInterval("reservation interval must have starts_at < ends_at")
        return result

    def to_dict(self) -> dict[str, str]:
        return {
            "idempotency_key": self.idempotency_key,
            "resource_ref": self.resource_ref,
            "customer_ref": self.customer_ref,
            "starts_at": _iso(_instant(self.starts_at, field="starts_at")),
            "ends_at": _iso(_instant(self.ends_at, field="ends_at")),
        }

    @property
    def digest(self) -> str:
        return canonical_payload_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class AvailabilitySnapshot:
    resource_ref: str
    intervals: tuple[tuple[str, str], ...]
    generation: int

    def covers(self, starts_at: str, ends_at: str) -> bool:
        starts = _instant(starts_at, field="starts_at")
        ends = _instant(ends_at, field="ends_at")
        return any(
            _instant(left, field="starts_at") <= starts
            and ends <= _instant(right, field="ends_at")
            for left, right in self.intervals
        )


class InMemoryAvailabilityState:
    """External-authoritative read port with monotonic observation generation."""

    def __init__(
        self,
        intervals: Mapping[str, Sequence[tuple[str, str]]],
        *,
        state_space_ref: str = "state-space:booking/availability",
        generation: int = 1,
    ) -> None:
        self.state_space_ref = state_space_ref
        self._lock = threading.RLock()
        self._generation = int(generation)
        self._intervals = {
            str(resource): tuple((str(start), str(end)) for start, end in values)
            for resource, values in intervals.items()
        }

    def snapshot(self, resource_ref: str) -> AvailabilitySnapshot:
        with self._lock:
            return AvailabilitySnapshot(
                resource_ref=resource_ref,
                intervals=self._intervals.get(resource_ref, ()),
                generation=self._generation,
            )

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def replace(
        self,
        intervals: Mapping[str, Sequence[tuple[str, str]]],
    ) -> int:
        with self._lock:
            self._intervals = {
                str(resource): tuple((str(start), str(end)) for start, end in values)
                for resource, values in intervals.items()
            }
            self._generation += 1
            return self._generation


class InMemoryAuditState:
    """Append-only audit port."""

    def __init__(self, *, state_space_ref: str = "state-space:booking/audit") -> None:
        self.state_space_ref = state_space_ref
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []

    def append(
        self,
        event: str,
        *,
        command_digest: str,
        recorded_at: str,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            value = {
                "sequence": len(self._events) + 1,
                "event": str(event),
                "command_digest": command_digest,
                "recorded_at": recorded_at,
                "details": copy.deepcopy(dict(details or {})),
            }
            value["event_digest"] = canonical_payload_digest(value)
            self._events.append(value)
            return copy.deepcopy(value)

    def events(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(copy.deepcopy(self._events))


class InMemoryBookingState:
    """Portable state with serialized interval and idempotency commits."""

    def __init__(self, *, state_space_ref: str = "state-space:booking/records") -> None:
        self.state_space_ref = state_space_ref
        self._lock = threading.RLock()
        self._bookings: dict[str, dict[str, Any]] = {}
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._reconciliation: dict[str, dict[str, Any]] = {}

    def begin(self, command: BookingCommand) -> dict[str, Any] | None:
        with self._lock:
            current = self._idempotency.get(command.idempotency_key)
            if current is not None:
                if current["command_digest"] != command.digest:
                    raise IdempotencyConflict("idempotency key was already used for another command")
                if current["status"] == "completed":
                    return copy.deepcopy(current["result"])
                if current["status"] == "reconciliation_required":
                    raise ReconciliationRequired(
                        "previous attempt requires reconciliation",
                        effect_receipt=current.get("effect_receipt"),
                    )
                raise BookingInProgress("an identical reservation command is already in progress")
            self._idempotency[command.idempotency_key] = {
                "command_digest": command.digest,
                "status": "in_progress",
            }
            return None

    def commit(
        self,
        command: BookingCommand,
        *,
        effect_receipt: Mapping[str, Any],
        availability_generation: int,
        current_availability_generation: int,
        committed_at: str,
    ) -> dict[str, Any]:
        with self._lock:
            if availability_generation != current_availability_generation:
                raise StaleAvailability("availability generation changed before commit")
            for item in self._bookings.values():
                if item["resource_ref"] == command.resource_ref and _overlaps(
                    item["starts_at"],
                    item["ends_at"],
                    command.starts_at,
                    command.ends_at,
                ):
                    raise BookingConflict("another confirmed booking overlaps the interval")
            token = command.digest.removeprefix("sha256:")[:24]
            booking = {
                "booking_ref": f"booking:{token}",
                "resource_ref": command.resource_ref,
                "customer_ref": command.customer_ref,
                "starts_at": command.to_dict()["starts_at"],
                "ends_at": command.to_dict()["ends_at"],
                "effect_receipt": copy.deepcopy(dict(effect_receipt)),
                "availability_generation": availability_generation,
                "committed_at": committed_at,
                "revision": 1,
                "status": "confirmed",
                "command_digest": command.digest,
            }
            booking["booking_digest"] = canonical_payload_digest(booking)
            self._bookings[booking["booking_ref"]] = booking
            self._idempotency[command.idempotency_key] = {
                "command_digest": command.digest,
                "status": "completed",
                "result": booking,
            }
            return copy.deepcopy(booking)

    def release(self, command: BookingCommand) -> None:
        with self._lock:
            current = self._idempotency.get(command.idempotency_key)
            if current and current["status"] == "in_progress":
                self._idempotency.pop(command.idempotency_key, None)

    def require_reconciliation(
        self,
        command: BookingCommand,
        *,
        effect_receipt: Mapping[str, Any],
        reason: str,
    ) -> None:
        with self._lock:
            value = {
                "command_digest": command.digest,
                "effect_receipt": copy.deepcopy(dict(effect_receipt)),
                "reason": reason,
            }
            self._reconciliation[command.idempotency_key] = value
            self._idempotency[command.idempotency_key] = {
                **value,
                "status": "reconciliation_required",
            }

    def records(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(copy.deepcopy(tuple(self._bookings.values())))

    def reconciliation_items(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(copy.deepcopy(tuple(self._reconciliation.values())))


class ScriptedReservationProvider:
    """External effect boundary with deterministic failures and receipts."""

    def __init__(
        self,
        *,
        provider_ref: str,
        outcomes: Sequence[str] = ("success",),
        compensation_fails: bool = False,
        barrier: threading.Barrier | None = None,
        on_effect: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.provider_ref = provider_ref
        self.outcomes = tuple(outcomes) or ("success",)
        self.compensation_fails = compensation_fails
        self.barrier = barrier
        self.on_effect = on_effect
        self._lock = threading.RLock()
        self.reserve_calls = 0
        self.compensation_calls = 0
        self.active_effects: dict[str, dict[str, Any]] = {}

    def reserve(self, command: BookingCommand, *, observed_at: str) -> dict[str, Any]:
        with self._lock:
            call = self.reserve_calls
            self.reserve_calls += 1
            outcome = self.outcomes[min(call, len(self.outcomes) - 1)]
        if outcome == "timeout_before_effect":
            raise ProviderTimeout("provider timed out before reporting an effect")
        receipt = {
            "effect_ref": f"provider-effect:{self.provider_ref}/{command.digest.removeprefix('sha256:')[:20]}/{call + 1}",
            "provider_ref": self.provider_ref,
            "command_digest": command.digest,
            "observed_at": observed_at,
            "status": "reserved",
        }
        receipt["receipt_digest"] = canonical_payload_digest(receipt)
        with self._lock:
            self.active_effects[receipt["effect_ref"]] = copy.deepcopy(receipt)
        if self.on_effect is not None:
            self.on_effect(copy.deepcopy(receipt))
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        if outcome == "timeout_after_effect":
            raise ProviderTimeout(
                "provider timed out after creating an effect",
                effect_receipt=receipt,
            )
        if outcome not in {"success", "timeout_after_effect"}:
            raise BookingError(f"unsupported provider outcome: {outcome}", effect_receipt=receipt)
        return copy.deepcopy(receipt)

    def compensate(self, effect_receipt: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.compensation_calls += 1
            if self.compensation_fails:
                raise BookingError("provider compensation failed", effect_receipt=effect_receipt)
            effect_ref = str(effect_receipt["effect_ref"])
            self.active_effects.pop(effect_ref, None)
            result = {
                "effect_ref": effect_ref,
                "provider_ref": self.provider_ref,
                "status": "compensated",
            }
            result["receipt_digest"] = canonical_payload_digest(result)
            return result


class SimulationReservationProvider(ScriptedReservationProvider):
    pass


class ProductionReservationProvider(ScriptedReservationProvider):
    pass


@dataclass(slots=True)
class BookingReservationService:
    availability: InMemoryAvailabilityState
    bookings: InMemoryBookingState
    audit: InMemoryAuditState
    provider: ScriptedReservationProvider
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def reserve(self, value: Mapping[str, Any]) -> dict[str, Any]:
        command = BookingCommand.from_mapping(value)
        current_time = self.now().astimezone(timezone.utc)
        if _instant(command.starts_at, field="starts_at") <= current_time:
            raise InvalidBookingInterval("reservation must start in the future")
        duplicate = self.bookings.begin(command)
        if duplicate is not None:
            return {**duplicate, "duplicate": True}
        recorded_at = _iso(current_time)
        self.audit.append(
            "booking.reserve.started",
            command_digest=command.digest,
            recorded_at=recorded_at,
        )
        snapshot = self.availability.snapshot(command.resource_ref)
        if not snapshot.covers(command.starts_at, command.ends_at):
            self.bookings.release(command)
            self.audit.append(
                "booking.reserve.unavailable",
                command_digest=command.digest,
                recorded_at=recorded_at,
                details={"availability_generation": snapshot.generation},
            )
            raise BookingUnavailable("requested interval is outside observed availability")

        try:
            effect = self.provider.reserve(command, observed_at=recorded_at)
        except ProviderTimeout as exc:
            if exc.effect_receipt is None:
                self.bookings.release(command)
                self.audit.append(
                    "booking.reserve.provider_timeout",
                    command_digest=command.digest,
                    recorded_at=recorded_at,
                    details={"effect_observed": False},
                )
                raise
            self._compensate_or_require_reconciliation(
                command,
                effect_receipt=exc.effect_receipt,
                reason="provider_timeout_after_effect",
                recorded_at=recorded_at,
            )
            raise

        try:
            result = self.bookings.commit(
                command,
                effect_receipt=effect,
                availability_generation=snapshot.generation,
                current_availability_generation=self.availability.generation,
                committed_at=recorded_at,
            )
        except (BookingConflict, StaleAvailability):
            self._compensate_or_require_reconciliation(
                command,
                effect_receipt=effect,
                reason="local_commit_rejected",
                recorded_at=recorded_at,
            )
            raise
        self.audit.append(
            "booking.reserve.confirmed",
            command_digest=command.digest,
            recorded_at=recorded_at,
            details={
                "booking_ref": result["booking_ref"],
                "effect_ref": effect["effect_ref"],
            },
        )
        return {**result, "duplicate": False}

    def _compensate_or_require_reconciliation(
        self,
        command: BookingCommand,
        *,
        effect_receipt: Mapping[str, Any],
        reason: str,
        recorded_at: str,
    ) -> None:
        try:
            compensation = self.provider.compensate(effect_receipt)
        except BookingError as exc:
            self.bookings.require_reconciliation(
                command,
                effect_receipt=effect_receipt,
                reason=reason,
            )
            self.audit.append(
                "booking.reserve.reconciliation_required",
                command_digest=command.digest,
                recorded_at=recorded_at,
                details={"reason": reason, "effect_ref": effect_receipt["effect_ref"]},
            )
            raise ReconciliationRequired(
                f"{reason}; compensation failed: {exc}",
                effect_receipt=effect_receipt,
            ) from exc
        self.bookings.release(command)
        self.audit.append(
            "booking.reserve.compensated",
            command_digest=command.digest,
            recorded_at=recorded_at,
            details={
                "reason": reason,
                "effect_ref": effect_receipt["effect_ref"],
                "compensation_receipt": compensation,
            },
        )


__all__ = [
    "AvailabilitySnapshot",
    "BookingCommand",
    "BookingConflict",
    "BookingError",
    "BookingInProgress",
    "BookingReservationService",
    "BookingUnavailable",
    "IdempotencyConflict",
    "InMemoryAuditState",
    "InMemoryAvailabilityState",
    "InMemoryBookingState",
    "InvalidBookingInterval",
    "ProductionReservationProvider",
    "ProviderTimeout",
    "ReconciliationRequired",
    "ScriptedReservationProvider",
    "SimulationReservationProvider",
    "StaleAvailability",
]
