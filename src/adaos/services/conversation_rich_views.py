"""Immutable rich-view definitions with deterministic channel fallbacks."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator


RICH_VIEW_SCHEMA = "adaos.conversation.rich_view.v1"
_WEB_ORDER = ("panel", "drawer", "modal", "compact_message", "deep_link")
_LIMITED_ORDER = ("compact_message", "deep_link")


class ConversationRichViewError(ValueError):
    """Raised when a view cannot be safely registered or presented."""


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "abi" / "conversation.rich_view.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_rich_view(value: Mapping[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(dict(value))
    errors = sorted(
        Draft202012Validator(_schema()).iter_errors(record),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "$"
        raise ConversationRichViewError(
            f"rich view validation failed at {location}: {errors[0].message}"
        )
    kinds = [str(item["kind"]) for item in record["presentations"]]
    if len(kinds) != len(set(kinds)):
        raise ConversationRichViewError("rich view presentation kinds must be unique")
    if "compact_message" not in kinds and "deep_link" not in kinds:
        raise ConversationRichViewError(
            "rich view requires a compact_message or deep_link limited-channel fallback"
        )
    return record


@dataclass(slots=True)
class ConversationRichViewRegistry:
    definitions: Iterable[Mapping[str, Any]] = ()
    _definitions: dict[tuple[str, int], dict[str, Any]] = field(init=False, default_factory=dict)
    _digests: dict[tuple[str, int], str] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        for definition in self.definitions:
            self.register(definition)

    def register(self, definition: Mapping[str, Any]) -> dict[str, Any]:
        record = validate_rich_view(definition)
        key = (str(record["view_id"]), int(record["version"]))
        digest = _digest(record)
        previous = self._digests.get(key)
        if previous is not None and previous != digest:
            raise ConversationRichViewError(
                f"mutable rich view registration rejected: {key[0]}@{key[1]}"
            )
        self._definitions[key] = record
        self._digests[key] = digest
        return {"view_ref": f"{key[0]}@{key[1]}", "definition_digest": digest}

    def resolve(
        self,
        view_id: str,
        capability_profile: Mapping[str, Any],
        *,
        version: int | None = None,
        presentation_hint: str | None = None,
    ) -> dict[str, Any]:
        identifier = str(view_id or "").strip()
        candidates = [key for key in self._definitions if key[0] == identifier]
        if not candidates:
            raise ConversationRichViewError(f"rich view is not registered: {identifier}")
        selected_version = int(version) if version is not None else max(key[1] for key in candidates)
        key = (identifier, selected_version)
        definition = self._definitions.get(key)
        if definition is None:
            raise ConversationRichViewError(
                f"rich view version is not registered: {identifier}@{selected_version}"
            )
        capabilities = {
            str(name): bool(enabled)
            for name, enabled in dict(capability_profile.get("capabilities") or {}).items()
        }
        surface = str(capability_profile.get("surface") or "chat").strip().lower()
        transport = str(capability_profile.get("transport") or "").strip().lower()
        order = list(
            _WEB_ORDER
            if transport == "web" or surface in {"web", "desktop", "browser"}
            else _LIMITED_ORDER
        )
        hint = str(presentation_hint or "").strip()
        if hint in order:
            order.remove(hint)
            order.insert(0, hint)
        indexed = {str(item["kind"]): dict(item) for item in definition["presentations"]}
        considered: list[dict[str, Any]] = []
        for kind in order:
            presentation = indexed.get(kind)
            if presentation is None:
                continue
            missing = [
                item
                for item in presentation["required_capabilities"]
                if not capabilities.get(str(item), False)
            ]
            considered.append({"kind": kind, "missing": missing})
            if missing:
                continue
            return {
                "schema": "adaos.conversation.rich_view_presentation.v1",
                "supported": True,
                "view_ref": f"{identifier}@{selected_version}",
                "definition_digest": self._digests[key],
                "kind": kind,
                "renderer_ref": presentation["renderer_ref"],
                "semantic_equivalence": presentation.get("semantic_equivalence") or "bounded",
                "data_contract_ref": definition.get("data_contract_ref"),
                "capability_profile_ref": {
                    "profile_id": capability_profile.get("profile_id"),
                    "version": capability_profile.get("version"),
                },
                "considered": considered,
            }
        return {
            "schema": "adaos.conversation.rich_view_presentation.v1",
            "supported": False,
            "view_ref": f"{identifier}@{selected_version}",
            "definition_digest": self._digests[key],
            "kind": "unsupported",
            "reason_code": "required_view_capabilities_unavailable",
            "considered": considered,
        }


__all__ = [
    "ConversationRichViewError",
    "ConversationRichViewRegistry",
    "RICH_VIEW_SCHEMA",
    "validate_rich_view",
]
