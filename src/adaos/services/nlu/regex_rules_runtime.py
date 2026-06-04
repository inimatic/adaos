from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import yaml

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.nlu.teacher_artifacts import accepted_artifact_metadata, portability_for_target
from adaos.services.nlu.teacher_events import append_event, make_event
from adaos.services.nlu.ycoerce import coerce_dict, iter_mappings
from adaos.services.scenarios import loader as scenarios_loader
from adaos.services.yjs.doc import async_get_ydoc
from adaos.services.yjs.store import ystore_write_metadata
from adaos.services.yjs.webspace import default_webspace_id

_log = logging.getLogger("adaos.nlu.regex_rules")


def _nlu_regex_rules_write_meta():
    return ystore_write_metadata(
        root_names=["data"],
        source="nlu.regex_rules_runtime",
        owner="core:nlu.regex_rules",
        channel="core.nlu.regex_rules.async",
    )


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


def _rule_portability(target: Mapping[str, Any] | None) -> str:
    return portability_for_target(target)


def _teacher_obj(data_map: Any) -> dict[str, Any]:
    return coerce_dict(getattr(data_map, "get", lambda _k: None)("nlu_teacher"))


def _read_nlu_obj(data_map: Any) -> dict[str, Any]:
    return coerce_dict(getattr(data_map, "get", lambda _k: None)("nlu"))


def _read_current_scenario_id(snapshot: dict[str, Any]) -> str | None:
    ui = coerce_dict(snapshot.get("ui"))
    token = ui.get("current_scenario")
    if isinstance(token, str) and token.strip():
        return token.strip()
    return None


def _append_or_update_rule(existing: list[dict[str, Any]], rule: dict[str, Any]) -> list[dict[str, Any]]:
    intent = rule.get("intent")
    pattern = rule.get("pattern")
    if not isinstance(intent, str) or not intent.strip():
        return existing
    if not isinstance(pattern, str) or not pattern.strip():
        return existing

    cleaned: list[dict[str, Any]] = []
    for item in existing:
        if not isinstance(item, dict):
            continue
        if item.get("intent") == intent and item.get("pattern") == pattern:
            updated = dict(item)
            # Backfill IDs on pre-existing rules to keep rule-level identity stable.
            if updated.get("id") in (None, "") and rule.get("id"):
                updated["id"] = rule.get("id")
            if updated.get("created_at") is None and rule.get("created_at") is not None:
                updated["created_at"] = rule.get("created_at")
            if updated.get("enabled") is None:
                updated["enabled"] = True
            if isinstance(rule.get("slots"), Mapping):
                merged_slots = dict(updated.get("slots") or {}) if isinstance(updated.get("slots"), Mapping) else {}
                for key, value in rule.get("slots", {}).items():
                    if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
                        merged_slots[key.strip()] = value.strip()
                if merged_slots:
                    updated["slots"] = merged_slots
            cleaned.append(updated)
        else:
            cleaned.append(dict(item))

    if any(x.get("intent") == intent and x.get("pattern") == pattern for x in cleaned):
        return cleaned
    cleaned.append(rule)
    return cleaned


def _rule_matches_rollback(
    item: Mapping[str, Any],
    *,
    rule_id: str | None,
    candidate_id: str | None,
    intent: str | None,
    pattern: str | None,
) -> bool:
    if rule_id and item.get("id") == rule_id:
        return True
    if candidate_id and item.get("candidate_id") == candidate_id:
        return True
    if not rule_id and not candidate_id and intent and pattern:
        return item.get("intent") == intent and item.get("pattern") == pattern and item.get("source") == "teacher"
    return False


def _remove_rules(
    rules: list[dict[str, Any]],
    *,
    rule_id: str | None,
    candidate_id: str | None,
    intent: str | None,
    pattern: str | None,
) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    removed = 0
    for item in rules:
        if not isinstance(item, dict):
            continue
        if _rule_matches_rollback(item, rule_id=rule_id, candidate_id=candidate_id, intent=intent, pattern=pattern):
            removed += 1
            continue
        kept.append(dict(item))
    return kept, removed


def _remove_scenario_regex_rule(
    *,
    scenario_id: str,
    rule_id: str | None,
    candidate_id: str | None,
    intent: str | None,
    pattern: str | None,
) -> int:
    root = scenarios_loader.scenario_root(scenario_id)
    path = root / "scenario.json"
    if not path.exists():
        return 0
    try:
        raw = path.read_text(encoding="utf-8-sig")
        payload = json.loads(raw)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    nlu = payload.get("nlu")
    if not isinstance(nlu, dict):
        return 0
    rules = nlu.get("regex_rules")
    if not isinstance(rules, list):
        return 0
    kept, removed = _remove_rules(
        [dict(x) for x in rules if isinstance(x, dict)],
        rule_id=rule_id,
        candidate_id=candidate_id,
        intent=intent,
        pattern=pattern,
    )
    if not removed:
        return 0
    nlu["regex_rules"] = kept
    try:
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    except Exception:
        return 0
    scenarios_loader.invalidate_cache(scenario_id=scenario_id, space="workspace")
    return removed


def _remove_skill_regex_rule(
    *,
    skill_name: str,
    rule_id: str | None,
    candidate_id: str | None,
    intent: str | None,
    pattern: str | None,
) -> int:
    ctx = get_ctx()
    path = Path(ctx.paths.skills_dir()) / skill_name / "skill.yaml"
    if not path.exists():
        return 0
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    nlu = payload.get("nlu")
    if not isinstance(nlu, dict):
        return 0
    rules = nlu.get("regex_rules")
    if not isinstance(rules, list):
        return 0
    kept, removed = _remove_rules(
        [dict(x) for x in rules if isinstance(x, dict)],
        rule_id=rule_id,
        candidate_id=candidate_id,
        intent=intent,
        pattern=pattern,
    )
    if not removed:
        return 0
    nlu["regex_rules"] = kept
    try:
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    except Exception:
        return 0
    return removed


def _remove_workspace_regex_rules(
    *,
    candidate_id: str | None,
    intent: str | None,
    pattern: str | None,
) -> int:
    if not candidate_id and not (intent and pattern):
        return 0
    ctx = get_ctx()
    removed = 0
    try:
        scenarios_root = Path(ctx.paths.scenarios_dir())
        for item in scenarios_root.iterdir():
            if not item.is_dir() or not (item / "scenario.json").exists():
                continue
            removed += _remove_scenario_regex_rule(
                scenario_id=item.name,
                rule_id=None,
                candidate_id=candidate_id,
                intent=intent,
                pattern=pattern,
            )
    except Exception:
        _log.debug("failed to scan workspace scenarios during regex rollback", exc_info=True)
    try:
        skills_root = Path(ctx.paths.skills_dir())
        for item in skills_root.iterdir():
            if not item.is_dir() or not (item / "skill.yaml").exists():
                continue
            removed += _remove_skill_regex_rule(
                skill_name=item.name,
                rule_id=None,
                candidate_id=candidate_id,
                intent=intent,
                pattern=pattern,
            )
    except Exception:
        _log.debug("failed to scan workspace skills during regex rollback", exc_info=True)
    return removed


def _write_scenario_regex_rule(*, scenario_id: str, rule: dict[str, Any]) -> bool:
    root = scenarios_loader.scenario_root(scenario_id)
    path = root / "scenario.json"
    if not path.exists():
        return False

    try:
        raw = path.read_text(encoding="utf-8-sig")
        payload = json.loads(raw)
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False

    nlu = payload.get("nlu")
    if not isinstance(nlu, dict):
        nlu = {}
        payload["nlu"] = nlu

    rules = nlu.get("regex_rules")
    if not isinstance(rules, list):
        rules = []
    nlu["regex_rules"] = _append_or_update_rule([dict(x) for x in rules if isinstance(x, dict)], rule)[-200:]

    try:
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    except Exception:
        return False

    scenarios_loader.invalidate_cache(scenario_id=scenario_id, space="workspace")
    return True


def _write_skill_regex_rule(*, skill_name: str, rule: dict[str, Any]) -> bool:
    ctx = get_ctx()
    skill_root = Path(ctx.paths.skills_dir()) / skill_name
    path = skill_root / "skill.yaml"
    if not path.exists():
        return False

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False

    nlu = payload.get("nlu")
    if not isinstance(nlu, dict):
        nlu = {}
        payload["nlu"] = nlu

    rules = nlu.get("regex_rules")
    if not isinstance(rules, list):
        rules = []
    nlu["regex_rules"] = _append_or_update_rule([dict(x) for x in rules if isinstance(x, dict)], rule)[-200:]

    try:
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    except Exception:
        return False
    return True


def _normalize_rule(rule: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    intent = rule.get("intent")
    pattern = rule.get("pattern")
    if not isinstance(intent, str) or not intent.strip():
        return None
    if not isinstance(pattern, str) or not pattern.strip():
        return None
    enabled = rule.get("enabled")
    out = {
        "id": rule.get("id"),
        "created_at": rule.get("created_at"),
        "intent": intent.strip(),
        "pattern": pattern,
        "enabled": bool(enabled) if enabled is not None else True,
        "source": rule.get("source"),
    }
    for key in ("candidate_id", "promotion", "provenance", "privacy"):
        value = rule.get(key)
        if value not in (None, "", [], {}):
            out[key] = dict(value) if isinstance(value, Mapping) else value
    slots = rule.get("slots")
    if isinstance(slots, Mapping):
        clean_slots: dict[str, str] = {}
        for key, value in slots.items():
            if not isinstance(key, str) or not key.strip():
                continue
            if not isinstance(value, str) or not value.strip():
                continue
            clean_slots[key.strip()] = value.strip()
        if clean_slots:
            out["slots"] = clean_slots
    return out


def _set_rule_target_metadata(rule: dict[str, Any], target: Mapping[str, Any] | None) -> None:
    if not isinstance(target, Mapping) or not target:
        return
    promotion = coerce_dict(rule.get("promotion"))
    promotion["portability"] = _rule_portability(target)
    rule["promotion"] = promotion
    provenance = coerce_dict(rule.get("provenance"))
    provenance["target"] = dict(target)
    rule["provenance"] = provenance


def _compact_probe_result(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(result.get("ok")),
        "accepted": bool(result.get("accepted")),
        "via": result.get("via"),
        "intent": result.get("intent"),
        "confidence": result.get("confidence"),
        "slots": dict(result.get("slots") or {}) if isinstance(result.get("slots"), Mapping) else {},
        "request_id": result.get("request_id"),
        "reason": result.get("reason"),
    }


async def _mark_candidate_verification(
    *,
    webspace_id: str,
    candidate_id: str | None,
    verification: Mapping[str, Any],
    candidate_patch: Mapping[str, Any] | None = None,
) -> None:
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        return
    async with _nlu_regex_rules_write_meta():
        async with async_get_ydoc(webspace_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
            data_map = ydoc.get_map("data")
            teacher = _teacher_obj(data_map)
            next_candidates: list[dict[str, Any]] = []
            for item in iter_mappings(teacher.get("candidates")):
                d = dict(item)
                if d.get("id") == candidate_id:
                    for key, value in (candidate_patch or {}).items():
                        if value not in (None, "", [], {}):
                            d[key] = dict(value) if isinstance(value, Mapping) else value
                    d["verification"] = dict(verification)
                    d["verified_at"] = time.time()
                    provenance = coerce_dict(d.get("provenance"))
                    provenance["verification_result"] = dict(verification)
                    d["provenance"] = provenance
                    if verification.get("status") == "intent_matched":
                        d["status"] = "intent_matched"
                    else:
                        d["status"] = "verification_failed"
                next_candidates.append(d)
            teacher["candidates"] = next_candidates
            with ydoc.begin_transaction() as txn:
                data_map.set(txn, "nlu_teacher", teacher)


@subscribe("nlp.teacher.regex_rule.rollback")
async def _on_regex_rule_rollback(evt: Any) -> None:
    ctx = get_ctx()
    payload = _payload(evt)
    webspace_id = _resolve_webspace_id(payload)
    meta = coerce_dict(payload.get("_meta"))
    candidate_id = payload.get("candidate_id") if isinstance(payload.get("candidate_id"), str) else None
    rule_id = payload.get("rule_id") if isinstance(payload.get("rule_id"), str) else None
    target = payload.get("target") if isinstance(payload.get("target"), Mapping) else None
    intent = payload.get("intent") if isinstance(payload.get("intent"), str) else None
    pattern = payload.get("pattern") if isinstance(payload.get("pattern"), str) else None
    request_id: str | None = None
    request_text = ""
    removed_owner = 0
    removed_runtime = 0
    resolved_target: dict[str, Any] | None = dict(target) if isinstance(target, Mapping) else None

    try:
        async with _nlu_regex_rules_write_meta():
            async with async_get_ydoc(webspace_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
                data_map = ydoc.get_map("data")
                teacher = _teacher_obj(data_map)
                next_candidates: list[dict[str, Any]] = []
                for item in iter_mappings(teacher.get("candidates")):
                    d = dict(item)
                    if candidate_id and d.get("id") == candidate_id:
                        request_id = d.get("request_id") if isinstance(d.get("request_id"), str) else None
                        request_text = d.get("text") if isinstance(d.get("text"), str) else ""
                        rr = d.get("regex_rule") if isinstance(d.get("regex_rule"), Mapping) else {}
                        intent = intent or (rr.get("intent") if isinstance(rr.get("intent"), str) else None)
                        pattern = pattern or (rr.get("pattern") if isinstance(rr.get("pattern"), str) else None)
                        applied = d.get("applied") if isinstance(d.get("applied"), Mapping) else {}
                        rule_id = rule_id or (applied.get("rule_id") if isinstance(applied.get("rule_id"), str) else None)
                        applied_target = applied.get("target") if isinstance(applied.get("target"), Mapping) else None
                        if resolved_target is None and isinstance(applied_target, Mapping):
                            resolved_target = dict(applied_target)
                        d["status"] = "rolled_back"
                        d["rolled_back_at"] = time.time()
                    next_candidates.append(d)

                if resolved_target:
                    t_type = resolved_target.get("type")
                    t_id = resolved_target.get("id")
                    if t_type == "scenario" and isinstance(t_id, str) and t_id.strip():
                        removed_owner = _remove_scenario_regex_rule(
                            scenario_id=t_id.strip(),
                            rule_id=rule_id,
                            candidate_id=candidate_id,
                            intent=intent,
                            pattern=pattern,
                        )
                    elif t_type == "skill" and isinstance(t_id, str) and t_id.strip():
                        removed_owner = _remove_skill_regex_rule(
                            skill_name=t_id.strip(),
                            rule_id=rule_id,
                            candidate_id=candidate_id,
                            intent=intent,
                            pattern=pattern,
                        )
                if not removed_owner:
                    removed_owner = _remove_workspace_regex_rules(
                        candidate_id=candidate_id,
                        intent=intent,
                        pattern=pattern,
                    )

                nlu_obj = _read_nlu_obj(data_map)
                rules = [dict(x) for x in iter_mappings(nlu_obj.get("regex_rules"))]
                kept, removed_runtime = _remove_rules(
                    rules,
                    rule_id=rule_id,
                    candidate_id=candidate_id,
                    intent=intent,
                    pattern=pattern,
                )
                nlu_obj["regex_rules"] = kept[-200:]

                rollback_info = {
                    "type": "regex_rule",
                    "rule_id": rule_id,
                    "candidate_id": candidate_id,
                    "target": dict(resolved_target or {}),
                    "removed_owner": removed_owner,
                    "removed_runtime": removed_runtime,
                }
                marked: list[dict[str, Any]] = []
                for d in next_candidates:
                    if candidate_id and d.get("id") == candidate_id:
                        d["rollback"] = rollback_info
                    marked.append(d)
                teacher["candidates"] = marked
                with ydoc.begin_transaction() as txn:
                    data_map.set(txn, "nlu", nlu_obj)
                    data_map.set(txn, "nlu_teacher", teacher)
    except Exception:
        _log.warning("failed to rollback regex rule webspace=%s candidate_id=%s", webspace_id, candidate_id, exc_info=True)
        return

    try:
        from adaos.services.nlu.pipeline import invalidate_dynamic_regex_cache

        invalidate_dynamic_regex_cache(webspace_id=webspace_id)
    except Exception:
        pass

    payload_out = {
        "webspace_id": webspace_id,
        "candidate_id": candidate_id,
        "rule_id": rule_id,
        "target": dict(resolved_target or {}),
        "removed_owner": removed_owner,
        "removed_runtime": removed_runtime,
        "_meta": dict(meta),
    }
    try:
        await append_event(
            webspace_id,
            make_event(
                webspace_id=webspace_id,
                request_id=request_id,
                request_text=request_text,
                kind="regex_rule.rolled_back",
                title="Regex rule rolled back",
                subtitle=str(rule_id or candidate_id or ""),
                raw=payload_out,
                meta=meta,
            ),
        )
    except Exception:
        _log.debug("failed to append teacher event (regex_rule.rolled_back) webspace=%s", webspace_id, exc_info=True)

    bus_emit(ctx.bus, "nlp.teacher.regex_rule.rolled_back", payload_out, source="nlu.regex_rules")


async def apply_regex_rule(evt: Any) -> None:
    """
    Apply a proposed regex rule (typically from NLU Teacher UI).

    Payload:
      - webspace_id
      - candidate_id (optional)
      - intent
      - pattern
      - _meta
    """
    ctx = get_ctx()
    payload = _payload(evt)
    webspace_id = _resolve_webspace_id(payload)

    candidate_id = payload.get("candidate_id")
    intent = payload.get("intent")
    pattern = payload.get("pattern")
    meta = coerce_dict(payload.get("_meta"))
    payload_target = payload.get("target") if isinstance(payload.get("target"), Mapping) else None
    candidate_patch = payload.get("candidate_patch") if isinstance(payload.get("candidate_patch"), Mapping) else {}
    payload_slots = payload.get("slots") if isinstance(payload.get("slots"), Mapping) else {}
    static_slots: dict[str, str] = {}
    for key, value in payload_slots.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        static_slots[key.strip()] = value.strip()

    if not isinstance(intent, str) or not intent.strip():
        return
    if not isinstance(pattern, str) or not pattern.strip():
        return
    try:
        re.compile(pattern)
    except re.error:
        _log.warning("invalid regex pattern intent=%s pattern=%s", intent, pattern)
        return

    rule_id = f"rx.{uuid.uuid4()}"
    artifact_meta = accepted_artifact_metadata(
        target=payload_target,
        source="nlu_teacher",
        webspace_id=webspace_id,
        candidate_id=candidate_id if isinstance(candidate_id, str) else None,
        operator_action="apply",
        meta=meta,
    )
    rule = {
        "id": rule_id,
        "created_at": time.time(),
        "intent": intent.strip(),
        "pattern": pattern,
        "enabled": True,
        "source": "teacher",
        "candidate_id": candidate_id if isinstance(candidate_id, str) else None,
        "promotion": dict(artifact_meta["promotion"]),
        "provenance": dict(artifact_meta["provenance"]),
        "privacy": dict(artifact_meta["privacy"]),
    }
    if static_slots:
        rule["slots"] = static_slots
    request_id: str | None = None
    request_text: str = ""
    applied_to: dict[str, Any] | None = None
    runtime_flags_update: dict[str, Any] | None = None
    applied_candidate_patch: dict[str, Any] = {}

    try:
        async with _nlu_regex_rules_write_meta():
            async with async_get_ydoc(webspace_id, prefer_live_room=True, load_mark_roots=["data", "ui"]) as ydoc:
                data_map = ydoc.get_map("data")
                teacher = _teacher_obj(data_map)
                if isinstance(candidate_id, str) and candidate_id:
                    for item in iter_mappings(teacher.get("candidates")):
                        if item.get("id") != candidate_id:
                            continue
                        request_id = item.get("request_id") if isinstance(item.get("request_id"), str) else None
                        request_text = item.get("text") if isinstance(item.get("text"), str) else ""
                        thread_id = item.get("thread_id") if isinstance(item.get("thread_id"), str) else None
                        provenance = coerce_dict(rule.get("provenance"))
                        provenance.update(
                            accepted_artifact_metadata(
                                target=payload_target,
                                source="nlu_teacher",
                                webspace_id=webspace_id,
                                request_id=request_id,
                                thread_id=thread_id,
                                candidate_id=candidate_id,
                                operator_action="apply",
                                meta=meta,
                                accepted_at=rule.get("created_at") if isinstance(rule.get("created_at"), (int, float)) else None,
                            )["provenance"]
                        )
                        rule["provenance"] = provenance
                        break

                # Prefer writing regex rules into scenario/skill definitions (workspace),
                # so NLU can evolve as part of skills/scenarios rather than per-webspace state.
                target = payload_target
                target_type = target.get("type") if isinstance(target, Mapping) else None
                target_id = target.get("id") if isinstance(target, Mapping) else None

                ui_map = ydoc.get_map("ui")
                token = ui_map.get("current_scenario")
                scenario_id = token.strip() if isinstance(token, str) and token.strip() else None

                applied_ok = False
                if target_type == "scenario" and isinstance(target_id, str) and target_id.strip():
                    _set_rule_target_metadata(rule, {"type": "scenario", "id": target_id.strip()})
                    applied_ok = _write_scenario_regex_rule(scenario_id=target_id.strip(), rule=rule)
                    if applied_ok:
                        applied_to = {"type": "scenario", "id": target_id.strip()}
                elif target_type == "skill" and isinstance(target_id, str) and target_id.strip():
                    _set_rule_target_metadata(rule, {"type": "skill", "id": target_id.strip()})
                    applied_ok = _write_skill_regex_rule(skill_name=target_id.strip(), rule=rule)
                    if applied_ok:
                        applied_to = {"type": "skill", "id": target_id.strip()}
                elif scenario_id:
                    try:
                        content = scenarios_loader.read_content(scenario_id)
                    except Exception:
                        content = {}
                    intents = (content.get("nlu") or {}).get("intents") if isinstance(content, dict) else None
                    if isinstance(intents, dict) and intent.strip() in intents:
                        _set_rule_target_metadata(rule, {"type": "scenario", "id": scenario_id})
                        applied_ok = _write_scenario_regex_rule(scenario_id=scenario_id, rule=rule)
                        if applied_ok:
                            applied_to = {"type": "scenario", "id": scenario_id}

                if not applied_ok:
                    _set_rule_target_metadata(rule, {"type": "webspace", "id": webspace_id})
                    # Backward-compatible fallback: keep per-webspace storage if we can't
                    # resolve a skill/scenario target.
                    nlu_obj = _read_nlu_obj(data_map)
                    rules = nlu_obj.get("regex_rules")
                    rules = [dict(x) for x in iter_mappings(rules)]
                    cleaned: list[dict[str, Any]] = []
                    for item in rules:
                        normalized = _normalize_rule(item)
                        if normalized:
                            cleaned.append(normalized)
                    cleaned.append(rule)
                    nlu_obj["regex_rules"] = cleaned[-200:]
                    with ydoc.begin_transaction() as txn:
                        data_map.set(txn, "nlu", nlu_obj)
                    applied_to = {"type": "webspace", "id": webspace_id}
                else:
                    # Mirror applied rules into per-webspace state as a runtime cache so the
                    # regex stage can pick them up immediately without depending on scenario
                    # reloads. Primary source-of-truth remains scenario.json / skill.yaml.
                    try:
                        nlu_obj = _read_nlu_obj(data_map)
                        rules = nlu_obj.get("regex_rules")
                        rules = [dict(x) for x in iter_mappings(rules)]
                        cleaned: list[dict[str, Any]] = []
                        for item in rules:
                            normalized = _normalize_rule(item)
                            if normalized:
                                cleaned.append(normalized)
                        cleaned.append(rule)
                        nlu_obj["regex_rules"] = cleaned[-200:]
                        with ydoc.begin_transaction() as txn:
                            data_map.set(txn, "nlu", nlu_obj)
                    except Exception:
                        pass

                # Mark candidate as applied (if present)
                candidates = teacher.get("candidates")
                if isinstance(candidate_id, str) and candidate_id:
                    applied_at = time.time()
                    base_promotion = {
                        "state": "local_learned",
                        "portability": rule["promotion"]["portability"],
                        "applied_artifact": {
                            "type": "regex_rule",
                            "rule_id": rule_id,
                            "target": dict(applied_to or {}),
                        },
                    }
                    base_provenance = coerce_dict(rule.get("provenance"))
                    base_provenance["rollback_pointer"] = {"rule_id": rule_id, "target": dict(applied_to or {})}
                    base_provenance["accepted_artifact_source"] = "local_overlay"
                    applied_candidate_patch = {
                        **({"validation": dict(candidate_patch.get("validation"))} if isinstance(candidate_patch.get("validation"), Mapping) else {}),
                        **({"validated_at": candidate_patch.get("validated_at")} if candidate_patch.get("validated_at") not in (None, "", [], {}) else {}),
                        "status": "applied",
                        "applied_at": applied_at,
                        "applied": {"type": "regex_rule", "rule_id": rule_id, "target": dict(applied_to or {})},
                        "promotion": base_promotion,
                        "provenance": base_provenance,
                        "privacy": dict(rule.get("privacy") or {}),
                    }
                    next_candidates: list[dict[str, Any]] = []
                    for item in iter_mappings(candidates):
                        d = dict(item)
                        if d.get("id") == candidate_id:
                            request_id = d.get("request_id") if isinstance(d.get("request_id"), str) else None
                            request_text = d.get("text") if isinstance(d.get("text"), str) else ""
                            for key in ("validation", "validated_at"):
                                value = candidate_patch.get(key)
                                if value not in (None, "", [], {}):
                                    d[key] = dict(value) if isinstance(value, Mapping) else value
                            d["status"] = "applied"
                            d["applied_at"] = applied_at
                            d["applied"] = {"type": "regex_rule", "rule_id": rule_id, "target": dict(applied_to or {})}
                            promotion = coerce_dict(d.get("promotion"))
                            promotion.setdefault("state", "local_learned")
                            promotion.setdefault("portability", rule["promotion"]["portability"])
                            promotion["applied_artifact"] = {
                                "type": "regex_rule",
                                "rule_id": rule_id,
                                "target": dict(applied_to or {}),
                            }
                            d["promotion"] = promotion
                            provenance = coerce_dict(d.get("provenance"))
                            provenance["rollback_pointer"] = {"rule_id": rule_id, "target": dict(applied_to or {})}
                            provenance["accepted_artifact_source"] = "local_overlay"
                            d["provenance"] = provenance
                            applied_candidate_patch = {
                                key: d[key]
                                for key in (
                                    "validation",
                                    "validated_at",
                                    "status",
                                    "applied_at",
                                    "applied",
                                    "promotion",
                                    "provenance",
                                    "privacy",
                                )
                                if key in d
                            }
                        next_candidates.append(d)
                    teacher["candidates"] = next_candidates

                with ydoc.begin_transaction() as txn:
                    data_map.set(txn, "nlu_teacher", teacher)
    except Exception:
        _log.warning("failed to apply regex rule webspace=%s intent=%s", webspace_id, intent, exc_info=True)
        return

    try:
        from adaos.services.nlu.runtime_flags import get_runtime_flags, set_runtime_flags

        flags = await get_runtime_flags(webspace_id)
        if not bool(flags.get("regex_enabled", True)):
            runtime_flags_update = await set_runtime_flags(
                webspace_id,
                {"regex_enabled": True},
                source="nlu.regex_rules.apply",
            )
            try:
                bus_emit(
                    ctx.bus,
                    "nlu.runtime.flags.changed",
                    {
                        "webspace_id": webspace_id,
                        "flags": dict(runtime_flags_update.get("flags") or {}),
                        "updated_at": runtime_flags_update.get("updated_at"),
                        "_meta": dict(meta),
                    },
                    source="nlu.regex_rules",
                )
            except Exception:
                pass
    except Exception:
        _log.warning(
            "failed to enable regex runtime after teacher rule apply webspace=%s candidate_id=%s",
            webspace_id,
            candidate_id,
            exc_info=True,
        )

    try:
        from adaos.services.nlu.pipeline import invalidate_dynamic_regex_cache  # local import to avoid cycles

        invalidate_dynamic_regex_cache(webspace_id=webspace_id)
    except Exception:
        pass

    verification: dict[str, Any] | None = None
    if request_text:
        try:
            from adaos.services.nlu.probe import probe_phrase  # local import to avoid cycles

            probe = await probe_phrase(
                request_text,
                webspace_id=webspace_id,
                use_rasa=False,
                emit_trace=True,
            )
            matched = bool(probe.get("accepted")) and probe.get("intent") == intent.strip()
            verification = {
                "status": "intent_matched" if matched else "intent_mismatch",
                "expected_intent": intent.strip(),
                "probe": _compact_probe_result(probe),
            }
            await _mark_candidate_verification(
                webspace_id=webspace_id,
                candidate_id=candidate_id if isinstance(candidate_id, str) else None,
                verification=verification,
                candidate_patch=applied_candidate_patch,
            )
            bus_emit(
                ctx.bus,
                "nlp.teacher.candidate.verified",
                {
                    "webspace_id": webspace_id,
                    "candidate_id": candidate_id,
                    "rule_id": rule_id,
                    "target": dict(applied_to or {}),
                    "verification": verification,
                    "_meta": dict(meta),
                },
                source="nlu.regex_rules",
            )
            try:
                await append_event(
                    webspace_id,
                    make_event(
                        webspace_id=webspace_id,
                        request_id=request_id,
                        request_text=request_text,
                        kind="candidate.verified",
                        title="Candidate verified" if matched else "Candidate verification failed",
                        subtitle=f"{intent}".strip(),
                        raw={"candidate_id": candidate_id, "rule_id": rule_id, "verification": verification},
                        meta=meta,
                    ),
                )
            except Exception:
                _log.debug("failed to append teacher event (candidate.verified) webspace=%s", webspace_id, exc_info=True)
            if matched:
                understanding_payload = {
                    "webspace_id": webspace_id,
                    "request_id": request_id,
                    "candidate_id": candidate_id,
                    "rule_id": rule_id,
                    "intent": intent.strip(),
                    "text": request_text,
                    "target": dict(applied_to or {}),
                    "verification": verification,
                    "_meta": dict(meta),
                }
                bus_emit(
                    ctx.bus,
                    "nlp.teacher.understanding.acquired",
                    understanding_payload,
                    source="nlu.regex_rules",
                )
                try:
                    await append_event(
                        webspace_id,
                        make_event(
                            webspace_id=webspace_id,
                            request_id=request_id,
                            request_text=request_text,
                            kind="understanding.acquired",
                            title="Understanding acquired",
                            subtitle=f"{intent}".strip(),
                            raw=understanding_payload,
                            meta=meta,
                        ),
                    )
                except Exception:
                    _log.debug(
                        "failed to append teacher event (understanding.acquired) webspace=%s",
                        webspace_id,
                        exc_info=True,
                    )
                try:
                    bus_emit(
                        ctx.bus,
                        "ui.notify",
                        {
                            "text": f"NLU Teacher acquired a new understanding for intent '{intent.strip()}'.",
                            "webspace_id": webspace_id,
                            "_meta": dict(meta),
                        },
                        source="nlu.regex_rules",
                    )
                except Exception:
                    pass
        except Exception:
            _log.warning(
                "failed to verify applied regex rule webspace=%s candidate_id=%s",
                webspace_id,
                candidate_id,
                exc_info=True,
            )

    try:
        await append_event(
            webspace_id,
            make_event(
                webspace_id=webspace_id,
                request_id=request_id,
                request_text=request_text,
                kind="regex_rule.applied",
                title="Regex rule applied",
                subtitle=f"{intent}".strip(),
                raw={
                    **rule,
                    "target": dict(applied_to or {}),
                    **({"runtime_flags": dict(runtime_flags_update.get("flags") or {})} if runtime_flags_update else {}),
                },
                meta=meta,
            ),
        )
    except Exception:
        _log.debug("failed to append teacher event (regex_rule.applied) webspace=%s", webspace_id, exc_info=True)

    bus_emit(
        ctx.bus,
        "nlp.teacher.regex_rule.applied",
        {"webspace_id": webspace_id, "rule": {**rule, "target": dict(applied_to or {})}, "_meta": dict(meta)},
        source="nlu.regex_rules",
    )

    try:
        tgt = dict(applied_to or {})
        tgt_type = tgt.get("type")
        tgt_id = tgt.get("id")
        if tgt_type in {"skill", "scenario"} and isinstance(tgt_id, str) and tgt_id:
            where = "навык" if tgt_type == "skill" else "сценарий"
            text = f"Правило распознавания установлено в {where} «{tgt_id}»."
        else:
            text = "Правило распознавания установлено."
        bus_emit(
            ctx.bus,
            "ui.notify",
            {"text": text, "webspace_id": webspace_id, "_meta": dict(meta)},
            source="nlu.regex_rules",
        )
    except Exception:
        pass


@subscribe("nlp.teacher.regex_rule.apply")
async def _on_regex_rule_apply(evt: Any) -> None:
    await apply_regex_rule(evt)
