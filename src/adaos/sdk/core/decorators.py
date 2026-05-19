from __future__ import annotations
from typing import Any, Callable, Dict, Iterable, List, Tuple, Optional
import asyncio
import inspect
import logging
import os
import time
from pathlib import Path
from adaos.sdk.data.bus import on, emit
from adaos.sdk.data.context import set_current_skill, clear_current_skill
from adaos.sdk.core._ctx import require_ctx
from adaos.sdk.core.errors import SdkRuntimeNotInitialized
from adaos.sdk.io.context import io_meta
from adaos.services.node_config import load_config
from adaos.services.status.hot_events import HotEventBudget
from adaos.services.skill.activation import load_skill_activation_policy, subscription_strategy_for_policy

# публичные реестры (стабильные имена)
subscriptions: List[Tuple[str, Callable]] = []
tools_registry: Dict[str, Dict[str, Callable]] = {}
tools_meta: Dict[str, dict] = {}  # по qualname функции
event_payloads: Dict[str, dict] = {}  # topic -> schema
emits_map: Dict[str, set[str]] = {}  # qualname -> {topics}
_registered: bool = False  # внутренняя защита от двойной регистрации
_SUBSCRIPTIONS = subscriptions
_TOOLS = tools_registry
_LOG = logging.getLogger("adaos.sdk.subscriptions")
_SUBSCRIPTION_DENY_LOG_AT: Dict[str, float] = {}
_SUBSCRIPTION_DENY_LOG_INTERVAL_S = 5.0
_SKILL_SUBSCRIPTION_GENERATIONS: Dict[str, int] = {}
_STREAM_CONTROL_SUBSCRIPTION_TOPICS = {
    "webio.stream.snapshot.requested",
    "webio.stream.subscription.changed",
}


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(str(os.getenv(name, str(default)) or str(default)).strip()))
    except Exception:
        return max(minimum, int(default))


_CRITICAL_CONTROL_PLANE_SUBSCRIPTION_TOPICS = {
    "core.update.status",
    "hub.core_update.status",
    "subnet.member.link.up",
    "subnet.member.link.down",
    "subnet.member.snapshot.changed",
    "subnet.member.update.result",
}
_CRITICAL_CONTROL_PLANE_BUDGET = HotEventBudget(
    debounce_ms=_env_int("ADAOS_SKILL_SUBSCRIPTION_CRITICAL_DEBOUNCE_MS", 2000, minimum=0),
    window_ms=_env_int("ADAOS_SKILL_SUBSCRIPTION_CRITICAL_WINDOW_MS", 10000),
    max_events=_env_int("ADAOS_SKILL_SUBSCRIPTION_CRITICAL_MAX_EVENTS", 3),
)
_SUBSCRIPTION_CRITICAL_BYPASS_LOG_AT: Dict[str, float] = {}


def _topic_matches_any(topic: str, patterns: str) -> bool:
    try:
        topic0 = str(topic or "")
        for raw in str(patterns or "").split(","):
            pat = raw.strip()
            if not pat:
                continue
            if pat == "*" or topic0 == pat:
                return True
            if pat.endswith("*") and topic0.startswith(pat[:-1]):
                return True
        return False
    except Exception:
        return False


def _run_sync_subscription_in_thread(topic: str) -> bool:
    """Keep known expensive synchronous skill handlers off the main event loop."""

    try:
        if str(os.getenv("ADAOS_SYNC_SUBSCRIPTION_TO_THREAD", "1") or "1").strip().lower() in {
            "0",
            "false",
            "no",
            "off",
        }:
            return False
        patterns = os.getenv(
            "ADAOS_SYNC_SUBSCRIPTION_THREAD_TOPICS",
            "sys.ready,webio.stream.snapshot.requested",
        )
        return _topic_matches_any(topic, patterns)
    except Exception:
        return False


def _local_node_id() -> str:
    try:
        conf = load_config()
        node_id = str(getattr(conf, "node_id", "") or "").strip()
        if node_id:
            return node_id
        nested = str(getattr(getattr(conf, "node_settings", None), "id", "") or "").strip()
        if nested:
            return nested
    except Exception:
        pass
    return ""


def _event_payload_dict(evt: object) -> dict[str, Any]:
    if isinstance(evt, dict):
        nested = evt.get("payload") if "payload" in evt and "type" in evt else None
        if isinstance(nested, dict):
            return nested
        return evt
    payload = getattr(evt, "payload", None) if hasattr(evt, "payload") else None
    if isinstance(payload, dict):
        return payload
    return {}


def _target_node_id_from_event(evt: object) -> str:
    payload = _event_payload_dict(evt)
    if not isinstance(payload, dict):
        return ""
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    return str(
        payload.get("target_node_id")
        or payload.get("node_target_id")
        or meta.get("target_node_id")
        or meta.get("node_target_id")
        or ""
    ).strip()


def _skill_event_targets_this_node(evt: object) -> bool:
    target_node_id = _target_node_id_from_event(evt)
    if not target_node_id:
        return True
    return target_node_id == _local_node_id()


def _webspace_id_from_event(evt: object) -> str:
    payload = _event_payload_dict(evt)
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    return str(
        payload.get("webspace_id")
        or payload.get("workspace_id")
        or meta.get("webspace_id")
        or meta.get("workspace_id")
        or ""
    ).strip()


def _payload_value(payload: dict[str, Any], *names: str) -> str:
    for name in names:
        try:
            value = payload.get(name)
        except Exception:
            value = None
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _nested_payload(payload: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        try:
            value = payload.get(name)
        except Exception:
            value = None
        if isinstance(value, dict):
            return value
    return {}


def _critical_control_plane_budget_key(skill_name: str | None, topic: str, evt: object) -> str:
    payload = _event_payload_dict(evt)
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    status = _nested_payload(payload, "status", "update_status")
    snapshot_update = _nested_payload(payload, "snapshot_update")
    snapshot_build = _nested_payload(payload, "snapshot_build")
    parts = [
        f"skill={skill_name or '-'}",
        f"webspace={_webspace_id_from_event(evt) or 'default'}",
        f"target={_target_node_id_from_event(evt) or _payload_value(payload, 'node_id', 'member_id') or '-'}",
        f"state={_payload_value(payload, 'state') or _payload_value(status, 'state') or _payload_value(snapshot_update, 'state') or '-'}",
        f"phase={_payload_value(payload, 'phase') or _payload_value(status, 'phase') or _payload_value(snapshot_update, 'phase') or '-'}",
        f"action={_payload_value(payload, 'action') or _payload_value(status, 'action') or _payload_value(snapshot_update, 'action') or '-'}",
        f"version={_payload_value(payload, 'target_version', 'target_rev') or _payload_value(status, 'target_version', 'target_rev') or '-'}",
        f"slot={_payload_value(payload, 'active_slot') or _payload_value(status, 'active_slot') or '-'}",
        f"commit={_payload_value(payload, 'active_git_short_commit') or _payload_value(status, 'active_git_short_commit') or _payload_value(snapshot_build, 'runtime_git_short_commit') or '-'}",
        f"event={str(getattr(evt, 'type', '') or topic or '').strip() or topic}",
    ]
    try:
        if isinstance(meta, dict):
            trace_id = _payload_value(meta, "trace_id", "yws_attempt_id", "event_id")
            if trace_id:
                parts.append(f"trace={trace_id}")
    except Exception:
        pass
    return "|".join(parts)


def _admit_critical_control_plane_subscription(
    skill_name: str | None,
    topic: str,
    evt: object,
    admission: dict[str, Any],
) -> dict[str, Any]:
    if admission.get("allowed", True):
        return admission
    topic_id = str(topic or "").strip()
    if topic_id not in _CRITICAL_CONTROL_PLANE_SUBSCRIPTION_TOPICS:
        return admission
    decision = _CRITICAL_CONTROL_PLANE_BUDGET.admit(
        topic_id,
        key=_critical_control_plane_budget_key(skill_name, topic_id, evt),
    )
    base = dict(admission or {})
    owner_guard_reason = str(base.get("reason") or "owner_guard_denied")
    base.update(
        {
            "governed": True,
            "critical_control_plane": True,
            "owner_guard_allowed": False,
            "owner_guard_reason": owner_guard_reason,
            "owner_guard_admission": dict(admission or {}),
            "hot_event": decision.to_dict(),
            "retry_after_s": float(decision.retry_after_ms or 0) / 1000.0,
        }
    )
    if decision.admitted:
        base.update(
            {
                "allowed": True,
                "reason": "critical_control_plane_budget",
            }
        )
        return base
    base.update(
        {
            "allowed": False,
            "reason": f"critical_control_plane_{decision.reason}",
        }
    )
    return base


def _admit_skill_subscription_yjs_work(skill_name: str | None, topic: str, evt: object) -> dict[str, Any]:
    if not skill_name:
        return {"allowed": True, "governed": False, "reason": "not_a_skill_subscription"}
    if str(topic or "").strip() in _STREAM_CONTROL_SUBSCRIPTION_TOPICS:
        return {
            "allowed": True,
            "governed": False,
            "reason": "stream_control_uses_stream_guard",
        }
    try:
        from adaos.services.yjs.owner_guard import admit_owner_work, skill_owner

        admission = admit_owner_work(
            webspace_id=_webspace_id_from_event(evt) or None,
            owner=skill_owner(skill_name),
            root_names=["data"],
            path=f"event/{topic}",
            source="sdk.subscription",
            channel="skill.subscription",
            work_kind="skill_subscription",
            tool=f"{skill_name}:subscribe:{topic}",
        )
        return _admit_critical_control_plane_subscription(skill_name, topic, evt, admission)
    except Exception:
        _LOG.debug(
            "failed to apply YJS owner guard for skill subscription skill=%s topic=%s",
            skill_name,
            topic,
            exc_info=True,
        )
        return {"allowed": True, "governed": False, "reason": "owner_guard_unavailable"}


def _log_subscription_critical_bypass(skill_name: str, topic: str, admission: dict[str, Any]) -> None:
    key = f"{admission.get('webspace_id') or '-'}:{skill_name}:{topic}:critical"
    now = time.monotonic()
    last = float(_SUBSCRIPTION_CRITICAL_BYPASS_LOG_AT.get(key) or 0.0)
    if now - last < _SUBSCRIPTION_DENY_LOG_INTERVAL_S:
        return
    _SUBSCRIPTION_CRITICAL_BYPASS_LOG_AT[key] = now
    hot_event = admission.get("hot_event") if isinstance(admission.get("hot_event"), dict) else {}
    _LOG.warning(
        "skill subscription admitted by critical control-plane budget skill=%s topic=%s owner=%s owner_guard_reason=%s budget_reason=%s key=%s",
        skill_name,
        topic,
        admission.get("owner") or f"skill:{skill_name}",
        admission.get("owner_guard_reason") or "-",
        hot_event.get("reason") or "-",
        hot_event.get("key") or "-",
    )


def _log_subscription_denied(skill_name: str, topic: str, admission: dict[str, Any]) -> None:
    key = f"{admission.get('webspace_id') or '-'}:{skill_name}:{topic}"
    now = time.monotonic()
    last = float(_SUBSCRIPTION_DENY_LOG_AT.get(key) or 0.0)
    if now - last < _SUBSCRIPTION_DENY_LOG_INTERVAL_S:
        return
    _SUBSCRIPTION_DENY_LOG_AT[key] = now
    quarantine = admission.get("quarantine") if isinstance(admission.get("quarantine"), dict) else {}
    _LOG.warning(
        "skill subscription skipped by YJS owner guard skill=%s topic=%s owner=%s reason=%s retry_after_s=%.1f trigger=%s",
        skill_name,
        topic,
        admission.get("owner") or f"skill:{skill_name}",
        admission.get("reason") or "owner_quarantined",
        float(admission.get("retry_after_s") or 0.0),
        quarantine.get("trigger") or admission.get("policy_state") or "-",
    )


def subscribe(topic: str):
    """Регистрирует обработчик; фактическая подписка делает register_subscriptions()."""

    def deco(fn: Callable):
        subscriptions.append((topic, fn))
        return fn

    return deco


def _subscription_is_current(skill_name: Optional[str], generation: int | None) -> bool:
    if not skill_name or generation is None:
        return True
    return generation == int(_SKILL_SUBSCRIPTION_GENERATIONS.get(skill_name) or generation)


def _target_subscription_entries(skill_names: Iterable[str] | None) -> list[Tuple[str, Callable]]:
    targets = {str(item or "").strip() for item in (skill_names or []) if str(item or "").strip()}
    if not targets:
        return list(subscriptions)
    latest: dict[tuple[str, str], tuple[int, str, Callable]] = {}
    for idx, (topic, fn) in enumerate(subscriptions):
        skill_name = _infer_skill_name(fn)
        if not skill_name or skill_name not in targets:
            continue
        latest[(skill_name, topic)] = (idx, topic, fn)
    return [(topic, fn) for idx, topic, fn in sorted(latest.values(), key=lambda item: item[0])]


async def register_subscriptions(
    *,
    skill_names: Iterable[str] | None = None,
    force: bool = False,
):
    """Подписать все функции, помеченные @subscribe, на bus (однократно)."""
    global _registered
    target_skills = {str(item or "").strip() for item in (skill_names or []) if str(item or "").strip()}
    if _registered and not force and not target_skills:
        return
    if target_skills:
        for skill_name in target_skills:
            _SKILL_SUBSCRIPTION_GENERATIONS[skill_name] = int(
                _SKILL_SUBSCRIPTION_GENERATIONS.get(skill_name) or 0
            ) + 1
    skill_topic_handlers: Dict[str, Dict[str, str]] = {}
    skill_summaries: Dict[str, list[tuple[str, str]]] = {}

    for topic, fn in _target_subscription_entries(target_skills):
        skill_name = _infer_skill_name(fn)
        generation: int | None = None
        if skill_name:
            generation = int(_SKILL_SUBSCRIPTION_GENERATIONS.setdefault(skill_name, 1))

        if skill_name:
            handlers_for_skill = skill_topic_handlers.setdefault(skill_name, {})
            if topic in handlers_for_skill:
                _LOG.warning(
                    "duplicate subscription skipped skill=%s topic=%s handler=%s existing=%s",
                    skill_name,
                    topic,
                    f"{fn.__module__}.{fn.__name__}",
                    handlers_for_skill[topic],
                )
                continue
            handlers_for_skill[topic] = f"{fn.__module__}.{fn.__name__}"

        if inspect.iscoroutinefunction(fn):

            async def _wrap(evt, _fn=fn, _skill=skill_name, _topic=topic, _generation=generation):
                if not _subscription_is_current(_skill, _generation):
                    return None
                if _skill and not _skill_event_targets_this_node(evt):
                    return None
                admission = _admit_skill_subscription_yjs_work(_skill, _topic, evt)
                if not admission.get("allowed", True):
                    if _skill:
                        _log_subscription_denied(_skill, _topic, admission)
                    return None
                if _skill and admission.get("critical_control_plane") and not admission.get("owner_guard_allowed", True):
                    _log_subscription_critical_bypass(_skill, _topic, admission)
                pushed = _maybe_push_skill(_fn, _skill)
                try:
                    payload = getattr(evt, "payload", None) if hasattr(evt, "payload") else None
                    meta = payload.get("_meta") if isinstance(payload, dict) else None
                    if isinstance(meta, dict):
                        with io_meta(meta):
                            return await _fn(evt)
                    return await _fn(evt)
                finally:
                    if pushed:
                        clear_current_skill()

        else:

            async def _wrap(evt, _fn=fn, _skill=skill_name, _topic=topic, _generation=generation):
                if not _subscription_is_current(_skill, _generation):
                    return None
                if _skill and not _skill_event_targets_this_node(evt):
                    return None
                admission = _admit_skill_subscription_yjs_work(_skill, _topic, evt)
                if not admission.get("allowed", True):
                    if _skill:
                        _log_subscription_denied(_skill, _topic, admission)
                    return None
                if _skill and admission.get("critical_control_plane") and not admission.get("owner_guard_allowed", True):
                    _log_subscription_critical_bypass(_skill, _topic, admission)
                pushed = _maybe_push_skill(_fn, _skill)
                try:
                    def _call_sync_handler():
                        payload = getattr(evt, "payload", None) if hasattr(evt, "payload") else None
                        meta = payload.get("_meta") if isinstance(payload, dict) else None
                        if isinstance(meta, dict):
                            with io_meta(meta):
                                return _fn(evt)
                        return _fn(evt)

                    if _run_sync_subscription_in_thread(_topic):
                        return await asyncio.to_thread(_call_sync_handler)
                    return _call_sync_handler()
                finally:
                    if pushed:
                        clear_current_skill()

        # Attach debug metadata so the core event bus can log slow handlers
        handler_name = f"{fn.__module__}.{fn.__name__}"
        setattr(_wrap, "_adaos_skill", skill_name or "<unknown>")
        setattr(_wrap, "_adaos_topic", topic)
        setattr(_wrap, "_adaos_handler", handler_name)
        setattr(_wrap, "_adaos_generation", generation)
        skill_key = skill_name or "<unknown>"
        skill_summaries.setdefault(skill_key, []).append((topic, fn.__name__))
        await on(topic, _wrap)
        try:
            await emit(
                "skill.subscription.registered",
                {"topic": topic, "handler": handler_name},
                source="sdk.core.decorators",
            )
        except Exception:
            _LOG.warning(
                "failed to emit subscription event handler=%s topic=%s",
                handler_name,
                topic,
                exc_info=True,
            )
    for skill, entries in sorted(skill_summaries.items()):
        summary = ", ".join(f"{topic}: {handler}" for topic, handler in entries)
        _LOG.info("skill=%s subscriptions=[%s]%s", skill, summary, _subscription_log_suffix(skill))

    if not target_skills:
        _registered = True


def tool(
    public_name: Optional[str] | Callable = None,
    *,
    summary: str = "",
    stability: str = "experimental",
    idempotent: Optional[bool] = None,
    side_effects: Optional[str] = None,
    examples: Optional[list[str]] = None,
    since: Optional[str] = None,
    version: Optional[str] = None,
    input_schema: Optional[dict] = None,
    output_schema: Optional[dict] = None,
):
    """Маркер инструмента с публичным именем и метаданными."""

    def deco(fn: Callable):
        name = fn.__name__ if callable(public_name) else (public_name or fn.__name__)
        mod = fn.__module__
        tools_registry.setdefault(mod, {})[name] = fn
        qn = f"{mod}.{fn.__name__}"
        tools_meta[qn] = {
            "public_name": name,
            "summary": summary,
            "stability": stability,
            "idempotent": idempotent,
            "side_effects": side_effects,
            "examples": (examples or []),
            "since": since,
            "version": version,
            "input_schema": input_schema,
            "output_schema": output_schema,
        }
        return fn

    if callable(public_name):
        return deco(public_name)

    return deco


def event_payload(topic: str, schema: dict):
    """Опишите форму payload для события (для экспорта)."""
    event_payloads[topic] = schema
    return lambda fn: fn


def emits(*topics: str):
    """Пометьте функцию как публикующую события (для карты событий)."""

    def _wrap(fn: Callable):
        qn = f"{fn.__module__}.{fn.__name__}"
        emits_map.setdefault(qn, set()).update(topics)
        return fn

    return _wrap


def resolve_tool(module_name: str, public_name: str) -> Callable | None:
    """Вернуть callable по публичному имени инструмента из модуля."""
    return (tools_registry.get(module_name) or {}).get(public_name)



def _infer_skill_name(fn: Callable) -> Optional[str]:
    """���஡����� ������ ��� ���몠 �� ��� � 䠩�� handlers/main.py."""
    try:
        path = Path(inspect.getfile(fn)).resolve()
    except Exception:
        return None
    parts = list(path.parts)
    for idx in range(len(parts) - 1, -1, -1):
        part = parts[idx]
        if part == "skills" and idx + 1 < len(parts):
            nxt = parts[idx + 1]
            if nxt == ".runtime" and idx + 2 < len(parts):
                return parts[idx + 2]
            return nxt
    return None


def _maybe_push_skill(fn: Callable, skill_name: Optional[str]) -> bool:
    """
    ��⠭����� CurrentSkill �� �६� �맮�� ��ࠡ��稪�.

    1) ��⠥��� �१ SkillContextService (set_current_skill).
    2) �᫨ ���뮪 �� ������ � registry, ����塞 ���� � skill root
       �� 䠩�� ������� � ��뢠�� ctx.skill_ctx.set(...) �������.
    """
    if not skill_name:
        return False

    try:
        if set_current_skill(skill_name):
            return True
    except SdkRuntimeNotInitialized:
        _LOG.debug("AgentContext not available when setting skill=%s", skill_name)
    except Exception:
        _LOG.warning("set_current_skill failed for %s", skill_name, exc_info=True)

    try:
        ctx = require_ctx("sdk.data.skill_memory")
        handler_path = Path(inspect.getfile(fn)).resolve()
        parts = list(handler_path.parts)
        skill_root: Path | None = None
        for idx in range(len(parts) - 1, -1, -1):
            part = parts[idx]
            if part == "skills" and idx + 1 < len(parts):
                skill_root = Path(*parts[: idx + 2])  # .../skills/<skill_name>
                break
        if skill_root is None:
            return False
        skill_ctx = getattr(ctx, "skill_ctx", None)
        if not skill_ctx:
            return False
        return bool(skill_ctx.set(skill_name, skill_root))
    except Exception:
        _LOG.debug("fallback skill_ctx.set failed for %s", skill_name, exc_info=True)
        return False


def _subscription_log_suffix(skill_name: str) -> str:
    token = str(skill_name or "").strip()
    if not token or token == "<unknown>":
        return ""
    try:
        ctx = require_ctx("sdk.core.decorators.subscription_summary")
        policy = load_skill_activation_policy(ctx.paths.workspace_dir(), token, fallback_to_scan=True)
    except Exception:
        return ""
    if policy is None:
        return ""
    strategy = subscription_strategy_for_policy(policy)
    return f" activation={policy.mode} subscription_strategy={strategy}"
