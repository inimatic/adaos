from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Mapping

import yaml

from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.nlu.ycoerce import coerce_dict, iter_mappings
from adaos.services.nlu_lookup_tables import collect_desktop_lookup_tables
from adaos.services.yjs.webspace import default_webspace_id


def _webspace_id(token: Any) -> str:
    if isinstance(token, str) and token.strip():
        return token.strip()
    return default_webspace_id()


def _limit(value: Any, *, default: int = 50, max_value: int = 250) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, min(parsed, max_value))


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes, bytearray)) or isinstance(value, Mapping) or not isinstance(value, Iterable):
        return []
    return [dict(item) for item in iter_mappings(value)]


def _hash_payload(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _read_yaml(path: Path) -> dict[str, Any] | None:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _path_from_ctx(ctx: AgentContext, name: str) -> Path | None:
    paths = getattr(ctx, "paths", None)
    if paths is None:
        return None
    value = getattr(paths, name, None)
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    if not value:
        return None
    return Path(value)


def _package_workspace_dir(ctx: AgentContext) -> Path | None:
    paths = getattr(ctx, "paths", None)
    package_dir = getattr(paths, "package_dir", None)
    if callable(package_dir):
        try:
            package_dir = package_dir()
        except Exception:
            package_dir = None
    if not package_dir:
        return None
    workspace = Path(package_dir) / ".adaos" / "workspace"
    return workspace if workspace.exists() else None


def _unique_paths(paths: Iterable[Path | None]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        if path is None:
            continue
        try:
            key = str(Path(path).resolve())
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(Path(path))
    return out


def _skill_roots(ctx: AgentContext) -> list[Path]:
    package = _package_workspace_dir(ctx)
    return _unique_paths(
        [
            _path_from_ctx(ctx, "skills_dir"),
            _path_from_ctx(ctx, "skills_workspace_dir"),
            package / "skills" if package else None,
        ]
    )


def _scenario_roots(ctx: AgentContext) -> list[Path]:
    package = _package_workspace_dir(ctx)
    return _unique_paths(
        [
            _path_from_ctx(ctx, "scenarios_dir"),
            _path_from_ctx(ctx, "scenarios_workspace_dir"),
            package / "scenarios" if package else None,
        ]
    )


def _read_skill_manifest(skill_id: str, *, ctx: AgentContext | None = None) -> tuple[dict[str, Any] | None, Path | None]:
    token = str(skill_id or "").strip()
    if not token:
        return None, None
    ctx = ctx or get_ctx()
    for root in _skill_roots(ctx):
        for name in ("skill.yaml", "skill.yml"):
            path = root / token / name
            if not path.exists():
                continue
            payload = _read_yaml(path)
            if payload is not None:
                return payload, path
    return None, None


def _read_scenario_manifest(scenario_id: str, *, ctx: AgentContext | None = None) -> tuple[dict[str, Any] | None, Path | None]:
    token = str(scenario_id or "").strip()
    if not token:
        return None, None
    ctx = ctx or get_ctx()
    for root in _scenario_roots(ctx):
        base = root / token
        for name in ("scenario.json", "scenario.yaml", "scenario.yml"):
            path = base / name
            if not path.exists():
                continue
            payload = _read_json(path) if name.endswith(".json") else _read_yaml(path)
            if payload is not None:
                return payload, path
    return None, None


def _read_yjs_teacher_snapshot(webspace_id: str) -> dict[str, Any]:
    from adaos.services.yjs.doc import get_ydoc

    ws = _webspace_id(webspace_id)
    try:
        with get_ydoc(ws, read_only=True, load_mark_roots=["data"]) as ydoc:
            data_map = ydoc.get_map("data")
            teacher = data_map.get("nlu_teacher")
            trace = data_map.get("nlu_trace")
            nlu = data_map.get("nlu")
            return {
                "webspace_id": ws,
                "teacher": coerce_dict(teacher),
                "trace": coerce_dict(trace),
                "nlu": coerce_dict(nlu),
            }
    except Exception as exc:
        return {
            "webspace_id": ws,
            "teacher": {},
            "trace": {},
            "nlu": {},
            "read_error": f"{type(exc).__name__}: {exc}",
        }


def _event_ts(item: Mapping[str, Any]) -> float:
    try:
        return float(item.get("ts") or 0.0)
    except Exception:
        return 0.0


def _request_id(item: Mapping[str, Any]) -> str | None:
    rid = item.get("request_id")
    return rid if isinstance(rid, str) and rid.strip() else None


def _candidate_id_from_event(item: Mapping[str, Any]) -> str | None:
    cid = item.get("candidate_id")
    if isinstance(cid, str) and cid.strip():
        return cid.strip()
    raw = item.get("raw") if isinstance(item.get("raw"), Mapping) else {}
    cid = raw.get("candidate_id")
    if isinstance(cid, str) and cid.strip():
        return cid.strip()
    candidate = raw.get("candidate") if isinstance(raw.get("candidate"), Mapping) else {}
    cid = candidate.get("id")
    return cid.strip() if isinstance(cid, str) and cid.strip() else None


def _compact_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    regex_rule = candidate.get("regex_rule") if isinstance(candidate.get("regex_rule"), Mapping) else {}
    target = candidate.get("target") if isinstance(candidate.get("target"), Mapping) else {}
    preview = candidate.get("preview") if isinstance(candidate.get("preview"), Mapping) else {}
    verification = candidate.get("verification") if isinstance(candidate.get("verification"), Mapping) else {}
    return {
        "id": candidate.get("id"),
        "request_id": candidate.get("request_id"),
        "text": candidate.get("text"),
        "kind": candidate.get("kind"),
        "status": candidate.get("status"),
        "target": dict(target) if target else None,
        "regex_rule": dict(regex_rule) if regex_rule else None,
        "preview": dict(preview) if preview else None,
        "verification": dict(verification) if verification else None,
        "created_at": candidate.get("ts"),
        "applied": dict(candidate.get("applied") or {}) if isinstance(candidate.get("applied"), Mapping) else None,
        "rolled_back_at": candidate.get("rolled_back_at"),
    }


def get_nlu_trace(
    *,
    webspace_id: str | None = None,
    request_id: str | None = None,
    candidate_id: str | None = None,
    limit: int = 80,
) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    snapshot = _read_yjs_teacher_snapshot(ws)
    max_items = _limit(limit, default=80, max_value=500)
    teacher = snapshot["teacher"]
    trace_obj = snapshot["trace"]
    trace_items = _as_list(trace_obj.get("items"))
    events = _as_list(teacher.get("events"))
    candidates = _as_list(teacher.get("candidates"))

    request_filter = str(request_id or "").strip() or None
    candidate_filter = str(candidate_id or "").strip() or None
    if candidate_filter and not request_filter:
        for candidate in candidates:
            if candidate.get("id") == candidate_filter:
                request_filter = _request_id(candidate)
                break

    def _match_request(item: Mapping[str, Any]) -> bool:
        return not request_filter or _request_id(item) == request_filter

    def _match_event(item: Mapping[str, Any]) -> bool:
        if request_filter and _request_id(item) != request_filter:
            return False
        if candidate_filter and _candidate_id_from_event(item) != candidate_filter:
            return False
        return True

    trace_rows = [item for item in trace_items if _match_request(item)]
    event_rows = [item for item in events if _match_event(item)]
    candidate_rows = []
    for candidate in candidates:
        if candidate_filter and candidate.get("id") != candidate_filter:
            continue
        if request_filter and candidate.get("request_id") != request_filter:
            continue
        candidate_rows.append(_compact_candidate(candidate))

    trace_rows = sorted(trace_rows, key=_event_ts)[-max_items:]
    event_rows = sorted(event_rows, key=_event_ts)[-max_items:]
    return {
        "ok": True,
        "webspace_id": ws,
        "request_id": request_filter,
        "candidate_id": candidate_filter,
        "trace": trace_rows,
        "teacher_events": event_rows,
        "candidates": candidate_rows[-max_items:],
        "read_error": snapshot.get("read_error"),
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }


def get_nlu_dialog_context(
    *,
    webspace_id: str | None = None,
    request_id: str | None = None,
    candidate_id: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    snapshot = _read_yjs_teacher_snapshot(ws)
    teacher = snapshot["teacher"]
    max_items = _limit(limit, default=25, max_value=100)
    request_filter = str(request_id or "").strip() or None
    candidate_filter = str(candidate_id or "").strip() or None

    candidates = _as_list(teacher.get("candidates"))
    if candidate_filter and not request_filter:
        for candidate in candidates:
            if candidate.get("id") == candidate_filter:
                request_filter = _request_id(candidate)
                break

    events = _as_list(teacher.get("events"))
    llm_logs = _as_list(teacher.get("llm_logs"))
    request_threads = _as_list(teacher.get("threads_by_request"))
    candidate_threads = _as_list(teacher.get("threads_by_candidate"))

    if request_filter:
        events = [item for item in events if item.get("request_id") == request_filter]
        llm_logs = [item for item in llm_logs if item.get("request_id") == request_filter]
        request_threads = [item for item in request_threads if item.get("request_id") == request_filter]
        candidates = [item for item in candidates if item.get("request_id") == request_filter]
        candidate_threads = [item for item in candidate_threads if item.get("request_id") == request_filter]
    if candidate_filter:
        candidates = [item for item in candidates if item.get("id") == candidate_filter]
        candidate_threads = [item for item in candidate_threads if item.get("candidate_id") == candidate_filter]

    latest_candidate = None
    if candidates:
        latest_candidate = _compact_candidate(sorted(candidates, key=_event_ts)[-1])

    latest_request_id = request_filter
    if latest_request_id is None:
        for item in sorted(events, key=_event_ts, reverse=True):
            latest_request_id = _request_id(item)
            if latest_request_id:
                break

    correction_target = None
    for candidate in sorted(_as_list(teacher.get("candidates")), key=_event_ts, reverse=True):
        status = str(candidate.get("status") or "")
        if status in {"rolled_back"}:
            continue
        correction_target = _compact_candidate(candidate)
        break

    return {
        "ok": True,
        "webspace_id": ws,
        "request_id": latest_request_id,
        "candidate_id": candidate_filter,
        "threads_by_request": request_threads[-max_items:],
        "threads_by_candidate": candidate_threads[-max_items:],
        "events": sorted(events, key=_event_ts)[-max_items:],
        "llm_logs": sorted(llm_logs, key=_event_ts)[-max_items:],
        "candidates": [_compact_candidate(item) for item in sorted(candidates, key=_event_ts)[-max_items:]],
        "latest_candidate": latest_candidate,
        "correction_context": {
            "active": bool(correction_target),
            "previous_candidate": correction_target,
        },
        "read_error": snapshot.get("read_error"),
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }


def get_nlu_recent_failures(*, webspace_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    snapshot = _read_yjs_teacher_snapshot(ws)
    teacher = snapshot["teacher"]
    trace_obj = snapshot["trace"]
    max_items = _limit(limit, default=50, max_value=200)

    rows: list[dict[str, Any]] = []
    for item in _as_list(teacher.get("items")):
        classification = item.get("classification") if isinstance(item.get("classification"), Mapping) else {}
        rows.append(
            {
                "ts": item.get("ts"),
                "source": "nlu_teacher.items",
                "request_id": item.get("request_id"),
                "text": item.get("text"),
                "reason": item.get("reason"),
                "via": item.get("via"),
                "status": item.get("status"),
                "classification": dict(classification),
                "teachable": bool(classification.get("teachable")),
            }
        )
    for item in _as_list(trace_obj.get("items")):
        if item.get("type") != "nlp.intent.not_obtained":
            continue
        rows.append(
            {
                "ts": item.get("ts"),
                "source": "nlu_trace",
                "request_id": item.get("request_id"),
                "text": item.get("text"),
                "reason": item.get("reason"),
                "via": item.get("via"),
                "status": "not_obtained",
                "classification": {},
                "teachable": None,
            }
        )
    rows = sorted(rows, key=_event_ts, reverse=True)[:max_items]
    return {
        "ok": True,
        "webspace_id": ws,
        "failures": rows,
        "read_error": snapshot.get("read_error"),
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }


def get_desktop_registry_lookup(*, webspace_id: str | None = None, include_live: bool = True) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    payload = collect_desktop_lookup_tables(get_ctx(), webspace_id=ws, include_live=include_live)
    return {
        **payload,
        "registry_kind": "desktop.lookup_tables",
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }


def _intent_action_summary(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions = spec.get("actions")
    out: list[dict[str, Any]] = []
    for action in iter_mappings(actions if isinstance(actions, Iterable) and not isinstance(actions, (str, bytes, Mapping)) else []):
        out.append({k: v for k, v in dict(action).items() if k in {"type", "target", "params"}})
    return out[:10]


def _nlu_descriptor_from_manifest(payload: Mapping[str, Any], *, owner_type: str, owner_id: str, path: Path | None) -> dict[str, Any]:
    nlu = payload.get("nlu") if isinstance(payload.get("nlu"), Mapping) else {}
    intents: dict[str, Any] = {}
    for name, spec in (nlu.get("intents") if isinstance(nlu.get("intents"), Mapping) else {}).items():
        if not isinstance(name, str) or not isinstance(spec, Mapping):
            continue
        examples = spec.get("examples")
        if not isinstance(examples, list):
            examples = []
        intents[name] = {
            "description": spec.get("description"),
            "scope": spec.get("scope"),
            "examples_count": len([item for item in examples if isinstance(item, str) and item.strip()]),
            "examples": [item for item in examples if isinstance(item, str) and item.strip()][:20],
            "actions": _intent_action_summary(spec),
        }
    rules = []
    for rule in iter_mappings(nlu.get("regex_rules")):
        rules.append(
            {
                "id": rule.get("id"),
                "intent": rule.get("intent"),
                "pattern": rule.get("pattern"),
                "enabled": rule.get("enabled", True),
                "source": rule.get("source"),
                "candidate_id": rule.get("candidate_id"),
            }
        )
    descriptor = {
        "ok": True,
        "owner": {"type": owner_type, "id": owner_id},
        "path": str(path) if path else None,
        "fingerprint": _hash_payload({"owner": [owner_type, owner_id], "nlu": nlu}),
        "nlu": {
            "intents": intents,
            "regex_rules": rules,
            "regex_rules_count": len(rules),
        },
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }
    if owner_type == "skill":
        events = payload.get("events") if isinstance(payload.get("events"), Mapping) else {}
        descriptor["skill_surface"] = {
            "subscribes": list(events.get("subscribe") or []) if isinstance(events.get("subscribe"), list) else [],
            "publishes": list(events.get("publish") or []) if isinstance(events.get("publish"), list) else [],
            "llm_policy": dict(payload.get("llm_policy") or {}) if isinstance(payload.get("llm_policy"), Mapping) else {},
        }
    return descriptor


def _target_matches(owner: Mapping[str, Any], *, owner_type: str | None, owner_id: str | None) -> bool:
    if owner_type and str(owner.get("type") or "") != owner_type:
        return False
    if owner_id and str(owner.get("id") or "") != owner_id:
        return False
    return True


def _template_id(payload: Mapping[str, Any]) -> str:
    owner = payload.get("owner") if isinstance(payload.get("owner"), Mapping) else {}
    kind = str(payload.get("kind") or "template").replace(".", "_")
    owner_type = str(owner.get("type") or "owner")
    owner_id = str(owner.get("id") or "unknown")
    digest = _hash_payload(payload)[:16]
    return f"tpl.{owner_type}.{owner_id}.{kind}.{digest}"


def _template_row(
    *,
    owner: Mapping[str, Any],
    intent: str | None,
    kind: str,
    path: Path | None,
    payload: Mapping[str, Any],
    mutation: str,
    status: str = "active",
) -> dict[str, Any]:
    base = {
        "owner": dict(owner),
        "intent": intent,
        "kind": kind,
        "status": status,
        "source_path": str(path) if path else None,
        "payload": dict(payload),
        "mutation": mutation,
    }
    base["fingerprint"] = _hash_payload(base)
    base["id"] = _template_id(base)
    return base


def _iter_artifact_nlu_templates(
    *,
    owner_type: str,
    owner_id: str,
    payload: Mapping[str, Any],
    path: Path | None,
) -> list[dict[str, Any]]:
    owner = {"type": owner_type, "id": owner_id}
    nlu = payload.get("nlu") if isinstance(payload.get("nlu"), Mapping) else {}
    rows: list[dict[str, Any]] = []
    intents = nlu.get("intents") if isinstance(nlu.get("intents"), Mapping) else {}
    for intent, spec in sorted(intents.items()):
        if not isinstance(intent, str) or not isinstance(spec, Mapping):
            continue
        examples = spec.get("examples")
        if isinstance(examples, list):
            for index, example in enumerate(examples):
                if not isinstance(example, str) or not example.strip():
                    continue
                rows.append(
                    _template_row(
                        owner=owner,
                        intent=intent,
                        kind="example",
                        path=path,
                        payload={"text": example.strip(), "index": index},
                        mutation="append_example",
                    )
                )
        actions = _intent_action_summary(spec)
        if actions:
            rows.append(
                _template_row(
                    owner=owner,
                    intent=intent,
                    kind="intent_route",
                    path=path,
                    payload={"actions": actions, "slots": _slots_from_actions(actions)},
                    mutation="none",
                )
            )
    for index, rule in enumerate(iter_mappings(nlu.get("regex_rules"))):
        intent = rule.get("intent") if isinstance(rule.get("intent"), str) else None
        rows.append(
            _template_row(
                owner=owner,
                intent=intent,
                kind="regex_rule",
                path=path,
                payload={
                    "id": rule.get("id"),
                    "pattern": rule.get("pattern"),
                    "enabled": rule.get("enabled", True),
                    "source": rule.get("source"),
                    "candidate_id": rule.get("candidate_id"),
                    "index": index,
                },
                mutation="append_regex_rule",
                status="active" if bool(rule.get("enabled", True)) else "disabled",
            )
        )
    return rows


def describe_skill_nlu(skill_id: str, *, ctx: AgentContext | None = None) -> dict[str, Any]:
    token = str(skill_id or "").strip()
    payload, path = _read_skill_manifest(token, ctx=ctx)
    if payload is None:
        return {"ok": False, "status": "not_found", "owner": {"type": "skill", "id": token}}
    return _nlu_descriptor_from_manifest(payload, owner_type="skill", owner_id=token, path=path)


def describe_scenario_nlu(scenario_id: str, *, ctx: AgentContext | None = None) -> dict[str, Any]:
    token = str(scenario_id or "").strip()
    payload, path = _read_scenario_manifest(token, ctx=ctx)
    if payload is None:
        return {"ok": False, "status": "not_found", "owner": {"type": "scenario", "id": token}}
    return _nlu_descriptor_from_manifest(payload, owner_type="scenario", owner_id=token, path=path)


def list_nlu_templates(
    *,
    webspace_id: str | None = None,
    owner_type: str | None = None,
    owner_id: str | None = None,
    include_system_actions: bool = True,
) -> dict[str, Any]:
    from adaos.services.nlu.system_actions_catalog import system_action_nlu_intents

    ctx = get_ctx()
    owner_type_token = str(owner_type or "").strip() or None
    owner_id_token = str(owner_id or "").strip() or None
    rows: list[dict[str, Any]] = []
    for root in _skill_roots(ctx):
        if not root.exists():
            continue
        for skill_dir in sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")):
            payload, path = _read_skill_manifest(skill_dir.name, ctx=ctx)
            if payload is None:
                continue
            owner = {"type": "skill", "id": skill_dir.name}
            if not _target_matches(owner, owner_type=owner_type_token, owner_id=owner_id_token):
                continue
            rows.extend(_iter_artifact_nlu_templates(owner_type="skill", owner_id=skill_dir.name, payload=payload, path=path))
    for root in _scenario_roots(ctx):
        if not root.exists():
            continue
        for scenario_dir in sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")):
            payload, path = _read_scenario_manifest(scenario_dir.name, ctx=ctx)
            if payload is None:
                continue
            owner = {"type": "scenario", "id": scenario_dir.name}
            if not _target_matches(owner, owner_type=owner_type_token, owner_id=owner_id_token):
                continue
            rows.extend(_iter_artifact_nlu_templates(owner_type="scenario", owner_id=scenario_dir.name, payload=payload, path=path))
    if include_system_actions:
        for intent, spec in sorted(system_action_nlu_intents().items()):
            owner = {"type": "system_action", "id": str(spec.get("action_id") or intent)}
            if not _target_matches(owner, owner_type=owner_type_token, owner_id=owner_id_token):
                continue
            examples = spec.get("examples")
            if isinstance(examples, list):
                for index, example in enumerate(examples):
                    if not isinstance(example, str) or not example.strip():
                        continue
                    rows.append(
                        _template_row(
                            owner=owner,
                            intent=intent,
                            kind="system_action_example",
                            path=None,
                            payload={"text": example.strip(), "index": index, "host_action": spec.get("host_action")},
                            mutation="none",
                        )
                    )
            actions = [dict(item) for item in iter_mappings(spec.get("actions"))]
            rows.append(
                _template_row(
                    owner=owner,
                    intent=intent,
                    kind="system_action_route",
                    path=None,
                    payload={"host_action": spec.get("host_action"), "actions": actions, "slots": _slots_from_actions(actions)},
                    mutation="none",
                )
            )

    rows = sorted(rows, key=lambda item: (str((item.get("owner") or {}).get("type") or ""), str((item.get("owner") or {}).get("id") or ""), str(item.get("kind") or ""), str(item.get("intent") or "")))
    return {
        "ok": True,
        "webspace_id": _webspace_id(webspace_id),
        "templates": rows,
        "summary": {
            "count": len(rows),
            "by_kind": {
                kind: sum(1 for item in rows if item.get("kind") == kind)
                for kind in sorted({str(item.get("kind") or "") for item in rows})
                if kind
            },
            "by_owner_type": {
                kind: sum(1 for item in rows if (item.get("owner") or {}).get("type") == kind)
                for kind in sorted({str((item.get("owner") or {}).get("type") or "") for item in rows})
                if kind
            },
        },
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }


def describe_sdk_surface(*, level: str = "std") -> dict[str, Any]:
    token = str(level or "std").strip().lower()
    items = [
        {
            "id": "adaos.eventbus.emit",
            "kind": "sdk_function",
            "status": "descriptive_only",
            "description": "Emit governed AdaOS events through service/runtime boundaries.",
            "execution_boundary": "not_callable_by_llm",
            "nlu_usage": "Map user intent to scenario/skill host actions; AdaOS dispatches, LLM does not call SDK directly.",
        },
        {
            "id": "adaos.nlu.probe_phrase",
            "kind": "sdk_function",
            "status": "descriptive_only",
            "description": "Run a dry NLU probe and return stage/ranking/slots evidence.",
            "execution_boundary": "available through nlu_authoring.check_phrase MCP only",
        },
        {
            "id": "adaos.desktop.host_actions",
            "kind": "host_action_surface",
            "status": "descriptive_only",
            "description": "Desktop actions are exposed as callHost scenario actions and previewed through AdaOS, not called by the LLM.",
            "execution_boundary": "scenario callHost/event bus",
        },
    ]
    if token == "mini":
        items = items[:2]
    return {
        "ok": True,
        "surface_id": "adaos.sdk.describe_surface.v1",
        "level": token if token in {"mini", "std", "rich"} else "std",
        "items": items,
        "authoring_boundaries": {
            "mode": "descriptive_only",
            "llm_direct_sdk_calls": False,
            "dispatch": False,
            "training_mutation": False,
            "side_effects": "none",
        },
    }


_SLOT_PATTERN = re.compile(r"\$slot\.([a-zA-Z_][a-zA-Z0-9_]*)")


def _slots_from_actions(actions: list[Mapping[str, Any]]) -> list[str]:
    slots: set[str] = set()
    for action in actions:
        text = json.dumps(action, ensure_ascii=False, default=str)
        slots.update(_SLOT_PATTERN.findall(text))
    return sorted(slots)


def list_training_targets(*, webspace_id: str | None = None, include_system_actions: bool = True) -> dict[str, Any]:
    from adaos.services.nlu.system_actions_catalog import system_action_nlu_intents

    ctx = get_ctx()
    targets: list[dict[str, Any]] = []
    for root in _skill_roots(ctx):
        if not root.exists():
            continue
        for skill_dir in sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")):
            descriptor = describe_skill_nlu(skill_dir.name, ctx=ctx)
            if descriptor.get("ok"):
                targets.append(
                    {
                        "type": "skill",
                        "id": skill_dir.name,
                        "path": descriptor.get("path"),
                        "fingerprint": descriptor.get("fingerprint"),
                        "intents": sorted((descriptor.get("nlu") or {}).get("intents") or {}),
                        "regex_rules_count": (descriptor.get("nlu") or {}).get("regex_rules_count", 0),
                        "llm_policy": (descriptor.get("skill_surface") or {}).get("llm_policy") or {},
                    }
                )
    for root in _scenario_roots(ctx):
        if not root.exists():
            continue
        for scenario_dir in sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")):
            descriptor = describe_scenario_nlu(scenario_dir.name, ctx=ctx)
            if descriptor.get("ok"):
                targets.append(
                    {
                        "type": "scenario",
                        "id": scenario_dir.name,
                        "path": descriptor.get("path"),
                        "fingerprint": descriptor.get("fingerprint"),
                        "intents": sorted((descriptor.get("nlu") or {}).get("intents") or {}),
                        "regex_rules_count": (descriptor.get("nlu") or {}).get("regex_rules_count", 0),
                    }
                )
    if include_system_actions:
        for intent, spec in sorted(system_action_nlu_intents().items()):
            actions = [dict(item) for item in iter_mappings(spec.get("actions"))]
            targets.append(
                {
                    "type": "system_action",
                    "id": str(spec.get("action_id") or intent),
                    "intent": intent,
                    "host_action": spec.get("host_action"),
                    "slots": _slots_from_actions(actions),
                    "examples_count": len(list(spec.get("examples") or [])) if isinstance(spec.get("examples"), list) else 0,
                    "fingerprint": _hash_payload({"intent": intent, "spec": spec}),
                }
            )
    return {
        "ok": True,
        "webspace_id": _webspace_id(webspace_id),
        "targets": targets,
        "summary": {
            "count": len(targets),
            "by_type": {
                kind: sum(1 for item in targets if item.get("type") == kind)
                for kind in sorted({str(item.get("type") or "") for item in targets})
                if kind
            },
        },
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }
