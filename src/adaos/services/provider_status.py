"""In-process registry and projection for capability provider readiness."""

from __future__ import annotations

import threading
from typing import Any, Iterable

from adaos.domain.provider_status import ProviderProtocolError, ProviderStatus


class ProviderStatusRegistry:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], ProviderStatus] = {}
        self._lock = threading.RLock()

    def record(self, status: ProviderStatus) -> ProviderStatus:
        key = (status.capability, status.provider_id)
        with self._lock:
            self._items[key] = status
        return status

    def record_many(self, statuses: Iterable[ProviderStatus]) -> None:
        for status in statuses:
            self.record(status)

    def negotiate(
        self,
        capability: str,
        required_protocol: str,
        *,
        preferred_provider: str | None = None,
    ) -> ProviderStatus:
        requested = str(capability or "").strip().lower()
        preferred = str(preferred_provider or "").strip().lower() or None
        with self._lock:
            candidates = [
                item
                for (item_capability, provider_id), item in self._items.items()
                if item_capability == requested
                and (preferred is None or provider_id == preferred)
                and item.health != "unavailable"
            ]
        failures: list[str] = []
        for item in sorted(candidates, key=lambda value: value.provider_id):
            try:
                item.require_protocol(required_protocol)
            except ProviderProtocolError as exc:
                failures.append(str(exc))
                continue
            return item
        detail = "; ".join(failures) or "no ready providers"
        raise ProviderProtocolError(
            f"no {requested} provider satisfies protocol {required_protocol}: {detail}"
        )

    def projection(self) -> dict[str, Any]:
        with self._lock:
            items = [
                item.to_dict()
                for _, item in sorted(self._items.items(), key=lambda entry: entry[0])
            ]
        return {
            "schema": "adaos.provider.status_projection.v1",
            "providers": items,
            "healthy": sum(item["health"] == "healthy" for item in items),
            "degraded": sum(item["health"] == "degraded" for item in items),
            "unavailable": sum(item["health"] == "unavailable" for item in items),
        }


def build_provider_status_registry(
    *,
    relational_broker: Any | None = None,
    executors: Iterable[Any] = (),
) -> ProviderStatusRegistry:
    registry = ProviderStatusRegistry()
    if relational_broker is not None:
        for profile in relational_broker.provider_profiles():
            registry.record(
                ProviderStatus(
                    capability="storage.relational",
                    provider_id=profile.provider_id,
                    protocol_version=profile.protocol_version,
                    health="healthy",
                    features=profile.features,
                    details={
                        "isolation": profile.isolation,
                        "max_concurrent_writers": profile.max_concurrent_writers,
                    },
                )
            )
    for executor in executors:
        profile = executor.capabilities
        registry.record(
            ProviderStatus(
                capability="execution.jobs",
                provider_id=profile.provider_id,
                protocol_version=profile.protocol_version,
                health="healthy",
                features=profile.features,
                details={"hostile_isolation": profile.hostile_isolation},
            )
        )
    return registry


__all__ = ["ProviderStatusRegistry", "build_provider_status_registry"]
