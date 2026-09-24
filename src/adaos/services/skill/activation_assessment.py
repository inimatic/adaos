from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ASSESSMENT_SCHEMA = "adaos.skill.activation_assessment.v1"
ASSESSOR = "builder.compiler"
_STARTUP_TOPICS = {"sys.ready", "runtime.ready", "adaos.runtime.ready"}
_UI_CONTROL_TOPICS = {
    "webio.stream.snapshot.requested",
    "webio.stream.subscription.changed",
    "webio.yjs.snapshot.requested",
    "webio.yjs.subscription.changed",
}
_SUBSCRIBE_DECORATOR = re.compile(
    r"@\s*(?:[A-Za-z_]\w*\.)*subscribe\s*\(",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class HandlerActivationProfile:
    source_digest: str
    subscription_topics: tuple[str, ...]
    dynamic_subscription_count: int
    tool_count: int

    @property
    def has_subscriptions(self) -> bool:
        return bool(self.subscription_topics or self.dynamic_subscription_count)


def handler_may_subscribe(path: Path) -> bool | None:
    """Cheap conservative boot-time check.

    ``None`` means the source could not be inspected and callers must retain the
    legacy eager import. Builder uses the full AST profile below when it needs a
    canonical assessment.
    """

    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    return bool(_SUBSCRIBE_DECORATOR.search(source))


def _decorator_name(value: ast.expr) -> str:
    if isinstance(value, ast.Attribute):
        return value.attr
    return value.id if isinstance(value, ast.Name) else ""


def _decorator_call(value: ast.expr) -> ast.Call | None:
    if not isinstance(value, ast.Call):
        return None
    return value if _decorator_name(value.func) else None


def inspect_handler_activation(path: Path) -> HandlerActivationProfile:
    source = Path(path).read_bytes()
    text = source.decode("utf-8")
    tree = ast.parse(text, filename=str(path))
    topics: list[str] = []
    dynamic = 0
    tools = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            call = _decorator_call(decorator)
            if call is None:
                continue
            name = _decorator_name(call.func)
            if name == "tool":
                tools += 1
                continue
            if name != "subscribe":
                continue
            if call.args and isinstance(call.args[0], ast.Constant) and isinstance(
                call.args[0].value, str
            ):
                topic = call.args[0].value.strip()
                if topic and topic not in topics:
                    topics.append(topic)
            else:
                dynamic += 1
    return HandlerActivationProfile(
        source_digest="sha256:" + hashlib.sha256(source).hexdigest(),
        subscription_topics=tuple(sorted(topics)),
        dynamic_subscription_count=dynamic,
        tool_count=tools,
    )


def _declared_subscriptions(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    events = manifest.get("events")
    raw = events.get("subscribe") if isinstance(events, Mapping) else None
    return tuple(
        sorted(
            {
                str(item).strip()
                for item in raw or []
                if isinstance(item, str) and str(item).strip()
            }
        )
    )


def recommended_activation(
    manifest: Mapping[str, Any],
    profile: HandlerActivationProfile,
) -> dict[str, Any]:
    declared = _declared_subscriptions(manifest)
    topics = tuple(sorted(set(declared) | set(profile.subscription_topics)))
    has_subscriptions = bool(topics or profile.dynamic_subscription_count)
    startup = any(topic in _STARTUP_TOPICS for topic in topics)
    background = any(
        topic not in _STARTUP_TOPICS
        and topic not in _UI_CONTROL_TOPICS
        and not topic.endswith(".action")
        for topic in topics
    ) or bool(profile.dynamic_subscription_count)
    classification = (
        "startup_event_subscriber"
        if startup
        else "event_subscriber"
        if has_subscriptions
        else "tool_only"
    )
    reasons = (
        ["startup_subscription_requires_early_handler_registration"]
        if startup
        else ["event_subscriptions_require_early_handler_registration"]
        if has_subscriptions
        else ["tool_invocation_can_import_handler_on_demand"]
    )
    assessment = {
        "schema": ASSESSMENT_SCHEMA,
        "assessor": ASSESSOR,
        "classification": classification,
        "handler_digest": profile.source_digest,
        "observed_subscriptions": list(profile.subscription_topics),
        "declared_subscriptions": list(declared),
        "dynamic_subscription_count": profile.dynamic_subscription_count,
        "tool_count": profile.tool_count,
        "reasons": reasons,
    }
    return {
        "mode": "lazy" if has_subscriptions else "on_demand",
        "startup_allowed": startup,
        "background_refresh": background if has_subscriptions else False,
        "assessment": assessment,
    }


def activation_without_assessment(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: value.get(key)
        for key in ("mode", "startup_allowed", "background_refresh", "when")
        if key in value
    }


__all__ = [
    "ASSESSMENT_SCHEMA",
    "ASSESSOR",
    "HandlerActivationProfile",
    "activation_without_assessment",
    "handler_may_subscribe",
    "inspect_handler_activation",
    "recommended_activation",
]
