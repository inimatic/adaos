from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from adaos.services import device_inventory

ENTITY_OBSERVED = "entity.observed"
ENTITY_DRAFT_NAME_SUGGESTED = "entity.draft_name.suggested"
ENTITY_DISPLAY_NAME_CHANGED = "entity.display_name.changed"
ENTITY_ALIAS_ADDED = "entity.alias.added"
ENTITY_ALIAS_REMOVED = "entity.alias.removed"
ENTITY_ALIAS_DEPRECATED = "entity.alias.deprecated"
ENTITY_ALIAS_CONFLICT_DETECTED = "entity.alias.conflict.detected"
ENTITY_REGISTRY_CHANGED = "entity.registry.changed"
ENTITY_RESOLUTION_AMBIGUOUS = "entity.resolution.ambiguous"
ENTITY_RESOLUTION_FAILED = "entity.resolution.failed"

ENTITY_EVENT_TOPICS: tuple[str, ...] = (
    ENTITY_OBSERVED,
    ENTITY_DRAFT_NAME_SUGGESTED,
    ENTITY_DISPLAY_NAME_CHANGED,
    ENTITY_ALIAS_ADDED,
    ENTITY_ALIAS_REMOVED,
    ENTITY_ALIAS_DEPRECATED,
    ENTITY_ALIAS_CONFLICT_DETECTED,
    ENTITY_REGISTRY_CHANGED,
    ENTITY_RESOLUTION_AMBIGUOUS,
    ENTITY_RESOLUTION_FAILED,
)

EntityStatus = Literal["draft", "confirmed", "observed", "conflicted", "deprecated"]
EntityMatchType = Literal[
    "display_name",
    "registered_name",
    "observed_name",
    "draft_name",
    "alias",
    "fallback_label",
]
EntityLabelRole = Literal["display", "registered", "observed", "draft", "alias", "fallback"]


_SPACE_RE = re.compile(r"\s+")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_or_none(value: Any) -> str | None:
    token = _text(value)
    return token or None


def _tuple_of_texts(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, Iterable):
        items = list(value)
    else:
        items = []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = _text(item)
        if not token:
            continue
        folded = normalize_entity_label(token)
        if folded in seen:
            continue
        seen.add(folded)
        out.append(token)
    return tuple(out)


def _locale(value: Any) -> str:
    token = _text(value)
    return token or "und"


def _preferred_locales(
    *,
    request_locale: str | None = None,
    preferred_locales: Iterable[str] | None = None,
) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    raw_values: list[str | None] = [request_locale]
    raw_values.extend(str(item) for item in tuple(preferred_locales or ()))
    for raw in raw_values:
        token = _locale(raw)
        folded = token.casefold()
        if not token or token == "und" or folded in seen:
            continue
        seen.add(folded)
        out.append(token)
        base = token.split("-", 1)[0]
        if base and base != token and base.casefold() not in seen:
            seen.add(base.casefold())
            out.append(base)
    return tuple(out)


def _locale_match_score(label_locale: str, preferred: tuple[str, ...]) -> float:
    locale = _locale(label_locale)
    if locale == "und":
        return 1.0
    if not preferred:
        return 0.95
    folded = locale.casefold()
    preferred_folded = {item.casefold() for item in preferred}
    if folded in preferred_folded:
        return 1.0
    base = locale.split("-", 1)[0].casefold()
    if base in preferred_folded:
        return 0.98
    return 0.9


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def normalize_entity_label(value: Any) -> str:
    token = _SPACE_RE.sub(" ", _text(value))
    return token.casefold()


def canonical_device_ref(device_ref: str) -> str:
    parsed = device_inventory.parse_device_ref(device_ref)
    if parsed is None:
        token = _text(device_ref)
        return token
    kind, link_id = parsed
    return f"device:{kind}:{link_id}"


def compatibility_device_ref(canonical_ref: str) -> str:
    token = _text(canonical_ref)
    prefix = "device:"
    if not token.startswith(prefix):
        return token
    return token[len(prefix) :]


def _title_token(value: Any) -> str:
    token = _text(value)
    if not token:
        return ""
    known = {
        "android": "Android",
        "chrome": "Chrome",
        "edge": "Edge",
        "firefox": "Firefox",
        "ios": "iOS",
        "iphone": "iPhone",
        "ipad": "iPad",
        "linux": "Linux",
        "mac": "Mac",
        "macos": "macOS",
        "safari": "Safari",
        "tablet": "Tablet",
        "windows": "Windows",
    }
    folded = token.casefold().replace("_", " ").replace("-", " ")
    return known.get(folded, " ".join(part.capitalize() for part in folded.split()))


def _browser_draft_name(identity: Mapping[str, Any], *, fallback: str) -> str | None:
    browser = _title_token(
        identity.get("browser_family")
        or identity.get("browser_name")
        or identity.get("browser")
    )
    os_name = _title_token(identity.get("os_name") or identity.get("os") or identity.get("platform"))
    form_factor = _title_token(identity.get("form_factor") or identity.get("device_type"))
    if form_factor.casefold() in {"desktop", "computer", "pc"}:
        form_factor = ""
    if browser and os_name and form_factor:
        return f"{browser} on {os_name} {form_factor}"
    if browser and os_name:
        return f"{browser} on {os_name}"
    if browser:
        return f"{browser} browser"
    if os_name:
        return f"Browser on {os_name}"
    short_id = _text(fallback)
    if short_id:
        return f"Browser {short_id[-6:]}" if len(short_id) > 6 else f"Browser {short_id}"
    return None


@dataclass(frozen=True)
class EntityLabel:
    text: str
    locale: str = "und"
    role: EntityLabelRole = "alias"
    status: EntityStatus = "confirmed"
    source: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _text(self.text))
        object.__setattr__(self, "locale", _locale(self.locale))
        object.__setattr__(self, "role", _text(self.role) or "alias")
        object.__setattr__(self, "status", _text(self.status) or "confirmed")
        object.__setattr__(self, "source", _text_or_none(self.source))

    @property
    def match_type(self) -> EntityMatchType:
        role = self.role
        if role == "display":
            return "display_name"
        if role == "registered":
            return "registered_name"
        if role == "observed":
            return "observed_name"
        if role == "draft":
            return "draft_name"
        if role == "fallback":
            return "fallback_label"
        return "alias"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "text": self.text,
            "locale": self.locale,
            "role": self.role,
            "status": self.status,
        }
        if self.source:
            payload["source"] = self.source
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        return payload


def _tuple_of_labels(value: Any) -> tuple[EntityLabel, ...]:
    if value is None:
        return ()
    if isinstance(value, (EntityLabel, Mapping, str)):
        items = [value]
    elif isinstance(value, Iterable):
        items = list(value)
    else:
        items = []
    labels: list[EntityLabel] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        if isinstance(item, EntityLabel):
            label = item
        elif isinstance(item, Mapping):
            label = EntityLabel(
                text=item.get("text") or item.get("label") or item.get("value"),
                locale=item.get("locale") or "und",
                role=item.get("role") or "alias",
                status=item.get("status") or "confirmed",
                source=item.get("source"),
                confidence=item.get("confidence") if isinstance(item.get("confidence"), (int, float)) else None,
            )
        else:
            label = EntityLabel(text=item, locale="und", role="alias")
        normalized = normalize_entity_label(label.text)
        if not normalized:
            continue
        key = (normalized, label.locale.casefold(), str(label.role))
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
    return tuple(labels)


@dataclass(frozen=True)
class NamedEntityRecord:
    canonical_ref: str
    kind: str
    display_name: str | None = None
    registered_names: tuple[str, ...] = field(default_factory=tuple)
    observed_name: str | None = None
    draft_name: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    labels: tuple[EntityLabel, ...] = field(default_factory=tuple)
    fallback_label: str | None = None
    scope: Mapping[str, Any] = field(default_factory=dict)
    source: str | None = None
    source_authority: Mapping[str, Any] = field(default_factory=dict)
    status: EntityStatus = "observed"
    updated_at: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_ref", _text(self.canonical_ref))
        object.__setattr__(self, "kind", _text(self.kind))
        object.__setattr__(self, "display_name", _text_or_none(self.display_name))
        object.__setattr__(self, "registered_names", _tuple_of_texts(self.registered_names))
        object.__setattr__(self, "observed_name", _text_or_none(self.observed_name))
        object.__setattr__(self, "draft_name", _text_or_none(self.draft_name))
        object.__setattr__(self, "aliases", _tuple_of_texts(self.aliases))
        object.__setattr__(self, "labels", _tuple_of_labels(self.labels))
        object.__setattr__(self, "fallback_label", _text_or_none(self.fallback_label))
        object.__setattr__(self, "scope", _mapping(self.scope))
        object.__setattr__(self, "source", _text_or_none(self.source))
        object.__setattr__(self, "source_authority", _mapping(self.source_authority))

    @property
    def display_label(self) -> str:
        return resolve_display_label(
            display_name=self.display_name,
            registered_names=self.registered_names,
            observed_name=self.observed_name,
            draft_name=self.draft_name,
            fallback_label=self.fallback_label or self.canonical_ref,
        )

    def label_records(self, *, include_fallback: bool = False) -> tuple[EntityLabel, ...]:
        candidates: list[EntityLabel] = []
        if self.display_name:
            display_status: EntityStatus = (
                self.status if self.status in {"confirmed", "conflicted", "deprecated"} else "confirmed"
            )
            candidates.append(
                EntityLabel(
                    text=self.display_name,
                    locale="und",
                    role="display",
                    status=display_status,
                    source=self.source_authority.get("display_name") or self.source,
                )
            )
        candidates.extend(
            EntityLabel(
                text=item,
                locale="und",
                role="registered",
                status="confirmed",
                source=self.source_authority.get("registered_names") or self.source,
            )
            for item in self.registered_names
        )
        if self.observed_name:
            candidates.append(
                EntityLabel(
                    text=self.observed_name,
                    locale="und",
                    role="observed",
                    status="observed",
                    source=self.source_authority.get("observed_name") or self.source,
                )
            )
        if self.draft_name:
            candidates.append(
                EntityLabel(
                    text=self.draft_name,
                    locale="und",
                    role="draft",
                    status="draft",
                    source=self.source_authority.get("draft_name") or self.source,
                )
            )
        candidates.extend(
            EntityLabel(text=item, locale="und", role="alias", status="confirmed", source=self.source)
            for item in self.aliases
        )
        candidates.extend(self.labels)
        if include_fallback and self.fallback_label:
            candidates.append(
                EntityLabel(text=self.fallback_label, locale="und", role="fallback", status="observed", source="fallback")
            )

        out: list[EntityLabel] = []
        seen: set[tuple[str, str, str]] = set()
        for label in candidates:
            normalized = normalize_entity_label(label.text)
            key = (normalized, label.locale.casefold(), label.role)
            if not normalized or key in seen:
                continue
            seen.add(key)
            out.append(label)
        return tuple(out)

    def label_candidates(self, *, include_fallback: bool = False) -> tuple[tuple[str, EntityMatchType], ...]:
        return tuple((label.text, label.match_type) for label in self.label_records(include_fallback=include_fallback))

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_ref": self.canonical_ref,
            "kind": self.kind,
            "display_name": self.display_name,
            "registered_names": list(self.registered_names),
            "observed_name": self.observed_name,
            "draft_name": self.draft_name,
            "aliases": list(self.aliases),
            "labels": [item.to_dict() for item in self.label_records(include_fallback=False)],
            "fallback_label": self.fallback_label,
            "display_label": self.display_label,
            "scope": dict(self.scope),
            "source": self.source,
            "source_authority": dict(self.source_authority),
            "status": self.status,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class EntityResolutionMatch:
    canonical_ref: str
    kind: str
    text: str
    normalized: str
    start: int
    end: int
    confidence: float
    match_type: EntityMatchType
    display_label: str
    locale: str = "und"

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_ref": self.canonical_ref,
            "kind": self.kind,
            "text": self.text,
            "normalized": self.normalized,
            "start": self.start,
            "end": self.end,
            "confidence": self.confidence,
            "match_type": self.match_type,
            "display_label": self.display_label,
            "locale": self.locale,
        }


@dataclass(frozen=True)
class EntityResolutionAmbiguity:
    text: str
    normalized: str
    candidates: tuple[EntityResolutionMatch, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "normalized": self.normalized,
            "candidates": [item.to_dict() for item in self.candidates],
            "locales": sorted({item.locale for item in self.candidates}),
        }


@dataclass(frozen=True)
class EntityResolutionResult:
    raw_text: str
    normalized_text: str
    resolved_entities: tuple[EntityResolutionMatch, ...] = field(default_factory=tuple)
    unresolved_entity_spans: tuple[str, ...] = field(default_factory=tuple)
    ambiguities: tuple[EntityResolutionAmbiguity, ...] = field(default_factory=tuple)
    request_locale: str | None = None
    preferred_locales: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "normalized_text": self.normalized_text,
            "resolved_entities": [item.to_dict() for item in self.resolved_entities],
            "unresolved_entity_spans": list(self.unresolved_entity_spans),
            "ambiguities": [item.to_dict() for item in self.ambiguities],
            "request_locale": self.request_locale,
            "preferred_locales": list(self.preferred_locales),
        }


def resolve_display_label(
    *,
    display_name: Any = None,
    registered_names: Any = None,
    observed_name: Any = None,
    draft_name: Any = None,
    fallback_label: Any = None,
) -> str:
    display = _text(display_name)
    if display:
        return display
    registered = _tuple_of_texts(registered_names)
    if registered:
        return registered[0]
    observed = _text(observed_name)
    if observed:
        return observed
    draft = _text(draft_name)
    if draft:
        return draft
    return _text(fallback_label)


def _confidence_for_match(match_type: EntityMatchType) -> float:
    if match_type in {"display_name", "alias"}:
        return 1.0
    if match_type == "registered_name":
        return 0.95
    if match_type == "observed_name":
        return 0.85
    if match_type == "draft_name":
        return 0.8
    return 0.55


def _entity_from_device(device: Mapping[str, Any]) -> NamedEntityRecord | None:
    ref = _text(device.get("ref"))
    if not ref:
        return None
    canonical_ref = canonical_device_ref(ref)
    kind_token = _text(device.get("kind"))
    identity = _mapping(device.get("identity"))
    policy = _mapping(device.get("policy"))
    observation = _mapping(device.get("observation"))
    diagnostics = _mapping(device.get("diagnostics"))
    if kind_token == "browser":
        entity_kind = "device.browser"
        fallback = _text(identity.get("browser_device_id")) or compatibility_device_ref(canonical_ref)
        draft_name = _browser_draft_name(identity, fallback=fallback)
    elif kind_token == "member":
        entity_kind = "device.member"
        fallback = _text(identity.get("node_id")) or compatibility_device_ref(canonical_ref)
        draft_name = None
    else:
        entity_kind = f"device.{kind_token}" if kind_token else "device"
        fallback = compatibility_device_ref(canonical_ref)
        draft_name = None
    display_name = _text_or_none(policy.get("display_name"))
    registered_names = _tuple_of_texts(identity.get("node_names"))
    observed_name = _text_or_none(identity.get("hostname"))
    status: EntityStatus = "confirmed" if display_name else "draft" if draft_name else "observed"
    updated_at = observation.get("last_seen_at") if isinstance(observation.get("last_seen_at"), (int, float)) else None
    return NamedEntityRecord(
        canonical_ref=canonical_ref,
        kind=entity_kind,
        display_name=display_name,
        registered_names=registered_names,
        observed_name=observed_name,
        draft_name=draft_name,
        fallback_label=fallback,
        scope={
            "node_id": identity.get("node_id"),
            "browser_device_id": identity.get("browser_device_id"),
            "last_webspace_id": observation.get("last_webspace_id"),
        },
        source="device_inventory",
        source_authority={
            "display_name": "access_links" if display_name else None,
            "registered_names": "device_inventory",
            "observed_name": observation.get("source") or "device_inventory",
            "draft_name": "named_entity_service.browser_draft" if draft_name else None,
            "diagnostics": diagnostics,
        },
        status=status,
        updated_at=updated_at,
    )


def _records_from_lookup_items(
    lookup_name: str,
    items: Any,
    *,
    webspace_id: str,
) -> list[NamedEntityRecord]:
    lookup_to_kind = {
        "modal_id": "modal",
        "app_id": "app",
        "scenario_id": "scenario",
        "webspace_id": "webspace",
        "skill_id": "skill",
    }
    entity_kind = lookup_to_kind.get(lookup_name)
    if not entity_kind:
        return []
    rows = list(items) if isinstance(items, list) else []
    records: list[NamedEntityRecord] = []
    for raw in rows:
        item = _mapping(raw)
        value = _text(item.get("value"))
        if not value:
            continue
        labels = _tuple_of_texts(item.get("labels"))
        display_name = labels[0] if labels else value
        registered_names = (value,) if normalize_entity_label(display_name) != normalize_entity_label(value) else ()
        records.append(
            NamedEntityRecord(
                canonical_ref=f"{entity_kind}:{value}",
                kind=entity_kind,
                display_name=display_name,
                registered_names=registered_names,
                aliases=labels[1:],
                fallback_label=value,
                scope={"webspace_id": webspace_id},
                source="nlu_lookup_tables",
                source_authority={
                    "lookup": lookup_name,
                    "sources": list(item.get("sources") or []),
                },
                status="confirmed",
            )
        )
    return records


class NamedEntityService:
    def __init__(
        self,
        *,
        device_inventory_service: Any | None = None,
        lookup_payload_provider: Any | None = None,
        default_webspace_id: str = "desktop",
        static_entities: Iterable[NamedEntityRecord] | None = None,
    ) -> None:
        self._device_inventory_service = device_inventory_service
        self._lookup_payload_provider = lookup_payload_provider
        self._default_webspace_id = _text(default_webspace_id) or "desktop"
        self._static_entities = tuple(static_entities or ())

    def list_entities(
        self,
        *,
        kind: str | None = None,
        webspace_id: str | None = None,
    ) -> list[NamedEntityRecord]:
        records: list[NamedEntityRecord] = list(self._static_entities)
        records.extend(self._list_device_entities())
        records.extend(self._list_lookup_entities(webspace_id=webspace_id))
        if kind:
            wanted = _text(kind)
            records = [item for item in records if item.kind == wanted]
        records.sort(key=lambda item: (item.kind, item.display_label.casefold(), item.canonical_ref))
        return records

    def resolve_text(
        self,
        text: str,
        *,
        kind: str | None = None,
        include_fallback: bool = False,
        webspace_id: str | None = None,
        request_locale: str | None = None,
        preferred_locales: Iterable[str] | None = None,
    ) -> EntityResolutionResult:
        raw_text = _text(text)
        normalized_text = normalize_entity_label(raw_text)
        locale_order = _preferred_locales(request_locale=request_locale, preferred_locales=preferred_locales)
        records = self.list_entities(kind=kind, webspace_id=webspace_id)
        matches_by_span: dict[tuple[int, int, str], list[EntityResolutionMatch]] = {}
        for record in records:
            labels = sorted(
                record.label_records(include_fallback=include_fallback),
                key=lambda item: (
                    -_locale_match_score(item.locale, locale_order),
                    -(_confidence_for_match(item.match_type)),
                    item.text.casefold(),
                ),
            )
            for label in labels:
                pattern = re.compile(rf"(?<!\w){re.escape(label.text)}(?!\w)", re.IGNORECASE)
                for match in pattern.finditer(raw_text):
                    key = (match.start(), match.end(), normalize_entity_label(match.group(0)))
                    confidence = _confidence_for_match(label.match_type) * _locale_match_score(label.locale, locale_order)
                    matches_by_span.setdefault(key, []).append(
                        EntityResolutionMatch(
                            canonical_ref=record.canonical_ref,
                            kind=record.kind,
                            text=match.group(0),
                            normalized=key[2],
                            start=match.start(),
                            end=match.end(),
                            confidence=round(confidence, 4),
                            match_type=label.match_type,
                            display_label=record.display_label,
                            locale=label.locale,
                        )
                    )

        resolved: list[EntityResolutionMatch] = []
        ambiguities: list[EntityResolutionAmbiguity] = []
        for (_start, _end, normalized), candidates in sorted(matches_by_span.items()):
            unique: dict[str, EntityResolutionMatch] = {}
            for candidate in sorted(candidates, key=lambda item: item.confidence, reverse=True):
                unique.setdefault(candidate.canonical_ref, candidate)
            deduped = tuple(unique.values())
            if len(deduped) == 1:
                resolved.append(deduped[0])
            else:
                ambiguities.append(
                    EntityResolutionAmbiguity(
                        text=deduped[0].text,
                        normalized=normalized,
                        candidates=deduped,
                    )
                )
        return EntityResolutionResult(
            raw_text=raw_text,
            normalized_text=normalized_text,
            resolved_entities=tuple(resolved),
            ambiguities=tuple(ambiguities),
            request_locale=_text_or_none(request_locale),
            preferred_locales=locale_order,
        )

    def _list_device_entities(self) -> list[NamedEntityRecord]:
        try:
            service = self._device_inventory_service or device_inventory.get_device_inventory_service()
            devices = list(service.list_devices() or [])
        except Exception:
            devices = []
        records: list[NamedEntityRecord] = []
        for device in devices:
            record = _entity_from_device(_mapping(device))
            if record is not None:
                records.append(record)
        return records

    def _lookup_payload(self, *, webspace_id: str | None = None) -> Mapping[str, Any]:
        webspace = _text(webspace_id) or self._default_webspace_id
        provider = self._lookup_payload_provider
        if provider is not None:
            try:
                payload = provider(webspace_id=webspace)
            except TypeError:
                payload = provider()
            except Exception:
                return {}
            return payload if isinstance(payload, Mapping) else {}
        try:
            from adaos.services.agent_context import get_ctx
            from adaos.services.nlu_lookup_tables import collect_desktop_lookup_tables

            payload = collect_desktop_lookup_tables(get_ctx(), webspace_id=webspace)
        except Exception:
            return {}
        return payload if isinstance(payload, Mapping) else {}

    def _list_lookup_entities(self, *, webspace_id: str | None = None) -> list[NamedEntityRecord]:
        payload = self._lookup_payload(webspace_id=webspace_id)
        lookups = payload.get("lookups") if isinstance(payload.get("lookups"), Mapping) else {}
        webspace = _text(payload.get("webspace_id")) or _text(webspace_id) or self._default_webspace_id
        records: list[NamedEntityRecord] = []
        records.extend(_records_from_lookup_items("modal_id", lookups.get("modal_id"), webspace_id=webspace))
        records.extend(_records_from_lookup_items("app_id", lookups.get("app_id"), webspace_id=webspace))
        records.extend(_records_from_lookup_items("scenario_id", lookups.get("scenario_id"), webspace_id=webspace))
        records.extend(_records_from_lookup_items("webspace_id", lookups.get("webspace_id"), webspace_id=webspace))
        records.extend(_records_from_lookup_items("skill_id", lookups.get("skill_id"), webspace_id=webspace))
        return records


_SERVICE: NamedEntityService | None = None


def get_named_entity_service() -> NamedEntityService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = NamedEntityService()
    return _SERVICE


def list_entities(*, kind: str | None = None, webspace_id: str | None = None) -> list[dict[str, Any]]:
    return [
        item.to_dict()
        for item in get_named_entity_service().list_entities(kind=kind, webspace_id=webspace_id)
    ]


def resolve_text(
    text: str,
    *,
    kind: str | None = None,
    include_fallback: bool = False,
    webspace_id: str | None = None,
    request_locale: str | None = None,
    preferred_locales: Iterable[str] | None = None,
) -> dict[str, Any]:
    return get_named_entity_service().resolve_text(
        text,
        kind=kind,
        include_fallback=include_fallback,
        webspace_id=webspace_id,
        request_locale=request_locale,
        preferred_locales=preferred_locales,
    ).to_dict()


def _registry_conflicts(records: Iterable[NamedEntityRecord]) -> list[dict[str, Any]]:
    by_label: dict[str, dict[str, Any]] = {}
    for record in records:
        for label in record.label_records(include_fallback=False):
            normalized = normalize_entity_label(label.text)
            if not normalized:
                continue
            bucket_key = f"{label.locale.casefold()}:{normalized}"
            bucket = by_label.setdefault(
                bucket_key,
                {
                    "label": label.text,
                    "locale": label.locale,
                    "normalized": normalized,
                    "candidates": {},
                },
            )
            candidates = bucket["candidates"]
            if record.canonical_ref not in candidates:
                candidates[record.canonical_ref] = {
                    "canonical_ref": record.canonical_ref,
                    "kind": record.kind,
                    "display_label": record.display_label,
                    "locale": label.locale,
                    "match_types": [],
                }
            candidate = candidates[record.canonical_ref]
            if label.match_type not in candidate["match_types"]:
                candidate["match_types"].append(label.match_type)
    conflicts: list[dict[str, Any]] = []
    for bucket in by_label.values():
        candidates = list(bucket["candidates"].values())
        if len(candidates) < 2:
            continue
        candidates.sort(key=lambda item: (str(item.get("kind") or ""), str(item.get("canonical_ref") or "")))
        conflicts.append(
            {
                "label": bucket["label"],
                "locale": bucket["locale"],
                "normalized": bucket["normalized"],
                "candidate_count": len(candidates),
                "candidates": candidates,
            }
        )
    conflicts.sort(
        key=lambda item: (
            str(item.get("locale") or ""),
            str(item.get("normalized") or ""),
            int(item.get("candidate_count") or 0),
        )
    )
    return conflicts


def compact_registry_payload(
    *,
    kind: str | None = None,
    webspace_id: str | None = None,
    service: NamedEntityService | None = None,
) -> dict[str, Any]:
    webspace = _text(webspace_id) or "desktop"
    records = (service or get_named_entity_service()).list_entities(kind=kind, webspace_id=webspace)
    items = [
        {
            "canonical_ref": item.canonical_ref,
            "kind": item.kind,
            "display_label": item.display_label,
            "labels": [label.to_dict() for label in item.label_records(include_fallback=False)],
            "status": item.status,
            "scope": dict(item.scope),
            "source": item.source,
        }
        for item in records
    ]
    conflicts = _registry_conflicts(records)
    fingerprint = hashlib.sha256(
        json.dumps(items, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    return {
        "version": 1,
        "webspace_id": webspace,
        "items": items,
        "summary": {
            "count": len(items),
            "fingerprint": fingerprint,
            "updated_at": time.time(),
            "conflict_count": len(conflicts),
        },
        "conflicts": conflicts,
    }


def entity_event_payload(
    *,
    entity_ref: str,
    entity_kind: str,
    source: str,
    scope: Mapping[str, Any] | None = None,
    actor: str | None = None,
    previous: Mapping[str, Any] | None = None,
    current: Mapping[str, Any] | None = None,
    reason: str | None = None,
    request_id: str | None = None,
    locale: str | None = None,
    preferred_locales: Iterable[str] | None = None,
) -> dict[str, Any]:
    return {
        "entity_ref": _text(entity_ref),
        "entity_kind": _text(entity_kind),
        "scope": _mapping(scope),
        "source": _text(source),
        "actor": _text_or_none(actor),
        "previous": _mapping(previous),
        "current": _mapping(current),
        "reason": _text_or_none(reason),
        "request_id": _text_or_none(request_id),
        "locale": _text_or_none(locale),
        "preferred_locales": list(
            _preferred_locales(request_locale=locale, preferred_locales=preferred_locales)
        ),
        "ts": time.time(),
    }


__all__ = [
    "ENTITY_ALIAS_ADDED",
    "ENTITY_ALIAS_CONFLICT_DETECTED",
    "ENTITY_ALIAS_DEPRECATED",
    "ENTITY_ALIAS_REMOVED",
    "ENTITY_DISPLAY_NAME_CHANGED",
    "ENTITY_DRAFT_NAME_SUGGESTED",
    "ENTITY_EVENT_TOPICS",
    "ENTITY_OBSERVED",
    "ENTITY_REGISTRY_CHANGED",
    "ENTITY_RESOLUTION_AMBIGUOUS",
    "ENTITY_RESOLUTION_FAILED",
    "EntityLabel",
    "EntityResolutionAmbiguity",
    "EntityResolutionMatch",
    "EntityResolutionResult",
    "NamedEntityRecord",
    "NamedEntityService",
    "canonical_device_ref",
    "compatibility_device_ref",
    "compact_registry_payload",
    "entity_event_payload",
    "get_named_entity_service",
    "list_entities",
    "normalize_entity_label",
    "resolve_display_label",
    "resolve_text",
]
