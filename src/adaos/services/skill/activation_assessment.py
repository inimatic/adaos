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
_MODE_RANK = {"on_demand": 0, "lazy": 1, "eager": 2}
_DECLARED_POLICY_REASON = "declared_activation_policy_retained"


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
    # Builder/compiler evidence describes the canonical package payload. Git
    # autocrlf and Windows write_text must not emit an assessment that becomes
    # stale solely because the package builder normalizes text to LF.
    source = Path(path).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return inspect_handler_activation_source(source, filename=str(path))


def inspect_handler_activation_source(
    source: bytes, *, filename: str = "<handler>"
) -> HandlerActivationProfile:
    """Inspect exact bytes, including canonical bytes already sealed in a package."""
    text = source.decode("utf-8")
    tree = ast.parse(text, filename=filename)
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


def _minimum_policy(
    *,
    topics: tuple[str, ...],
    dynamic_subscription_count: int,
) -> dict[str, Any]:
    has_subscriptions = bool(topics or dynamic_subscription_count)
    startup = any(topic in _STARTUP_TOPICS for topic in topics)
    background = any(
        topic not in _STARTUP_TOPICS
        and topic not in _UI_CONTROL_TOPICS
        and not topic.endswith(".action")
        for topic in topics
    ) or bool(dynamic_subscription_count)
    return {
        "mode": "lazy" if has_subscriptions else "on_demand",
        "startup_allowed": startup,
        "background_refresh": background if has_subscriptions else False,
    }


def _assessment_minimum_policy(value: Any) -> dict[str, Any] | None:
    """Reconstruct the source minimum pinned by an existing assessment."""

    if not isinstance(value, Mapping):
        return None
    observed = value.get("observed_subscriptions")
    declared = value.get("declared_subscriptions")
    if not isinstance(observed, list) or not isinstance(declared, list):
        return None
    try:
        dynamic = int(value.get("dynamic_subscription_count") or 0)
    except (TypeError, ValueError):
        return None
    topics = tuple(
        sorted(
            {
                str(item).strip()
                for item in (*observed, *declared)
                if isinstance(item, str) and str(item).strip()
            }
        )
    )
    return _minimum_policy(topics=topics, dynamic_subscription_count=dynamic)


def _declared_policy_override(
    manifest: Mapping[str, Any],
    minimum: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return an explicit policy only when it is at least as active as required.

    The source assessment supplies a safety minimum, not an optimization
    mandate.  An author may intentionally keep a service eager or permit its
    background worker.  Compiler-produced policies remain re-optimizable: an
    unchanged assessed policy is not mistaken for an author override.
    """

    runtime = manifest.get("runtime")
    activation = runtime.get("activation") if isinstance(runtime, Mapping) else None
    if not isinstance(activation, Mapping):
        return None
    mode = str(activation.get("mode") or "").strip().lower()
    if mode not in _MODE_RANK:
        return None
    candidate = {
        "mode": mode,
        "startup_allowed": activation.get("startup_allowed") is True,
        "background_refresh": activation.get("background_refresh") is True,
    }
    assessment = activation.get("assessment")
    reasons = assessment.get("reasons") if isinstance(assessment, Mapping) else ()
    previously_retained = _DECLARED_POLICY_REASON in {
        str(item).strip() for item in reasons or () if isinstance(item, str)
    }
    previous_minimum = _assessment_minimum_policy(assessment)
    explicit = (
        assessment is None
        or previously_retained
        or (previous_minimum is not None and candidate != previous_minimum)
    )
    if not explicit:
        return None
    if _MODE_RANK[candidate["mode"]] < _MODE_RANK[str(minimum["mode"])]:
        return None
    if bool(minimum["startup_allowed"]) and not candidate["startup_allowed"]:
        return None
    if bool(minimum["background_refresh"]) and not candidate["background_refresh"]:
        return None
    return candidate


def recommended_activation(
    manifest: Mapping[str, Any],
    profile: HandlerActivationProfile,
) -> dict[str, Any]:
    declared = _declared_subscriptions(manifest)
    topics = tuple(sorted(set(declared) | set(profile.subscription_topics)))
    has_subscriptions = bool(topics or profile.dynamic_subscription_count)
    minimum = _minimum_policy(
        topics=topics,
        dynamic_subscription_count=profile.dynamic_subscription_count,
    )
    startup = bool(minimum["startup_allowed"])
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
    retained = _declared_policy_override(manifest, minimum)
    if retained is not None and retained != minimum:
        reasons.append(_DECLARED_POLICY_REASON)
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
        **(retained or minimum),
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
    "inspect_handler_activation_source",
    "recommended_activation",
]
