"""Versioned notification contracts and deterministic release compilation."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from string import Formatter
from typing import Any, Literal, Mapping, TypedDict

import yaml
from jsonschema import Draft202012Validator, SchemaError

Mode = Literal["off", "center_only", "digest", "immediate"]
Urgency = Literal["normal", "important", "urgent"]


class NotificationError(ValueError):
    code = "notification_invalid"
    retryable = False

    def as_dict(self):
        result = {"code": self.code, "message": str(self)[:4000], "retryable": self.retryable}
        if isinstance(self, NotificationBackpressure):
            result.update(reason=self.reason, retry_after_seconds=self.retry_after_seconds)
        return result


class NotificationConflict(NotificationError):
    code = "notification_conflict"


class NotificationUnavailable(NotificationError):
    code = "notification_unavailable"
    retryable = True


class NotificationBackpressure(NotificationUnavailable):
    """Core did not admit the intent; replay the original durable source outbox."""
    code = "notification_backpressure"
    def __init__(self, reason: str, *, retry_after_seconds: int):
        self.reason = reason
        self.retry_after_seconds = max(1, min(3600, int(retry_after_seconds)))
        super().__init__(reason)


class NotificationReceipt(TypedDict):
    schema: str
    intent_id: str
    notification_id: str | None
    content_revision: int
    status: Literal["committed", "suppressed", "stale"]


class NotificationPage(TypedDict):
    items: list[dict[str, Any]]
    next_cursor: str | None


class PreferenceChoice(TypedDict):
    mode: Mode
    endpoint_ids: list[str]


@dataclass(frozen=True)
class ProducerBinding:
    """Core-issued invocation binding. Never deserialize this from Application input."""
    application_id: str
    instance_id: str
    namespace: str
    release_digest: str
    principal_ref: str
    subnet_ref: str
    author_ref: str
    author_label: str
    test_only: bool = False
    admitted_recipients: tuple[str, ...] = ()
    authority: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for key in ("application_id", "instance_id", "namespace", "release_digest",
                    "principal_ref", "subnet_ref", "author_ref", "author_label"):
            token(getattr(self, key), key)
        if not self.principal_ref.startswith("user:"):
            raise PermissionError("notification principal must be a verified user")


def token(value: Any, field: str, limit: int = 240) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise NotificationError(f"{field} must contain 1..{limit} characters")
    return value


def dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise NotificationError("notification data must be finite JSON") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(dump(value).encode()).hexdigest()


def _bounded_payload_schema(schema):
    """A closed structural subset prevents regex/combinator work in Core ingress."""
    allowed = {"$schema", "$id", "$comment", "title", "description", "default", "examples", "deprecated",
        "readOnly", "writeOnly", "type", "properties", "required", "additionalProperties", "items",
        "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "minProperties", "maxProperties",
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "enum", "const"}
    stack, count = [(schema, 0)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > 256 or depth > 8 or not isinstance(node, dict):
            raise NotificationError("notification payload schema exceeds structural budget (256 nodes / depth 8)")
        if set(node) - allowed:
            raise NotificationError("notification payload schema uses an unsupported keyword; regex, references and combinators are not admitted")
        if not any(key in node for key in ("type", "enum", "const")):
            raise NotificationError("notification payload fields must declare a type, enum or constant")
        if "enum" in node and (not isinstance(node["enum"], list) or len(node["enum"]) > 100):
            raise NotificationError("notification payload enum exceeds 100 entries")
        properties = node.get("properties", {})
        if not isinstance(properties, dict) or len(properties) > 100:
            raise NotificationError("notification payload object exceeds 100 declared properties")
        types = node.get("type", [])
        if (properties or types == "object" or isinstance(types, list) and "object" in types) and node.get("additionalProperties") is not False:
            raise NotificationError("nested notification payload objects must be closed")
        stack.extend((item, depth + 1) for item in properties.values())
        if "items" in node:
            stack.append((node["items"], depth + 1))
        unique_bound = node.get("maxItems", 101)
        if node.get("uniqueItems") and (type(unique_bound) is not int or not 0 <= unique_bound <= 100):
            raise NotificationError("unique notification arrays require maxItems <=100")


@lru_cache(maxsize=16)
def validator(name: str) -> Draft202012Validator:
    path = Path(__file__).resolve().parents[1] / "abi" / f"notifications.{name}.v1.schema.json"
    return Draft202012Validator(json.loads(path.read_text(encoding="utf8")))


def validate(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    if len(dump(result).encode()) > 131072:
        raise NotificationError("notification contract exceeds 128 KiB")
    errors = list(validator(name).iter_errors(result))
    if errors:
        error = errors[0]
        raise NotificationError(f"{name}.{'.'.join(map(str, error.absolute_path))}: {error.message}")
    return result


def _document(value: Any, root: Path | None) -> Any:
    if not isinstance(value, str):
        return value
    if root is None:
        raise NotificationError("relative notification document needs an admitted package root")
    target = (root / value).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise NotificationError("notification document escapes package or is missing")
    if target.stat().st_size > 65536:
        raise NotificationError("notification document exceeds 64 KiB")
    return yaml.safe_load(target.read_text(encoding="utf8"))


def compile_manifest(value: Mapping[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    manifest = validate("manifest", value)
    categories = {}
    disclosures = []
    for item in manifest["categories"]:
        category = copy.deepcopy(item)
        category_id = category["id"]
        if category_id in categories:
            raise NotificationError("duplicate notification category")
        category["payload_schema"] = _document(category["payload_schema"], root)
        category["template"] = _document(category["template"], root)
        schema = category["payload_schema"]
        if not isinstance(schema, dict) or schema.get("type") != "object" or schema.get("additionalProperties") is not False:
            raise NotificationError("notification payload must be a closed object schema")
        # Local schemas cannot initiate network access or inspect package-external files.
        if any(key in dump(schema) for key in ('"$ref"', '"$dynamicRef"')):
            raise NotificationError("notification payload schema must be self-contained")
        _bounded_payload_schema(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise NotificationError("notification payload schema is invalid") from exc
        validate("manifest", {"schema": manifest["schema"], "categories": [category]})
        if not isinstance(category["template"], dict):
            raise NotificationError("notification template must resolve to localized text")
        for text in [*category["template"].values(), *(item["target"] for item in category.get("actions", []))]:
            for _, field, spec, conversion in Formatter().parse(text):
                if field is not None and (
                    not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", field)
                    or field not in schema.get("properties", {}) or spec or conversion
                ):
                    raise NotificationError("template uses an undeclared or unsafe field")
        category.setdefault("grouping", {"replacement": "none"})
        category.setdefault("material_updates", False)
        category.setdefault("actions", [])
        categories[category_id] = category
        disclosures.append({key: category[key] for key in (
            "id", "title_key", "purpose_key", "recipient_policy", "sensitivity", "maximum_urgency",
        )})
    result = {"schema": "adaos.notifications.compiled.v1", "categories": categories,
              "permission_disclosure": disclosures}
    return {**result, "digest": "sha256:" + digest(result)}


def render(category: Mapping[str, Any], payload: Mapping[str, Any], locale: str = "en") -> str:
    if len(dump(payload).encode()) > 32768:
        raise NotificationError("notification payload exceeds 32 KiB")
    errors = list(Draft202012Validator(category["payload_schema"]).iter_errors(dict(payload)))
    if errors:
        raise NotificationError("notification payload does not match the declared schema")
    templates = category["template"]
    template = templates.get(locale) or templates.get(locale.split("-")[0]) or templates.get("en") or next(iter(templates.values()))
    try:
        text = template.format_map(dict(payload))
    except (KeyError, ValueError, TypeError) as exc:
        raise NotificationError("notification template parameters are missing") from exc
    if len(text) > 8000:
        raise NotificationError("rendered notification exceeds 8000 characters")
    return text
