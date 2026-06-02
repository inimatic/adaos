from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import y_py as Y
import yaml

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.nlu.teacher_events import append_event, make_event
from adaos.services.reliability import (
    ReadinessStatus,
    observe_hub_root_integration_outbox,
    set_integration_readiness,
)
from adaos.services.scenarios import loader as scenarios_loader
from adaos.services.root.client import RootHttpClient
from adaos.services.yjs.doc import async_get_ydoc
from adaos.services.yjs.store import ystore_write_metadata
from adaos.services.yjs.webspace import default_webspace_id

from .ycoerce import coerce_dict, is_iterable_like, iter_mappings, iter_scalars

_log = logging.getLogger("adaos.nlu.teacher.llm")

_TRUE_VALUES = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disable", "disabled"}


def _env_enabled(value: str | None) -> bool | None:
    token = str(value or "").strip().lower()
    if not token:
        return None
    if token in _TRUE_VALUES:
        return True
    if token in _FALSE_VALUES:
        return False
    return None


_TEACHER_ENABLED: bool | None = _env_enabled(os.getenv("ADAOS_NLU_TEACHER"))
_LLM_TEACHER_ENABLED: bool | None = _env_enabled(os.getenv("ADAOS_NLU_LLM_TEACHER"))
_MODEL = os.getenv("ADAOS_NLU_LLM_MODEL") or os.getenv("OPENAI_RESPONSES_MODEL") or "gpt-4o-mini"
_MAX_TOKENS = int(os.getenv("ADAOS_NLU_LLM_MAX_TOKENS", "500") or "500")
_TIMEOUT_S = float(os.getenv("ADAOS_NLU_LLM_TIMEOUT_S", "20") or "20")
_MCP_EVIDENCE_TIMEOUT_S = float(os.getenv("ADAOS_NLU_MCP_EVIDENCE_TIMEOUT_S", "8") or "8")
_MCP_EVIDENCE_CACHE_TTL_S = float(os.getenv("ADAOS_NLU_MCP_EVIDENCE_CACHE_TTL_S", "15") or "15")
_MCP_EVIDENCE_CACHE_MAX_ENTRIES = max(1, int(os.getenv("ADAOS_NLU_MCP_EVIDENCE_CACHE_MAX_ENTRIES", "64") or "64"))
_DUPLICATE_ACTIVE_STATUSES = {
    "pending",
    "proposed",
    "previewed",
    "applied",
    "intent_matched",
    "verification_failed",
}
_SLOT_GROUP_ALIASES = {
    "scenario": "scenario_id",
    "modal": "modal_id",
    "app": "app_id",
    "node": "node_ref",
    "skill": "skill_id",
    "webspace": "webspace_id",
}
_BACKGROUND_TRUE_VALUES = {"1", "true", "yes", "on"}
_BACKGROUND_FALSE_VALUES = {"0", "false", "no", "off"}
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
_BACKGROUND_INFLIGHT: set[str] = set()
_BACKGROUND_SEMAPHORE: asyncio.Semaphore | None = None
_BACKGROUND_SEMAPHORE_LOOP: asyncio.AbstractEventLoop | None = None
_BACKGROUND_MAX_CONCURRENCY = max(1, int(os.getenv("ADAOS_NLU_LLM_TEACHER_CONCURRENCY", "1") or "1"))
_UI_NAVIGATION_INTENTS = {
    "desktop.open_modal",
    "desktop.open_node_modal",
    "desktop.open_scenario",
    "desktop.switch_scenario",
}
_READ_ONLY_INTENTS = {
    "desktop.open_weather",
    "weather.current",
}
_ALLOWED_TRAINING_STRATEGIES = {
    "regex",
    "rasa_example",
    "neural_example",
    "entity_alias",
    "descriptor_fix",
    "development_task",
    "clarification",
    "ignore",
}
_TRAINING_STRATEGY_ALIASES = {
    "regex_rule": "regex",
    "regex_template": "regex",
    "rasa": "rasa_example",
    "rasa_examples": "rasa_example",
    "rasa_training_example": "rasa_example",
    "neural": "neural_example",
    "neural_examples": "neural_example",
    "neural_training_example": "neural_example",
    "alias": "entity_alias",
    "entity": "entity_alias",
    "named_entity_alias": "entity_alias",
    "descriptor": "descriptor_fix",
    "llm_hints": "descriptor_fix",
    "nlu_hints": "descriptor_fix",
    "task": "development_task",
    "skill_task": "development_task",
    "scenario_task": "development_task",
    "clarify": "clarification",
    "ask_user": "clarification",
}
_EXAMPLE_TRAINING_STRATEGIES = {"rasa_example", "neural_example"}
_NON_REGEX_TRAINING_STRATEGIES = {
    "rasa_example",
    "neural_example",
    "entity_alias",
    "descriptor_fix",
    "development_task",
    "clarification",
    "ignore",
}
_MCP_DESCRIPTOR_TOOL_IDS = {
    "nlu_authoring.get_context",
    "desktop.registry.lookup",
    "nlu_authoring.list_training_targets",
    "nlu_authoring.list_templates",
    "sdk.describe_surface",
}
_MCP_DESCRIPTOR_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_MCP_DESCRIPTOR_CACHE_LOCK = threading.RLock()


def _nlu_llm_write_meta():
    return ystore_write_metadata(
        root_names=["data"],
        source="nlu.llm_teacher_runtime",
        owner="core:nlu.llm_teacher",
        channel="core.nlu.llm_teacher.async",
    )


def _root_teacher_policy_enabled(ctx: Any) -> bool:
    try:
        return bool(getattr(getattr(ctx.config, "root_settings", None), "llm", None).allow_nlu_teacher)  # type: ignore[attr-defined]
    except Exception:
        return True


def _teacher_enabled(ctx: Any) -> bool:
    if _TEACHER_ENABLED is not None:
        return bool(_TEACHER_ENABLED)
    return _root_teacher_policy_enabled(ctx)


def _llm_teacher_enabled(ctx: Any) -> bool:
    if _LLM_TEACHER_ENABLED is not None:
        return bool(_LLM_TEACHER_ENABLED)
    return _root_teacher_policy_enabled(ctx)


def _payload(evt: Any) -> Dict[str, Any]:
    if isinstance(evt, dict):
        return evt
    if hasattr(evt, "payload"):
        data = getattr(evt, "payload")
        return data if isinstance(data, dict) else {}
    return {}


def _resolve_webspace_id(payload: Mapping[str, Any]) -> str:
    meta = coerce_dict(payload.get("_meta"))
    token = payload.get("webspace_id") or payload.get("workspace_id") or meta.get("webspace_id") or meta.get("workspace_id")
    if isinstance(token, str) and token.strip():
        return token.strip()
    return default_webspace_id()


def _teacher_obj(data_map: Any) -> dict[str, Any]:
    return coerce_dict(data_map.get("nlu_teacher"))


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        try:
            return json.dumps(str(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except Exception:
            return "\"<unserializable>\""


def _sha256_payload(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _target_key(target: Any) -> tuple[str, str]:
    if not isinstance(target, Mapping):
        return ("", "")
    t_type = target.get("type")
    t_id = target.get("id")
    return (
        str(t_type or "").strip(),
        str(t_id or "").strip(),
    )


def _compact_candidate_for_context(candidate: Mapping[str, Any]) -> dict[str, Any]:
    rr = candidate.get("regex_rule") if isinstance(candidate.get("regex_rule"), Mapping) else {}
    verification = candidate.get("verification") if isinstance(candidate.get("verification"), Mapping) else {}
    applied = candidate.get("applied") if isinstance(candidate.get("applied"), Mapping) else {}
    action_candidate = candidate.get("action_candidate") if isinstance(candidate.get("action_candidate"), Mapping) else {}
    training_strategy = candidate.get("training_strategy") if isinstance(candidate.get("training_strategy"), Mapping) else {}
    out = {
        "candidate_id": candidate.get("id"),
        "status": candidate.get("status"),
        "kind": candidate.get("kind"),
        "text": candidate.get("text"),
        "request_id": candidate.get("request_id"),
        "target": dict(candidate.get("target") or {}) if isinstance(candidate.get("target"), Mapping) else None,
        "training_strategy": dict(training_strategy) if training_strategy else None,
        "action_candidate": {
            "class": action_candidate.get("class"),
            "intent": action_candidate.get("intent"),
            "slots": dict(action_candidate.get("slots") or {}) if isinstance(action_candidate.get("slots"), Mapping) else None,
            "side_effect_class": action_candidate.get("side_effect_class"),
            "status": action_candidate.get("status"),
        }
        if action_candidate
        else None,
        "regex_rule": {
            "intent": rr.get("intent") if isinstance(rr.get("intent"), str) else None,
            "pattern": rr.get("pattern") if isinstance(rr.get("pattern"), str) else None,
        },
        "verification": {
            "status": verification.get("status"),
            "expected_intent": verification.get("expected_intent"),
            "probe_intent": (verification.get("probe") or {}).get("intent")
            if isinstance(verification.get("probe"), Mapping)
            else None,
        },
        "applied": {
            "rule_id": applied.get("rule_id"),
            "target": dict(applied.get("target") or {}) if isinstance(applied.get("target"), Mapping) else None,
        },
    }
    return {k: v for k, v in out.items() if v not in (None, {}, [])}


def _looks_like_correction(text: str) -> bool:
    lower = " " + str(text or "").strip().lower() + " "
    if not lower.strip():
        return False
    if re.search(r"(^|\W)(no|nope|нет)(\W|$)", lower):
        return True
    markers = (
        " wrong ",
        " not that ",
        " that's not ",
        " that is not ",
        " instead ",
        " correct it ",
        " i meant ",
        " не то ",
        " неверно ",
        " неправильно ",
        " нужно ",
        " надо ",
        " имел в виду ",
        " я имел в виду ",
    )
    return any(marker in lower for marker in markers)


def _correction_thread_context(*, teacher: Mapping[str, Any], text: str, request_id: str) -> dict[str, Any] | None:
    if not _looks_like_correction(text):
        return None
    candidates = list(iter_mappings(teacher.get("candidates")))
    if not candidates:
        return None
    for item in sorted(candidates, key=lambda x: float(x.get("ts") or 0.0), reverse=True):
        candidate_id = item.get("id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            continue
        status = item.get("status") if isinstance(item.get("status"), str) else ""
        if status in {"rolled_back", "rejected"}:
            continue
        previous = _compact_candidate_for_context(item)
        previous_request_id = previous.get("request_id")
        thread_id = f"thread.{previous_request_id or candidate_id}"
        return {
            "active": True,
            "thread_id": thread_id,
            "request_id": request_id,
            "correction_text": text,
            "previous_candidate": previous,
        }
    return None


def _ydoc_to_snapshot(ydoc: Any) -> dict[str, Any]:
    def _normalize(node: Any):
        if isinstance(node, dict):
            return {str(k): _normalize(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_normalize(x) for x in node]
        if isinstance(node, Y.YMap):
            keys = list(node.keys())
            return {str(k): _normalize(node.get(k)) for k in keys}
        if isinstance(node, Y.YArray):
            return [_normalize(x) for x in node]
        if node is None:
            return None
        return node

    try:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
    except Exception:
        return {}
    return {"ui": _normalize(ui_map) or {}, "data": _normalize(data_map) or {}}


def _extract_webspace_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    ui = coerce_dict(snapshot.get("ui"))
    data = coerce_dict(snapshot.get("data"))
    current_scenario = ui.get("current_scenario")
    if not isinstance(current_scenario, str):
        current_scenario = None

    catalog = coerce_dict(data.get("catalog"))
    apps = list(iter_mappings(catalog.get("apps")))
    widgets = list(iter_mappings(catalog.get("widgets")))

    installed = coerce_dict(data.get("installed"))
    installed_apps = list(iter_scalars(installed.get("apps")))
    installed_widgets = list(iter_scalars(installed.get("widgets")))

    def _strip_app(app: Any) -> Optional[dict[str, Any]]:
        if not isinstance(app, Mapping):
            return None
        out = {
            "id": app.get("id"),
            "title": app.get("title"),
            "scenario_id": app.get("scenario_id"),
            "launchModal": app.get("launchModal"),
            "origin": app.get("origin"),
        }
        return {k: v for k, v in out.items() if v is not None}

    def _strip_widget(w: Any) -> Optional[dict[str, Any]]:
        if not isinstance(w, Mapping):
            return None
        out = {"id": w.get("id"), "title": w.get("title"), "type": w.get("type"), "origin": w.get("origin")}
        return {k: v for k, v in out.items() if v is not None}

    # Backward-compatible (legacy) per-webspace rules.
    nlu = coerce_dict(data.get("nlu"))
    regex_rules = list(iter_mappings(nlu.get("regex_rules")))

    def _strip_rule(rule: Any) -> Optional[dict[str, Any]]:
        if not isinstance(rule, Mapping):
            return None
        out = {
            "id": rule.get("id"),
            "intent": rule.get("intent"),
            "pattern": rule.get("pattern"),
            "enabled": rule.get("enabled"),
            "source": rule.get("source"),
        }
        out = {k: v for k, v in out.items() if v is not None}
        if not isinstance(out.get("intent"), str) or not out.get("intent"):
            return None
        if not isinstance(out.get("pattern"), str) or not out.get("pattern"):
            return None
        return out

    out: dict[str, Any] = {
        "current_scenario": current_scenario,
        "catalog": {
            "apps": [x for x in (_strip_app(a) for a in apps) if x],
            "widgets": [x for x in (_strip_widget(w) for w in widgets) if x],
        },
        "installed": {
            "apps": [x for x in installed_apps if isinstance(x, (str, int))],
            "widgets": [x for x in installed_widgets if isinstance(x, (str, int))],
        },
        "regex_rules": [x for x in (_strip_rule(r) for r in regex_rules) if x][:50],
    }

    # Provide a lightweight view of existing skill-level NLU artifacts, so the LLM can
    # propose improvements to existing skills instead of always creating new ones.
    try:
        skills = _infer_skills_from_catalog(apps=apps, widgets=widgets)
        out["skill_nlu"] = _load_skill_nlu_artifacts(skills)
    except Exception:
        out["skill_nlu"] = {}
        skills = []
    out["skills"] = [s for s in skills if isinstance(s, str)]

    # Prefer workspace-owned regex rules (scenario/skills) so the LLM can avoid duplicates.
    try:
        collected: list[dict[str, Any]] = list(out.get("regex_rules") or [])

        # Scenario rules
        if isinstance(current_scenario, str) and current_scenario:
            try:
                content = scenarios_loader.read_content(current_scenario)
            except Exception:
                content = {}
            nlu_section = content.get("nlu") if isinstance(content, dict) else None
            rr = (nlu_section or {}).get("regex_rules") if isinstance(nlu_section, dict) else None
            if isinstance(rr, list):
                for x in (_strip_rule(r) for r in rr):
                    if not x:
                        continue
                    x["owner"] = {"type": "scenario", "id": current_scenario}
                    collected.append(x)

        # Skill rules
        ctx = get_ctx()
        skills_dir = Path(ctx.paths.skills_dir())
        for skill_name in skills:
            path = skills_dir / skill_name / "skill.yaml"
            if not path.exists():
                continue
            try:
                payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            nlu_section = payload.get("nlu")
            if not isinstance(nlu_section, dict):
                continue
            rr = nlu_section.get("regex_rules")
            if not isinstance(rr, list):
                continue
            for x in (_strip_rule(r) for r in rr):
                if not x:
                    continue
                x["owner"] = {"type": "skill", "id": skill_name}
                collected.append(x)

        # De-dupe
        uniq: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for r in collected:
            intent = r.get("intent")
            pattern = r.get("pattern")
            if not isinstance(intent, str) or not isinstance(pattern, str):
                continue
            key = (intent, pattern)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(r)
        out["regex_rules"] = uniq[:80]
    except Exception:
        pass

    return out


def _build_intent_routes_and_policies(
    *, scenario_nlu: Mapping[str, Any], skills: list[str]
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """
    Derive routing hints for the LLM:
    - intent -> callSkill topic -> skill name (by subscription)
    - intent -> callHost target (system action)

    Also returns per-skill llm_policy flags so we can auto-apply teacher changes when trusted.
    """
    intents_map = scenario_nlu.get("intents") if isinstance(scenario_nlu.get("intents"), dict) else None
    if not isinstance(intents_map, dict):
        return ([], {}, [])

    ctx = get_ctx()
    skills_dir = Path(ctx.paths.skills_dir())

    topic_to_skill: dict[str, str] = {}
    skill_policies: dict[str, Any] = {}
    skill_manifests: list[dict[str, Any]] = []
    for skill_name in [s for s in skills if isinstance(s, str) and s.strip()]:
        path = skills_dir / skill_name / "skill.yaml"
        if not path.exists():
            continue
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue

        llm_policy = payload.get("llm_policy")
        skill_policies[skill_name] = llm_policy if isinstance(llm_policy, dict) else {}

        try:
            tools = payload.get("tools")
            if isinstance(tools, list):
                tools_list = [
                    {"name": t.get("name"), "description": t.get("description")}
                    for t in tools
                    if isinstance(t, dict) and isinstance(t.get("name"), str)
                ]
            else:
                tools_list = []
        except Exception:
            tools_list = []

        events = payload.get("events")
        subs = (events or {}).get("subscribe") if isinstance(events, dict) else None
        pubs = (events or {}).get("publish") if isinstance(events, dict) else None
        try:
            nlu = payload.get("nlu")
            rr = (nlu or {}).get("regex_rules") if isinstance(nlu, dict) else None
            regex_count = len(rr) if isinstance(rr, list) else 0
        except Exception:
            regex_count = 0
        skill_manifests.append(
            {
                "name": skill_name,
                "description": payload.get("description"),
                "llm_policy": skill_policies.get(skill_name) or {},
                "events": {
                    "subscribe": [x for x in (subs or []) if isinstance(x, str)][:50] if isinstance(subs, list) else [],
                    "publish": [x for x in (pubs or []) if isinstance(x, str)][:50] if isinstance(pubs, list) else [],
                },
                "tools": tools_list[:30],
                "regex_rules_count": regex_count,
            }
        )

        events = payload.get("events")
        subs = (events or {}).get("subscribe") if isinstance(events, dict) else None
        if isinstance(subs, list):
            for t in subs:
                if isinstance(t, str) and t.strip() and t.strip() not in topic_to_skill:
                    topic_to_skill[t.strip()] = skill_name

    routes: list[dict[str, Any]] = []
    for intent_name, spec in intents_map.items():
        if not isinstance(intent_name, str) or not isinstance(spec, dict):
            continue
        actions = spec.get("actions")
        if not isinstance(actions, list):
            continue
        for a in actions:
            if not isinstance(a, dict):
                continue
            a_type = a.get("type")
            target = a.get("target")
            if not isinstance(a_type, str) or not isinstance(target, str) or not target.strip():
                continue
            if a_type == "callSkill":
                routes.append(
                    {
                        "intent": intent_name,
                        "action": "callSkill",
                        "target": target.strip(),
                        "skill": topic_to_skill.get(target.strip()),
                    }
                )
            elif a_type == "callHost":
                routes.append(
                    {
                        "intent": intent_name,
                        "action": "callHost",
                        "target": target.strip(),
                    }
                )
    return (routes[:150], skill_policies, skill_manifests[:50])


def _infer_skills_from_catalog(*, apps: list[Any], widgets: list[Any]) -> list[str]:
    skills: set[str] = set()
    for item in list(apps) + list(widgets):
        if not isinstance(item, dict):
            continue
        origin = item.get("origin")
        if not isinstance(origin, str):
            continue
        if origin.startswith("skill:") and len(origin) > len("skill:"):
            skills.add(origin[len("skill:") :].strip())
    return sorted([s for s in skills if s])


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here.parent, *here.parents]:
        if (p / ".adaos" / "workspace" / "skills").is_dir():
            return p
    return Path.cwd()


def _safe_read_text(path: Path, *, limit: int = 6000) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
    s = text.strip()
    if len(s) <= limit:
        return s
    return s[:limit] + f"... <truncated {len(s) - limit} chars>"


def _load_skill_nlu_artifacts(skills: list[str]) -> dict[str, Any]:
    """
    Returns a small, prompt-friendly mapping of skill->nlu files (raw text).

    This is intentionally conservative: we only include a few files per skill and we truncate.
    """
    root = _find_repo_root()
    base = root / ".adaos" / "workspace" / "skills"
    out: dict[str, Any] = {}

    # Keep token usage bounded.
    for skill in skills[:10]:
        if not skill or "/" in skill or "\\" in skill:
            continue
        skill_dir = base / skill
        if not skill_dir.is_dir():
            continue

        files: dict[str, str] = {}
        intents_yml = skill_dir / "interpreter" / "intents.yml"
        if intents_yml.is_file():
            content = _safe_read_text(intents_yml, limit=8000)
            if content:
                files["interpreter/intents.yml"] = content

        nlu_yml = skill_dir / "interpreter" / "nlu.yml"
        if nlu_yml.is_file() and "interpreter/intents.yml" not in files:
            content = _safe_read_text(nlu_yml, limit=8000)
            if content:
                files["interpreter/nlu.yml"] = content

        if files:
            out[skill] = files

    return out


def _extract_scenario_nlu(*, scenario_id: str | None) -> dict[str, Any]:
    if not scenario_id:
        return {}
    try:
        content = scenarios_loader.read_content(scenario_id)
    except Exception:
        content = {}
    if not isinstance(content, dict):
        content = {}
    nlu = content.get("nlu")
    if not isinstance(nlu, dict):
        nlu = {}
    try:
        from adaos.services.nlu.baseline_content import merge_default_desktop_nlu

        nlu = merge_default_desktop_nlu(str(scenario_id), nlu)
    except Exception:
        pass
    intents = nlu.get("intents")
    if not isinstance(intents, dict):
        return {}

    out: dict[str, Any] = {"intents": {}}
    for intent, spec in intents.items():
        if not isinstance(intent, str) or not intent:
            continue
        if not isinstance(spec, Mapping):
            continue
        examples = [x for x in spec.get("examples") if isinstance(x, str) and x.strip()] if is_iterable_like(spec.get("examples")) else []
        actions = list(iter_mappings(spec.get("actions")))
        out["intents"][intent] = {
            "description": spec.get("description"),
            "scope": spec.get("scope"),
            "examples": examples[:10],
            "actions": [
                {k: v for k, v in a.items() if k in {"type", "target", "params"}}
                for a in actions
            ][:5],
        }
    return out


def _extract_first_output_text(res: Any) -> str:
    """
    Root /v1/llm/response returns OpenAI Responses API payload.
    Try to extract the first text output robustly.
    """
    if isinstance(res, dict):
        out = res.get("output")
        if isinstance(out, list):
            for item in out:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") in {"output_text", "text"}:
                        t = c.get("text")
                        if isinstance(t, str) and t.strip():
                            return t.strip()
        # Some proxies might return {choices:[{message:{content:"..."}}]}
        choices = res.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
    return ""


def _parse_llm_json_object(text: str) -> dict[str, Any] | None:
    """
    Parse the first JSON object from an LLM response.

    The prompt asks for plain JSON, but some providers still wrap the object in
    markdown fences. Keep this tolerant so the Teacher does not silently ignore
    an otherwise valid candidate.
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    candidates = [raw]
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        for idx, ch in enumerate(candidate):
            if ch != "{":
                continue
            try:
                parsed, _end = decoder.raw_decode(candidate[idx:])
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def _preview_regex_candidate(*, pattern: str, text: str) -> dict[str, Any]:
    try:
        compiled = re.compile(pattern, re.IGNORECASE | re.UNICODE)
    except re.error as exc:
        return {"ok": False, "status": "invalid_regex", "error": str(exc)}
    try:
        match = compiled.search(text)
    except re.error as exc:
        return {"ok": False, "status": "runtime_regex_error", "error": str(exc)}
    if not match:
        return {"ok": False, "status": "source_text_miss", "slots": {}}
    slots: dict[str, Any] = {}
    for key, value in match.groupdict().items():
        if value is None:
            continue
        slots[str(key)] = value.strip() if isinstance(value, str) else value
    return {
        "ok": True,
        "status": "regex_matched",
        "matched": match.group(0),
        "slots": slots,
    }


_OPEN_MODAL_TEXT_RE = re.compile(
    r"^\s*(?:покажи|открой|показать|открыть|show|open|launch)\s+(?P<entity>.+?)\s*[.!?]?\s*$",
    re.IGNORECASE | re.UNICODE,
)
_SCENARIO_WORD_RE = re.compile(r"\b(?:scenario|сценар(?:ий|ия|ию|ием|ии))\b", re.IGNORECASE | re.UNICODE)
_OPEN_MODAL_REPAIR_INTENTS = {
    "desktop.open_app",
    "desktop.toggle_app_install",
    "desktop.open_scenario",
    "desktop.switch_scenario",
}


def _lookup_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _compact_lookup_key(value: Any) -> str:
    return re.sub(r"[\s_\-:]+", "", str(value or "").strip()).casefold()


def _lookup_rows_from_context(context: Mapping[str, Any], lookup: str) -> list[dict[str, Any]]:
    root_mcp = coerce_dict(context.get("root_mcp"))
    registry = coerce_dict(root_mcp.get("desktop_registry_lookup"))
    lookups = registry.get("lookups") if isinstance(registry.get("lookups"), Mapping) else {}
    rows = lookups.get(lookup)
    if not isinstance(rows, list):
        return []
    return [dict(item) for item in iter_mappings(rows)]


def _row_aliases(row: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    value = row.get("value")
    if isinstance(value, str) and value.strip():
        out.append(value.strip())
    labels = row.get("labels")
    if isinstance(labels, list):
        out.extend(str(item).strip() for item in labels if str(item).strip())
    return out


def _regex_alt(value: str) -> str:
    escaped = re.escape(str(value or "").strip())
    return re.sub(r"(?:\\\s|\s)+", r"\\s+", escaped)


def _open_modal_pattern_for_entity(*, entity: str, modal_id: str, aliases: list[str]) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for raw in [entity, *aliases, modal_id]:
        token = str(raw or "").strip()
        if not token or token.startswith("node:"):
            continue
        key = _lookup_key(token)
        if key in seen:
            continue
        seen.add(key)
        values.append(token)
    alts = [_regex_alt(item) for item in values[:10] if item.strip()]
    if not alts:
        alts = [_regex_alt(entity)]
    return r"\b(?:покажи|открой|показать|открыть|show|open|launch)\s+(?P<modal_id>" + "|".join(alts) + r")\b"


def _infer_open_modal_repair(*, text: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    if _SCENARIO_WORD_RE.search(text or ""):
        return None
    match = _OPEN_MODAL_TEXT_RE.match(text or "")
    if not match:
        return None
    entity = str(match.group("entity") or "").strip().strip(" \t\r\n\"'«»")
    if not entity:
        return None
    entity_key = _lookup_key(entity)
    entity_compact = _compact_lookup_key(entity)

    modal_rows = _lookup_rows_from_context(context, "modal_id")
    for row in modal_rows:
        modal_id = str(row.get("value") or "").strip()
        if not modal_id or modal_id.startswith("node:"):
            continue
        aliases = _row_aliases(row)
        alias_keys = {_lookup_key(alias) for alias in aliases if alias}
        alias_compact_keys = {_compact_lookup_key(alias) for alias in aliases if alias}
        if entity_key not in alias_keys and entity_compact not in alias_compact_keys:
            continue
        return {
            "intent": "desktop.open_modal",
            "pattern": _open_modal_pattern_for_entity(entity=entity, modal_id=modal_id, aliases=aliases),
            "modal_id": modal_id,
            "entity": entity,
            "matched": "modal_id",
        }

    catalog = coerce_dict(context.get("catalog"))
    apps = list(iter_mappings(catalog.get("apps")))
    for app in apps:
        launch_modal = str(app.get("launchModal") or app.get("launch_modal") or "").strip()
        if not launch_modal:
            continue
        aliases = [
            str(app.get("id") or "").strip(),
            str(app.get("title") or "").strip(),
            str(app.get("name") or "").strip(),
        ]
        alias_keys = {_lookup_key(alias) for alias in aliases if alias}
        alias_compact_keys = {_compact_lookup_key(alias) for alias in aliases if alias}
        if entity_key not in alias_keys and entity_compact not in alias_compact_keys:
            continue
        return {
            "intent": "desktop.open_modal",
            "pattern": _open_modal_pattern_for_entity(entity=entity, modal_id=launch_modal, aliases=aliases),
            "modal_id": launch_modal,
            "entity": entity,
            "matched": "catalog.app.launchModal",
        }
    return None


def _normalize_regex_rule_slots(*, pattern: str, slots: Mapping[str, Any]) -> tuple[str, dict[str, Any], dict[str, str]]:
    normalized_pattern = str(pattern or "")
    normalized_slots = dict(slots) if isinstance(slots, Mapping) else {}
    aliases_used: dict[str, str] = {}

    for alias, canonical in _SLOT_GROUP_ALIASES.items():
        if f"(?P<{alias}>" not in normalized_pattern:
            continue
        if f"(?P<{canonical}>" in normalized_pattern:
            continue
        normalized_pattern = re.sub(
            rf"\(\?P<{re.escape(alias)}>",
            f"(?P<{canonical}>",
            normalized_pattern,
        )
        aliases_used[alias] = canonical
        if alias in normalized_slots and canonical not in normalized_slots:
            normalized_slots[canonical] = normalized_slots.pop(alias)

    return normalized_pattern, normalized_slots, aliases_used


def _resolve_regex_target(
    *,
    intent: str,
    proposed_target: Mapping[str, Any] | None,
    routes: list[dict[str, Any]],
    current_scenario: Any,
) -> dict[str, Any] | None:
    intent_token = str(intent or "").strip()
    for route in routes:
        if route.get("intent") != intent_token or route.get("action") != "callSkill":
            continue
        skill = route.get("skill")
        if isinstance(skill, str) and skill.strip():
            return {"type": "skill", "id": skill.strip()}

    for route in routes:
        if route.get("intent") != intent_token or route.get("action") != "callHost":
            continue
        if isinstance(current_scenario, str) and current_scenario.strip():
            return {"type": "scenario", "id": current_scenario.strip()}

    if isinstance(proposed_target, Mapping):
        t_type = proposed_target.get("type")
        t_id = proposed_target.get("id")
        if isinstance(t_type, str) and isinstance(t_id, str) and t_type.strip() and t_id.strip():
            if t_type.strip() in {"skill", "scenario"}:
                return {"type": t_type.strip(), "id": t_id.strip()}

    if isinstance(current_scenario, str) and current_scenario.strip():
        return {"type": "scenario", "id": current_scenario.strip()}
    return None


def _candidate_class_for_intent(*, intent: str, target: Mapping[str, Any] | None) -> str:
    if intent in _UI_NAVIGATION_INTENTS:
        return "interface_action"
    if target and str(target.get("type") or "").strip() == "skill":
        return "skill_action"
    if target and str(target.get("type") or "").strip() == "scenario":
        return "scenario_flow" if "scenario" in intent else "interface_action"
    return "nlu_correction"


def _side_effect_class_for_intent(intent: str) -> str:
    if intent in _READ_ONLY_INTENTS:
        return "read_only"
    if intent in _UI_NAVIGATION_INTENTS:
        return "ui_navigation"
    if intent.startswith("desktop."):
        return "local_state_change"
    return "unknown"


def _normalized_training_strategy(
    *,
    suggestion: Mapping[str, Any],
    decision: str,
    regex_rule: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raw = suggestion.get("training_strategy")
    if isinstance(raw, str) and raw.strip():
        primary = raw.strip()
        source = "llm"
    elif isinstance(raw, Mapping):
        primary_token = raw.get("primary") or raw.get("strategy") or raw.get("type")
        primary = str(primary_token or "").strip()
        source = "llm"
    else:
        primary = ""
        source = "adaos.default"

    primary_before_alias = primary
    primary = _TRAINING_STRATEGY_ALIASES.get(primary.lower(), primary.lower()) if primary else ""

    why_not_regex = suggestion.get("why_not_regex")
    if not isinstance(why_not_regex, str):
        why_not_regex = ""

    if not primary:
        if decision == "propose_regex_rule" and regex_rule and not why_not_regex.strip():
            primary = "regex"
        elif decision == "propose_regex_rule" and regex_rule and why_not_regex.strip():
            primary = "rasa_example"
        elif decision == "revise_nlu":
            primary = "rasa_example"
        elif decision in {"create_skill_candidate", "create_scenario_candidate"}:
            primary = "development_task"
        else:
            primary = "ignore"

    normalized_from: str | None = None
    if primary and primary not in _ALLOWED_TRAINING_STRATEGIES:
        normalized_from = primary
        primary = "ignore"

    result = {
        "primary": primary,
        "source": source,
        "why_not_regex": why_not_regex.strip() or None,
    }
    if primary_before_alias and primary_before_alias.lower() != primary:
        result["alias_of"] = primary_before_alias
    if normalized_from:
        result["unknown_strategy"] = normalized_from
    if isinstance(raw, Mapping):
        alternatives = raw.get("allowed") or raw.get("alternatives")
        if isinstance(alternatives, list):
            result["alternatives"] = [
                _TRAINING_STRATEGY_ALIASES.get(str(x).strip().lower(), str(x).strip().lower())
                for x in alternatives
                if str(x).strip()
            ][:10]
        rationale = raw.get("rationale") or raw.get("reason")
        if isinstance(rationale, str) and rationale.strip():
            result["rationale"] = rationale.strip()
    return result


def _clarification_allowed_answers(suggestion: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_options = suggestion.get("options")
    options = raw_options if isinstance(raw_options, list) else []
    answers: list[dict[str, Any]] = []
    for idx, option in enumerate(options[:6]):
        if isinstance(option, Mapping):
            option_id = str(option.get("id") or option.get("value") or f"option_{idx + 1}").strip()
            label = str(option.get("label") or option.get("title") or option_id).strip()
            if not option_id and not label:
                continue
            answer = {"id": option_id or f"option_{idx + 1}", "label": label or option_id}
            for key in (
                "effect",
                "candidate_id",
                "target",
                "intent",
                "slots",
                "action_candidate",
                "template_candidate",
                "risk_notes",
            ):
                value = option.get(key)
                if value is not None:
                    answer[key] = value
            answers.append(answer)
            continue
        if isinstance(option, str) and option.strip():
            answers.append({"id": f"option_{idx + 1}", "label": option.strip(), "effect": "answer"})

    if answers:
        return answers

    action_candidate = (
        suggestion.get("action_candidate") if isinstance(suggestion.get("action_candidate"), Mapping) else None
    )
    if action_candidate:
        return [
            {
                "id": "yes",
                "label": "yes",
                "effect": "accept_hypothesis",
                "action_candidate": dict(action_candidate),
            },
            {"id": "no", "label": "no", "effect": "reject_hypothesis"},
        ]
    return []


def _build_regex_candidate_envelopes(
    *,
    candidate_id: str,
    request_id: str,
    text: str,
    intent: str,
    pattern: str,
    target: Mapping[str, Any] | None,
    preview: Mapping[str, Any],
    slots: Mapping[str, Any],
    context: Mapping[str, Any],
    training_strategy: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    owner = dict(target or {})
    preview_slots = preview.get("slots") if isinstance(preview.get("slots"), Mapping) else {}
    action_status = "phrase_previewed" if bool(preview.get("ok")) else "quarantined"
    action_candidate = {
        "id": f"act.{candidate_id.removeprefix('cand.')}",
        "candidate_id": candidate_id,
        "request_id": request_id,
        "class": _candidate_class_for_intent(intent=intent, target=target),
        "intent": intent,
        "text": text,
        "slots": dict(preview_slots or {}),
        "slot_schema": dict(slots or {}),
        "owner": owner or None,
        "side_effect_class": _side_effect_class_for_intent(intent),
        "status": action_status,
        "phrase_preview": {
            "status": preview.get("status"),
            "ok": bool(preview.get("ok")),
            "slots": dict(preview_slots or {}),
        },
        "action_preview": {
            "status": "not_run",
            "reason": "m1_action_candidate_envelope",
        },
        "scope": {
            "scenario_id": context.get("current_scenario") if isinstance(context.get("current_scenario"), str) else None,
        },
    }
    template_candidate = {
        "id": f"tplcand.{candidate_id.removeprefix('cand.')}",
        "candidate_id": candidate_id,
        "request_id": request_id,
        "class": "template_candidate",
        "engine": "regex",
        "training_strategy": dict(training_strategy),
        "intent": intent,
        "owner": owner or None,
        "operation": "add_regex_rule",
        "patch": {
            "intent": intent,
            "pattern": pattern,
            "slots": dict(slots or {}),
        },
        "phrase_preview": {
            "status": preview.get("status"),
            "ok": bool(preview.get("ok")),
            "slots": dict(preview_slots or {}),
        },
        "status": "phrase_previewed" if bool(preview.get("ok")) else "quarantined",
    }
    return action_candidate, template_candidate


def _coerce_target(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    t_type = value.get("type")
    t_id = value.get("id")
    if isinstance(t_type, str) and isinstance(t_id, str) and t_type.strip() and t_id.strip():
        return {"type": t_type.strip(), "id": t_id.strip()}
    return None


def _regex_policy_rejection(
    *,
    pattern: str,
    intent: str,
    training_strategy: Mapping[str, Any],
    confidence: float,
) -> dict[str, Any] | None:
    primary = str(training_strategy.get("primary") or "").strip()
    why_not_regex = training_strategy.get("why_not_regex")
    if primary in _NON_REGEX_TRAINING_STRATEGIES:
        return {
            "reason": "strategy_not_regex",
            "strategy": primary,
            "why_not_regex": why_not_regex,
        }
    if isinstance(why_not_regex, str) and why_not_regex.strip():
        return {
            "reason": "llm_rejected_regex",
            "strategy": primary or "regex",
            "why_not_regex": why_not_regex.strip(),
        }
    compact = re.sub(r"\s+", "", str(pattern or "")).strip().lower()
    if compact in {".*", "^.*$", ".+", "^.+$", "(.*)", "(.+)"}:
        return {"reason": "overbroad_regex", "strategy": "rasa_example", "pattern": pattern}
    if len(compact) < 4:
        return {"reason": "too_short_regex", "strategy": "rasa_example", "pattern": pattern}
    if confidence < 0.45 and intent not in _READ_ONLY_INTENTS:
        return {
            "reason": "low_confidence_non_read_only_regex",
            "strategy": "clarification",
            "confidence": confidence,
        }
    return None


def _strategy_candidate_kind(primary: str) -> tuple[str, str, str]:
    if primary in _EXAMPLE_TRAINING_STRATEGIES:
        engine = "rasa" if primary == "rasa_example" else "neural"
        return ("training_example", "template_candidate", engine)
    if primary == "entity_alias":
        return ("entity_alias", "entity_correction", "entity_alias")
    if primary == "descriptor_fix":
        return ("descriptor_fix", "descriptor_fix", "descriptor_fix")
    if primary == "development_task":
        return ("development_task", "development_task", "development_task")
    if primary == "clarification":
        return ("clarification", "clarification", "clarification")
    return ("nlu_strategy", "nlu_strategy", primary or "ignore")


def _build_strategy_candidate_entry(
    *,
    candidate_id: str,
    request_id: str,
    text: str,
    decision: str,
    suggestion: Mapping[str, Any],
    intent: str | None,
    target: Mapping[str, Any] | None,
    examples: list[str],
    slots: Mapping[str, Any],
    context: Mapping[str, Any],
    training_strategy: Mapping[str, Any],
    llm_meta: Mapping[str, Any],
    notes: str,
    status: str = "pending",
    regex_rejection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    primary = str(training_strategy.get("primary") or "ignore").strip()
    kind, candidate_class, engine = _strategy_candidate_kind(primary)
    candidate_payload = suggestion.get("candidate") if isinstance(suggestion.get("candidate"), Mapping) else {}
    action_candidate = suggestion.get("action_candidate") if isinstance(suggestion.get("action_candidate"), Mapping) else None
    template_candidate_in = (
        suggestion.get("template_candidate") if isinstance(suggestion.get("template_candidate"), Mapping) else None
    )
    owner = _coerce_target(target)
    example_rows = examples[:] if examples else ([text] if text else [])
    strategy_candidate: dict[str, Any] = {
        "id": f"strat.{candidate_id.removeprefix('cand.')}",
        "candidate_id": candidate_id,
        "request_id": request_id,
        "class": candidate_class,
        "strategy": primary,
        "engine": engine,
        "decision": decision,
        "intent": intent,
        "owner": owner,
        "examples": example_rows[:20],
        "slots": dict(slots or {}),
        "status": status,
        "apply_policy": "operator_approval_required",
        "scope": {
            "scenario_id": context.get("current_scenario") if isinstance(context.get("current_scenario"), str) else None,
        },
    }
    if primary in _EXAMPLE_TRAINING_STRATEGIES:
        strategy_candidate["template_candidate"] = {
            "id": f"tplcand.{candidate_id.removeprefix('cand.')}",
            "candidate_id": candidate_id,
            "request_id": request_id,
            "class": "template_candidate",
            "engine": engine,
            "training_strategy": dict(training_strategy),
            "intent": intent,
            "owner": owner,
            "operation": "save_example",
            "patch": {"intent": intent, "examples": example_rows[:20], "slots": dict(slots or {})},
            "status": status,
        }
    if action_candidate:
        strategy_candidate["action_candidate"] = dict(action_candidate)
    if template_candidate_in:
        strategy_candidate["template_candidate"] = dict(template_candidate_in)
    if candidate_payload:
        strategy_candidate["proposal"] = dict(candidate_payload)
    if regex_rejection:
        strategy_candidate["regex_rejection"] = dict(regex_rejection)

    entry = {
        "id": candidate_id,
        "ts": time.time(),
        "kind": kind,
        "text": text,
        "request_id": request_id,
        "origin_scenario_id": context.get("current_scenario") if isinstance(context.get("current_scenario"), str) else None,
        "intent": intent,
        **({"target": owner} if owner else {}),
        **({"examples": example_rows[:20]} if example_rows else {}),
        **({"slots": dict(slots)} if slots else {}),
        "candidate": dict(candidate_payload)
        if candidate_payload
        else {
            "name": f"{primary} for {intent or 'unknown intent'}",
            "description": "Proposed non-regex NLU Teacher strategy.",
        },
        "training_strategy": dict(training_strategy),
        "strategy_candidate": strategy_candidate,
        "llm": dict(llm_meta),
        "notes": notes,
        "status": status,
    }
    if regex_rejection:
        entry["rejected_regex_rule"] = dict(regex_rejection)
    return entry


def _truncate(text: Any, limit: int) -> Any:
    if not isinstance(text, str):
        return text
    s = text.strip()
    if len(s) <= limit:
        return s
    return s[:limit] + f"... <truncated {len(s) - limit} chars>"


def _redact_messages(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        out.append(
            {
                "role": m.get("role"),
                "content": _truncate(m.get("content"), 1200),
            }
        )
    return out


def _invoke_root_mcp_authoring_tool(
    tool_id: str,
    *,
    arguments: dict[str, Any],
    request_id: str | None,
    trace_id: str | None,
    dry_run: bool = True,
) -> dict[str, Any] | None:
    try:
        from adaos.services.root_mcp.service import invoke_tool

        response = invoke_tool(
            tool_id,
            arguments=arguments,
            request_id=request_id,
            trace_id=trace_id,
            actor="nlu.teacher.llm",
            auth_method="internal",
            dry_run=dry_run,
            auth_context={
                "capabilities": ["development.read.descriptors"],
                "grant_source": "internal_nlu_teacher",
            },
        )
    except Exception:
        _log.debug("failed to invoke Root MCP authoring tool %s", tool_id, exc_info=True)
        return None
    if not bool(getattr(response, "ok", False)):
        return {
            "ok": False,
            "tool_id": getattr(response, "tool_id", tool_id),
            "status": getattr(response, "status", "error"),
            "error": getattr(getattr(response, "error", None), "code", None),
        }
    result = getattr(response, "result", None)
    return dict(result) if isinstance(result, Mapping) else {"ok": True, "result": result}


def _clear_root_mcp_descriptor_cache() -> None:
    with _MCP_DESCRIPTOR_CACHE_LOCK:
        _MCP_DESCRIPTOR_CACHE.clear()


def _root_mcp_descriptor_cache_key(tool_id: str, arguments: Mapping[str, Any]) -> str:
    return _sha256_payload(
        {
            "tool_id": tool_id,
            "arguments": dict(arguments),
        }
    )


def _prune_root_mcp_descriptor_cache_locked() -> None:
    if len(_MCP_DESCRIPTOR_CACHE) <= int(_MCP_EVIDENCE_CACHE_MAX_ENTRIES):
        return
    remove_count = len(_MCP_DESCRIPTOR_CACHE) - int(_MCP_EVIDENCE_CACHE_MAX_ENTRIES)
    for key, _entry in sorted(_MCP_DESCRIPTOR_CACHE.items(), key=lambda item: item[1][0])[:remove_count]:
        _MCP_DESCRIPTOR_CACHE.pop(key, None)


def _invoke_root_mcp_authoring_tool_cached(
    tool_id: str,
    *,
    arguments: dict[str, Any],
    request_id: str | None,
    trace_id: str | None,
    dry_run: bool = True,
    cache_stats: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    ttl_s = max(0.0, float(_MCP_EVIDENCE_CACHE_TTL_S))
    cacheable = bool(dry_run and ttl_s > 0 and tool_id in _MCP_DESCRIPTOR_TOOL_IDS)
    if cacheable:
        key = _root_mcp_descriptor_cache_key(tool_id, arguments)
        now = time.monotonic()
        with _MCP_DESCRIPTOR_CACHE_LOCK:
            entry = _MCP_DESCRIPTOR_CACHE.get(key)
            if entry is not None:
                stored_at, payload = entry
                if now - stored_at <= ttl_s:
                    if cache_stats is not None:
                        cache_stats["hits"] = int(cache_stats.get("hits") or 0) + 1
                    return copy.deepcopy(payload)
                _MCP_DESCRIPTOR_CACHE.pop(key, None)
        if cache_stats is not None:
            cache_stats["misses"] = int(cache_stats.get("misses") or 0) + 1

    result = _invoke_root_mcp_authoring_tool(
        tool_id,
        arguments=arguments,
        request_id=request_id,
        trace_id=trace_id,
        dry_run=dry_run,
    )
    if cacheable and isinstance(result, Mapping) and result.get("ok") is not False:
        with _MCP_DESCRIPTOR_CACHE_LOCK:
            _MCP_DESCRIPTOR_CACHE[key] = (time.monotonic(), copy.deepcopy(dict(result)))
            _prune_root_mcp_descriptor_cache_locked()
        if cache_stats is not None:
            cache_stats["stores"] = int(cache_stats.get("stores") or 0) + 1
    return result


def _compact_desktop_registry_lookup(payload: Mapping[str, Any]) -> dict[str, Any]:
    lookups = payload.get("lookups") if isinstance(payload.get("lookups"), Mapping) else {}
    compact_lookups: dict[str, list[dict[str, Any]]] = {}
    for lookup in ("modal_id", "app_id", "scenario_id"):
        rows = lookups.get(lookup)
        if not isinstance(rows, list):
            continue
        compact_rows: list[dict[str, Any]] = []
        for row in iter_mappings(rows):
            value = str(row.get("value") or "").strip()
            if not value or value.startswith("node:"):
                continue
            labels = [str(item).strip() for item in (row.get("labels") or []) if str(item).strip()] if isinstance(row.get("labels"), list) else []
            compact_rows.append(
                {
                    "value": value,
                    **({"labels": labels[:20]} if labels else {}),
                    "sources": [str(item).strip() for item in (row.get("sources") or []) if str(item).strip()][:5]
                    if isinstance(row.get("sources"), list)
                    else [],
                }
            )
            if len(compact_rows) >= 80:
                break
        compact_lookups[lookup] = compact_rows
    return {
        "ok": bool(payload.get("ok", True)),
        "webspace_id": payload.get("webspace_id"),
        "summary": list(payload.get("summary") or []) if isinstance(payload.get("summary"), list) else [],
        "lookups": compact_lookups,
        "fingerprint": payload.get("fingerprint"),
        "root_scope": dict(payload.get("root_scope") or {}) if isinstance(payload.get("root_scope"), Mapping) else {},
    }


def _collect_root_mcp_authoring_evidence(
    *,
    webspace_id: str,
    text: str,
    request_id: str,
    request_locale: str | None = None,
    preferred_locales: list[str] | None = None,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    cache_stats: dict[str, Any] = {
        "enabled": bool(float(_MCP_EVIDENCE_CACHE_TTL_S) > 0),
        "ttl_s": float(_MCP_EVIDENCE_CACHE_TTL_S),
        "hits": 0,
        "misses": 0,
        "stores": 0,
    }
    base_args: dict[str, Any] = {"webspace_id": webspace_id}
    if request_locale:
        base_args["request_locale"] = request_locale
    if preferred_locales:
        base_args["preferred_locales"] = list(preferred_locales)

    context_result = _invoke_root_mcp_authoring_tool_cached(
        "nlu_authoring.get_context",
        arguments=dict(base_args),
        request_id=request_id,
        trace_id=f"{request_id}.mcp.context",
        dry_run=True,
        cache_stats=cache_stats,
    )
    if isinstance(context_result, Mapping):
        context_payload = context_result.get("context")
        evidence["nlu_authoring_context"] = dict(context_payload) if isinstance(context_payload, Mapping) else dict(context_result)

    registry_result = _invoke_root_mcp_authoring_tool_cached(
        "desktop.registry.lookup",
        arguments={**base_args, "include_live": True},
        request_id=request_id,
        trace_id=f"{request_id}.mcp.registry_lookup",
        dry_run=True,
        cache_stats=cache_stats,
    )
    if isinstance(registry_result, Mapping):
        evidence["desktop_registry_lookup"] = _compact_desktop_registry_lookup(registry_result)

    check_args = {
        **base_args,
        "text": text,
        "use_rasa": True,
        "emit_trace": False,
    }
    check_result = _invoke_root_mcp_authoring_tool(
        "nlu_authoring.check_phrase",
        arguments=check_args,
        request_id=request_id,
        trace_id=f"{request_id}.mcp.check",
        dry_run=True,
    )
    if isinstance(check_result, Mapping):
        evidence["nlu_authoring_phrase_check"] = dict(check_result)

    dialog_args = {**base_args, "request_id": request_id, "limit": 5}
    dialog_result = _invoke_root_mcp_authoring_tool(
        "nlu_authoring.get_dialog_context",
        arguments=dialog_args,
        request_id=request_id,
        trace_id=f"{request_id}.mcp.dialog",
        dry_run=True,
    )
    if isinstance(dialog_result, Mapping):
        evidence["nlu_dialog_context"] = {
            "request_id": dialog_result.get("request_id"),
            "candidate_id": dialog_result.get("candidate_id"),
            "latest_candidate": dialog_result.get("latest_candidate"),
            "correction_context": dialog_result.get("correction_context"),
            "events": list(dialog_result.get("events") or [])[:10] if isinstance(dialog_result.get("events"), list) else [],
        }

    targets_result = _invoke_root_mcp_authoring_tool_cached(
        "nlu_authoring.list_training_targets",
        arguments={**base_args, "include_system_actions": True},
        request_id=request_id,
        trace_id=f"{request_id}.mcp.targets",
        dry_run=True,
        cache_stats=cache_stats,
    )
    if isinstance(targets_result, Mapping):
        targets = list(targets_result.get("targets") or []) if isinstance(targets_result.get("targets"), list) else []
        evidence["nlu_training_targets"] = {
            "summary": dict(targets_result.get("summary") or {}) if isinstance(targets_result.get("summary"), Mapping) else {},
            "targets": targets[:60],
        }

    templates_result = _invoke_root_mcp_authoring_tool_cached(
        "nlu_authoring.list_templates",
        arguments={**base_args, "include_system_actions": True},
        request_id=request_id,
        trace_id=f"{request_id}.mcp.templates",
        dry_run=True,
        cache_stats=cache_stats,
    )
    if isinstance(templates_result, Mapping):
        templates = list(templates_result.get("templates") or []) if isinstance(templates_result.get("templates"), list) else []
        evidence["nlu_templates"] = {
            "summary": dict(templates_result.get("summary") or {}) if isinstance(templates_result.get("summary"), Mapping) else {},
            "templates": templates[:80],
        }

    sdk_result = _invoke_root_mcp_authoring_tool_cached(
        "sdk.describe_surface",
        arguments={"level": "mini"},
        request_id=request_id,
        trace_id=f"{request_id}.mcp.sdk",
        dry_run=True,
        cache_stats=cache_stats,
    )
    if isinstance(sdk_result, Mapping):
        evidence["sdk_surface"] = dict(sdk_result)
    if cache_stats["hits"] or cache_stats["misses"] or cache_stats["stores"]:
        evidence["_meta"] = {"descriptor_cache": cache_stats}
    return evidence


async def _collect_root_mcp_authoring_evidence_async(
    *,
    webspace_id: str,
    text: str,
    request_id: str,
    request_locale: str | None = None,
    preferred_locales: list[str] | None = None,
) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _collect_root_mcp_authoring_evidence,
                webspace_id=webspace_id,
                text=text,
                request_id=request_id,
                request_locale=request_locale,
                preferred_locales=preferred_locales,
            ),
            timeout=max(0.1, float(_MCP_EVIDENCE_TIMEOUT_S)),
        )
    except asyncio.TimeoutError:
        _log.warning(
            "nlu teacher MCP evidence timed out request_id=%s webspace=%s timeout_s=%.3f",
            request_id,
            webspace_id,
            float(_MCP_EVIDENCE_TIMEOUT_S),
        )
        return {
            "_meta": {
                "status": "timeout",
                "timeout_s": float(_MCP_EVIDENCE_TIMEOUT_S),
                "reason": "mcp_evidence_timeout",
            }
        }
    except Exception as exc:
        _log.debug("nlu teacher MCP evidence failed request_id=%s webspace=%s", request_id, webspace_id, exc_info=True)
        return {
            "_meta": {
                "status": "error",
                "reason": "mcp_evidence_failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        }


def _build_prompt(*, request: dict[str, Any], webspace_id: str, context: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        "You are AdaOS NLU teacher. Decide what to do with a user utterance.\n"
        "You must return ONLY valid JSON (no markdown).\n\n"
        "Output schema:\n"
        "{\n"
        '  \"decision\": \"revise_nlu\" | \"propose_regex_rule\" | \"create_skill_candidate\" | \"create_scenario_candidate\" | \"ignore\",\n'
        '  \"intent\": string|null,\n'
        '  \"regex_rule\": {\"intent\": string, \"pattern\": string} | null,\n'
        '  \"target\": {\"type\": \"skill\"|\"scenario\", \"id\": string} | null,\n'
        '  \"examples\": string[],\n'
        '  \"slots\": object,  // e.g. {\"city\": {\"type\": \"string\"}}\n'
        '  \"training_strategy\": \"regex\" | \"rasa_example\" | \"neural_example\" | \"entity_alias\" | \"descriptor_fix\" | \"development_task\" | \"clarification\" | \"ignore\" | object | null,\n'
        '  \"action_candidate\": object|null,\n'
        '  \"need_clarification\": boolean|null,\n'
        '  \"clarification_question\": string|null,\n'
        '  \"options\": object[]|string[]|null,\n'
        '  \"why_not_regex\": string|null,\n'
        '  \"risk_notes\": string|null,\n'
        '  \"confidence\": number, // 0..1\n'
        '  \"notes\": string,\n'
        '  \"candidate\": object|null\n'
        "}\n\n"
        "Rules:\n"
        "- If the utterance is not actionable for AdaOS, decision=ignore.\n"
        "- Prefer existing intents from context (scenario_nlu.intents keys) over inventing new ones.\n"
        "- Use provided context (scenario_nlu, intent_routes, system_actions, host_actions, skills_manifest, builtin_regex, regex_rules, catalog, skill_nlu) to reuse existing intents.\n"
        "- Treat context.root_mcp as governed AdaOS MCP evidence. It is read-only; you may use it to understand entities and phrase-check results, but you must not execute SDK/tool/UI actions.\n"
        "- context.root_mcp.nlu_authoring_context.action_surface.available_actions is the primary governed action inventory. Prefer actions/intents from this surface and use runtime_state/process_state/developer_hints to resolve what is currently available.\n"
        "- If developer_hints describe aliases, primary_actions, slot_schemas, entities, or owner_hints for a skill/scenario, treat them as curated authoring guidance and prefer them over guessing from names alone.\n"
        "- context.root_mcp.desktop_registry_lookup contains canonical modal_id/app_id/scenario_id values and labels/aliases. Use canonical slots for intended actions; display labels are only match evidence.\n"
        "- context.root_mcp.nlu_training_targets and nlu_templates are the governed placement/inventory surfaces; choose a target that exists there and avoid duplicate examples/regex patterns.\n"
        "- SDK surfaces in context.root_mcp are descriptive only. The LLM must propose AdaOS actions/templates, not direct SDK calls.\n"
        "- If context.correction_thread.active=true, the utterance is a correction of a previous candidate. Use the previous candidate only as failure context, and propose a corrected candidate rather than repeating the same rule.\n"
        "- If context.confirmation_retry.active=true, the user rejected the previous voice confirmation. Do not repeat the same intent, target, and pattern; propose a supported alternate hypothesis or return decision=ignore if no safe alternate exists.\n"
        "- host_actions entries include stable system action ids, host event names, slots, examples, and linked intents.\n"
        "- If it matches a known app/widget/scenario through an existing executable intent, prefer teaching that intent with propose_regex_rule.\n"
        "- If the user says show/open/launch/покажи/открой plus a known app or modal title and that app has launchModal, prefer intent desktop.open_modal with slot modal_id. Do not use desktop.toggle_app_install for showing/opening an already available app.\n"
        "- Use desktop.switch_scenario/open_scenario only when the user explicitly asks to switch/open a scenario (e.g. says scenario/сценарий/переключи сценарий). Generic 'покажи X' for a desktop app/modal is modal opening, not scenario switching.\n"
        "- If this is a fallback after NLU missed and an existing intent/action is the right match, prefer propose_regex_rule so AdaOS can replay the phrase through regex.dynamic.\n"
        "- Use revise_nlu only when a compact safe regex is not enough or the best next step is curated dataset examples for neural/Rasa.\n"
        "- Choose training_strategy deliberately. Use regex only for stable command phrases and lookup-backed slots. For broad semantic wording, repeated corrections, or ambiguity, prefer rasa_example, neural_example, entity_alias, descriptor_fix, development_task, or clarification.\n"
        "- If regex is not the right strategy, set why_not_regex with a concise reason.\n"
        "- If the likely action exists but the phrase is ambiguous, set need_clarification=true, ask one short clarification_question, and provide 2-4 options with ids, labels, and action_candidate details when possible.\n"
        "- action_candidate is descriptive only: describe the intended AdaOS action/intent/slots, but do not call any action.\n"
        "- propose_regex_rule.pattern MUST be a Python regex with named capture groups for slots (e.g. (?P<city>...)).\n"
        "- Avoid proposing duplicate regex rules if builtin_regex or regex_rules already cover the utterance.\n"
        "- If user asks about weather/temperature but doesn't say the exact keyword, propose a regex rule for intent desktop.open_weather.\n"
        "- For lookup-backed slots such as scenario_id, modal_id, app_id, node_ref, webspace_id, and skill_id, capture the user text in a named group; AdaOS canonicalizes known values/labels before dispatch.\n"
        "- Use the exact slot names expected by the existing intent/action. For scenario switching use (?P<scenario_id>...), not (?P<scenario>...). For modal opening use (?P<modal_id>...), not (?P<modal>...).\n"
        "- The regex must match the exact user request text after normal case-insensitive Python regex matching.\n"
        "- When the user used a localized label (e.g. Russian text), include that surface form in the regex alternative so preview matches the exact request; AdaOS will canonicalize the captured label through lookup aliases.\n"
        "- Regex rules should be reasonably general (avoid overfitting to a single verb like \"покажи\"); capture city via (?P<city>...).\n"
        "- When proposing a regex rule, also set target to where the rule should be stored:\n"
        "  - Prefer the skill that handles the intent (see context.intent_routes) over the scenario.\n"
        "  - For intents that trigger system actions (callHost targets from context.system_actions/host_actions), target should be the current scenario that owns the intent, not the scenario/app/modal being opened.\n"
        "- If it suggests a new capability, propose create_skill_candidate or create_scenario_candidate.\n"
        "- Keep intent names short and namespaced (e.g. desktop.open_weather, smalltalk.how_are_you).\n"
    )
    utterance = request.get("text") if isinstance(request.get("text"), str) else ""
    user = {
        "webspace_id": webspace_id,
        "request": {
            "id": request.get("id"),
            "request_id": request.get("request_id"),
            "text": utterance,
            "reason": request.get("reason"),
            "via": request.get("via"),
        },
        "context": context,
    }
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


async def _llm_call(messages: list[dict[str, str]], *, request_id: str | None = None) -> dict[str, Any]:
    ctx = get_ctx()
    http = RootHttpClient.from_settings(ctx.settings)
    body = {"model": _MODEL, "messages": messages, "max_tokens": _MAX_TOKENS, "temperature": 0.2}
    headers: dict[str, str] = {}
    subnet_id = str(getattr(getattr(ctx, "config", None), "subnet_id", "") or "").strip()
    node_id = str(getattr(getattr(ctx, "config", None), "node_id", "") or "").strip()
    if subnet_id:
        headers["X-AdaOS-Subnet-Id"] = subnet_id
    if node_id:
        headers["X-AdaOS-Node-Id"] = node_id
    req_id = str(request_id or "").strip()
    if req_id:
        body["request_id"] = req_id
    try:
        result = await asyncio.to_thread(
            http.request,
            "POST",
            "/v1/llm/response",
            json=body,
            headers=headers or None,
            timeout=_TIMEOUT_S,
        )
    except Exception as exc:
        try:
            observe_hub_root_integration_outbox(
                "llm",
                publish_fail=1,
                connected=False,
                operation_key=req_id or None,
                idempotency_mode="request_id" if req_id else "none",
                conflict=1 if "llm_request_id_conflict" in str(exc) else None,
                last_error=f"{type(exc).__name__}: {exc}",
            )
        except Exception:
            pass
        try:
            set_integration_readiness(
                "llm",
                status=ReadinessStatus.DEGRADED,
                summary="root LLM proxy request failed",
                details={"error": f"{type(exc).__name__}: {exc}"},
            )
        except Exception:
            pass
        raise

    protocol = result.get("_protocol") if isinstance(result, dict) and isinstance(result.get("_protocol"), dict) else {}
    cache_state = str(protocol.get("dedupe") or "").strip().lower()
    try:
        observe_hub_root_integration_outbox(
            "llm",
            publish_ok=1,
            connected=True,
            operation_key=req_id or None,
            idempotency_mode="request_id" if req_id else "none",
            cache_hit=1 if cache_state == "hit" else None,
            cache_miss=1 if req_id and cache_state != "hit" else None,
        )
    except Exception:
        pass
    try:
        details = {"model": _MODEL}
        if req_id:
            details["request_id"] = req_id
        if cache_state:
            details["cache"] = cache_state
        set_integration_readiness(
            "llm",
            status=ReadinessStatus.READY,
            summary="root LLM proxy request succeeded",
            details=details,
        )
    except Exception:
        pass
    return result


async def _append_llm_log(webspace_id: str, entry: dict[str, Any]) -> None:
    async with _nlu_llm_write_meta():
        async with async_get_ydoc(webspace_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
            data_map = ydoc.get_map("data")
            teacher = _teacher_obj(data_map)
            logs = list(iter_mappings(teacher.get("llm_logs")))
            logs.append(entry)
            teacher["llm_logs"] = logs[-300:]
            with ydoc.begin_transaction() as txn:
                data_map.set(txn, "nlu_teacher", teacher)


async def _patch_llm_log(webspace_id: str, *, log_id: str, patch: dict[str, Any]) -> None:
    async with _nlu_llm_write_meta():
        async with async_get_ydoc(webspace_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
            data_map = ydoc.get_map("data")
            teacher = _teacher_obj(data_map)
            logs = list(iter_mappings(teacher.get("llm_logs")))
            next_logs: list[dict[str, Any]] = []
            for item in logs:
                if item.get("id") == log_id:
                    updated = dict(item)
                    updated.update(patch)
                    next_logs.append(updated)
                else:
                    next_logs.append(item)
            teacher["llm_logs"] = next_logs[-300:]
            with ydoc.begin_transaction() as txn:
                data_map.set(txn, "nlu_teacher", teacher)


async def _update_revision_by_request_id(
    webspace_id: str,
    *,
    request_id: str,
    patch: dict[str, Any],
) -> Optional[dict[str, Any]]:
    async with _nlu_llm_write_meta():
        async with async_get_ydoc(webspace_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
            data_map = ydoc.get_map("data")
            teacher = _teacher_obj(data_map)
            revisions = list(iter_mappings(teacher.get("revisions")))
            updated: Optional[dict[str, Any]] = None
            cleaned: list[dict[str, Any]] = []
            for item in revisions:
                if item.get("request_id") == request_id and item.get("status") in {"pending", "proposed"}:
                    updated = dict(item)
                    updated.update(patch)
                    cleaned.append(updated)
                else:
                    cleaned.append(item)
            teacher["revisions"] = cleaned
            with ydoc.begin_transaction() as txn:
                data_map.set(txn, "nlu_teacher", teacher)
            return updated


async def _append_candidate(webspace_id: str, candidate: dict[str, Any]) -> None:
    async with _nlu_llm_write_meta():
        async with async_get_ydoc(webspace_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
            data_map = ydoc.get_map("data")
            teacher = _teacher_obj(data_map)
            candidates = list(iter_mappings(teacher.get("candidates")))
            candidates.append(candidate)
            teacher["candidates"] = candidates[-200:]
            with ydoc.begin_transaction() as txn:
                data_map.set(txn, "nlu_teacher", teacher)


async def _propose_strategy_candidate(
    ctx: Any,
    *,
    webspace_id: str,
    request_id: str,
    request_text: str,
    entry: dict[str, Any],
    meta: Mapping[str, Any],
) -> None:
    try:
        await _append_candidate(webspace_id, entry)
    except Exception:
        _log.debug("failed to append strategy candidate webspace=%s", webspace_id, exc_info=True)
    bus_emit(
        ctx.bus,
        "nlp.teacher.candidate.proposed",
        {"webspace_id": webspace_id, "candidate": entry, "_meta": dict(meta)},
        source="nlu.teacher.llm",
    )
    try:
        await append_event(
            webspace_id,
            make_event(
                webspace_id=webspace_id,
                request_id=request_id,
                request_text=request_text,
                kind="candidate.proposed",
                title="Candidate proposed",
                subtitle=f"{entry.get('kind')}: {((entry.get('training_strategy') or {}).get('primary') or '')}".strip(),
                raw=entry,
                meta=meta,
            ),
        )
    except Exception:
        _log.debug("failed to append teacher event (candidate.proposed strategy) webspace=%s", webspace_id, exc_info=True)


async def _find_duplicate_regex_candidate(
    webspace_id: str,
    *,
    intent: str,
    pattern: str,
    target: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    target_token = _target_key(target)
    async with async_get_ydoc(webspace_id, read_only=True, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
        data_map = ydoc.get_map("data")
        teacher = _teacher_obj(data_map)
        for item in reversed(list(iter_mappings(teacher.get("candidates")))):
            status = item.get("status") if isinstance(item.get("status"), str) else ""
            if status and status not in _DUPLICATE_ACTIVE_STATUSES:
                continue
            rr = item.get("regex_rule") if isinstance(item.get("regex_rule"), Mapping) else {}
            if rr.get("intent") != intent:
                continue
            if rr.get("pattern") != pattern:
                continue
            if _target_key(item.get("target") if isinstance(item.get("target"), Mapping) else None) != target_token:
                continue
            return dict(item)
    return None


async def _handle_teacher_request(evt: Any) -> None:
    ctx = None
    webspace_id = None
    try:
        ctx = get_ctx()
        payload = _payload(evt)
        webspace_id = _resolve_webspace_id(payload)
        req = payload.get("request") if isinstance(payload.get("request"), Mapping) else None
        if not req:
            return

        req_meta = req.get("_meta") if isinstance(req.get("_meta"), Mapping) else {}
        text = req.get("text")
        request_id = req.get("request_id")
        if not isinstance(text, str) or not text.strip():
            return
        if not isinstance(request_id, str) or not request_id.strip():
            return
        text = text.strip()
        request_id = request_id.strip()

        if not _teacher_enabled(ctx):
            return
        if not _llm_teacher_enabled(ctx):
            try:
                await append_event(
                    webspace_id,
                    make_event(
                        webspace_id=webspace_id,
                        request_id=request_id,
                        request_text=text,
                        kind="llm.skipped",
                        title="LLM Teacher skipped",
                        subtitle="llm_teacher_disabled",
                        raw={
                            "reason": "llm_teacher_disabled",
                            "env": {
                                "ADAOS_NLU_TEACHER": _TEACHER_ENABLED,
                                "ADAOS_NLU_LLM_TEACHER": _LLM_TEACHER_ENABLED,
                            },
                            "root_policy": _root_teacher_policy_enabled(ctx),
                        },
                        meta=req_meta,
                    ),
                )
            except Exception:
                _log.debug("failed to append teacher event (llm.skipped) webspace=%s", webspace_id, exc_info=True)
            bus_emit(
                ctx.bus,
                "nlp.teacher.llm.skipped",
                {
                    "webspace_id": webspace_id,
                    "request_id": request_id,
                    "reason": "llm_teacher_disabled",
                    "_meta": dict(req_meta),
                },
                source="nlu.teacher.llm",
            )
            return

        # Build lightweight context snapshot for LLM.
        try:
            async with async_get_ydoc(webspace_id, read_only=True, prefer_live_room=True, load_mark_roots=["data", "ui"]) as ydoc:
                snapshot = _ydoc_to_snapshot(ydoc)
        except Exception:
            snapshot = {}
        context = _extract_webspace_context(snapshot if isinstance(snapshot, dict) else {})
        teacher_snapshot = coerce_dict(coerce_dict((snapshot or {}).get("data")).get("nlu_teacher")) if isinstance(snapshot, dict) else {}
        correction_context = _correction_thread_context(
            teacher=teacher_snapshot,
            text=text,
            request_id=request_id,
        )
        if correction_context:
            context["correction_thread"] = correction_context
        rejected_candidate_id = req_meta.get("rejected_candidate_id")
        if isinstance(rejected_candidate_id, str) and rejected_candidate_id.strip():
            for item in iter_mappings(teacher_snapshot.get("candidates")):
                if item.get("id") != rejected_candidate_id.strip():
                    continue
                context["confirmation_retry"] = {
                    "active": True,
                    "attempt": req_meta.get("nlu_teacher_confirmation_attempt"),
                    "previous_request_id": req_meta.get("previous_request_id"),
                    "rejected_candidate": _compact_candidate_for_context(item),
                }
                break
        context["scenario_nlu"] = _extract_scenario_nlu(scenario_id=context.get("current_scenario"))
        try:
            routes, skill_policies, skill_manifests = _build_intent_routes_and_policies(
                scenario_nlu=context.get("scenario_nlu") if isinstance(context.get("scenario_nlu"), Mapping) else {},
                skills=context.get("skills") if isinstance(context.get("skills"), list) else [],
            )
        except Exception:
            routes, skill_policies, skill_manifests = ([], {}, [])
        context["intent_routes"] = routes
        # System actions are "callHost" targets exposed by the current scenario intents.
        context["system_actions"] = sorted(
            {str(r.get("target")) for r in routes if r.get("action") == "callHost" and isinstance(r.get("target"), str)}
        )[:150]
        context["skills_manifest"] = skill_manifests
        try:
            from adaos.services.nlu.system_actions_catalog import (
                SYSTEM_ACTION_CATALOG_VERSION,
                describe_system_actions,
            )

            context["host_actions"] = describe_system_actions()
            context["host_actions_version"] = SYSTEM_ACTION_CATALOG_VERSION
        except Exception:
            context["host_actions"] = []
        try:
            from adaos.services.nlu.pipeline import describe_builtin_regex_rules  # local import to avoid cycles

            context["builtin_regex"] = describe_builtin_regex_rules()
        except Exception:
            context["builtin_regex"] = []
        request_locale = req.get("request_locale") if isinstance(req.get("request_locale"), str) else None
        if request_locale is None:
            request_locale = req_meta.get("request_locale") if isinstance(req_meta.get("request_locale"), str) else None
        preferred_locales_raw = req.get("preferred_locales") or req_meta.get("preferred_locales")
        preferred_locales = [str(x).strip() for x in iter_scalars(preferred_locales_raw) if str(x).strip()]
        mcp_evidence = await _collect_root_mcp_authoring_evidence_async(
            webspace_id=webspace_id,
            text=text,
            request_id=request_id,
            request_locale=request_locale,
            preferred_locales=preferred_locales,
        )
        if mcp_evidence:
            context["root_mcp"] = mcp_evidence

        messages = _build_prompt(request=dict(req), webspace_id=webspace_id, context=context)
        prompt_audit = {
            "request_hash": _sha256_payload({"webspace_id": webspace_id, "request": dict(req)}),
            "context_hash": _sha256_payload(context),
            "prompt_hash": _sha256_payload(messages),
            "correction_thread": {
                "active": True,
                "thread_id": correction_context.get("thread_id"),
                "previous_candidate_id": (correction_context.get("previous_candidate") or {}).get("candidate_id")
                if isinstance(correction_context.get("previous_candidate"), Mapping)
                else None,
            }
            if isinstance(correction_context, Mapping)
            else {"active": False},
        }

        log_id = f"llm.{int(time.time() * 1000)}"
        started_at = time.time()

        try:
            await _append_llm_log(
                webspace_id,
                {
                    "id": log_id,
                    "ts": started_at,
                    "request_id": request_id,
                    "webspace_id": webspace_id,
                    "model": _MODEL,
                    "audit": dict(prompt_audit),
                    "request": {
                        "messages": _redact_messages(messages),
                        "max_tokens": _MAX_TOKENS,
                        "timeout_s": _TIMEOUT_S,
                    },
                    "status": "request",
                },
            )
        except Exception:
            _log.debug("failed to append llm log webspace=%s", webspace_id, exc_info=True)

        try:
            await append_event(
                webspace_id,
                make_event(
                    webspace_id=webspace_id,
                    request_id=request_id,
                    request_text=text,
                    kind="llm.request",
                    title="LLM request",
                    subtitle=_MODEL,
                    raw={
                        "log_id": log_id,
                        "model": _MODEL,
                        "messages": _redact_messages(messages),
                        "max_tokens": _MAX_TOKENS,
                        "timeout_s": _TIMEOUT_S,
                        "audit": dict(prompt_audit),
                    },
                    meta=req_meta,
                ),
            )
        except Exception:
            _log.debug("failed to append teacher event (llm.request) webspace=%s", webspace_id, exc_info=True)

        try:
            res = await _llm_call(messages, request_id=request_id or log_id)
        except Exception as exc:
            _log.warning("llm teacher call failed: %s", exc)
            try:
                await _patch_llm_log(
                    webspace_id,
                    log_id=log_id,
                    patch={
                        "status": "error",
                        "error": str(exc),
                        "duration_s": max(0.0, time.time() - started_at),
                    },
                )
            except Exception:
                _log.debug("failed to patch llm log webspace=%s", webspace_id, exc_info=True)
            return

        raw_text = _extract_first_output_text(res)
        if not raw_text:
            _log.warning("llm teacher returned empty output")
            try:
                await _patch_llm_log(
                    webspace_id,
                    log_id=log_id,
                    patch={
                        "status": "error",
                        "error": "empty_output",
                        "response": {"raw": None},
                        "duration_s": max(0.0, time.time() - started_at),
                    },
                )
            except Exception:
                _log.debug("failed to patch llm log webspace=%s", webspace_id, exc_info=True)
            return

        suggestion = _parse_llm_json_object(raw_text)
        if not isinstance(suggestion, dict):
            suggestion = {"decision": "ignore", "notes": raw_text, "confidence": 0.0}

        try:
            await _patch_llm_log(
                webspace_id,
                log_id=log_id,
                patch={
                    "status": "response",
                    "response": {"raw": _truncate(raw_text, 4000), "parsed": suggestion},
                    "duration_s": max(0.0, time.time() - started_at),
                },
            )
        except Exception:
            _log.debug("failed to patch llm log webspace=%s", webspace_id, exc_info=True)

        decision = suggestion.get("decision") if isinstance(suggestion.get("decision"), str) else "ignore"
        intent = suggestion.get("intent") if isinstance(suggestion.get("intent"), str) else None
        regex_rule = suggestion.get("regex_rule") if isinstance(suggestion.get("regex_rule"), Mapping) else None
        target = suggestion.get("target") if isinstance(suggestion.get("target"), Mapping) else None
        examples = suggestion.get("examples") if isinstance(suggestion.get("examples"), list) else None
        if examples is None:
            examples = [text]
        examples = [x.strip() for x in examples if isinstance(x, str) and x.strip()]
        slots = suggestion.get("slots") if isinstance(suggestion.get("slots"), Mapping) else {}
        confidence = suggestion.get("confidence")
        try:
            confidence_f = float(confidence) if confidence is not None else 0.0
        except Exception:
            confidence_f = 0.0
        notes = suggestion.get("notes") if isinstance(suggestion.get("notes"), str) else ""
        training_strategy = _normalized_training_strategy(
            suggestion=suggestion,
            decision=decision,
            regex_rule=regex_rule,
        )
        strategy_primary = str(training_strategy.get("primary") or "").strip()

        llm_meta = {
            "model": _MODEL,
            "ts": time.time(),
            "decision": decision,
            "confidence": confidence_f,
            "training_strategy": dict(training_strategy),
            "audit": dict(prompt_audit),
        }

        try:
            await append_event(
                webspace_id,
                make_event(
                    webspace_id=webspace_id,
                    request_id=request_id,
                    request_text=text,
                    kind="llm.response",
                    title="LLM response",
                    subtitle=f"{decision} ({confidence_f:.2f})",
                    raw={"log_id": log_id, "decision": decision, "suggestion": suggestion},
                    meta=req_meta,
                ),
            )
        except Exception:
            _log.debug("failed to append teacher event (llm.response) webspace=%s", webspace_id, exc_info=True)

        need_clarification = bool(suggestion.get("need_clarification")) or strategy_primary == "clarification"
        clarification_question = (
            suggestion.get("clarification_question") if isinstance(suggestion.get("clarification_question"), str) else ""
        )
        if need_clarification and clarification_question.strip():
            action_candidate = (
                dict(suggestion.get("action_candidate"))
                if isinstance(suggestion.get("action_candidate"), Mapping)
                else None
            )
            session = {
                "id": f"clarify.{int(time.time() * 1000)}",
                "ts": time.time(),
                "kind": "llm_clarification",
                "uncertainty_kind": "llm_ambiguity",
                "request_id": request_id,
                "request_text": text,
                "question": clarification_question.strip(),
                "allowed_answers": _clarification_allowed_answers(suggestion),
                "attempt": int(req_meta.get("nlu_teacher_confirmation_attempt") or 0)
                if str(req_meta.get("nlu_teacher_confirmation_attempt") or "").isdigit()
                else 0,
                "llm": llm_meta,
                "action_candidate": action_candidate,
                "training_strategy": dict(training_strategy),
                "_meta": dict(req_meta),
            }
            risk_notes = suggestion.get("risk_notes")
            if isinstance(risk_notes, str) and risk_notes.strip():
                session["risk_notes"] = risk_notes.strip()
            try:
                from adaos.services.nlu.teacher_confirmation_runtime import request_clarification

                await request_clarification(webspace_id, session, meta=req_meta)
            except Exception:
                _log.warning(
                    "failed to request NLU Teacher clarification webspace=%s request_id=%s",
                    webspace_id,
                    request_id,
                    exc_info=True,
                )
            return

        resolved_strategy_target = _coerce_target(target)
        if intent and resolved_strategy_target is None:
            resolved_strategy_target = _resolve_regex_target(
                intent=intent,
                proposed_target=target,
                routes=routes,
                current_scenario=context.get("current_scenario"),
            )

        if strategy_primary in {"entity_alias", "descriptor_fix", "development_task", "clarification"} or (
            strategy_primary in _EXAMPLE_TRAINING_STRATEGIES and decision != "propose_regex_rule"
        ):
            candidate_id = f"cand.{int(time.time()*1000)}"
            entry = _build_strategy_candidate_entry(
                candidate_id=candidate_id,
                request_id=request_id,
                text=text,
                decision=decision,
                suggestion=suggestion,
                intent=intent,
                target=resolved_strategy_target,
                examples=examples,
                slots=slots,
                context=context,
                training_strategy=training_strategy,
                llm_meta=llm_meta,
                notes=notes,
            )
            if isinstance(correction_context, Mapping):
                previous_candidate = correction_context.get("previous_candidate")
                entry["thread_id"] = correction_context.get("thread_id")
                entry["correction_of"] = {
                    "candidate_id": previous_candidate.get("candidate_id")
                    if isinstance(previous_candidate, Mapping)
                    else None,
                    "request_id": previous_candidate.get("request_id")
                    if isinstance(previous_candidate, Mapping)
                    else None,
                    "status": previous_candidate.get("status") if isinstance(previous_candidate, Mapping) else None,
                }
            await _propose_strategy_candidate(
                ctx,
                webspace_id=webspace_id,
                request_id=request_id,
                request_text=text,
                entry=entry,
                meta=req_meta,
            )
            return

        if decision == "revise_nlu" and intent:
            patch = {
                "status": "proposed",
                "proposal": {"intent": intent, "examples": examples, "slots": dict(slots)},
                "llm": llm_meta,
                "training_strategy": dict(training_strategy),
                "note": notes or "LLM proposed NLU revision.",
                "proposed_at": time.time(),
            }
            if isinstance(correction_context, Mapping):
                previous_candidate = correction_context.get("previous_candidate")
                patch["thread_id"] = correction_context.get("thread_id")
                patch["correction_of"] = {
                    "candidate_id": previous_candidate.get("candidate_id")
                    if isinstance(previous_candidate, Mapping)
                    else None,
                    "request_id": previous_candidate.get("request_id") if isinstance(previous_candidate, Mapping) else None,
                    "status": previous_candidate.get("status") if isinstance(previous_candidate, Mapping) else None,
                }
            try:
                updated = await _update_revision_by_request_id(webspace_id, request_id=request_id, patch=patch)
            except Exception:
                _log.warning(
                    "failed to update teacher revision webspace=%s request_id=%s", webspace_id, request_id, exc_info=True
                )
                updated = None
            bus_emit(
                ctx.bus,
                "nlp.teacher.revision.suggested",
                {"webspace_id": webspace_id, "request_id": request_id, "revision": updated, "suggestion": suggestion},
                source="nlu.teacher.llm",
            )
            try:
                await append_event(
                    webspace_id,
                    make_event(
                        webspace_id=webspace_id,
                        request_id=request_id,
                        request_text=text,
                        kind="revision.suggested",
                        title="Revision suggested",
                        subtitle=intent,
                        raw=updated if isinstance(updated, Mapping) else {"intent": intent, "examples": examples},
                        meta=req_meta,
                    ),
                )
            except Exception:
                _log.debug("failed to append teacher event (revision.suggested) webspace=%s", webspace_id, exc_info=True)
            return

        if decision == "propose_regex_rule" and regex_rule:
            rr_intent = regex_rule.get("intent")
            rr_pattern = regex_rule.get("pattern")
            if isinstance(rr_intent, str) and rr_intent.strip() and isinstance(rr_pattern, str) and rr_pattern.strip():
                rr_pattern, slots, slot_aliases = _normalize_regex_rule_slots(pattern=rr_pattern, slots=slots)
                rr_intent = rr_intent.strip()
                regex_policy_rejection = _regex_policy_rejection(
                    pattern=rr_pattern,
                    intent=rr_intent,
                    training_strategy=training_strategy,
                    confidence=confidence_f,
                )
                if regex_policy_rejection:
                    rejected_strategy = dict(training_strategy)
                    fallback_primary = str(regex_policy_rejection.get("strategy") or "").strip()
                    if fallback_primary not in _ALLOWED_TRAINING_STRATEGIES or fallback_primary == "regex":
                        fallback_primary = "rasa_example"
                    rejected_strategy["primary"] = fallback_primary
                    rejected_strategy["source"] = (
                        "adaos.policy"
                        if str(training_strategy.get("primary") or "").strip() == "regex"
                        else training_strategy.get("source") or "llm"
                    )
                    rejected_strategy["why_not_regex"] = (
                        str(regex_policy_rejection.get("why_not_regex") or "").strip()
                        or str(regex_policy_rejection.get("reason") or "").strip()
                        or "regex rejected by NLU Teacher strategy policy"
                    )
                    target_out = _resolve_regex_target(
                        intent=rr_intent,
                        proposed_target=target,
                        routes=routes,
                        current_scenario=context.get("current_scenario"),
                    )
                    candidate_id = f"cand.{int(time.time()*1000)}"
                    entry = _build_strategy_candidate_entry(
                        candidate_id=candidate_id,
                        request_id=request_id,
                        text=text,
                        decision=decision,
                        suggestion=suggestion,
                        intent=rr_intent,
                        target=target_out,
                        examples=examples,
                        slots=slots,
                        context=context,
                        training_strategy=rejected_strategy,
                        llm_meta={**dict(llm_meta), "training_strategy": dict(rejected_strategy)},
                        notes=(
                            (notes + "\n") if notes else ""
                        )
                        + f"AdaOS rejected regex strategy: {regex_policy_rejection.get('reason')}.",
                        regex_rejection={
                            **dict(regex_policy_rejection),
                            "regex_rule": {"intent": rr_intent, "pattern": rr_pattern},
                        },
                    )
                    await _propose_strategy_candidate(
                        ctx,
                        webspace_id=webspace_id,
                        request_id=request_id,
                        request_text=text,
                        entry=entry,
                        meta=req_meta,
                    )
                    return
                initial_preview = _preview_regex_candidate(pattern=rr_pattern, text=text)
                open_modal_repair = _infer_open_modal_repair(text=text, context=context)
                repair_meta: dict[str, Any] | None = None
                initial_preview_slots = initial_preview.get("slots") if isinstance(initial_preview.get("slots"), Mapping) else {}
                if isinstance(open_modal_repair, Mapping) and (
                    rr_intent in _OPEN_MODAL_REPAIR_INTENTS
                    or (
                        rr_intent == "desktop.open_modal"
                        and (not initial_preview.get("ok") or not str(initial_preview_slots.get("modal_id") or "").strip())
                    )
                ):
                    repair_meta = dict(open_modal_repair)
                    repair_meta["from_intent"] = rr_intent
                    repair_meta["from_pattern"] = rr_pattern
                    repair_meta["from_preview"] = dict(initial_preview)
                    rr_intent = "desktop.open_modal"
                    rr_pattern = str(open_modal_repair.get("pattern") or "").strip() or rr_pattern
                    initial_preview = _preview_regex_candidate(pattern=rr_pattern, text=text)
                    notes = (
                        (notes + "\n") if notes else ""
                    ) + "AdaOS repaired the LLM proposal to desktop.open_modal using desktop registry lookup aliases."
                target_out = _resolve_regex_target(
                    intent=rr_intent,
                    proposed_target=target,
                    routes=routes,
                    current_scenario=context.get("current_scenario"),
                )
                preview = initial_preview
                normalization: dict[str, Any] = {}
                if slot_aliases:
                    normalization["slot_aliases"] = slot_aliases
                if repair_meta:
                    normalization["llm_proposal_repair"] = repair_meta
                candidate_id = f"cand.{int(time.time()*1000)}"
                action_candidate, template_candidate = _build_regex_candidate_envelopes(
                    candidate_id=candidate_id,
                    request_id=request_id,
                    text=text,
                    intent=rr_intent,
                    pattern=rr_pattern,
                    target=target_out,
                    preview=preview,
                    slots=slots,
                    context=context,
                    training_strategy=training_strategy,
                )
                entry = {
                    "id": candidate_id,
                    "ts": time.time(),
                    "kind": "regex_rule",
                    "text": text,
                    "request_id": request_id,
                    "origin_scenario_id": context.get("current_scenario")
                    if isinstance(context.get("current_scenario"), str)
                    else None,
                    "candidate": {
                        "name": f"Regex rule for {rr_intent.strip()}",
                        "description": "Proposed regex rule to improve fast NLU stage.",
                    },
                    "regex_rule": {"intent": rr_intent, "pattern": rr_pattern},
                    **({"target": target_out} if target_out else {}),
                    **({"slots": dict(slots)} if slots else {}),
                    **({"normalization": normalization} if normalization else {}),
                    "training_strategy": dict(training_strategy),
                    "action_candidate": action_candidate,
                    "template_candidate": template_candidate,
                    "llm": llm_meta,
                    "notes": notes,
                    "preview": preview,
                    "status": "pending" if preview.get("ok") else "quarantined",
                }
                if isinstance(correction_context, Mapping):
                    previous_candidate = correction_context.get("previous_candidate")
                    entry["thread_id"] = correction_context.get("thread_id")
                    entry["correction_of"] = {
                        "candidate_id": previous_candidate.get("candidate_id")
                        if isinstance(previous_candidate, Mapping)
                        else None,
                        "request_id": previous_candidate.get("request_id")
                        if isinstance(previous_candidate, Mapping)
                        else None,
                        "status": previous_candidate.get("status") if isinstance(previous_candidate, Mapping) else None,
                    }
                try:
                    duplicate = await _find_duplicate_regex_candidate(
                        webspace_id,
                        intent=rr_intent,
                        pattern=rr_pattern,
                        target=target_out,
                    )
                except Exception:
                    duplicate = None
                if isinstance(duplicate, Mapping):
                    duplicate_payload = {
                        "webspace_id": webspace_id,
                        "request_id": request_id,
                        "candidate_id": duplicate.get("id"),
                        "duplicate_of": duplicate.get("id"),
                        "suppressed": {
                            "intent": rr_intent,
                            "pattern": rr_pattern,
                            "target": dict(target_out or {}),
                            "preview": preview,
                            "llm": llm_meta,
                        },
                        "_meta": dict(req_meta),
                    }
                    bus_emit(
                        ctx.bus,
                        "nlp.teacher.candidate.duplicate_suppressed",
                        duplicate_payload,
                        source="nlu.teacher.llm",
                    )
                    try:
                        await append_event(
                            webspace_id,
                            make_event(
                                webspace_id=webspace_id,
                                request_id=request_id,
                                request_text=text,
                                kind="candidate.duplicate_suppressed",
                                title="Candidate duplicate suppressed",
                                subtitle=f"regex_rule: {rr_intent}",
                                raw=duplicate_payload,
                                meta=req_meta,
                            ),
                        )
                    except Exception:
                        _log.debug(
                            "failed to append teacher event (candidate.duplicate_suppressed) webspace=%s",
                            webspace_id,
                            exc_info=True,
                        )
                    return
                try:
                    await _append_candidate(webspace_id, entry)
                except Exception:
                    _log.debug("failed to append regex rule candidate webspace=%s", webspace_id, exc_info=True)
                bus_emit(
                    ctx.bus,
                    "nlp.teacher.candidate.proposed",
                    {"webspace_id": webspace_id, "candidate": entry, "_meta": dict(req_meta)},
                    source="nlu.teacher.llm",
                )
                # Auto-apply if the owning skill explicitly trusts NLU Teacher output.
                try:
                    route_id = str(req_meta.get("route_id") or req_meta.get("route") or "").strip()
                    if (
                        route_id != "voice_chat"
                        and entry.get("status") == "pending"
                        and isinstance(target_out, dict)
                        and target_out.get("type") == "skill"
                    ):
                        skill_name = target_out.get("id")
                        policy = skill_policies.get(skill_name) if isinstance(skill_name, str) else None
                        if isinstance(policy, Mapping) and bool(policy.get("autoapply_nlu_teacher")):
                            bus_emit(
                                ctx.bus,
                                "nlp.teacher.candidate.apply",
                                {
                                    "webspace_id": webspace_id,
                                    "candidate_id": entry.get("id"),
                                    "target": dict(target_out),
                                    "_meta": dict(req_meta),
                                },
                                source="nlu.teacher.llm",
                            )
                except Exception:
                    _log.debug("failed to auto-apply teacher regex candidate webspace=%s", webspace_id, exc_info=True)
                try:
                    await append_event(
                        webspace_id,
                        make_event(
                            webspace_id=webspace_id,
                            request_id=request_id,
                            request_text=text,
                            kind="candidate.proposed",
                            title="Candidate proposed",
                            subtitle=f"regex_rule: {rr_intent}",
                            raw=entry,
                            meta=req_meta,
                        ),
                    )
                except Exception:
                    _log.debug("failed to append teacher event (candidate.proposed regex_rule) webspace=%s", webspace_id, exc_info=True)

                if str(req_meta.get("route_id") or req_meta.get("route") or "").strip() == "voice_chat":
                    return

                bus_emit(
                    ctx.bus,
                    "io.out.chat.append",
                    {
                        "id": "",
                        "from": "hub",
                        "text": (
                            f"Я не смог распознать запрос в Rasa: «{text}».\n\n"
                            "Я предложил улучшение NLU в виде regex-правила, чтобы такие запросы распознавались сразу.\n"
                            "Открой «NLU Teacher» (Apps) и нажми Apply у кандидата типа regex_rule.\n"
                            "После Apply тот же запрос начнёт распознаваться на этапе regex без обращения к LLM."
                        ),
                        "ts": time.time(),
                        "_meta": {"webspace_id": webspace_id, **dict(req_meta)},
                    },
                    source="router.nlu",
                )
                return

        if decision in {"create_skill_candidate", "create_scenario_candidate"}:
            candidate = suggestion.get("candidate") if isinstance(suggestion.get("candidate"), Mapping) else {}
            entry = {
                "id": f"cand.{int(time.time()*1000)}",
                "ts": time.time(),
                "kind": "skill" if decision == "create_skill_candidate" else "scenario",
                "text": text,
                "request_id": request_id,
                "candidate": dict(candidate),
                "llm": llm_meta,
                "notes": notes,
                "status": "pending",
            }
            try:
                await _append_candidate(webspace_id, entry)
            except Exception:
                _log.debug("failed to append candidate webspace=%s", webspace_id, exc_info=True)
            bus_emit(
                ctx.bus,
                "nlp.teacher.candidate.proposed",
                {"webspace_id": webspace_id, "candidate": entry, "_meta": dict(req_meta)},
                source="nlu.teacher.llm",
            )
            try:
                await append_event(
                    webspace_id,
                    make_event(
                        webspace_id=webspace_id,
                        request_id=request_id,
                        request_text=text,
                        kind="candidate.proposed",
                        title="Candidate proposed",
                        subtitle=f"{entry.get('kind')}: {(entry.get('candidate') or {}).get('name') or ''}".strip(),
                        raw=entry,
                        meta=req_meta,
                    ),
                )
            except Exception:
                _log.debug("failed to append teacher event (candidate.proposed) webspace=%s", webspace_id, exc_info=True)
            return

        patch = {"status": "ignored", "llm": llm_meta, "note": notes or "LLM decided to ignore.", "ignored_at": time.time()}
        try:
            await _update_revision_by_request_id(webspace_id, request_id=request_id, patch=patch)
        except Exception:
            _log.debug("failed to update ignored revision webspace=%s request_id=%s", webspace_id, request_id, exc_info=True)
        bus_emit(
            ctx.bus,
            "nlp.teacher.ignored",
            {"webspace_id": webspace_id, "request_id": request_id, "suggestion": suggestion},
            source="nlu.teacher.llm",
        )
        try:
            await append_event(
                webspace_id,
                make_event(
                    webspace_id=webspace_id,
                    request_id=request_id,
                    request_text=text,
                    kind="llm.ignored",
                    title="LLM ignored",
                    subtitle=notes or "",
                    raw={"suggestion": suggestion},
                    meta=req_meta,
                ),
            )
        except Exception:
            _log.debug("failed to append teacher event (llm.ignored) webspace=%s", webspace_id, exc_info=True)
    except Exception:
        # Never crash the eventbus handler; log and exit.
        _log.warning("llm teacher handler crashed webspace=%s", webspace_id, exc_info=True)
        return


def _background_teacher_enabled() -> bool:
    raw = str(os.getenv("ADAOS_NLU_LLM_TEACHER_BACKGROUND", "1") or "1").strip().lower()
    if raw in _BACKGROUND_FALSE_VALUES:
        return False
    if raw in _BACKGROUND_TRUE_VALUES:
        return "PYTEST_CURRENT_TEST" not in os.environ
    return True


def _background_semaphore() -> asyncio.Semaphore:
    global _BACKGROUND_SEMAPHORE, _BACKGROUND_SEMAPHORE_LOOP
    loop = asyncio.get_running_loop()
    if _BACKGROUND_SEMAPHORE is None or _BACKGROUND_SEMAPHORE_LOOP is not loop:
        _BACKGROUND_SEMAPHORE = asyncio.Semaphore(_BACKGROUND_MAX_CONCURRENCY)
        _BACKGROUND_SEMAPHORE_LOOP = loop
    return _BACKGROUND_SEMAPHORE


def _teacher_request_key(evt: Any) -> str:
    payload = _payload(evt)
    req = payload.get("request") if isinstance(payload.get("request"), Mapping) else {}
    webspace_id = _resolve_webspace_id(payload)
    request_id = str(req.get("request_id") or req.get("id") or "").strip()
    text = str(req.get("text") or "").strip()
    return request_id or f"{webspace_id}:{hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()[:12]}"


async def _run_teacher_request_background(evt: Any, key: str) -> None:
    try:
        async with _background_semaphore():
            await _handle_teacher_request(evt)
    finally:
        _BACKGROUND_INFLIGHT.discard(key)


@subscribe("nlp.teacher.request")
async def _on_teacher_request(evt: Any) -> None:
    if not _background_teacher_enabled():
        await _handle_teacher_request(evt)
        return
    key = _teacher_request_key(evt)
    if key in _BACKGROUND_INFLIGHT:
        _log.debug("nlu teacher request already in flight key=%s", key)
        return
    _BACKGROUND_INFLIGHT.add(key)
    task = asyncio.create_task(_run_teacher_request_background(evt, key))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
