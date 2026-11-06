"""Root-owned, identity-free public Site delivery observations."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


SCHEMA = "adaos.public_site_delivery_observation.v1"
REGISTRY_SCHEMA = "adaos.public_site_delivery_telemetry_registry.v1"
SNAPSHOT_SCHEMA = "adaos.public_site_delivery_telemetry.v1"
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_KINDS = {"bootstrap", "descriptor", "artifact", "publication_probe"}
_STATUSES = {"healthy", "fallback", "degraded", "unavailable", "tampered"}
_CACHE_STATES = {"hit", "miss", "stale", "bypass", "unknown"}
_PROVIDERS = {"firebase", "cloudflare", "regru", "s3", "unknown"}
_ALLOWED = {
    "schema",
    "observed_at",
    "kind",
    "status",
    "bootstrap",
    "descriptor_generation",
    "selected_zone",
    "fallback_zone",
    "provider",
    "cache_state",
    "site_id",
    "deployment_digest",
    "request_count",
    "bytes_delivered",
    "latency_ms",
}
_FORBIDDEN_IDENTITY_MARKERS = {
    "actor",
    "cookie",
    "device",
    "email",
    "ip",
    "session",
    "subject",
    "token",
    "user",
    "visitor",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _timestamp(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        return _now()
    parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("delivery telemetry timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _identifier(value: Any, label: str, *, optional: bool = False) -> str | None:
    token = str(value or "").strip()
    if optional and not token:
        return None
    if not _IDENTIFIER.fullmatch(token):
        raise ValueError(f"delivery telemetry {label} is invalid")
    return token


def _bounded_int(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"delivery telemetry {label} is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"delivery telemetry {label} is invalid") from exc
    if result < minimum or result > maximum:
        raise ValueError(f"delivery telemetry {label} is invalid")
    return result


def normalize_delivery_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    """Admit only bounded delivery dimensions, never browser/visitor identity."""

    if not isinstance(value, Mapping):
        raise ValueError("delivery telemetry observation must be an object")
    keys = {str(key) for key in value}
    unknown = keys - _ALLOWED
    forbidden = {
        key
        for key in keys
        if set(key.lower().split("_")).intersection(_FORBIDDEN_IDENTITY_MARKERS)
    }
    if unknown or forbidden:
        raise ValueError("delivery telemetry contains unsupported or identifying fields")
    if value.get("schema", SCHEMA) != SCHEMA:
        raise ValueError("delivery telemetry schema is invalid")
    kind = str(value.get("kind") or "").strip()
    status = str(value.get("status") or "").strip()
    provider = str(value.get("provider") or "unknown").strip()
    cache_state = str(value.get("cache_state") or "unknown").strip()
    if kind not in _KINDS or status not in _STATUSES:
        raise ValueError("delivery telemetry kind or status is invalid")
    if provider not in _PROVIDERS or cache_state not in _CACHE_STATES:
        raise ValueError("delivery telemetry provider or cache state is invalid")
    digest = str(value.get("deployment_digest") or "").strip()
    if not _DIGEST.fullmatch(digest):
        raise ValueError("delivery telemetry deployment digest is invalid")
    generation = _bounded_int(
        value.get("descriptor_generation"),
        "descriptor generation",
        minimum=1,
        maximum=2_147_483_647,
    )
    request_count = _bounded_int(
        value.get("request_count", 1), "request count", minimum=1, maximum=100_000
    )
    bytes_delivered = _bounded_int(
        value.get("bytes_delivered", 0),
        "delivered bytes",
        minimum=0,
        maximum=10 * 1024 * 1024 * 1024,
    )
    latency = value.get("latency_ms")
    latency_ms = (
        None
        if latency is None
        else _bounded_int(latency, "latency", minimum=0, maximum=3_600_000)
    )
    selected_zone = _identifier(value.get("selected_zone"), "selected zone", optional=True)
    fallback_zone = _identifier(value.get("fallback_zone"), "fallback zone", optional=True)
    if status == "fallback" and not fallback_zone:
        raise ValueError("fallback delivery telemetry requires a fallback zone")
    return {
        "schema": SCHEMA,
        "observed_at": _timestamp(value.get("observed_at")),
        "kind": kind,
        "status": status,
        "bootstrap": _identifier(value.get("bootstrap"), "bootstrap"),
        "descriptor_generation": generation,
        "selected_zone": selected_zone,
        "fallback_zone": fallback_zone,
        "provider": provider,
        "cache_state": cache_state,
        "site_id": _identifier(value.get("site_id"), "site id"),
        "deployment_digest": digest,
        "request_count": request_count,
        "bytes_delivered": bytes_delivered,
        "latency_ms": latency_ms,
    }


class PublicSiteDeliveryTelemetryService:
    """Retain bounded synthetic/provider observations and aggregate usage."""

    def __init__(self, *, state_dir: Path, max_events: int = 4096) -> None:
        self.root = Path(state_dir).resolve() / "public-site-delivery-telemetry"
        self.path = self.root / "registry.json"
        self.lock_path = self.root / ".mutation.lock"
        self.max_events = max(100, min(int(max_events), 20_000))

    def record(
        self,
        observation: Mapping[str, Any],
        *,
        source: str,
        observation_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_delivery_observation(observation)
        source = str(_identifier(source, "source"))
        identity = str(observation_id or "psobs_" + uuid4().hex)
        if not _IDENTIFIER.fullmatch(identity):
            raise ValueError("delivery telemetry observation id is invalid")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            duplicate = next(
                (item for item in registry["events"] if item["observation_id"] == identity),
                None,
            )
            if duplicate is not None:
                if duplicate["observation"] != normalized:
                    raise ValueError("delivery telemetry observation id conflict")
                return deepcopy(duplicate)
            retained = {
                "observation_id": identity,
                "source": source,
                "observation": normalized,
                "recorded_at": _now(),
            }
            events = [*registry["events"], retained][-self.max_events :]
            registry.update(
                revision=int(registry["revision"]) + 1,
                updated_at=_now(),
                events=events,
            )
            atomic_write_json(self.path, registry)
        return deepcopy(retained)

    def status(
        self,
        *,
        site_id: str | None = None,
        deployment_digest: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if site_id is not None:
            site_id = str(_identifier(site_id, "site id"))
        if deployment_digest is not None and not _DIGEST.fullmatch(deployment_digest):
            raise ValueError("delivery telemetry deployment digest is invalid")
        limit = max(1, min(int(limit), 500))
        registry = self._read()
        events = [
            deepcopy(item)
            for item in registry["events"]
            if (site_id is None or item["observation"]["site_id"] == site_id)
            and (
                deployment_digest is None
                or item["observation"]["deployment_digest"] == deployment_digest
            )
        ]
        dimensions: dict[str, dict[str, Any]] = {}
        for retained in events:
            item = retained["observation"]
            dimension = {
                key: item[key]
                for key in (
                    "bootstrap",
                    "descriptor_generation",
                    "selected_zone",
                    "fallback_zone",
                    "provider",
                    "cache_state",
                    "site_id",
                    "deployment_digest",
                    "kind",
                    "status",
                )
            }
            key = hashlib.sha256(
                json.dumps(dimension, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            row = dimensions.setdefault(
                key,
                {
                    "dimensions": dimension,
                    "observations": 0,
                    "request_count": 0,
                    "bytes_delivered": 0,
                    "latency_ms": {"samples": 0, "sum": 0, "max": None},
                    "first_observed_at": item["observed_at"],
                    "last_observed_at": item["observed_at"],
                },
            )
            row["observations"] += 1
            row["request_count"] += item["request_count"]
            row["bytes_delivered"] += item["bytes_delivered"]
            row["first_observed_at"] = min(row["first_observed_at"], item["observed_at"])
            row["last_observed_at"] = max(row["last_observed_at"], item["observed_at"])
            if item["latency_ms"] is not None:
                latency = row["latency_ms"]
                latency["samples"] += 1
                latency["sum"] += item["latency_ms"]
                latency["max"] = max(latency["max"] or 0, item["latency_ms"])
        aggregates = []
        for row in dimensions.values():
            latency = row["latency_ms"]
            latency["average"] = (
                round(latency.pop("sum") / latency["samples"], 2)
                if latency["samples"]
                else None
            )
            aggregates.append(row)
        aggregates.sort(
            key=lambda item: (item["last_observed_at"], item["observations"]), reverse=True
        )
        return {
            "schema": SNAPSHOT_SCHEMA,
            "privacy": {
                "visitor_identity": "not_collected",
                "allowed_dimensions": sorted(_ALLOWED - {"schema", "observed_at"}),
            },
            "filters": {"site_id": site_id, "deployment_digest": deployment_digest},
            "totals": {
                "observations": len(events),
                "request_count": sum(item["observation"]["request_count"] for item in events),
                "bytes_delivered": sum(
                    item["observation"]["bytes_delivered"] for item in events
                ),
                "fallback_observations": sum(
                    item["observation"]["status"] == "fallback" for item in events
                ),
                "unavailable_observations": sum(
                    item["observation"]["status"] in {"unavailable", "tampered"}
                    for item in events
                ),
            },
            "aggregates": aggregates[:limit],
            "observed_at": _now(),
        }

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {
                "schema": REGISTRY_SCHEMA,
                "revision": 0,
                "updated_at": _now(),
                "events": [],
            }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("public Site delivery telemetry is unreadable") from exc
        if payload.get("schema") != REGISTRY_SCHEMA or not isinstance(
            payload.get("events"), list
        ):
            raise RuntimeError("public Site delivery telemetry schema is invalid")
        return payload


__all__ = [
    "PublicSiteDeliveryTelemetryService",
    "normalize_delivery_observation",
]
