"""Public subscription usage projections for skills and scenarios."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


CODEX_TOKEN_RESOURCE = "codex.api.tokens"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(0, int(float(str(value))))
    except (TypeError, ValueError):
        return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True, slots=True)
class CodexUsageSnapshot:
    """Bounded subscription view of Codex token use for one subnet."""

    schema: str
    status: str
    resource: str
    period: str
    used_tokens: int | None
    remaining_tokens: int | None
    limit_tokens: int | None
    fresh_plus_output_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    runs: int | None
    accuracy: str
    metering: str
    updated_at: str
    webspace_id: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_codex_usage_model(
    *,
    webspace_id: str | None = None,
    refresh: bool = False,
    timeout: float = 8.0,
) -> CodexUsageSnapshot:
    """Return 24-hour Codex usage and remaining subscription tokens.

    Set ``refresh=True`` for user-initiated refreshes. Root/provider failures
    are represented by ``stale`` or ``unavailable`` status and never raised to
    the calling skill. The projection is intentionally bounded and contains no
    subscription credentials or raw entitlement payload.
    """

    from adaos.services import economic_policy

    refresh_error = ""
    if refresh:
        try:
            economic_policy.refresh_entitlement_snapshot_from_root(timeout=max(0.1, float(timeout)))
        except Exception as exc:
            refresh_error = f"{type(exc).__name__}: {_text(exc)}"[:240]

    try:
        economic = economic_policy.current_subnet_economic_status()
    except Exception as exc:
        return CodexUsageSnapshot(
            schema="adaos.sdk.subscription.codex_usage.v1",
            status="unavailable",
            resource=CODEX_TOKEN_RESOURCE,
            period="24h",
            used_tokens=None,
            remaining_tokens=None,
            limit_tokens=None,
            fresh_plus_output_tokens=None,
            cached_input_tokens=None,
            output_tokens=None,
            runs=None,
            accuracy="unavailable",
            metering="",
            updated_at="",
            webspace_id=_text(webspace_id) or "desktop",
            reason=f"{type(exc).__name__}: {_text(exc)}"[:240],
        )

    usage = _mapping(_mapping(economic.get("usage")).get(CODEX_TOKEN_RESOURCE))
    breakdown = _mapping(usage.get("usage_breakdown"))
    window = _mapping(breakdown.get("window_24h"))
    entitlement = _mapping(economic.get("entitlement_snapshot"))
    has_usage = bool(usage)
    loaded = entitlement.get("loaded") is True
    status = "ready" if has_usage and not refresh_error else "stale" if has_usage else "unavailable"
    reason = refresh_error or ("codex_usage_not_metered" if not has_usage else "")
    updated_at = _text(
        usage.get("last_seen_at")
        or usage.get("updated_at")
        or entitlement.get("updated_at")
        or economic.get("generated_at")
    )
    accuracy = _text(usage.get("accuracy")) or ("root_snapshot" if loaded else "unavailable")

    return CodexUsageSnapshot(
        schema="adaos.sdk.subscription.codex_usage.v1",
        status=status,
        resource=CODEX_TOKEN_RESOURCE,
        period="24h",
        used_tokens=_optional_int(usage.get("used_24h")),
        remaining_tokens=_optional_int(usage.get("quota_remaining")),
        limit_tokens=_optional_int(usage.get("quota_limit")),
        fresh_plus_output_tokens=_optional_int(window.get("fresh_plus_output_tokens")),
        cached_input_tokens=_optional_int(window.get("cached_input_tokens")),
        output_tokens=_optional_int(window.get("output_tokens")),
        runs=_optional_int(window.get("runs")),
        accuracy=accuracy,
        metering=_text(usage.get("metering")),
        updated_at=updated_at,
        webspace_id=_text(webspace_id) or "desktop",
        reason=reason or None,
    )


def get_codex_usage_snapshot(
    *,
    webspace_id: str | None = None,
    refresh: bool = False,
    timeout: float = 8.0,
) -> Mapping[str, Any]:
    """Return serializable 24-hour Codex token usage and remaining quota."""

    return get_codex_usage_model(
        webspace_id=webspace_id,
        refresh=refresh,
        timeout=timeout,
    ).to_dict()


__all__ = [
    "CodexUsageSnapshot",
    "get_codex_usage_model",
    "get_codex_usage_snapshot",
]
